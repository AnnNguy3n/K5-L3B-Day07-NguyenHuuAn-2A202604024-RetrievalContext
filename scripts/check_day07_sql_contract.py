"""Read-only SQL/export parity check; no embeddings, benchmark or ranking."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_connections import CheckFailed, load_config
from starter.convert_day6 import load_records, normalize_sql_row, require, unique_ids, write_json


def check_sql_contract(config, data_dir):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'ranking_executed': False, 'benchmark_opened': False, 'strategies': {}}
    try:
        with psycopg.connect(config['DATABASE_URL'], connect_timeout=config['CONNECTION_TIMEOUT'],
                             options='-c default_transaction_read_only=on -c statement_timeout=10000',
                             row_factory=dict_row) as db:
            db.read_only = True
            db.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            read_only = db.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value']
            require(read_only == 'on', 'SQL transaction must be read-only')
            report['transaction_read_only'] = read_only
            table = sql.Identifier(config['DATABASE_SCHEMA'], config['DATABASE_TABLE'])
            for strategy, count in (('A', 206), ('B', 131)):
                raw = db.execute(sql.SQL('''SELECT strategy, chunk_id, text, metadata, model_digest
                                           FROM {} WHERE strategy=%s ORDER BY chunk_id''').format(table),
                                 (strategy,)).fetchall()
                normalized = [normalize_sql_row(row) for row in raw]
                unique_ids(normalized, 'SQL chunk')
                exports = load_records(data_dir / strategy / 'chunks.json')
                expected = {row['id']: row for row in exports}
                require(len(raw) == len(expected) == count, f'{strategy}: unexpected count')
                require({row['id'] for row in normalized} == set(expected), f'{strategy}: SQL/export IDs differ')
                for row in normalized:
                    exported = expected[row['id']]
                    for key in ('id', 'source_id', 'source', 'page', 'text'):
                        require(row[key] == exported[key], f'{strategy}: SQL/export {key} differs')
                    expected_meta = dict(exported['metadata'], document_id=exported['source_id'],
                                         source=exported['source'], page=exported['page'])
                    require(row['metadata'] == expected_meta, f'{strategy}: SQL/export metadata differs')
                    require(row['model_digest'].removeprefix('sha256:') ==
                            config['EMBEDDING_DIGEST'].removeprefix('sha256:'), 'SQL model digest differs')
                report['strategies'][strategy] = {'count': count, 'unique_ids': count,
                                                  'id_text_source_page_metadata_match': True}
    except psycopg.Error:
        raise CheckFailed('SQL connection/query failed; credentials and connection details withheld.') from None
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data/day06')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check_sql_contract(load_config(args.env_file), args.data_dir)
    except (CheckFailed, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    write_json(args.output, report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
