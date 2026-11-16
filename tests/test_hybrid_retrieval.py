"""Fixture tests prove fusion behavior; only opt-in smoke uses real services."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_hybrid_dev as runner
from scripts.check_connections import load_config
from src.dense_retrieval import DenseRetriever
from src.hybrid_retrieval import HybridRetriever, HybridRetrievalError
from src.lexical_retrieval import LexicalRetriever


def ranking(ids, branch):
    return [dict(id=identifier, text=f'text {identifier}', source_id='doc', source='doc.pdf', page=1,
                 metadata={'section': 'A', 'language': 'en', 'document_type': 'book'},
                 score=1. / rank, rank=rank, strategy=branch)
            for rank, identifier in enumerate(ids, 1)]


class Branch:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.calls = []
        self.last_trace = {}

    def search(self, query, *, filters=None, top_k=3):
        self.calls.append((query, deepcopy(filters), top_k))
        if self.error:
            raise self.error
        return deepcopy(self.rows[:top_k])


class HybridTests(unittest.TestCase):
    def hybrid(self, dense=('a', 'b', 'c'), lexical=('c', 'b', 'd'), **kwargs):
        return HybridRetriever(Branch(ranking(dense, 'dense')), Branch(ranking(lexical, 'lexical')), **kwargs)

    def test_hand_calculated_rrf_and_one_based_ranks(self):
        hybrid = self.hybrid()
        rows = hybrid.search('original')
        self.assertEqual([r['id'] for r in rows], ['c', 'b', 'a'])
        self.assertAlmostEqual(rows[0]['score'], 1/63 + 1/61)
        self.assertAlmostEqual(rows[1]['score'], 2/62)
        self.assertAlmostEqual(rows[2]['score'], 1/61)
        self.assertEqual([r['rank'] for r in rows], [1, 2, 3])
        self.assertEqual(rows[0]['component_ranks'], {'dense': 3, 'lexical': 1})
        self.assertEqual(rows[0]['component_scores'], {'dense': 1/3, 'lexical': 1.})
        self.assertTrue(all(r['strategy'] == 'hybrid' for r in rows))
        self.assertEqual(hybrid.last_trace['branches']['dense']['results'], ranking(('a', 'b', 'c'), 'dense'))

    def test_component_score_scales_do_not_change_fusion(self):
        hybrid = self.hybrid()
        before = hybrid.search('q')
        for row in hybrid.dense.rows:
            row['score'] *= 100000
        after = hybrid.search('q')
        self.assertEqual([(r['id'], r['score']) for r in before], [(r['id'], r['score']) for r in after])
        self.assertNotEqual(before[0]['component_scores'], after[0]['component_scores'])

    def test_ties_by_id_and_cross_branch_dedup(self):
        rows = self.hybrid(('b', 'a'), ('a', 'b')).search('q')
        self.assertEqual([r['id'] for r in rows], ['a', 'b'])
        self.assertEqual(rows[0]['score'], rows[1]['score'])
        self.assertEqual(len(rows), len({r['id'] for r in rows}))

    def test_same_query_and_filters_to_both_candidate_pools(self):
        hybrid = self.hybrid(candidate_k=7)
        filters = {'section': 'A', 'language': 'en', 'document_type': 'book'}
        original = '  Qúery RAW!?  '
        hybrid.search(original, filters=filters, top_k=2)
        self.assertEqual(hybrid.dense.calls, [(original, filters, 7)])
        self.assertEqual(hybrid.lexical.calls, hybrid.dense.calls)
        self.assertEqual(filters, {'section': 'A', 'language': 'en', 'document_type': 'book'})

    def test_filter_mutation_by_one_adapter_does_not_reach_peer_or_caller(self):
        hybrid = self.hybrid()
        original_search = hybrid.dense.search
        def mutating(query, *, filters, top_k):
            result = original_search(query, filters=filters, top_k=top_k)
            filters['language'] = 'vi'
            return result
        hybrid.dense.search = mutating
        filters = {'language': 'en'}
        hybrid.search('q', filters=filters)
        self.assertEqual(hybrid.lexical.calls[0][1], {'language': 'en'})
        self.assertEqual(filters, {'language': 'en'})

    def test_empty_one_or_both_branches_are_valid(self):
        for dense, lexical in (((), ('a',)), (('a',), ()), ((), ())):
            with self.subTest(dense=dense, lexical=lexical):
                hybrid = self.hybrid(dense, lexical)
                rows = hybrid.search('q')
                self.assertEqual(hybrid.last_trace['status'], 'ok')
                self.assertEqual(len(rows), int(bool(dense or lexical)))
                if rows:
                    self.assertEqual(rows[0]['score'], 1/61)
                    self.assertEqual(len(rows[0]['component_ranks']), 1)

    def test_branch_failure_is_explicit_no_fallback_and_peer_still_traced(self):
        for branch in ('dense', 'lexical'):
            with self.subTest(branch=branch):
                hybrid = self.hybrid()
                getattr(hybrid, branch).error = RuntimeError('private postgres://secret')
                with self.assertRaisesRegex(HybridRetrievalError, branch):
                    hybrid.search('q')
                trace = hybrid.last_trace
                self.assertEqual(trace['status'], 'error')
                self.assertEqual(trace['failed_stage'], 'branches')
                self.assertEqual(trace['branches'][branch]['error']['type'], 'RuntimeError')
                self.assertNotIn('secret', json.dumps(trace))
                self.assertTrue(hybrid.dense.calls and hybrid.lexical.calls)

    def test_both_branch_errors_are_recorded(self):
        hybrid = self.hybrid()
        hybrid.dense.error = OSError('offline')
        hybrid.lexical.error = RuntimeError('failure')
        with self.assertRaisesRegex(HybridRetrievalError, 'dense, lexical'):
            hybrid.search('q')
        self.assertTrue(all(e['status'] == 'error' for e in hybrid.last_trace['branches'].values()))

    def test_metadata_union_including_nested_sql_additions_and_copy_isolation(self):
        hybrid = self.hybrid(('a',), ('a',))
        hybrid.dense.rows[0]['metadata'].update(source='doc.pdf', page=1, document_id='doc', nested={'sql': [1]})
        hybrid.lexical.rows[0]['metadata']['nested'] = {'json': [2]}
        before = deepcopy((hybrid.dense.rows, hybrid.lexical.rows))
        row = hybrid.search('q')[0]
        self.assertEqual(row['metadata']['source'], 'doc.pdf')
        self.assertEqual(row['metadata']['nested'], {'sql': [1], 'json': [2]})
        row['metadata']['nested']['sql'].append(3)
        row['component_ranks']['dense'] = 999
        self.assertEqual((hybrid.dense.rows, hybrid.lexical.rows), before)
        self.assertEqual(hybrid.last_trace['branches']['dense']['results'][0]['metadata']['nested'], {'sql': [1]})
        self.assertEqual(hybrid.search('q')[0]['component_ranks']['dense'], 1)

    def test_shared_core_conflict_fails_even_outside_final_cutoff(self):
        for key, value in (('text', 'other text'), ('source_id', 'other'), ('source', 'other.pdf'), ('page', 2)):
            with self.subTest(key=key):
                hybrid = self.hybrid(('a', 'b'), ('a', 'b'))
                hybrid.lexical.rows[1][key] = value
                with self.assertRaisesRegex(HybridRetrievalError, key):
                    hybrid.search('q', top_k=1)
                self.assertEqual(hybrid.last_trace['failed_stage'], 'fusion')

    def test_shared_metadata_conflicts_including_nested_and_type(self):
        for dense_value, lexical_value in (('en', 'vi'), ({'page': 1}, {'page': 2}), (1, True)):
            hybrid = self.hybrid(('a',), ('a',))
            hybrid.dense.rows[0]['metadata']['shared'] = dense_value
            hybrid.lexical.rows[0]['metadata']['shared'] = lexical_value
            with self.subTest(dense=dense_value), self.assertRaisesRegex(HybridRetrievalError, 'metadata.shared'):
                hybrid.search('q')

    def test_invalid_configuration_and_adapter(self):
        for candidate_k in (0, -1, True, False, 2.5, '20', None):
            with self.subTest(candidate_k=candidate_k), self.assertRaises(ValueError):
                self.hybrid(candidate_k=candidate_k)
        for rrf_k in (-1, True, False, float('nan'), 1.5, '60', None):
            with self.subTest(rrf_k=rrf_k), self.assertRaises(ValueError):
                self.hybrid(rrf_k=rrf_k)
        with self.assertRaises(ValueError):
            HybridRetriever(object(), Branch())
        row = self.hybrid(('a',), (), rrf_k=0).search('q')[0]
        self.assertEqual(row['score'], 1.)

    def test_invalid_request_fails_before_any_branch(self):
        invalid = [dict(query=q) for q in ('', ' \t', None, 5)]
        invalid += [dict(query='q', top_k=k) for k in (0, -1, True, False, 1.5, '3', None, 21)]
        invalid += [dict(query='q', filters=f) for f in ([], False, {'page': '1'}, {'section': 2})]
        for kwargs in invalid:
            hybrid = self.hybrid()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                hybrid.search(**kwargs)
            self.assertFalse(hybrid.dense.calls or hybrid.lexical.calls)
        with self.assertRaises(TypeError):
            self.hybrid().search('q', {}, 3)

    def test_invalid_branch_rank_duplicate_score_order_schema_and_filter(self):
        mutations = (
            lambda rows: rows[0].update(rank=0),
            lambda rows: rows[0].update(rank=True),
            lambda rows: rows[0].update(strategy='wrong'),
            lambda rows: rows[1].update(id='a'),
            lambda rows: rows[0].update(score=float('nan')),
            lambda rows: rows[1].update(score=999.),
            lambda rows: rows[0].update(page=True),
            lambda rows: rows[0].pop('text'),
            lambda rows: rows[0]['metadata'].update(language='vi'),
        )
        for mutate in mutations:
            hybrid = self.hybrid()
            mutate(hybrid.dense.rows)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(HybridRetrievalError, 'dense'):
                hybrid.search('q', filters={'language': 'en'})

    def test_fresh_trace_after_success_then_error(self):
        hybrid = self.hybrid()
        hybrid.search('first')
        hybrid.dense.error = OSError('down')
        with self.assertRaises(HybridRetrievalError):
            hybrid.search('second')
        self.assertEqual(hybrid.last_trace['query'], 'second')
        self.assertNotIn('fusion_ms', hybrid.last_trace)


class FakeDense:
    """Uses real corpus for runner mechanics; never quality evidence."""
    def __init__(self, config):
        self.lexical = LexicalRetriever(runner.read(runner.lexical_runner.CORPUS))
        self.last_trace = {}
        self.readiness = {'fixture': True}

    def search(self, query, *, filters=None, top_k=3):
        rows = self.lexical.search(query, filters=filters, top_k=top_k)
        for row in rows:
            row['strategy'] = 'dense'
            row['metadata'].update(document_id=row['source_id'], source=row['source'], page=row['page'])
        return rows

    def close(self):
        pass


class HybridRunnerTests(unittest.TestCase):
    def fixture_run(self, output):
        with patch.object(runner, 'DenseRetriever', FakeDense), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(runner.run(output), 0)
        return runner.read(output / 'summary.json'), [json.loads(line) for line in (output / 'trace.jsonl').read_text().splitlines()]

    def test_run_uses_no_labels_and_audits_every_query_and_method(self):
        real_read = runner.read
        def runtime_only(path):
            self.assertNotIn(path.name, ('cases.json', 'queries.test.json'))
            return real_read(path)
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'read', side_effect=runtime_only):
            summary, records = self.fixture_run(Path(tmp) / 'run')
            self.assertEqual((summary['attempted'], summary['succeeded'], summary['errors']), (22, 22, 0))
            self.assertEqual(summary['config_sha256'], runner.object_hash(summary['config']))
            self.assertTrue(all(m['succeeded'] == 22 for m in summary['methods'].values()))
            runner.audit_records(records, summary)
            def no_labels(value):
                if isinstance(value, dict):
                    self.assertFalse({'gold', 'answerable', 'absence_reason', 'expected_answer'} & value.keys())
                    for item in value.values():
                        no_labels(item)
                elif isinstance(value, list):
                    for item in value:
                        no_labels(item)
            no_labels(records)
            with self.assertRaises(FileExistsError):
                runner.run(Path(tmp) / 'run')

    def test_audit_rejects_wrong_rrf_component_metadata_query_and_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary, records = self.fixture_run(Path(tmp) / 'run')
            mutations = (
                lambda r: r[0]['results'][0].update(score=.9),
                lambda r: r[0]['results'][0]['component_ranks'].update(dense=99),
                lambda r: r[0]['trace']['branches']['dense']['results'][0]['metadata'].update(page=999),
                lambda r: r[0]['query'].update(question='changed'),
                lambda r: r[0]['baselines']['lexical']['results'][0].update(score=999.),
                lambda r: r[0]['trace']['branches']['dense'].update(status='error', error={'type': 'OSError'}),
            )
            for mutate in mutations:
                invalid = deepcopy(records)
                mutate(invalid)
                with self.subTest(mutate=mutate), self.assertRaises((ValueError, HybridRetrievalError)):
                    runner.audit_records(invalid, summary)

    def test_score_is_offline_and_hash_bound_with_denominator_twenty(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            self.fixture_run(output)
            with patch.object(runner, 'DenseRetriever', side_effect=AssertionError('no service')), \
                    patch.object(runner, 'LexicalRetriever', side_effect=AssertionError('no index')), \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(runner.score(output), 0)
            metrics = runner.read(output / 'comparison.json')
            for method in runner.METHODS:
                self.assertEqual(metrics['methods'][method]['hit_at_1']['denominator'], 20)
                self.assertEqual(metrics['methods'][method]['excluded_unanswerable'], 2)
            with (output / 'trace.jsonl').open('a') as handle:
                handle.write('\n')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                runner.score(output)

    def test_runner_branch_failure_retains_all_queries_and_error_counts(self):
        def fail(adapter, query, **kwargs):
            raise OSError('private details')
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'DenseRetriever', FakeDense), \
                patch.object(FakeDense, 'search', fail), patch('sys.stdout', new_callable=io.StringIO):
            output = Path(tmp) / 'run'
            self.assertEqual(runner.run(output), 1)
            summary = runner.read(output / 'summary.json')
            self.assertEqual((summary['attempted'], summary['errors']), (22, 22))
            self.assertEqual(summary['methods']['lexical']['succeeded'], 22)
            self.assertEqual(runner.score(output), 1)
            comparison = runner.read(output / 'comparison.json')
            self.assertEqual(comparison['methods']['hybrid']['hit_at_1']['hits'], 0)
            self.assertEqual(comparison['methods']['hybrid']['hit_at_1']['denominator'], 20)

    def test_preflight_and_parameter_errors_do_not_claim_live_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO):
            for kwargs in (dict(candidate_k=2), dict(rrf_k=-1)):
                with self.assertRaises(ValueError):
                    runner.run(Path(tmp) / 'invalid', **kwargs)
            with patch.object(runner, 'DenseRetriever', side_effect=RuntimeError('secret')):
                output = Path(tmp) / 'failed'
                self.assertEqual(runner.run(output), 1)
            summary = runner.read(output / 'summary.json')
            self.assertEqual(summary['attempted'], 0)
            self.assertIn('preflight_error', summary)
            self.assertNotIn('secret', json.dumps(summary))


@unittest.skipUnless(os.environ.get('DENSE_LIVE_TEST') == '1', 'opt-in real Day06 B integration')
class HybridLiveTests(unittest.TestCase):
    def test_real_adapters_filter_before_cutoff_merge_metadata_and_empty_filter(self):
        dense = DenseRetriever(load_config(runner.ROOT / '.env'))
        try:
            chunks = runner.read(runner.lexical_runner.CORPUS)
            lexical = LexicalRetriever(chunks)
            hybrid = HybridRetriever(dense, lexical)
            full = dense.search('project phases', top_k=131)
            section = next(row['metadata']['section'] for row in reversed(full)
                           if row['metadata']['section'] != full[0]['metadata']['section'])
            filters = {'section': section, 'language': 'en',
                       'document_type': next(row['metadata']['document_type'] for row in full
                                             if row['metadata']['section'] == section)}
            result = hybrid.search('project phases', filters=filters)
            self.assertTrue(result)
            for entry in hybrid.last_trace['branches'].values():
                self.assertTrue(all(all(row['metadata'].get(k) == v for k, v in filters.items())
                                    for row in entry['results']))
            for row in result:
                self.assertEqual(row['strategy'], 'hybrid')
                if 'dense' in row['component_ranks']:
                    self.assertEqual(row['metadata']['document_id'], row['source_id'])
            self.assertEqual(hybrid.search('project phases', filters={'language': '__absent__'}), [])
        finally:
            dense.close()


if __name__ == '__main__':
    unittest.main()
