"""Read-only Day06 readiness check, independent of ingestion and benchmarks."""
import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class CheckFailed(Exception):
    """Only messages deliberately safe to print belong here."""


def require(condition, message):
    if not condition:
        raise CheckFailed(message)


def load_config(env_file):
    from dotenv import dotenv_values

    # Never search parent directories for .env or implicitly import Day06 secrets.
    values = {**dotenv_values(env_file, interpolate=False), **os.environ}
    defaults = dotenv_values(ROOT / '.env.example', interpolate=False)
    config = {**defaults, **values}
    config['day06'] = (ROOT / config['DAY06_PATH']).resolve()
    for key in ('EMBEDDING_DIMENSIONS', 'CONNECTION_TIMEOUT'):
        config[key] = int(config[key])
        require(config[key] > 0, f'{key} must be positive.')
    endpoint = urlsplit(config['EMBEDDING_ENDPOINT'])
    require(endpoint.scheme in ('http', 'https') and endpoint.hostname
            and not endpoint.username and not endpoint.password
            and not endpoint.query and not endpoint.fragment,
            'EMBEDDING_ENDPOINT must be an HTTP(S) base URL without credentials/query.')
    require(bool(config['EMBEDDING_DIGEST']), 'EMBEDDING_DIGEST is required.')
    return config


