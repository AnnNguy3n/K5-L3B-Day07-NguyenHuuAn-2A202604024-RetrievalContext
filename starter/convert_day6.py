"""Lossless Day06 JSON/JSONL and SQL-row adapter; no retrieval or ranking.

See docs/DAY07_DATA_CONTRACT.md for the schema and reproducible CLI.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def page_number(value):
    return type(value) is int and value > 0


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_records(path, input_format='auto'):
    """Read an array or one object per nonblank line; never guess CSV/SQL text."""
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig')
    if input_format == 'auto':
        input_format = 'jsonl' if path.suffix.lower() == '.jsonl' else (
            'json' if text.lstrip().startswith('[') else 'jsonl')
    require(input_format in ('json', 'jsonl'), 'Unknown input format')
    try:
        if input_format == 'json':
            rows = json.loads(text)
        else:
            rows = []
            for line_number, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        raise ValueError(f'{path.name}: invalid JSONL at line {line_number}') from None
    except json.JSONDecodeError:
        raise ValueError(f'{path.name}: invalid JSON array') from None
    require(isinstance(rows, list) and bool(rows), f'{path.name}: expected nonempty records')
    require(all(isinstance(row, dict) for row in rows), f'{path.name}: every record must be an object')
    return rows


def aliased(record, keys, metadata=None):
    """Reject conflicting trace fields instead of silently choosing one."""
    values = [record[k] for k in keys if k in record]
    if metadata is not None:
        values += [metadata[k] for k in keys if k in metadata]
    require(bool(values), f'Missing {keys[0]}')
    require(all(type(v) is type(values[0]) and v == values[0] for v in values),
            f'Conflicting aliases for {keys[0]}')
    return deepcopy(values[0])


def convert_chunk(c):
    """Normalize exports and dict SQL rows; preserve metadata and extra columns."""
    require(isinstance(c, dict), 'Chunk must be an object')
    metadata = c.get('metadata', {})
    require(isinstance(metadata, dict), 'Chunk metadata must be an object (decode SQL JSON first)')
    result = {k: deepcopy(v) for k, v in c.items() if k not in ('chunk_id', 'document_id')}
    result.update(id=aliased(c, ('id', 'chunk_id')),
                  source_id=aliased(c, ('source_id', 'document_id'), metadata),
                  source=aliased(c, ('source',), metadata),
                  page=aliased(c, ('page',), metadata),
                  metadata=deepcopy(metadata))
    require(all(nonempty(result.get(k)) for k in ('id', 'source_id', 'source', 'text')),
            'Chunk id/source_id/source/text must be nonempty strings')
    require(page_number(result['page']), 'Chunk page must be a positive physical PDF page')
    return result


def normalize_sql_row(row):
    """psycopg dict_row: chunk_id,text,metadata[,score,strategy,model_digest]."""
    return convert_chunk(row)


def convert_evidence(g):
    require(isinstance(g, dict), 'Evidence must be an object')
    result = {k: deepcopy(v) for k, v in g.items()
              if k not in ('gold_source_id', 'gold_page', 'evidence_quote')}
    result.update(source_id=aliased(g, ('source_id', 'gold_source_id')),
                  page=aliased(g, ('page', 'gold_page')),
                  evidence=aliased(g, ('evidence', 'evidence_quote')))
    require(nonempty(result['source_id']) and nonempty(result['evidence'])
            and page_number(result['page']), 'Invalid evidence source/page/text')
    return result


def convert_question(q):
    require(isinstance(q, dict), 'Question must be an object')
    if 'gold' in q:
        require(isinstance(q['gold'], list) and bool(q['gold']), 'gold must be a nonempty list')
        gold = [convert_evidence(g) for g in q['gold']]
        require(not any(k in q for k in ('gold_source_id', 'gold_page', 'evidence_quote')),
                'Use gold or legacy primary label fields, not both')
    else:
        gold = [convert_evidence({k: q[k] for k in
                                 ('gold_source_id', 'gold_page', 'evidence_quote') if k in q})]
    alternatives = q.get('accepted_alternatives', [])
    require(isinstance(alternatives, list), 'accepted_alternatives must be a list')
    gold.extend(convert_evidence(g) for g in alternatives)
    legacy = {'gold_source_id', 'gold_page', 'evidence_quote', 'accepted_alternatives'}
    result = {k: deepcopy(v) for k, v in q.items() if k not in legacy}
    result['gold'] = gold
    require(nonempty(result.get('id')) and nonempty(result.get('question'))
            and nonempty(result.get('expected_answer')), 'Question id/question/expected_answer required')
    require(result.get('split') in ('dev', 'test'), 'Question split must be dev or test')
    if 'filter' in result:
        require(isinstance(result['filter'], dict), 'Question filter must be an object')
    # Deliberately do not infer any filter from labels or corpus metadata.
    return result


def query_record(question):
    """Explicit allowlist at the offline-label / runtime-query boundary."""
    return {k: deepcopy(question[k]) for k in ('id', 'question', 'filter') if k in question}


def unique_ids(rows, name):
    require(len({r['id'] for r in rows}) == len(rows), f'Duplicate {name} IDs')


def resolve_source_path(source, day06_root, registry):
    """source is an identifier; only the registry's locator is opened."""
    entries = [r for r in registry['sources'] if r['source'] == source]
    require(len(entries) == 1, 'Unknown or ambiguous source identifier')
    locator = entries[0]['relative_path']
    require(not urlsplit(locator).scheme and not Path(locator).is_absolute(),
            'Source locator must be relative to DAY06_PATH')
    root = Path(day06_root).resolve()
    path = (root / locator).resolve()
    require(path.is_relative_to(root), 'Source locator escapes DAY06_PATH')
    require(path.is_file(), f'Source file missing: {locator}')
    return path


