"""Build/check Day07 labels offline. Never run retrieval, ranking, model or agent.

cases/review/history are authored inputs. build regenerates queries/audit/manifest;
check verifies the frozen hashes and repeats source checks without writing files.
"""
import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from starter.convert_day6 import (convert_chunk, load_records, nonempty, page_number,
                                  query_record, require, resolve_source_path, sha256)

VERSION = '1.0.0'
PAGES = list(range(35, 64)) + [65, 66, 67]
MINIMUM = {('dev', True): 20, ('dev', False): 2,
           ('test', True): 5, ('test', False): 2}
COMMON = {'id', 'split', 'question', 'answerable', 'intent_id', 'provenance', 'verification'}
DERIVED = ('queries.dev.json', 'queries.test.json', 'audit.json')
AUTHORED = ('cases.json', 'review.json', 'history.json', 'README.md')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def normalized(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFC', value).casefold()))


def source_compact(value):
    # For PDF quote alignment ONLY: discard line wraps, soft/visible hyphens and
    # punctuation. Runtime evidence matching remains exact and case-sensitive.
    return ''.join(c for c in unicodedata.normalize('NFC', value).casefold() if c.isalnum())


def strings(values):
    return isinstance(values, list) and bool(values) and all(nonempty(v) for v in values)


def valid_pages(values):
    return (isinstance(values, list) and bool(values)
            and all(page_number(p) and p in PAGES for p in values)
            and len(values) == len(set(values)))


def validate_cases(cases, chunks, legacy):
    require(isinstance(cases, list) and bool(cases), 'cases must be a nonempty array')
    require(isinstance(chunks, list) and len(chunks) == 131, 'Expected 131 B chunks')
    require(len({c['id'] for c in chunks}) == 131, 'Duplicate chunk IDs')
    require(sorted({c['page'] for c in chunks}) == PAGES, 'B page scope changed')
    ids, counts, evidence_matches = set(), Counter(), []
    legacy_by_id = {q['id']: q for q in legacy}
    for q in cases:
        require(isinstance(q, dict), 'case must be an object')
        require(all(nonempty(q.get(k)) for k in ('id', 'question', 'intent_id')), 'Missing case string')
        require(q['id'] not in ids, 'Duplicate case ID')
        ids.add(q['id'])
        require(q.get('split') in ('dev', 'test') and type(q.get('answerable')) is bool,
                'Invalid split/answerable')
        counts[q['split'], q['answerable']] += 1
        fields = {'expected_answer', 'gold'} if q['answerable'] else {
            'expected_behavior', 'absence_reason', 'absence_check'}
        require(COMMON <= q.keys() and q.keys() <= COMMON | fields | {'filter'}, 'Unknown/missing case fields')
        require(fields <= q.keys(), 'Missing label fields')
        p = q['provenance']
        require(isinstance(p, dict) and p.get('origin') in ('day06_migrated', 'day07_authored'),
                'Missing provenance')
        if p['origin'] == 'day06_migrated':
            original = legacy_by_id.get(q['id'])
            require(original is not None and q['split'] == 'dev', 'All Day06 IDs must be dev')
            require(p.get('path') == 'data/day06/B/questions.json'
                    and p.get('original_id') == q['id'] and p.get('original_split') == original['split']
                    and nonempty(p.get('history')), 'Invalid migration provenance')
            require(all(q.get(k) == original.get(k) for k in ('question', 'expected_answer', 'filter')),
                    'Do not change inherited Day06 answers/questions/filters')
            if q.get('gold') != original['gold']:
                revision = p.get('label_revision', {})
                require(q['id'] == 'q20' and revision.get('original_gold') == original['gold']
                        and nonempty(revision.get('reason')) and len(q.get('gold', [])) == 1,
                        'Gold revision requires preserved original and source review')
                g, old = q['gold'][0], original['gold'][0]
                require(g.get('source_id') == old['source_id'] and g.get('page') == old['page']
                        and g.get('evidence') == old['evidence'][old['evidence'].index(
                            'team members from high-context cultures'):], 'Unapproved gold revision')
        else:
            require(q['id'] not in legacy_by_id and nonempty(p.get('method'))
                    and p.get('source_id') in {c['source_id'] for c in chunks}
                    and valid_pages(p.get('source_pages')), 'Invalid authored provenance')
            require('filter' not in q, 'New cases must not infer filters from gold')
        if 'filter' in q:
            require(isinstance(q['filter'], dict) and q['filter'] == legacy_by_id[q['id']].get('filter'),
                    'Only unchanged pre-existing Day06 filters allowed')
        v = q['verification']
        require(isinstance(v, dict) and v.get('status') == (
            'source_checked' if q['answerable'] else 'absence_checked'), 'Unresolved source/absence review')
        require(all(nonempty(v.get(k)) for k in ('reviewer', 'date', 'method', 'conditions_checked'))
                and valid_pages(v.get('pages_read')), 'Incomplete verification notes')
        if q['answerable']:
            require(nonempty(q.get('expected_answer')), 'Expected answer required')
            require(isinstance(q.get('gold'), list) and bool(q['gold']), 'Gold required')
            seen_gold = set()
            for i, g in enumerate(q['gold']):
                require(isinstance(g, dict) and set(g) == {'source_id', 'page', 'evidence'}, 'Gold schema')
                require(nonempty(g['source_id']) and page_number(g['page']) and nonempty(g['evidence']), 'Invalid gold')
                key = (g['source_id'], g['page'], g['evidence'])
                require(key not in seen_gold, 'Duplicate alternative evidence')
                seen_gold.add(key)
                require(g['page'] in v['pages_read'], 'Evidence page not reviewed')
                matches = [c['id'] for c in chunks if c['source_id'] == g['source_id']
                           and c['page'] == g['page'] and g['evidence'] in c['text']]
                require(bool(matches), f"{q['id']}: evidence {i} not in one correct source/page chunk")
                evidence_matches.append({'id': q['id'], 'split': q['split'], 'evidence_index': i,
                                         'source_id': g['source_id'], 'page': g['page'], 'chunk_ids': matches})
        else:
            require(q.get('expected_behavior') == 'search_and_abstain' and nonempty(q.get('absence_reason')),
                    'Missing abstention label/reason')
            a = q['absence_check']
            require(isinstance(a, dict) and set(a) == {'scope', 'pages_read', 'related_pages_inspected',
                    'terms', 'method', 'semantic_review'}, 'Absence check schema')
            require(a['pages_read'] == PAGES and v['pages_read'] == PAGES,
                    'Absence requires full selected corpus reading, not top-k')
            require(valid_pages(a['related_pages_inspected']) and strings(a['terms'])
                    and all(nonempty(a.get(k)) for k in ('scope', 'method', 'semantic_review')),
                    'Incomplete absence investigation')
    require(set(legacy_by_id) <= ids, 'Missing migrated Day06 cases')
    for key, minimum in MINIMUM.items():
        require(counts[key] >= minimum, f'Insufficient {key} cases: {counts[key]}/{minimum}')
    check_duplicates(cases)
    return {'counts': {s: {'answerable': counts[s, True], 'unanswerable': counts[s, False]}
                       for s in ('dev', 'test')}, 'evidence': evidence_matches}


def check_duplicates(cases):
    for a, b in itertools.combinations(cases, 2):
        x, y = normalized(a['question']), normalized(b['question'])
        require(x != y, 'Duplicate normalized question')
        if a['split'] == b['split']:
            continue
        require(a['intent_id'] != b['intent_id'], 'Cross-split answer intent overlap')
        tokens_x, tokens_y = set(x.split()), set(y.split())
        jaccard = len(tokens_x & tokens_y) / len(tokens_x | tokens_y)
        require(jaccard < .65 and SequenceMatcher(None, x, y).ratio() < .82,
                f"Possible cross-split paraphrase: {a['id']} / {b['id']}")
        if a['answerable'] and b['answerable']:
            require(normalized(a['expected_answer']) != normalized(b['expected_answer']),
                    'Cross-split same normalized answer')
            require(not ({source_compact(g['evidence']) for g in a['gold']}
                         & {source_compact(g['evidence']) for g in b['gold']}), 'Cross-split same evidence')


def validate_review(cases, review, case_hash):
    dev = [q['id'] for q in cases if q['split'] == 'dev']
    test = [q['id'] for q in cases if q['split'] == 'test']
    require(review.get('reviewed_cases_sha256') == case_hash, 'Semantic review stale: cases hash changed')
    require(review.get('reviewed_dev_ids') == dev and nonempty(review.get('method')),
            'Semantic review must cover every dev case')
    require(review.get('pending_user_confirmation') == [], 'Pending user confirmation; benchmark not sealed')
    rows = review.get('test_reviews', [])
    require(len(rows) == len(test) and {r['test_id'] for r in rows} == set(test), 'Missing test intent reviews')
    for r in rows:
        require(r.get('compared_to_dev_ids') == dev and r.get('decision') == 'distinct_answer_target'
                and r.get('nearest_dev_id') in dev and nonempty(r.get('reason')), 'Incomplete semantic review')
    return {'reviewed_dev_test_pairs': len(dev) * len(test),
            'reviewer': review['reviewer'], 'automatic_checks': 'exact question/answer/evidence, intent, Jaccard .65, sequence .82',
            'limitation': 'Heuristics do not prove semantic independence; assistant comparison is recorded, no external blind review.'}


def validate_queries(cases, queries):
    for split in ('dev', 'test'):
        require(isinstance(queries[split], list), 'Queries must be arrays')
        for q in queries[split]:
            require(isinstance(q, dict) and set(q) <= {'id', 'question', 'filter'}, 'Runtime label leak')
        require(queries[split] == [query_record(q) for q in cases if q['split'] == split],
                'Runtime queries differ from approved allowlist projection')


def validate_history(history, cases, legacy):
    require(history.get('version') == VERSION and history.get('base_commit') == 'c3a5d70', 'History version/base')
    require(history.get('id_split_mapping') == [
        {'original_id': q['id'], 'original_split': q['split'], 'day07_id': q['id'], 'day07_split': 'dev'}
        for q in legacy], 'Split mapping changed')
    require(history.get('new_ids') == [q['id'] for q in cases if q['provenance']['origin'] == 'day07_authored'],
            'New IDs missing from history')
    policy = history.get('test_policy', {})
    require(policy.get('state') == 'sealed_for_task11_evaluation'
            and policy.get('configuration_locked') is False and policy.get('evaluation_runs') == [],
            'v1 authoring stage must have no test evaluation/configuration selection')


PDF_SCRIPT = '''
import json, sys, pypdf
reader = pypdf.PdfReader(sys.argv[1])
pages = json.loads(sys.argv[2])
print(json.dumps({'page_count': len(reader.pages), 'pypdf_version': pypdf.__version__,
                  'pages': {str(n): reader.pages[n-1].extract_text() for n in pages}}, ensure_ascii=False))
'''


def audit_sources(cases, chunks, registry, day06_root, pdf_python):
    require(len(registry['sources']) == 1, 'Expected one source')
    s = registry['sources'][0]
    require(s['selected_pdf_pages'] == PAGES, 'Source selection changed')
    require(all(c['source_id'] == s['source_id'] and c['source'] == s['source'] for c in chunks), 'Unknown chunk source')
    path = resolve_source_path(s['source'], day06_root, registry)
    require(sha256(path) == s['snapshot']['sha256'], 'Original PDF hash mismatch')
    process = subprocess.run([str(pdf_python), '-c', PDF_SCRIPT, str(path), json.dumps(PAGES)],
                             capture_output=True, text=True, timeout=60)
    require(process.returncode == 0, 'PDF reader failed; use --pdf-python with pypdf installed. '
            + process.stderr[-1200:])
    extracted = json.loads(process.stdout)
    require(extracted['page_count'] == s['physical_page_count'], 'PDF physical page count mismatch')
    page_text = {int(n): text for n, text in extracted['pages'].items()}
    require(sorted(page_text) == PAGES and all(nonempty(t) for t in page_text.values()), 'Missing PDF pages')
    records = []
    for q in cases:
        if q['answerable']:
            for i, g in enumerate(q['gold']):
                require(source_compact(g['evidence']) in source_compact(page_text[g['page']]),
                        f"{q['id']}: quote not aligned with original PDF page {g['page']}")
                records.append({'id': q['id'], 'evidence_index': i, 'page': g['page'],
                                'pdf_quote_aligned': True, 'conditions_note': q['verification']['conditions_checked']})
    absence = []
    for q in cases:
        if q['answerable']:
            continue
        inventory = []
        for term in q['absence_check']['terms']:
            # Exhaustive literal inventory, including zero matches. No relevance
            # scoring, query embedding, ranking, selection, top-k or agent calls.
            inventory.append({'term': term,
                              'chunk_ids': [c['id'] for c in chunks if term.casefold() in c['text'].casefold()],
                              'pdf_pages': [n for n, t in page_text.items() if term.casefold() in t.casefold()]})
        absence.append({'id': q['id'], 'chunks_scanned': len(chunks), 'pdf_pages_scanned': PAGES,
                        'term_inventory': inventory, 'semantic_review': q['absence_check']['semantic_review'],
                        'visual_original_pdf_pages': [40, 54, 63],
                        'limitation': 'Term inventory is reproducible; semantic absence and visual reading are authored review notes, not automatically proved.'})
    return {'pdf_sha256': sha256(path), 'physical_page_count': extracted['page_count'],
            'selected_pdf_pages': PAGES, 'pypdf_version': extracted['pypdf_version'],
            'quote_alignment_rule': 'NFC/casefold then alphanumeric-only; source alignment only, runtime hits remain exact',
            'extracted_page_text_sha256': {str(n): hashlib.sha256(t.encode()).hexdigest() for n, t in page_text.items()},
            'answerable_reviews': records, 'absence_reviews': absence}


def artifacts(folder, day06_root, pdf_python):
    cases, review, history = (read(folder / n) for n in ('cases.json', 'review.json', 'history.json'))
    chunks, legacy, registry = (read(ROOT / 'data/day06/B' / n)
                                for n in ('chunks.json', 'questions.json', 'sources.json'))
    require([convert_chunk(c) for c in load_records(day06_root / 'starter/results/chunks_B.jsonl')] == chunks,
            'B snapshot does not match actual Day06 export')
    migrated = load_records(day06_root / 'benchmark/questions.jsonl')
    from starter.convert_day6 import convert_question
    require([convert_question(q) for q in migrated] == legacy, 'Day06 question snapshot mismatch')
    audit = validate_cases(cases, chunks, legacy)
    audit['duplicate_review'] = validate_review(cases, review, sha256(folder / 'cases.json'))
    validate_history(history, cases, legacy)
    audit['source_audit'] = audit_sources(cases, chunks, registry, day06_root, pdf_python)
    audit.update(schema_version=1, benchmark_version=VERSION, label_validation_only=True,
                 retrieval_executed=False, agent_executed=False, test_evaluation_executed=False,
                 pending_user_confirmation=[])
    queries = {s: [query_record(q) for q in cases if q['split'] == s] for s in ('dev', 'test')}
    validate_queries(cases, queries)
    return {**{f'queries.{s}.json': value for s, value in queries.items()}, 'audit.json': audit}


def manifest(folder, derived, day06_root):
    history_paths = ['starter/results/checkpoint6/selection_lock.json',
                     'starter/results/checkpoint6/test_summary.json',
                     'report/individual/2A202604024_NguyenHuuAn.md']
    snapshots = sorted((ROOT / 'data/day06').glob('*/*.json'))
    return {'schema_version': 1, 'benchmark_version': VERSION, 'date': '2026-11-16',
            'base_commit': 'c3a5d70', 'state': 'labels_verified_test_not_evaluated',
            'split_counts': derived['audit.json']['counts'],
            'gold_semantics': 'OR; exact single-chunk source/page/evidence; answerable only denominator',
            'artifacts_sha256': {**{n: sha256(folder / n) for n in AUTHORED},
                                 **{n: digest(v) for n, v in derived.items()}},
            'snapshot_sha256': {str(p.relative_to(ROOT)): sha256(p) for p in snapshots},
            'implementation_sha256': {n: sha256(ROOT / n) for n in ('scripts/day07_benchmark.py',)},
            'policy_sha256': {n: sha256(ROOT / n) for n in ('BENCHMARK.md', 'RULES.md',
                              'data/README.md', 'docs/DAY07_DATA_CONTRACT.md')},
            'day06_history_sha256': {n: sha256(day06_root / n) for n in history_paths},
            'source_pdf_sha256': derived['audit.json']['source_audit']['pdf_sha256'],
            'test_policy': read(folder / 'history.json')['test_policy'],
            'hash_note': 'Manifest cannot hash itself; record its SHA-256 in any later configuration/evaluation lock.'}


def check_hashes(folder, frozen):
    require(frozen.get('benchmark_version') == VERSION, 'Frozen benchmark version mismatch')
    for group, base in (('artifacts_sha256', folder), ('snapshot_sha256', ROOT),
                        ('implementation_sha256', ROOT), ('policy_sha256', ROOT)):
        for name, expected in frozen[group].items():
            require(sha256(base / name) == expected, f'Frozen hash mismatch: {name}')
    # Projection validation produces a targeted leak error even when no labels
    # are passed to any runtime. It is also used independently in regression tests.
    validate_queries(read(folder / 'cases.json'), {s: read(folder / f'queries.{s}.json') for s in ('dev', 'test')})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('build', 'check'))
    p.add_argument('--benchmark', type=Path, default=ROOT / 'benchmark/day07')
    p.add_argument('--day06-root', type=Path, required=True)
    p.add_argument('--pdf-python', type=Path, default=Path(sys.executable), help='Python with pypdf for original source audit')
    args = p.parse_args(argv)
    try:
        folder, day06_root = args.benchmark.resolve(), args.day06_root.resolve()
        require(not folder.is_relative_to(day06_root), 'Cannot write into Day06 snapshot')
        require(not any((folder / n).is_symlink() for n in (*AUTHORED, *DERIVED, 'manifest.json')),
                'Benchmark files must not be symlinks')
        if args.action == 'check':
            frozen = read(folder / 'manifest.json')
            check_hashes(folder, frozen)
        derived = artifacts(folder, day06_root, args.pdf_python)
        current_manifest = manifest(folder, derived, day06_root)
        if args.action == 'build':
            # All validation and source checks finish before writing any output.
            for name, value in {**derived, 'manifest.json': current_manifest}.items():
                (folder / name).write_bytes(encoded(value))
        else:
            require(frozen == current_manifest, 'Manifest/source audit changed; inspect differences before a new release')
            require(all(read(folder / n) == v for n, v in derived.items()), 'Generated artifact changed')
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        p.exit(2, f'Benchmark {args.action} failed: {exc}\n')
    print(f"{args.action}: {derived['audit.json']['counts']}; "
          f"{len(derived['audit.json']['evidence'])} evidence alternatives aligned. "
          'Label/source audit only; no retrieval/agent/test evaluation.')
    return 0


if __name__ == '__main__':
    main()