def digest(value):
    return value.removeprefix('sha256:')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_artifacts(c):
    """Read only chunk exports and infrastructure artifacts; never open benchmark."""
    base = c['day06']
    results = base / 'starter/results'
    checkpoint = results / 'checkpoint5_initial.json'
    saved = json.loads(checkpoint.read_text(encoding='utf-8'))
    model = saved['model']
    report = {
        'source': 'Day06 files, not evidence of a running service',
        'checkpoint': str(checkpoint.relative_to(base)),
        'checkpoint_sha256': sha256(checkpoint),
        'recorded_postgres': saved['postgres'],
        'recorded_pgvector': saved['pgvector'],
        'recorded_stored': saved['stored'],
        'model': model['tag']['name'],
        'digest': model['tag']['digest'],
        'dimensions': model['tag']['details']['embedding_length'],
        'query_prefix': model['query_prefix'],
        'document_prefix': model['document_prefix'],
        'exports': {},
    }
    for strategy in ('A', 'B'):
        path = results / f'chunks_{strategy}.jsonl'
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        ids = {row['chunk_id'] for row in rows}
        require(len(rows) == len(ids), f'Duplicate chunk IDs in export {strategy}.')
        report['exports'][strategy] = {'count': len(rows), 'sha256': sha256(path)}
    # Parse constants without importing Day06 code (which could have side effects).
    constants = {}
    source = base / 'starter/src/embeddings.py'
    for node in ast.parse(source.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    report['embedding_code_sha256'] = sha256(source)
    require(constants['MODEL'] == report['model'] == c['EMBEDDING_MODEL'], 'Model differs from Day06.')
    require(digest(report['digest']) == digest(c['EMBEDDING_DIGEST']), 'Digest differs from Day06.')
    require(report['dimensions'] == c['EMBEDDING_DIMENSIONS'], 'Dimension differs from Day06.')
    for code, field, setting in (
        ('QUERY', 'query_prefix', 'EMBEDDING_QUERY_PREFIX'),
        ('DOCUMENT', 'document_prefix', 'EMBEDDING_DOCUMENT_PREFIX'),
    ):
        require(constants[code] == model[field] == c[setting], f'{setting} differs from Day06.')
    require({k: v['count'] for k, v in report['exports'].items()} == {'A': 206, 'B': 131},
            'Day06 exports differ from expected A=206, B=131.')
    return report


def request(c, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    req = Request(c['EMBEDDING_ENDPOINT'].rstrip('/') + '/api/' + path,
                  data=data, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=c['CONNECTION_TIMEOUT']) as response:
            return json.load(response)
    except HTTPError as exc:
        raise CheckFailed(f'Embedding API HTTP {exc.code}; response body withheld.') from None
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, PermissionError):
            message = 'Local network access denied by sandbox/OS; service status unknown.'
        elif isinstance(reason, ConnectionRefusedError):
            message = 'Embedding connection refused; start the existing local endpoint.'
        else:
            message = 'Embedding network/DNS/timeout error; verify endpoint and network permissions.'
        raise CheckFailed(message) from None


def check_embedding(c):
    models = request(c, 'tags')['models']
    tag = next((m for m in models if m.get('name') == c['EMBEDDING_MODEL']), None)
    require(tag is not None, 'Expected embedding model is absent; no pull or replacement performed.')
    require(digest(tag['digest']) == digest(c['EMBEDDING_DIGEST']), 'Live model digest mismatch.')
    # Day06 endpoint exposes these fields in tags. Standard Ollama may use show.
    details = tag
    if 'capabilities' not in tag:
        details = request(c, 'show', {'model': c['EMBEDDING_MODEL']})
    require('embedding' in details.get('capabilities', []), 'Live model lacks embedding capability.')
    native = tag.get('details', {}).get('embedding_length')
    if native is not None:
        require(native == c['EMBEDDING_DIMENSIONS'], 'Live native dimension mismatch.')
    inputs = [c['EMBEDDING_QUERY_PREFIX'] + 'project phases',
              c['EMBEDDING_DOCUMENT_PREFIX'] + 'project phases']
    reply = request(c, 'embed', {'model': c['EMBEDDING_MODEL'], 'input': inputs, 'truncate': False})
    require(reply.get('model') == c['EMBEDDING_MODEL'], 'Embedding response model mismatch.')
    vectors = reply.get('embeddings', [])
    require(len(vectors) == 2, 'Expected two real embedding vectors.')
    for vector in vectors:
        require(isinstance(vector, list) and len(vector) == c['EMBEDDING_DIMENSIONS']
                and all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                and any(vector), 'Invalid/nonfinite/zero embedding vector or wrong dimension.')
    return ({'source': 'live API', 'model': reply['model'], 'digest': tag['digest'],
             'probe_dimensions': [len(v) for v in vectors], 'query_prefix': c['EMBEDDING_QUERY_PREFIX'],
             'document_prefix': c['EMBEDDING_DOCUMENT_PREFIX'], 'truncate': False,
             'dimensions_parameter': None}, vectors[0])


def check_database(c, query_vector):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    require(bool(c.get('DATABASE_URL')), 'DATABASE_URL missing; set it privately in Day07 .env.')
    try:
        # Validate without logging the DSN or libpq exceptions (which can contain secrets).
        conninfo_to_dict(c['DATABASE_URL'])
        with psycopg.connect(c['DATABASE_URL'], connect_timeout=c['CONNECTION_TIMEOUT'],
                             options='-c default_transaction_read_only=on -c statement_timeout=10000') as db:
            db.read_only = True
            db.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            read_only, version = db.execute(
                "SELECT current_setting('transaction_read_only'), current_setting('server_version')"
            ).fetchone()
            require(read_only == 'on', 'Database transaction is not read-only; stopped.')
            extension = db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            require(extension is not None, 'pgvector is absent; no CREATE EXTENSION performed.')
            table = sql.Identifier(c['DATABASE_SCHEMA'], c['DATABASE_TABLE'])
            groups = db.execute(sql.SQL('''
                SELECT strategy, model_digest, count(*), count(DISTINCT chunk_id),
                       min(vector_dims(embedding)), max(vector_dims(embedding)),
                       count(*) FILTER (WHERE embedding IS NULL)
                FROM {} GROUP BY strategy, model_digest ORDER BY strategy, model_digest
            ''').format(table)).fetchall()
            rows = [dict(zip(('strategy', 'digest', 'count', 'distinct_ids', 'min_dimensions',
                              'max_dimensions', 'null_vectors'), row)) for row in groups]
            require(len(rows) == 2 and {r['strategy'] for r in rows} == {'A', 'B'},
                    'Database must have exactly physical A/B groups, one digest each.')
            for row in rows:
                expected = {'A': 206, 'B': 131}[row['strategy']]
                require(row['count'] == row['distinct_ids'] == expected, 'Database A/B count or ID mismatch.')
                require(digest(row['digest']) == digest(c['EMBEDDING_DIGEST']), 'Stored model digest mismatch.')
                require(row['min_dimensions'] == row['max_dimensions'] == c['EMBEDDING_DIMENSIONS']
                        and row['null_vectors'] == 0, 'Stored vector dimensions/null mismatch.')
            probe = 'skipped: embedding check did not pass'
            if query_vector is not None:
                for strategy in ('A', 'B'):
                    result = db.execute(sql.SQL('''
                        SELECT 1 - (embedding <=> %s::vector) FROM {}
                        WHERE strategy=%s AND model_digest=%s
                        ORDER BY embedding <=> %s::vector LIMIT 1
                    ''').format(table), (json.dumps(query_vector), strategy,
                                         rows[0]['digest'], json.dumps(query_vector))).fetchone()
                    require(result is not None and math.isfinite(result[0]), 'Cosine operator probe failed.')
                probe = 'passed on A and B using real query vector; no benchmark questions'
            # Context exit commits a read-only transaction; no DDL/DML can run here.
            return {'source': 'live SQL snapshot', 'transaction_read_only': read_only,
                    'postgres': version, 'pgvector': extension[0], 'stored': rows, 'cosine_probe': probe}
    except psycopg.Error as exc:
        code = exc.sqlstate
        raise CheckFailed('PostgreSQL connection/query failed; verify existing service, credentials, '
                          'schema, SELECT privileges and pgvector. '
                          f'SQLSTATE={code or "unavailable"}; connection details withheld.') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--offline', action='store_true', help='Only inspect artifacts; does not certify services')
    args = parser.parse_args(argv)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
              'mode': 'offline' if args.offline else 'live', 'checks': {}}
    if sys.version_info < (3, 11):
        parser.exit(2, 'Python >=3.11 required; use .venv/bin/python.\n')
    try:
        c = load_config(args.env_file)
    except Exception as exc:
        message = str(exc) if isinstance(exc, CheckFailed) else f'Invalid configuration or missing dependency ({type(exc).__name__}); use .env.example and requirements.txt.'
        report['checks']['configuration'] = {'status': 'error', 'message': message}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    def run(name, callback):
        try:
            value = callback()
            report['checks'][name] = {'status': 'ok', 'details': value}
            return value
        except Exception as exc:
            # Avoid raw exception text/tracebacks containing URLs, credentials or payloads.
            message = str(exc) if isinstance(exc, CheckFailed) else f'{type(exc).__name__}; verify expected schema/dependencies/files; details withheld.'
            report['checks'][name] = {'status': 'error', 'message': message}
            return None

    run('artifacts', lambda: check_artifacts(c))
    if not args.offline:
        def embedding():
            details, vector = check_embedding(c)
            query_vector.extend(vector)
            return details
        query_vector = []
        run('embedding', embedding)
        run('database', lambda: check_database(c, query_vector or None))
    report['services_verified'] = not args.offline and all(
        report['checks'][name]['status'] == 'ok' for name in ('embedding', 'database'))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(v['status'] == 'ok' for v in report['checks'].values()) else 1


if __name__ == '__main__':
    sys.exit(main())