def build_source_registry(chunks, manifest, day06_root):
    sources = manifest.get('sources', [])
    require(isinstance(sources, list), 'Manifest sources must be a list')
    registry = {'schema_version': 1, 'root_variable': 'DAY06_PATH', 'sources': []}
    for source_id, source in sorted({(c['source_id'], c['source']) for c in chunks}):
        matches = [s for s in sources if s['source_id'] == source_id
                   and s['source_path_or_url'] == source]
        require(len(matches) == 1, 'Chunk source_id/source not uniquely in Day06 manifest')
        entry = deepcopy(matches[0])  # Includes snapshot, licensing and attribution.
        entry.update(source=source, relative_path=entry['source_path_or_url'])
        registry['sources'].append(entry)
    for entry in registry['sources']:
        path = resolve_source_path(entry['source'], day06_root, registry)
        require(sha256(path) == entry['snapshot']['sha256'], 'Source snapshot SHA-256 mismatch')
        pages = entry['selected_pdf_pages']
        for c in chunks:
            if c['source_id'] != entry['source_id'] or c['source'] != entry['source']:
                continue
            require(c['page'] in pages and c['page'] <= entry['physical_page_count'],
                    'Chunk page not in manifest selected physical pages')
            meta = c['metadata']
            if 'page_id' in meta:
                require(meta['page_id'] == f"{c['source_id']}:p{c['page']:03d}", 'metadata.page_id mismatch')
    return registry


