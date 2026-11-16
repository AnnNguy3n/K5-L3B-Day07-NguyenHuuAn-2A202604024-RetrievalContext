"""Lexical contract and dev-only runner checks; no held-out retrieval evaluation."""
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_lexical_dev as runner
from src.lexical_retrieval import CORE_FIELDS, LexicalRetriever
from starter.baseline import TfidfIndex

ROOT = Path(__file__).resolve().parents[1]


def chunk(id, text, **metadata):
    return dict(id=id, source_id='doc', source='doc.pdf', page=1, text=text,
                metadata=dict(section='A', language='en', document_type='textbook', **metadata))


class LexicalRetrievalTests(unittest.TestCase):
    def test_raw_tf_smoothed_idf_and_cosine_ranking(self):
        rows = [chunk('b', 'alpha beta'), chunk('a', 'alpha alpha'), chunk('c', 'gamma')]
        results = LexicalRetriever(rows).search('alpha')
        self.assertEqual([r['id'] for r in results], ['a', 'b'])
        alpha_idf = math.log(4 / 3) + 1
        beta_idf = math.log(4 / 2) + 1
        self.assertAlmostEqual(results[0]['score'], 1)
        self.assertAlmostEqual(results[1]['score'], alpha_idf / math.sqrt(alpha_idf**2 + beta_idf**2))

    def test_ties_sort_ids_and_ranks_independent_of_input_order(self):
        rows = [chunk('z', 'alpha'), chunk('b', 'alpha'), chunk('a', 'alpha')]
        for order in (rows, rows[::-1]):
            results = LexicalRetriever(order).search('alpha', top_k=2)
            self.assertEqual([r['id'] for r in results], ['a', 'b'])
            self.assertEqual([r['rank'] for r in results], [1, 2])

    def test_no_match_punctuation_or_only_oov_returns_empty(self):
        index = LexicalRetriever([chunk('a', 'alpha'), chunk('b', '!!!')])
        for query in ('unseen', '!?', '不存在'):
            self.assertEqual(index.search(query), [])
        self.assertEqual(len(index.search('alpha', top_k=10)), 1)

    def test_oov_dropped_before_query_normalization(self):
        index = LexicalRetriever([chunk('a', 'alpha beta')])
        self.assertEqual(index.search('alpha unknown unknown'), index.search('alpha'))

    def test_unicode_nfc_casefold_and_word_tokenization(self):
        index = LexicalRetriever([chunk('a', 'Café Straße hoàn_tiền 2026')])
        for query in ('CAFE\u0301', 'STRASSE', 'HOÀN_TIỀN', '2026'):
            self.assertTrue(index.search(query), query)
        self.assertEqual(index.search('hoàn'), [])  # underscore stays within one \w+ token

    def test_filter_before_cutoff_and_idf_stays_global(self):
        rows = [chunk('a', 'alpha'), chunk('b', 'alpha'), chunk('z', 'alpha beta')]
        rows[-1]['metadata']['section'] = 'B'
        index = LexicalRetriever(rows)
        all_rows = index.search('alpha beta', top_k=10)
        original_idf = deepcopy(index._index.idf)
        result = index.search('alpha', filters={'section': 'B'}, top_k=1)
        self.assertEqual(result[0]['id'], 'z')
        unfiltered = index.search('alpha', top_k=10)
        self.assertEqual(result[0]['score'], next(r['score'] for r in unfiltered if r['id'] == 'z'))
        self.assertEqual(index._index.idf, original_idf)
        self.assertNotEqual(result[0]['score'], LexicalRetriever([rows[-1]]).search('alpha')[0]['score'])
        self.assertEqual(len(all_rows), 3)

    def test_filters_exact_and_combined_missing_key_not_matched(self):
        rows = [chunk('a', 'alpha'), chunk('b', 'alpha')]
        rows[1]['metadata']['language'] = 'vi'
        index = LexicalRetriever(rows)
        self.assertEqual([r['id'] for r in index.search('alpha', filters={'language': 'vi', 'section': 'A'})], ['b'])
        self.assertEqual(index.search('alpha', filters={'document_type': 'TEXTBOOK'}), [])
        self.assertEqual(index.search('alpha', filters={'section': ''}), [])
        del rows[0]['metadata']['section']
        self.assertEqual(LexicalRetriever(rows).search('alpha', filters={'section': 'A'})[0]['id'], 'b')

    def test_none_and_empty_filter_equivalent(self):
        index = LexicalRetriever([chunk('a', 'alpha')])
        self.assertEqual(index.search('alpha'), index.search('alpha', filters={}))

    def test_invalid_query(self):
        index = LexicalRetriever([chunk('a', 'alpha')])
        for query in ('', ' \n\t', None, 2, True, []):
            with self.subTest(query=query), self.assertRaises(ValueError):
                index.search(query)

    def test_top_k_validation_and_keyword_only(self):
        index = LexicalRetriever([chunk('a', 'alpha')])
        for k in (0, -1, True, False, 1.5, '3', None):
            with self.subTest(k=k), self.assertRaises(ValueError):
                index.search('alpha', top_k=k)
        self.assertEqual(len(index.search('alpha', top_k=1)), 1)
        with self.assertRaises(TypeError):
            index.search('alpha', {}, 3)

    def test_invalid_filters_even_with_oov_query(self):
        index = LexicalRetriever([chunk('a', 'alpha')])
        for filters in ([], '', False, {'page': '1'}, {'section': None}, {'language': 2}, {1: 'en'}):
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                index.search('unseen', filters=filters)

    def test_duplicate_id_rejected_before_df_build(self):
        row = chunk('a', 'alpha')
        with self.assertRaisesRegex(ValueError, 'unique'):
            LexicalRetriever([row, deepcopy(row)])

    def test_chunk_schema_validation(self):
        for value in (None, {}, [], [None], [{}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LexicalRetriever(value)
        for key, value in (('id', ''), ('text', 3), ('source_id', None), ('source', ' '),
                           ('page', True), ('page', 0), ('page', 1.2), ('metadata', [])):
            row = chunk('a', 'alpha')
            row[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                LexicalRetriever([row])

    def test_deep_snapshot_and_result_isolation(self):
        rows = [chunk('a', 'alpha', nested={'values': [1]})]
        original = deepcopy(rows)
        index = LexicalRetriever(rows)
        result = index.search('alpha')
        self.assertEqual(rows, original)
        self.assertEqual(set(result[0]), set(CORE_FIELDS) | {'score', 'rank', 'strategy'})
        self.assertEqual(result[0]['strategy'], 'lexical')
        rows[0]['text'] = 'changed'
        rows[0]['metadata']['nested']['values'].append(2)
        result[0]['metadata']['nested']['values'].append(3)
        result[0]['text'] = 'changed again'
        again = index.search('alpha')[0]
        self.assertEqual({k: again[k] for k in CORE_FIELDS}, original[0])

    def test_matches_starter_without_filter(self):
        rows = [chunk('a', 'alpha alpha beta'), chunk('b', 'alpha gamma'), chunk('c', 'other')]
        expected = TfidfIndex(rows).search('alpha beta unseen')
        result = LexicalRetriever(rows).search('alpha beta unseen')
        self.assertEqual([(r['id'], r['score']) for r in result], [(r['id'], r['score']) for r in expected])

    def test_real_corpus_core_metadata_and_unique_positive_results(self):
        rows = runner.read(runner.CORPUS)
        before = deepcopy(rows)
        result = LexicalRetriever(rows).search('project management', top_k=131)
        self.assertEqual(rows, before)
        self.assertEqual(len({r['id'] for r in result}), len(result))
        self.assertEqual([r['rank'] for r in result], list(range(1, len(result) + 1)))
        by_id = {r['id']: r for r in rows}
        for row in result:
            self.assertGreater(row['score'], 0)
            self.assertEqual({k: row[k] for k in CORE_FIELDS}, by_id[row['id']])


def offline_fixture():
    cases, records = [], []
    for i in range(22):
        case = dict(id=str(i), split='dev', question='alpha', filter={}, answerable=i < 20)
        if i < 20:
            case['gold'] = [dict(source_id='wrong', page=2, evidence='missing'),
                            dict(source_id='doc', page=1, evidence='alpha')]
        cases.append(case)
        row = {**chunk('a', 'alpha'), 'rank': 1, 'score': 1., 'strategy': 'lexical'}
        records.append(dict(query=dict(id=str(i), question='alpha', filter={}), top_k=3,
                            status='ok', results=[row]))
    return records, cases


class LexicalDevRunnerTests(unittest.TestCase):
    def test_offline_or_scoring_and_unanswerable_exclusion(self):
        records, cases = offline_fixture()
        records[0]['results'][0]['page'] = 2  # exact source AND page AND full quote required
        records[1]['results'][0]['text'] = 'alph'
        records[2]['results'][0]['source_id'] = 'other'
        records[3]['status'] = 'error'
        records[3]['error'] = {'type': 'RuntimeError', 'message': 'failure'}
        records[4]['results'].insert(0, {**chunk('b', 'other'), 'rank': 1})
        records[4]['results'][1]['rank'] = 2
        result = runner.evaluate(records, cases)
        self.assertEqual(result['hit_at_1'], dict(hits=15, denominator=20, rate=.75))
        self.assertEqual(result['evidence_hit_at_3'], dict(hits=16, denominator=20, rate=.8))
        self.assertEqual(result['errors'], ['3'])
        self.assertEqual(result['excluded_unanswerable'], 2)
        self.assertIsNone(result['per_query'][-1]['hit_at_1'])

    def test_offline_requires_all_dev_queries_and_original_filter(self):
        records, cases = offline_fixture()
        for bad in (records[:-1], records[:-1] + [records[0]]):
            with self.assertRaises(ValueError):
                runner.evaluate(bad, cases)
        records[0]['query']['filter'] = {'language': 'en'}
        with self.assertRaisesRegex(ValueError, 'query/filter'):
            runner.evaluate(records, cases)

    def test_rankings_reject_duplicate_zero_score_and_metadata_change(self):
        records, _ = offline_fixture()
        runner.validate_rankings(records, [chunk('a', 'alpha')])
        for change in ('duplicate', 'zero', 'metadata'):
            altered = deepcopy(records)
            row = altered[0]['results'][0]
            if change == 'duplicate':
                altered[0]['results'].append({**deepcopy(row), 'rank': 2})
            elif change == 'zero':
                row['score'] = 0
            else:
                row['metadata']['section'] = 'changed'
            with self.subTest(change=change), self.assertRaises(ValueError):
                runner.validate_rankings(altered, [chunk('a', 'alpha')])

    def test_runtime_query_hash_and_label_allowlist(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'BENCHMARK', Path(tmp)):
            path = Path(tmp) / 'queries.dev.json'
            queries = [dict(id=str(i), question='alpha') for i in range(22)]
            for key in ('gold', 'answerable', 'absence_reason', 'expected_answer'):
                bad = deepcopy(queries)
                bad[0][key] = 'label'
                runner.write(path, bad)
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'labels forbidden'):
                    runner.dev_queries({'artifacts_sha256': {'queries.dev.json': runner.sha256(path)}})
            with self.assertRaisesRegex(ValueError, 'hash'):
                runner.dev_queries({'artifacts_sha256': {'queries.dev.json': 'changed'}})

    def test_run_reads_no_labels_preserves_queries_and_logs_query_errors(self):
        real_read = runner.read
        def read_runtime_only(path):
            self.assertNotIn(path.name, ('cases.json', 'queries.test.json'))
            return real_read(path)
        real_search = LexicalRetriever.search
        def sometimes_fail(index, query, **kwargs):
            if 'closeout' in query:
                raise RuntimeError('synthetic search failure')
            return real_search(index, query, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner, 'read', side_effect=read_runtime_only), \
                patch.object(LexicalRetriever, 'search', sometimes_fail), patch('sys.stdout', new_callable=io.StringIO):
            output = Path(tmp) / 'run'
            self.assertEqual(runner.run(output), 1)
            summary = real_read(output / 'summary.json')
            self.assertEqual((summary['attempted'], summary['succeeded'], summary['errors']), (22, 21, 1))
            records = [json.loads(line) for line in (output / 'trace.jsonl').read_text().splitlines()]
            self.assertEqual([r['query'] for r in records], real_read(ROOT / 'benchmark/day07/queries.dev.json'))
            self.assertEqual(next(r for r in records if r['status'] == 'error')['error']['type'], 'RuntimeError')
            def check_no_labels(value):
                if isinstance(value, dict):
                    self.assertFalse({'gold', 'answerable', 'absence_reason', 'expected_answer'} & value.keys())
                    for child in value.values():
                        check_no_labels(child)
                elif isinstance(value, list):
                    for child in value:
                        check_no_labels(child)
            check_no_labels(records)

    def test_run_logs_corpus_hash_preflight_error(self):
        real_hash = runner.sha256
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stdout', new_callable=io.StringIO), \
                patch.object(runner, 'sha256', side_effect=lambda p: 'changed' if p == runner.CORPUS else real_hash(p)):
            output = Path(tmp) / 'run'
            self.assertEqual(runner.run(output), 1)
            summary = runner.read(output / 'summary.json')
            self.assertIn('Corpus B', summary['preflight_error']['message'])
            self.assertEqual(summary['attempted'], 0)

    def test_cli_runs_offline_from_other_working_directory_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'run'
            command = [sys.executable, str(ROOT / 'scripts/run_lexical_dev.py'), 'run', '--output', str(output)]
            first = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            summary = runner.read(output / 'summary.json')
            self.assertEqual((summary['attempted'], summary['succeeded']), (22, 22))
            self.assertEqual(runner.sha256(output / 'trace.jsonl'), summary['trace_sha256'])
            again = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(again.returncode, 2)


if __name__ == '__main__':
    unittest.main()
