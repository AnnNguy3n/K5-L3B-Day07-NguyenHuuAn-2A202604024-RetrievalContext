"""Mock tests verify code only. Opt-in live tests use the existing read-only B store."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import psycopg

from scripts.check_connections import load_config
from scripts import run_dense_dev as runner
from src import dense_retrieval as dense

ROOT = dense.ROOT


def config():
    with patch.dict(os.environ, {}, clear=True):
        return load_config(ROOT / '.env.example')


def corpus():
    return json.loads((ROOT / 'data/day06/B/chunks.json').read_text())


def sql_row(chunk, score=0.8):
    return dict(chunk_id=chunk['id'], text=chunk['text'],
                metadata=dict(deepcopy(chunk['metadata']), document_id=chunk['source_id'],
                              source=chunk['source'], page=chunk['page']),
                model_digest=config()['EMBEDDING_DIGEST'], score=score)


def bare_retriever():
    retriever = dense.DenseRetriever.__new__(dense.DenseRetriever)
    retriever.config = config()
    retriever.expected = {c['id']: c for c in corpus()}
    retriever.stored_digest = retriever.config['EMBEDDING_DIGEST']
    retriever.db = Mock(closed=False)
    retriever.embedder = Mock()
    retriever.embedder.embed_query.return_value = [1.0] * 768
    retriever.readiness = {}
    retriever.last_trace = {}
    return retriever


class DenseTests(unittest.TestCase):
    def test_request_validation_before_network(self):
        retriever = bare_retriever()
        for query, filters, k in [('', None, 3), (' \n', None, 3), (None, None, 3),
                                  ('q', None, True), ('q', None, 0), ('q', None, 1.5),
                                  ('q', {'source': 'x'}, 3), ('q', {'section': None}, 3),
                                  ('q', [], 3), ('q', {'language': ['en']}, 3)]:
            with self.subTest(query=query, filters=filters, k=k), self.assertRaises(ValueError):
                retriever.search(query, filters=filters, top_k=k)
        retriever.embedder.embed_query.assert_not_called()
        retriever.db.execute.assert_not_called()
        self.assertEqual(dense.validate_request(' q ', None, 3), {})
        self.assertEqual(dense.validate_request('q', {}, 3), {})

    def test_parameterized_sql_filters_before_cutoff_and_safe_identifiers(self):
        retriever = bare_retriever()
        retriever.config['DATABASE_TABLE'] = 'table"; DROP TABLE x;--'
        retriever.db.execute.return_value.fetchall.return_value = []
        filters = {'section': "x' OR true --", 'language': 'en', 'document_type': 'book'}
        self.assertEqual(retriever.search(' original query ', filters=filters, top_k=1), [])
        statement, params = retriever.db.execute.call_args.args
        text = statement.as_string()
        self.assertIn('AS MATERIALIZED', text)
        self.assertLess(text.index('metadata @> %s'), text.index('ORDER BY'))
        self.assertLess(text.index('ORDER BY'), text.index('LIMIT %s'))
        self.assertIn('score DESC, chunk_id COLLATE "C" ASC', text)
        self.assertNotIn(filters['section'], text)
        self.assertIn('"table""; DROP TABLE x;--"', text)
        self.assertEqual(params[1:3], ('B', retriever.stored_digest))
        self.assertEqual(params[3].obj, filters)
        self.assertEqual(params[4], 1)
        retriever.embedder.embed_query.assert_called_once_with(' original query ')

    def test_schema_source_metadata_and_ties_are_preserved(self):
        retriever = bare_retriever()
        chunks = sorted(corpus()[:2], key=lambda c: c['id'])
        raw = [sql_row(c) for c in chunks]
        retriever.db.execute.return_value.fetchall.return_value = raw
        rows = retriever.search('query')
        for rank, (chunk, row) in enumerate(zip(chunks, rows), 1):
            for key in dense.CORE:
                self.assertEqual(row[key], chunk[key])
            self.assertEqual(row['metadata'], raw[rank - 1]['metadata'])
            self.assertEqual((row['rank'], row['strategy'], row['score']), (rank, 'dense', 0.8))
        rows[0]['metadata']['source'] = 'changed by consumer'
        self.assertNotEqual(rows[0]['metadata'], raw[0]['metadata'])

    def test_rejects_duplicate_bad_tie_order_nonfinite_or_outside_filter(self):
        chunks = sorted(corpus()[:2], key=lambda c: c['id'])
        invalid = [([sql_row(chunks[0]), sql_row(chunks[0])], None),
                   ([sql_row(chunks[1]), sql_row(chunks[0])], None),
                   ([sql_row(chunks[0], 0.1), sql_row(chunks[1], 0.9)], None),
                   ([sql_row(chunks[0], float('nan'))], None),
                   ([sql_row(chunks[0])], {'language': 'not-the-language'})]
        for raw, filters in invalid:
            retriever = bare_retriever()
            db = retriever.db
            db.execute.return_value.fetchall.return_value = raw
            with self.subTest(raw=raw[0]['chunk_id'], filters=filters), self.assertRaises(dense.DenseRetrievalError):
                retriever.search('query', filters=filters)
            db.close.assert_called_once()

    def test_wrong_dimension_nonfinite_zero_and_bool_stop_before_sql(self):
        for vector in ([1.0] * 767, [float('nan')] * 768, [0.] * 768, [True] * 768):
            retriever = bare_retriever()
            retriever.embedder.embed_query.return_value = vector
            with self.assertRaisesRegex(dense.DenseRetrievalError, 'Invalid query embedding'):
                retriever.search('query')
            retriever.db.execute.assert_not_called()

    def test_sql_core_metadata_and_digest_drift_rejected(self):
        retriever = bare_retriever()
        raw = sql_row(corpus()[0])
        mutations = [dict(raw, text='wrong'), dict(raw, chunk_id='absent'),
                     dict(raw, model_digest='different'), dict(raw, metadata={}),
                     dict(raw, metadata=dict(raw['metadata'], page=999)),
                     dict(raw, metadata=dict(raw['metadata'], unexpected='changed'))]
        for invalid in mutations:
            with self.subTest(invalid=invalid.keys()), self.assertRaises((ValueError, dense.DenseRetrievalError)):
                retriever._verify_row(invalid)

    def test_database_preflight_dimension_digest_count_and_readonly(self):
        for change, message in [('dimension', 'dimension'), ('digest', 'digest'),
                                ('count', 'count'), ('readonly', 'read-only'), ('zero', 'norm')]:
            retriever = bare_retriever()
            rows = [dict(sql_row(c), dimensions=768, norm=1.0) for c in corpus()]
            setting = {'value': 'on'}
            if change == 'dimension':
                rows[0]['dimensions'] = 767
            if change == 'digest':
                rows[0]['model_digest'] = 'wrong'
            if change == 'count':
                rows.pop()
            if change == 'readonly':
                setting['value'] = 'off'
            if change == 'zero':
                rows[0]['norm'] = 0.0
            retriever.db.execute.side_effect = [Mock(fetchone=lambda: setting), Mock(fetchall=lambda: rows)]
            with self.subTest(change=change), self.assertRaisesRegex(dense.DenseRetrievalError, message):
                retriever._verify_database()

    def test_close_idempotent_and_closed_search_has_fresh_trace(self):
        retriever = bare_retriever()
        db = retriever.db
        retriever.last_trace = {'events': ['previous success']}
        retriever.close()
        retriever.close()
        db.close.assert_called_once()
        with self.assertRaisesRegex(dense.DenseRetrievalError, 'closed'):
            retriever.search('query')
        self.assertEqual(retriever.last_trace['events'], [])

    def test_db_and_api_errors_propagate_without_fake_results_or_secret(self):
        for stage in ('embedding', 'search'):
            retriever = bare_retriever()
            db = retriever.db
            if stage == 'embedding':
                retriever.embedder.embed_query.side_effect = OSError('secret-marker')
            else:
                db.execute.side_effect = psycopg.OperationalError('password=secret-marker')
            with self.assertRaises(dense.DenseRetrievalError) as caught:
                retriever.search('query')
            self.assertNotIn('secret-marker', str(caught.exception))
            self.assertEqual(retriever.last_trace['failed_stage'], stage)
            if stage == 'embedding':
                db.execute.assert_not_called()
            else:
                db.close.assert_called_once()

    def test_constructor_failure_closes_connection_and_hides_credentials(self):
        cfg = config()
        cfg['DATABASE_URL'] = 'postgresql://user:secret-marker@localhost/db'
        for error in (None, psycopg.OperationalError('secret-marker')):
            db = Mock()
            with patch.object(dense.checks, 'check_artifacts', return_value={}), \
                    patch.object(dense, 'day06_embedder', return_value=Mock(info={})), \
                    patch.object(dense.psycopg, 'connect', side_effect=error, return_value=db), \
                    patch.object(dense.DenseRetriever, '_verify_database', side_effect=ValueError('secret-marker')):
                with self.assertRaises(dense.DenseRetrievalError) as caught:
                    dense.DenseRetriever(cfg)
            self.assertNotIn('secret-marker', str(caught.exception))
            if error is None:
                db.close.assert_called_once()

    def test_prefix_mismatch_rejected_before_endpoint(self):
        cfg = config()
        cfg['EMBEDDING_QUERY_PREFIX'] = cfg['EMBEDDING_QUERY_PREFIX'].rstrip()
        with patch.object(dense, 'day06_embedder') as embed:
            with self.assertRaisesRegex(dense.DenseRetrievalError, 'PREFIX differs'):
                dense.DenseRetriever(cfg)
        embed.assert_not_called()

    def test_day06_adapter_reuses_prefix_without_starter_collision(self):
        cfg = config()
        tag = dict(name=cfg['EMBEDDING_MODEL'], digest=cfg['EMBEDDING_DIGEST'],
                   capabilities=['embedding'], details={'embedding_length': 768})
        payloads = []
        def request(c, path, payload=None):
            if path == 'tags':
                return {'models': [tag]}
            if path == 'version':
                return {'version': 'test-only'}
            payloads.append(payload)
            return dict(model=cfg['EMBEDDING_MODEL'], embeddings=[[1.] * 768 for _ in payload['input']])
        starter = sys.modules['starter']
        with patch.object(dense.checks, 'request', side_effect=request):
            embed = dense.day06_embedder(cfg)
            embed.embed_query('  original question  ')
        self.assertIs(sys.modules['starter'], starter)
        self.assertEqual(payloads[-1]['input'], ['task: search result | query:   original question  '])
        self.assertFalse(payloads[-1]['truncate'])
        self.assertNotIn('dimensions', payloads[-1])

    def test_adapter_digest_dimension_and_api_fail_before_query(self):
        cfg = config()
        for tag in (dict(name=cfg['EMBEDDING_MODEL'], digest='wrong'),
                    dict(name=cfg['EMBEDDING_MODEL'], digest=cfg['EMBEDDING_DIGEST'],
                         details={'embedding_length': 767})):
            with patch.object(dense.checks, 'request', return_value={'models': [tag]}) as request:
                with self.assertRaises(dense.DenseRetrievalError):
                    dense.day06_embedder(cfg)
                self.assertEqual(request.call_count, 1)
        failure = HTTPError('private', 503, 'private', {}, None)
        try:
            with patch.object(dense.checks, 'urlopen', side_effect=failure):
                with self.assertRaisesRegex(dense.checks.CheckFailed, 'HTTP 503') as caught:
                    dense.day06_embedder(cfg)
        finally:
            failure.close()
        self.assertNotIn('private', str(caught.exception))


class OfflineScoreTests(unittest.TestCase):
    def sample(self):
        cases = [dict(id=f'q{i}', split='dev', question='q', answerable=True,
                      gold=[dict(source_id='s', page=1, evidence='first'),
                            dict(source_id='s', page=2, evidence='alternative')]) for i in range(20)]
        cases += [dict(id=f'abs{i}', split='dev', question='q', answerable=False) for i in range(2)]
        records = [dict(query={k: c[k] for k in ('id', 'question')}, status='ok', results=[
            dict(rank=1, source_id='s', page=2, text='contains alternative evidence')]) for c in cases]
        return records, cases

    def test_or_gold_denominator_twenty_and_unanswerable_excluded(self):
        records, cases = self.sample()
        result = runner.evaluate(records, cases)
        self.assertEqual(result['hit_at_1'], dict(hits=20, denominator=20, rate=1.0))
        self.assertIsNone(result['per_query'][-1]['hit_at_1'])

    def test_wrong_source_page_partial_quote_and_errors_are_not_hits(self):
        records, cases = self.sample()
        records[0]['results'][0]['source_id'] = 'wrong'
        records[1]['results'][0]['page'] = 99
        records[2]['results'][0]['text'] = 'alternativ'
        records[3].update(status='error', error={'type': 'DB failure'})
        result = runner.evaluate(records, cases)
        self.assertEqual(result['hit_at_1']['hits'], 16)
        self.assertEqual(result['errors'], ['q3'])
        self.assertEqual(result['hit_at_1']['denominator'], 20)

    def test_missing_duplicate_or_modified_queries_rejected(self):
        records, cases = self.sample()
        for invalid in (records[:-1], records + [records[0]],
                        [dict(records[0], query=dict(id='q0', question='changed'))] + records[1:]):
            with self.assertRaises(ValueError):
                runner.evaluate(invalid, cases)


@unittest.skipUnless(os.environ.get('DENSE_LIVE_TEST') == '1', 'opt-in real Day06 read-only smoke')
class DenseLiveTests(unittest.TestCase):
    def test_filter_before_top1_matches_full_ranking_and_snapshot(self):
        retriever = dense.DenseRetriever(load_config(ROOT / '.env'))
        try:
            query = 'project phases'
            full = retriever.search(query, top_k=131)
            self.assertEqual(len(full), 131)
            self.assertEqual(len({r['id'] for r in full}), 131)
            self.assertEqual(full, sorted(full, key=lambda r: (-r['score'], r['id'])))
            # Pick a section absent from global top-1: post-cutoff filtering would return nothing.
            section = next(r['metadata']['section'] for r in reversed(full)
                           if r['metadata']['section'] != full[0]['metadata']['section'])
            expected = next(r for r in full if r['metadata']['section'] == section)
            filtered = retriever.search(query, filters={'section': section}, top_k=1)
            self.assertEqual([r['id'] for r in filtered], [expected['id']])
            self.assertAlmostEqual(filtered[0]['score'], expected['score'], places=6)
            self.assertEqual(retriever.search(query, filters={'language': '__absent__'}), [])
            for row in full:
                for key in dense.CORE:
                    self.assertEqual(row[key], retriever.expected[row['id']][key])
            self.assertEqual(retriever.readiness['database']['transaction_read_only'], 'on')
        finally:
            retriever.close()


if __name__ == '__main__':
    unittest.main()