def validate_dev(chunks, questions):
    """Exact label-to-corpus audit, only dev; no query/search/ranking executed."""
    records = []
    for q in questions:
        if q['split'] != 'dev':
            continue
        for index, g in enumerate(q['gold']):
            page_chunks = [c for c in chunks if c['source_id'] == g['source_id'] and c['page'] == g['page']]
            matches = [c['id'] for c in page_chunks if g['evidence'] in c['text']]
            records.append({'question_id': q['id'], 'evidence_index': index,
                            'source_page_exists': bool(page_chunks), 'chunk_ids': matches})
    unresolved = [r for r in records if not r['chunk_ids']]
    return {'split': 'dev', 'questions': sum(q['split'] == 'dev' for q in questions),
            'evidence_count': len(records), 'resolved_evidence': len(records) - len(unresolved),
            'unresolved': unresolved, 'matches': records,
            'test_evidence_checked': False, 'ranking_executed': False}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chunks', required=True, type=Path)
    parser.add_argument('--questions', required=True, type=Path)
    parser.add_argument('--chunk-format', choices=('auto', 'json', 'jsonl'), default='auto')
    parser.add_argument('--question-format', choices=('auto', 'json', 'jsonl'), default='auto')
    parser.add_argument('--day06-root', required=True, type=Path, help='Root for Day06 source paths, never CWD')
    parser.add_argument('--manifest', type=Path, help='Default: DAY06_ROOT/data/manifest.json')
    parser.add_argument('--expected-chunks', type=int, help='Fail if count differs')
    parser.add_argument('--strict-dev', action='store_true', help='Fail on any unresolved dev evidence')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.day06_root.resolve()
        output = args.output.resolve()
        require(not output.is_relative_to(root), 'Output must be outside the read-only Day06 snapshot')
        manifest_path = args.manifest or root / 'data/manifest.json'
        raw_chunks = load_records(args.chunks, args.chunk_format)
        raw_questions = load_records(args.questions, args.question_format)
        chunks = [convert_chunk(c) for c in raw_chunks]
        questions = [convert_question(q) for q in raw_questions]
        unique_ids(chunks, 'chunk')
        unique_ids(questions, 'question')
        if args.expected_chunks is not None:
            require(len(chunks) == args.expected_chunks, 'Chunk count differs from --expected-chunks')
        registry = build_source_registry(chunks, json.loads(manifest_path.read_text(encoding='utf-8')), root)
        dev = validate_dev(chunks, questions)
        if args.strict_dev:
            require(not dev['unresolved'], 'Unresolved dev evidence; rerun without --strict-dev to save diagnostic report')
        # Check unchanged core values explicitly, including SQL metadata fallbacks.
        require(all(c['id'] == aliased(r, ('id', 'chunk_id')) and c['text'] == r['text']
                    and c['metadata'] == r.get('metadata', {}) for r, c in zip(raw_chunks, chunks)),
                'Conversion changed IDs/text/metadata')
        report = {'schema_version': 1, 'chunks': len(chunks), 'unique_chunk_ids': len({c['id'] for c in chunks}),
                  'id_text_metadata_unchanged': True,
                  'questions': len(questions), 'splits': {s: sum(q['split'] == s for q in questions)
                                                       for s in ('dev', 'test')},
                  'alternative_evidence_count': sum(len(q.get('accepted_alternatives', [])) for q in raw_questions),
                  'sources_verified': len(registry['sources']), 'dev': dev,
                  'inputs': {name: {'relative_to_day06': str(path.resolve().relative_to(root))
                                    if path.resolve().is_relative_to(root) else None,
                                    'sha256': sha256(path)} for name, path in
                             (('chunks', args.chunks), ('questions', args.questions), ('manifest', manifest_path))}}
        artifacts = {'chunks.json': chunks, 'questions.json': questions, 'sources.json': registry}
        artifacts.update({f'queries.{split}.json': [query_record(q) for q in questions if q['split'] == split]
                          for split in ('dev', 'test')})
        output.mkdir(parents=True, exist_ok=True)
        # Guard symlinks so the snapshot cannot be overwritten via an output directory.
        for name in (*artifacts, 'validation.json'):
            require(not (output / name).is_symlink(), 'Output file must not be a symlink')
        for name, value in artifacts.items():
            write_json(output / name, value)
        report['output_sha256'] = {name: sha256(output / name) for name in artifacts}
        write_json(output / 'validation.json', report)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f'Conversion failed: {exc}\n')
    print(f"Converted {len(chunks)} chunks, {len(questions)} questions; "
          f"dev evidence {dev['resolved_evidence']}/{dev['evidence_count']}; "
          f"unresolved={len(dev['unresolved'])}. No ranking executed.")
    return 0


if __name__ == '__main__':
    main()
