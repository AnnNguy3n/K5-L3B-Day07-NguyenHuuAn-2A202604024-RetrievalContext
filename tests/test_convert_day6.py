"""Regression coverage for the Day06 -> Day07 offline data boundary."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starter.convert_day6 import (build_source_registry, convert_chunk, convert_question,
                                 load_records, main, normalize_sql_row, query_record,
                                 resolve_source_path, unique_ids, validate_dev)


class ConversionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'day06'
        self.root.mkdir()
        (self.root / 'data/raw').mkdir(parents=True)
        self.pdf = self.root / 'data/raw/book.pdf'
        self.pdf.write_bytes(b'frozen source fixture')
        self.chunk = {'chunk_id': 'original-id', 'document_id': 'book-v1',
                      'source': 'data/raw/book.pdf', 'page': 3, 'text': 'First evidence. Second evidence.',
                      'metadata': {'page_id': 'book-v1:p003', 'printed_page': 1,
                                   'chunking': {'version': 4}, 'char_start': 0, 'char_end': 32}}
        self.question = {'id': 'q1', 'split': 'dev', 'question': 'Which evidence?',
                         'expected_answer': 'First or second.', 'gold_source_id': 'book-v1',
                         'gold_page': 3, 'evidence_quote': 'First evidence.',
                         'accepted_alternatives': [{'gold_source_id': 'book-v1', 'gold_page': 3,
                                                    'evidence_quote': 'Second evidence.', 'note': 'reviewed'}],
                         'filter': {'section': 'user-provided'}}
        self.manifest = {'sources': [{'source_id': 'book-v1', 'source_path_or_url': 'data/raw/book.pdf',
                                     'snapshot': {'sha256': hashlib.sha256(self.pdf.read_bytes()).hexdigest()},
                                     'physical_page_count': 4, 'selected_pdf_pages': [3],
                                     'license_or_permission': {'attribution': 'Original attribution'}}]}
        (self.root / 'data/manifest.json').write_text(json.dumps(self.manifest))

    def records(self, name, rows, jsonl=False):
        p = self.root / name
        p.write_text(('\ufeff\n' + '\n\n'.join(json.dumps(r) for r in rows) + '\n')
                     if jsonl else json.dumps(rows), encoding='utf-8')
        return p

    def test_jsonl_and_json_array_cli_equivalence(self):
        outputs = []
        for extension, jsonl in (('json', False), ('jsonl', True)):
            chunks = self.records('chunks.' + extension, [self.chunk], jsonl)
            questions = self.records('questions.' + extension, [self.question], jsonl)
            output = self.base / extension
            with patch('builtins.print'):
                self.assertEqual(main(['--chunks', str(chunks), '--questions', str(questions),
                                       '--day06-root', str(self.root), '--expected-chunks', '1',
                                       '--strict-dev', '--output', str(output)]), 0)
            outputs.append({name: json.loads((output / name).read_text()) for name in
                            ('chunks.json', 'questions.json', 'queries.dev.json', 'sources.json')})
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(len(outputs[0]['questions.json'][0]['gold']), 2)
        self.assertEqual(outputs[0]['queries.dev.json'], [query_record(self.question)])

    def test_jsonl_line_error_and_explicit_format(self):
        p = self.records('records.txt', [self.chunk], True)
        self.assertEqual(load_records(p, 'jsonl'), [self.chunk])
        p.write_text('{}\nnot json\n')
        with self.assertRaisesRegex(ValueError, 'line 2'):
            load_records(p, 'jsonl')
        p.write_text('{}')
        with self.assertRaises(ValueError):
            load_records(p, 'json')

    def test_lossless_export_mapping_and_independent_metadata(self):
        before = deepcopy(self.chunk)
        result = convert_chunk(self.chunk)
        self.assertEqual(result['id'], before['chunk_id'])
        self.assertEqual(result['source_id'], before['document_id'])
        for key in ('text', 'page', 'source', 'metadata'):
            self.assertEqual(result[key], before[key])
        result['metadata']['chunking']['version'] = 999
        self.assertEqual(self.chunk, before)

    def test_sql_metadata_promoted_and_all_trace_fields_preserved(self):
        row = {'chunk_id': self.chunk['chunk_id'], 'text': self.chunk['text'],
               'metadata': dict(self.chunk['metadata'], document_id=self.chunk['document_id'],
                                source=self.chunk['source'], page=self.chunk['page']),
               'score': 0.91, 'strategy': 'B', 'model_digest': 'frozen-digest'}
        original = deepcopy(row)
        result = normalize_sql_row(row)
        export = convert_chunk(self.chunk)
        for key in ('id', 'text', 'source_id', 'source', 'page'):
            self.assertEqual(result[key], export[key])
        for key in ('metadata', 'score', 'strategy', 'model_digest'):
            self.assertEqual(result[key], row[key])
        self.assertEqual(row, original)
        self.assertEqual(normalize_sql_row(result), result)

    def test_conflicts_and_invalid_required_fields_fail(self):
        bad_rows = [dict(self.chunk, id='different'), dict(self.chunk, page=True),
                    dict(self.chunk, text=''), dict(self.chunk, metadata=None),
                    dict(self.chunk, metadata={'page': 99}),
                    {k: v for k, v in self.chunk.items() if k != 'document_id'}]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                convert_chunk(row)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            unique_ids([convert_chunk(self.chunk)] * 2, 'chunk')

    def test_alternatives_become_or_evidence_and_are_idempotent(self):
        original = deepcopy(self.question)
        q = convert_question(self.question)
        self.assertEqual([g['evidence'] for g in q['gold']], ['First evidence.', 'Second evidence.'])
        self.assertEqual(q['gold'][1]['note'], 'reviewed')
        self.assertEqual(q['split'], 'dev')
        self.assertEqual(q['filter'], self.question['filter'])
        self.assertEqual(convert_question(q), q)
        self.assertEqual(self.question, original)

    def test_no_filter_inference_and_no_labels_in_query(self):
        for value in (None, {}, {'section': 'user-provided'}):
            q = deepcopy(self.question)
            if value is None:
                del q['filter']
            else:
                q['filter'] = value
            q['private_review'] = 'do not send to model'
            result = convert_question(q)
            query = query_record(result)
            self.assertEqual(set(query), {'id', 'question'} | ({'filter'} if value is not None else set()))
            self.assertEqual('filter' in result, value is not None)
            if value is not None:
                self.assertEqual(query['filter'], value)

    def test_source_resolution_uses_day06_root_and_is_portable(self):
        chunks = [convert_chunk(self.chunk)]
        registry = build_source_registry(chunks, self.manifest, self.root)
        self.assertEqual(resolve_source_path(self.chunk['source'], self.root, registry), self.pdf.resolve())
        moved = self.base / 'another-machine/day06'
        (moved / 'data/raw').mkdir(parents=True)
        (moved / 'data/raw/book.pdf').write_bytes(self.pdf.read_bytes())
        self.assertEqual(build_source_registry(chunks, self.manifest, moved), registry)
        self.assertEqual(resolve_source_path(self.chunk['source'], moved, registry), (moved / 'data/raw/book.pdf').resolve())
        self.assertNotIn(str(self.root), json.dumps(registry))
        self.assertEqual(registry['sources'][0]['license_or_permission'],
                         self.manifest['sources'][0]['license_or_permission'])

    def test_source_hash_page_and_locator_validation(self):
        for changes in ({'page': 4}, {'source': 'missing.pdf'}, {'source_id': 'wrong'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_source_registry([dict(convert_chunk(self.chunk), **changes)], self.manifest, self.root)
        self.pdf.write_bytes(b'changed snapshot')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            build_source_registry([convert_chunk(self.chunk)], self.manifest, self.root)
        for locator in ('../outside.pdf', '/absolute.pdf', 'https://example.org/book.pdf'):
            registry = {'sources': [{'source': 'identifier', 'relative_path': locator}]}
            with self.subTest(locator=locator), self.assertRaises(ValueError):
                resolve_source_path('identifier', self.root, registry)

    def test_dev_audit_checks_each_alternative_and_skips_test_labels(self):
        q = convert_question(self.question)
        test = deepcopy(q)
        test.update(id='test-only', split='test', gold=[{'intentionally': 'not inspected here'}])
        result = validate_dev([convert_chunk(self.chunk)], [q, test])
        self.assertEqual((result['questions'], result['evidence_count'], result['resolved_evidence']), (1, 2, 2))
        self.assertFalse(result['test_evidence_checked'])
        q['gold'][1]['evidence'] = 'not in any chunk'
        result = validate_dev([convert_chunk(self.chunk)], [q, test])
        self.assertEqual(result['unresolved'][0]['evidence_index'], 1)
        self.assertTrue(result['unresolved'][0]['source_page_exists'])

    def test_cli_rejects_count_duplicate_and_snapshot_output(self):
        chunks = self.records('chunks.json', [self.chunk])
        questions = self.records('questions.json', [self.question])
        args = ['--chunks', str(chunks), '--questions', str(questions), '--day06-root', str(self.root)]
        for tail in (['--expected-chunks', '2', '--output', str(self.base / 'bad-count')],
                     ['--output', str(self.root / 'forbidden')]):
            with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
                main(args + tail)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.root / 'forbidden').exists())
        chunks.write_text(json.dumps([self.chunk, self.chunk]))
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main(args + ['--output', str(self.base / 'duplicate')])

    def test_strict_dev_fails_without_rewriting_labels(self):
        q = deepcopy(self.question)
        q['evidence_quote'] = 'spans missing chunk boundary'
        chunks = self.records('chunks.json', [self.chunk])
        questions = self.records('questions.json', [q])
        args = ['--chunks', str(chunks), '--questions', str(questions), '--day06-root', str(self.root),
                '--output', str(self.base / 'out')]
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            main(args + ['--strict-dev'])
        self.assertFalse((self.base / 'out').exists())
        with patch('builtins.print'):
            main(args)
        report = json.loads((self.base / 'out/validation.json').read_text())
        self.assertEqual(len(report['dev']['unresolved']), 1)
        self.assertEqual(load_records(questions), [q])


if __name__ == '__main__':
    unittest.main()
