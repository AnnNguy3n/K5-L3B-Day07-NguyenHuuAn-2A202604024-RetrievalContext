"""Run only Day07 dev queries; score saved rankings in a separate offline command."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_connections import load_config, sha256
from src.dense_retrieval import DenseRetriever, DenseRetrievalError, validate_request

BENCHMARK = ROOT / 'benchmark/day07'
OUTPUT = ROOT / 'artifacts/retrieval/dense'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def object_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def error_record(exc):
    return {'type': type(exc).__name__,
            'message': str(exc) if isinstance(exc, (DenseRetrievalError, ValueError)) else 'Unexpected failure; details withheld',
            'frames': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(exc.__traceback__)]}


def dev_queries():
    manifest = read(BENCHMARK / 'manifest.json')
    path = BENCHMARK / 'queries.dev.json'
    if sha256(path) != manifest['artifacts_sha256']['queries.dev.json']:
        raise ValueError('Dev query hash differs from manifest')
    queries = read(path)
    if len(queries) != 22 or len({q['id'] for q in queries}) != 22:
        raise ValueError('Expected all 22 unique dev queries')
    for q in queries:
        if set(q) - {'id', 'question', 'filter'} or not isinstance(q['id'], str) or not q['id']:
            raise ValueError('Invalid runtime query schema; labels are forbidden')
        validate_request(q['question'], q.get('filter'), 3)
    return queries


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    summary = dict(created_at_utc=datetime.now(timezone.utc).isoformat(), mode='live-dev',
                   status='error', top_k=3, query_rewrite=False, attempted=0, succeeded=0, errors=0,
                   python=platform.python_version(), test_executed=False, agent_executed=False)
    retriever = None
    records = []
    try:
        queries = dev_queries()
        config = load_config(ROOT / '.env')
        safe_keys = ('EMBEDDING_ENDPOINT', 'EMBEDDING_MODEL', 'EMBEDDING_DIGEST', 'EMBEDDING_DIMENSIONS',
                     'EMBEDDING_QUERY_PREFIX', 'EMBEDDING_DOCUMENT_PREFIX', 'DATABASE_SCHEMA',
                     'DATABASE_TABLE', 'CONNECTION_TIMEOUT')
        summary['config'] = {key: config[key] for key in safe_keys}
        summary['config'].update(day06=str(config['day06']), physical_strategy='B', top_k=3,
                                 database_url='withheld', transaction='read only / repeatable read')
        summary['config_sha256'] = object_hash(summary['config'])
        summary['hashes'] = {name: sha256(ROOT / name) for name in (
            'data/day06/B/chunks.json', 'benchmark/day07/manifest.json',
            'benchmark/day07/queries.dev.json', 'src/dense_retrieval.py',
            'scripts/run_dense_dev.py', 'starter/convert_day6.py', 'scripts/check_connections.py')}
        summary['benchmark_version'] = read(BENCHMARK / 'manifest.json')['benchmark_version']
        started = perf_counter()
        retriever = DenseRetriever(config)
        summary['preflight_ms'] = (perf_counter() - started) * 1000
        summary['readiness'] = retriever.readiness
        for query in queries:
            record = dict(query=deepcopy(query), filters=query.get('filter'), top_k=3, status='error')
            summary['attempted'] += 1
            started = perf_counter()
            try:
                record['results'] = retriever.search(query['question'], filters=query.get('filter'), top_k=3)
                record['status'] = 'ok'
                summary['succeeded'] += 1
            except Exception as exc:
                record['error'] = error_record(exc)
                summary['errors'] += 1
            record['elapsed_ms'] = (perf_counter() - started) * 1000
            record['trace'] = deepcopy(retriever.last_trace)
            records.append(record)
        summary['status'] = 'ok' if summary['succeeded'] == 22 else 'error'
        summary['timings_ms'] = {stage: sum(r['trace'].get(stage) or 0 for r in records)
                                 for stage in ('embedding_ms', 'search_ms')}
    except Exception as exc:
        summary['preflight_error'] = error_record(exc)
    finally:
        if retriever is not None:
            retriever.close()
        trace_path = output / 'trace.jsonl'
        trace_path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in records),
                              encoding='utf-8')
        summary['trace_sha256'] = sha256(trace_path)
        write(output / 'summary.json', summary)
    print(json.dumps({k: summary[k] for k in ('status', 'attempted', 'succeeded', 'errors')}, indent=2))
    print(str(output))
    return 0 if summary['status'] == 'ok' else 1


def evaluate(records, cases):
    """Pure offline OR scoring: exact source, page, and full quote in one chunk."""
    expected = {case['id']: case for case in cases if case['split'] == 'dev'}
    if len(expected) != 22 or {r['query']['id'] for r in records} != set(expected) or len(records) != 22:
        raise ValueError('Offline score requires exactly the 22 dev records, no duplicates')
    answerable = [case for case in expected.values() if case['answerable']]
    if len(answerable) != 20:
        raise ValueError('Offline score requires denominator 20 answerable dev cases')
    grades = []
    for record in records:
        case = expected[record['query']['id']]
        projection = {k: case[k] for k in ('id', 'question', 'filter') if k in case}
        if projection != record['query']:
            raise ValueError('Recorded query/filter differs from offline dev case')
        if record['status'] != 'ok':
            grades.append(dict(id=case['id'], answerable=case['answerable'], error=record.get('error'),
                               hit_at_1=False if case['answerable'] else None,
                               evidence_hit_at_3=False if case['answerable'] else None))
            continue
        results = record['results']
        if len(results) > 3 or [r['rank'] for r in results] != list(range(1, len(results) + 1)):
            raise ValueError('Invalid recorded top-3 ranks')
        matches = [r['rank'] for r in results if case['answerable'] and any(
            r['source_id'] == g['source_id'] and r['page'] == g['page'] and g['evidence'] in r['text']
            for g in case['gold'])]
        grades.append(dict(id=case['id'], answerable=case['answerable'], matched_ranks=matches,
                           hit_at_1=(1 in matches) if case['answerable'] else None,
                           evidence_hit_at_3=bool(matches) if case['answerable'] else None))
    def metric(key):
        hits = sum(g[key] is True for g in grades)
        return dict(hits=hits, denominator=20, rate=hits / 20)
    return dict(mode='offline-dev-score', hit_at_1=metric('hit_at_1'),
                evidence_hit_at_3=metric('evidence_hit_at_3'),
                errors=[g['id'] for g in grades if 'error' in g],
                excluded_unanswerable=2, abstention='not evaluated: no agent', per_query=grades)


def score(output):
    # No retriever, embedding API or database is instantiated in this command.
    summary = read(output / 'summary.json')
    trace = output / 'trace.jsonl'
    if sha256(trace) != summary['trace_sha256'] or summary.get('mode') != 'live-dev':
        raise ValueError('Trace integrity/mode mismatch')
    for name in ('benchmark/day07/manifest.json', 'benchmark/day07/queries.dev.json', 'data/day06/B/chunks.json'):
        if sha256(ROOT / name) != summary['hashes'][name]:
            raise ValueError('Run corpus/benchmark hash no longer matches')
    manifest = read(BENCHMARK / 'manifest.json')
    if sha256(BENCHMARK / 'cases.json') != manifest['artifacts_sha256']['cases.json']:
        raise ValueError('Offline labels differ from benchmark manifest')
    records = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines() if line]
    # cases is the shared offline label container; only dev is selected for scoring.
    result = evaluate(records, read(BENCHMARK / 'cases.json'))
    result.update(trace_sha256=sha256(trace), summary_sha256=sha256(output / 'summary.json'),
                  cases_sha256=sha256(BENCHMARK / 'cases.json'))
    write(output / 'metrics.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'per_query'}, indent=2))
    return 1 if result['errors'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'score'))
    parser.add_argument('--output', type=Path, help='New run directory; existing directory for offline score')
    args = parser.parse_args(argv)
    output = args.output or OUTPUT / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if args.command == 'score' and args.output is None:
        parser.error('score requires --output pointing to an existing run')
    try:
        return run(output) if args.command == 'run' else score(output)
    except (ValueError, OSError, KeyError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else 'check paths/schema/hash; details withheld'
        parser.exit(2, f'Dense dev command failed ({type(exc).__name__}): {detail}\n')


if __name__ == '__main__':
    sys.exit(main())
