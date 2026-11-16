"""Offline label/protection regressions; these tests never evaluate retrieval."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import day07_benchmark as benchmark


ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'benchmark/day07'


class Day07BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.cases = benchmark.read(FOLDER / 'cases.json')
        self.chunks = benchmark.read(ROOT / 'data/day06/B/chunks.json')
        self.legacy = benchmark.read(ROOT / 'data/day06/B/questions.json')

    def validate(self):
        return benchmark.validate_cases(self.cases, self.chunks, self.legacy)

    def case(self, id):
        return next(q for q in self.cases if q['id'] == id)

    def test_counts_migration_and_all_alternatives_resolve_without_ranking(self):
        result = self.validate()
        self.assertEqual(result['counts'], {'dev': {'answerable': 20, 'unanswerable': 2},
                                            'test': {'answerable': 5, 'unanswerable': 2}})
        self.assertEqual(len(result['evidence']), 26)
        self.assertEqual(len(self.case('q11')['gold']), 2)
        self.assertTrue(all(self.case(f'q{i:02}')['split'] == 'dev' for i in range(1, 21)))
        self.assertEqual({q['provenance']['original_split'] for q in self.cases
                          if q['id'] in {f'q{i}' for i in range(16, 21)}}, {'test'})

    def test_absence_has_no_fake_answer_gold_and_requires_full_scope(self):
        q = self.case('d07-t06')
        self.assertNotIn('gold', q)
        self.assertNotIn('expected_answer', q)
        q['absence_check']['pages_read'] = [43, 44]
        with self.assertRaisesRegex(ValueError, 'full selected corpus'):
            self.validate()

    def test_absence_rejects_gold_even_empty_and_wrong_behavior(self):
        q = self.case('d07-t07')
        q['gold'] = []
        with self.assertRaisesRegex(ValueError, 'fields'):
            self.validate()
        del q['gold']
        q['expected_behavior'] = 'answer_from_memory'
        with self.assertRaisesRegex(ValueError, 'abstention'):
            self.validate()

    def test_unique_ids_questions_and_split_minimum(self):
        self.cases.append(deepcopy(self.cases[0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate case ID'):
            self.validate()
        self.cases.pop()
        self.cases = [q for q in self.cases if q['id'] != 'd07-t07']
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            self.validate()

    def test_invalid_boolean_page_split_and_unknown_fields(self):
        original = deepcopy(self.cases)
        for change in ({'answerable': 1}, {'split': 'train'}, {'answerable_hint': True}):
            with self.subTest(change=change):
                self.cases = deepcopy(original)
                self.case('d07-t01').update(change)
                with self.assertRaises(ValueError):
                    self.validate()
        self.cases = deepcopy(original)
        self.case('d07-t01')['gold'][0]['page'] = True
        with self.assertRaisesRegex(ValueError, 'Invalid gold'):
            self.validate()

    def test_answerable_cannot_omit_gold_and_all_or_alternatives_must_be_valid(self):
        q = self.case('d07-t02')
        q['gold'].append({'source_id': q['gold'][0]['source_id'], 'page': 43, 'evidence': 'fabricated'})
        with self.assertRaisesRegex(ValueError, 'evidence 1 not in one'):
            self.validate()
        q['gold'] = []
        with self.assertRaisesRegex(ValueError, 'Gold required'):
            self.validate()

    def test_duplicate_alternative_and_wrong_source_page_rejected(self):
        q = self.case('d07-t03')
        q['gold'].append(deepcopy(q['gold'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate alternative'):
            self.validate()
        q['gold'].pop()
        q['gold'][0]['source_id'] = 'unindexed-source'
        with self.assertRaisesRegex(ValueError, 'correct source/page'):
            self.validate()
        q['gold'][0]['source_id'] = self.chunks[0]['source_id']
        q['gold'][0]['page'] = 47
        with self.assertRaisesRegex(ValueError, 'not reviewed'):
            self.validate()

    def test_q20_source_correction_keeps_original_snapshot_label(self):
        q = self.case('q20')
        original = next(q for q in self.legacy if q['id'] == 'q20')
        self.assertEqual(q['provenance']['label_revision']['original_gold'], original['gold'])
        self.assertIn('team members from high-context cultures', q['gold'][0]['evidence'])
        self.assertFalse(any(original['gold'][0]['evidence'] in c['text'] for c in self.chunks))
        self.assertTrue(any(q['gold'][0]['evidence'] in c['text'] for c in self.chunks))
        q['provenance']['label_revision']['original_gold'] = []
        with self.assertRaisesRegex(ValueError, 'preserved original'):
            self.validate()

    def test_do_not_silently_rewrite_inherited_answers_or_infer_filters(self):
        self.case('q01')['expected_answer'] = 'new answer'
        with self.assertRaisesRegex(ValueError, 'inherited'):
            self.validate()
        self.setUp()
        self.case('d07-t01')['filter'] = {'page': 38}
        with self.assertRaisesRegex(ValueError, 'infer filters'):
            self.validate()

    def test_runtime_queries_are_exact_allowlist_and_reject_all_label_leaks(self):
        queries = {s: benchmark.read(FOLDER / f'queries.{s}.json') for s in ('dev', 'test')}
        benchmark.validate_queries(self.cases, queries)
        for field in ('gold', 'answerable', 'absence_reason', 'expected_answer', 'verification', 'source_page'):
            with self.subTest(field=field):
                bad = deepcopy(queries)
                bad['test'][0][field] = 'secret label'
                with self.assertRaisesRegex(ValueError, 'Runtime label leak'):
                    benchmark.validate_queries(self.cases, bad)
        queries['test'].pop()
        with self.assertRaisesRegex(ValueError, 'projection'):
            benchmark.validate_queries(self.cases, queries)

    def test_cross_split_same_intent_answer_evidence_and_paraphrase_alarms(self):
        a = deepcopy(self.case('q17'))
        b = deepcopy(self.case('d07-t01'))
        for field in ('intent_id', 'expected_answer', 'gold'):
            with self.subTest(field=field):
                altered = deepcopy(b)
                altered[field] = deepcopy(a[field])
                with self.assertRaises(ValueError):
                    benchmark.check_duplicates([a, altered])
        b['question'] = a['question'].replace('ideal', 'optimal')
        with self.assertRaisesRegex(ValueError, 'Possible cross-split paraphrase'):
            benchmark.check_duplicates([a, b])
        b['question'] = a['question'].upper() + '?'
        with self.assertRaisesRegex(ValueError, 'Duplicate normalized question'):
            benchmark.check_duplicates([a, b])

    def test_semantic_review_is_hash_bound_and_covers_all_pairs(self):
        review = benchmark.read(FOLDER / 'review.json')
        h = benchmark.sha256(FOLDER / 'cases.json')
        self.assertEqual(benchmark.validate_review(self.cases, review, h)['reviewed_dev_test_pairs'], 154)
        with self.assertRaisesRegex(ValueError, 'stale'):
            benchmark.validate_review(self.cases, review, '0' * 64)
        review['test_reviews'][0]['compared_to_dev_ids'].pop()
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            benchmark.validate_review(self.cases, review, h)

    def test_pending_source_or_user_confirmation_blocks_release(self):
        self.case('d07-t01')['verification']['status'] = 'pending'
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            self.validate()
        review = benchmark.read(FOLDER / 'review.json')
        review['pending_user_confirmation'] = ['d07-t07']
        with self.assertRaisesRegex(ValueError, 'Pending user'):
            benchmark.validate_review(self.cases, review, benchmark.sha256(FOLDER / 'cases.json'))

    def test_history_cannot_claim_a_test_run_during_construction(self):
        history = benchmark.read(FOLDER / 'history.json')
        benchmark.validate_history(history, self.cases, self.legacy)
        history['test_policy']['evaluation_runs'] = [{'config': 'chosen-from-test'}]
        with self.assertRaisesRegex(ValueError, 'no test evaluation'):
            benchmark.validate_history(history, self.cases, self.legacy)

    def test_source_audit_rejects_wrong_original_pdf_hash_before_extraction(self):
        registry = benchmark.read(ROOT / 'data/day06/B/sources.json')
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / 'wrong.pdf'
            pdf.write_bytes(b'wrong snapshot')
            with patch.object(benchmark, 'resolve_source_path', return_value=pdf), \
                    patch.object(benchmark.subprocess, 'run') as extraction:
                with self.assertRaisesRegex(ValueError, 'PDF hash mismatch'):
                    benchmark.audit_sources(self.cases, self.chunks, registry, Path(tmp), Path('python'))
                extraction.assert_not_called()

    def test_frozen_hashes_and_projection_reject_tampering(self):
        frozen = benchmark.read(FOLDER / 'manifest.json')
        benchmark.check_hashes(FOLDER, frozen)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            for name in frozen['artifacts_sha256']:
                (target / name).write_bytes((FOLDER / name).read_bytes())
            path = target / 'queries.test.json'
            value = benchmark.read(path)
            value[0]['answerable'] = True
            path.write_bytes(benchmark.encoded(value))
            with self.assertRaisesRegex(ValueError, 'Frozen hash mismatch'):
                benchmark.check_hashes(target, frozen)

    def test_audit_is_labels_only_and_absence_scans_every_chunk_page(self):
        audit = benchmark.read(FOLDER / 'audit.json')
        self.assertTrue(audit['label_validation_only'])
        for field in ('retrieval_executed', 'agent_executed', 'test_evaluation_executed'):
            self.assertFalse(audit[field])
        for row in audit['source_audit']['absence_reviews']:
            self.assertEqual(row['chunks_scanned'], 131)
            self.assertEqual(row['pdf_pages_scanned'], benchmark.PAGES)
            self.assertTrue(row['term_inventory'])
        # PDF alignment normalization is deliberately NOT used by runtime hit.
        self.assertEqual(benchmark.source_compact('synchro-\nnous'), 'synchronous')


if __name__ == '__main__':
    unittest.main()
