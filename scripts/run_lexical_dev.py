"""Run all 22 label-free dev queries, or score their saved top-3 offline."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.lexical_retrieval import CORE_FIELDS, LexicalRetriever, validate_request

BENCHMARK = ROOT / 'benchmark/day07'
CORPUS = ROOT / 'data/day06/B/chunks.json'
OUTPUT = ROOT / 'artifacts/retrieval/lexical'
CONFIG = {
    'strategy': 'lexical', 'physical_strategy': 'B', 'top_k': 3,
    'normalization': 'NFC then casefold', 'tokenization': r'Unicode re.findall(r"\w+")',
    'tf': 'raw term count', 'idf': 'ln((1 + N) / (1 + df)) + 1',
    'fit_scope': 'all 131 corpus B chunks; once at build',
    'similarity': 'cosine; document and in-vocabulary query L2 normalized',
    'oov': 'drop before query normalization; empty vector returns []',
    'filters': 'exact metadata section/language/document_type; before cutoff',
    'tie_break': 'id ascending', 'minimum_score': 'strictly > 0',
    'query_rewrite': False, 'stopwords': False, 'stemming': False,
    'services': [],
}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def error_record(exc):
    return {'type': type(exc).__name__, 'message': str(exc)}


def dev_queries(manifest):
    path = BENCHMARK / 'queries.dev.json'
    if sha256(path) != manifest['artifacts_sha256']['queries.dev.json']:
        raise ValueError('Dev query hash differs from manifest')
    queries = read(path)
    if not isinstance(queries, list) or len(queries) != 22:
        raise ValueError('Expected all 22 dev queries')
    seen = set()
    for query in queries:
        if (not isinstance(query, dict) or set(query) - {'id', 'question', 'filter'}
                or not isinstance(query.get('id'), str) or not query['id'].strip()
                or query['id'] in seen):
            raise ValueError('Invalid runtime query schema or duplicate ID; labels forbidden')
        validate_request(query.get('question'), query.get('filter'), 3)
        seen.add(query['id'])
    return queries


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    summary = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), mode='offline-lexical-dev',
                   status='error', split='dev', top_k=3, attempted=0, succeeded=0, errors=0,
                   python=platform.python_version(), config=deepcopy(CONFIG),
                   config_sha256=object_hash(CONFIG), test_executed=False, agent_executed=False)
    records = []
    try:
        manifest = read(BENCHMARK / 'manifest.json')
        summary['benchmark_version'] = manifest['benchmark_version']
        summary['hashes'] = {name: sha256(ROOT / name) for name in (
            'data/day06/B/chunks.json', 'benchmark/day07/manifest.json',
            'benchmark/day07/queries.dev.json', 'src/lexical_retrieval.py',
            'scripts/run_lexical_dev.py', 'starter/baseline.py')}
        if sha256(CORPUS) != manifest['snapshot_sha256']['data/day06/B/chunks.json']:
            raise ValueError('Corpus B differs from locked snapshot')
        queries = dev_queries(manifest)
        chunks = read(CORPUS)
        if len(chunks) != 131 or len({(c['source_id'], c['page']) for c in chunks}) != 32:
            raise ValueError('Expected fixed B corpus: 131 chunks / 32 pages')
        started = perf_counter()
        retriever = LexicalRetriever(chunks)
        summary['build_ms'] = (perf_counter() - started) * 1000
        summary.update(chunks=len(chunks), pages=32, vocabulary_size=retriever.vocabulary_size)
        for query in queries:
            record = dict(query=deepcopy(query), filters=deepcopy(query.get('filter')),
                          top_k=3, status='error', results=[])
            summary['attempted'] += 1
            started = perf_counter()
            try:
                record['results'] = retriever.search(query['question'], filters=query.get('filter'), top_k=3)
                record['status'] = 'ok'
                summary['succeeded'] += 1
            except Exception as exc:
                record['error'] = error_record(exc)
                summary['errors'] += 1
            record['search_ms'] = (perf_counter() - started) * 1000
            records.append(record)
        timings = [record['search_ms'] for record in records]
        summary['timings_ms'] = dict(search_total=sum(timings), search_mean=statistics.mean(timings),
                                     search_median=statistics.median(timings), search_max=max(timings))
        summary['status'] = 'ok' if summary['succeeded'] == 22 else 'error'
    except Exception as exc:
        summary['preflight_error'] = error_record(exc)
    finally:
        trace = output / 'trace.jsonl'
        trace.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records),
                         encoding='utf-8')
        summary['trace_sha256'] = sha256(trace)
        write(output / 'summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary['status'] == 'ok' else 1


def evaluate(records, dev_cases):
    """Offline only: one exact source/page/full evidence match among OR alternatives."""
    expected = {case['id']: case for case in dev_cases}
    if (len(dev_cases) != 22 or len(expected) != 22 or any(c['split'] != 'dev' for c in dev_cases)
            or len(records) != 22 or {r['query']['id'] for r in records} != set(expected)):
        raise ValueError('Scoring requires exactly the 22 unique dev cases and records')
    if sum(case['answerable'] is True for case in dev_cases) != 20:
        raise ValueError('Scoring requires 20 answerable dev cases')
    grades = []
    for record in records:
        case = expected[record['query']['id']]
        if record['query'] != {k: case[k] for k in ('id', 'question', 'filter') if k in case}:
            raise ValueError('Recorded query/filter differs from offline dev case')
        matches = []
        if record['status'] == 'ok' and case['answerable']:
            matches = [row['rank'] for row in record['results'] if any(
                row['source_id'] == gold['source_id'] and row['page'] == gold['page']
                and gold['evidence'] in row['text'] for gold in case['gold'])]
        grade = dict(id=case['id'], answerable=case['answerable'], matched_ranks=matches,
                     hit_at_1=(1 in matches) if case['answerable'] else None,
                     evidence_hit_at_3=bool(matches) if case['answerable'] else None)
        if record['status'] != 'ok':
            grade['error'] = record.get('error', {'message': 'query failed'})
        grades.append(grade)
    def metric(key):
        hits = sum(g[key] is True for g in grades)
        return dict(hits=hits, denominator=20, rate=hits / 20)
    return dict(mode='offline-dev-score', top_k=3, hit_at_1=metric('hit_at_1'),
                evidence_hit_at_3=metric('evidence_hit_at_3'), excluded_unanswerable=2,
                errors=[g['id'] for g in grades if 'error' in g], per_query=grades)


def validate_rankings(records, chunks):
    corpus = {c['id']: c for c in chunks}
    for record in records:
        validate_request(record['query']['question'], record['query'].get('filter'), record['top_k'])
        rows = record['results']
        if (record['top_k'] != 3 or len(rows) > 3 or len({r['id'] for r in rows}) != len(rows)
                or [r['rank'] for r in rows] != list(range(1, len(rows) + 1))
                or rows != sorted(rows, key=lambda r: (-r['score'], r['id']))):
            raise ValueError('Invalid top-3 ranking')
        for row in rows:
            if (row['strategy'] != 'lexical' or not 0 < row['score'] <= 1 + 1e-12
                    or row['id'] not in corpus
                    or any(row[key] != corpus[row['id']][key] for key in CORE_FIELDS)
                    or any(row['metadata'].get(k) != v for k, v in (record['query'].get('filter') or {}).items())):
                raise ValueError('Recorded result differs from corpus/filter/score contract')


def score(output):
    summary = read(output / 'summary.json')
    trace = output / 'trace.jsonl'
    if summary.get('mode') != 'offline-lexical-dev' or sha256(trace) != summary['trace_sha256']:
        raise ValueError('Trace integrity/mode mismatch')
    for name in ('data/day06/B/chunks.json', 'benchmark/day07/manifest.json', 'benchmark/day07/queries.dev.json'):
        if sha256(ROOT / name) != summary['hashes'][name]:
            raise ValueError('Run corpus/benchmark hash no longer matches')
    manifest = read(BENCHMARK / 'manifest.json')
    if sha256(BENCHMARK / 'cases.json') != manifest['artifacts_sha256']['cases.json']:
        raise ValueError('Offline label hash differs from manifest')
    records = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line]
    validate_rankings(records, read(CORPUS))
    # Shared label container: project dev immediately, never evaluate test records.
    cases = [case for case in read(BENCHMARK / 'cases.json') if case['split'] == 'dev']
    result = evaluate(records, cases)
    result.update(trace_sha256=sha256(trace), summary_sha256=sha256(output / 'summary.json'),
                  cases_sha256=sha256(BENCHMARK / 'cases.json'))
    write(output / 'metrics.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'per_query'}, indent=2))
    return 1 if result['errors'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'score'))
    parser.add_argument('--output', type=Path, help='New run directory; existing directory for score')
    args = parser.parse_args(argv)
    if args.command == 'score' and args.output is None:
        parser.error('score requires --output')
    output = args.output or OUTPUT / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    try:
        return run(output) if args.command == 'run' else score(output)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'Lexical dev command failed ({type(exc).__name__}): {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
