"""Offline TF-IDF retrieval over a fixed, copied corpus; no label/service access."""
from collections import Counter
from copy import deepcopy

from starter.baseline import TfidfIndex, tokenize


CORE_FIELDS = ('id', 'source_id', 'source', 'page', 'text', 'metadata')
FILTER_FIELDS = frozenset(('section', 'language', 'document_type'))


def validate_request(query, filters, top_k):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('query must be a nonempty string')
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError('top_k must be a positive integer, excluding bool')
    if filters is not None and (
        not isinstance(filters, dict)
        or any(key not in FILTER_FIELDS or not isinstance(value, str)
               for key, value in filters.items())
    ):
        raise ValueError('filters must map section/language/document_type to strings')


class LexicalRetriever:
    """Fit IDF once on all chunks, then filter candidates without refitting.

    Duplicate IDs are rejected before indexing so they cannot inflate DF or
    appear twice in results. Returned core fields are independent deep copies.
    """

    def __init__(self, chunks: list[dict]):
        if not isinstance(chunks, list) or not chunks:
            raise ValueError('chunks must be a nonempty list')
        seen = set()
        snapshot = []
        for chunk in chunks:
            if not isinstance(chunk, dict) or any(key not in chunk for key in CORE_FIELDS):
                raise ValueError('chunk must contain all core fields')
            if any(not isinstance(chunk[key], str) or not chunk[key].strip()
                   for key in ('id', 'source_id', 'source', 'text')):
                raise ValueError('chunk id/source_id/source/text must be nonempty strings')
            if (isinstance(chunk['page'], bool) or not isinstance(chunk['page'], int)
                    or chunk['page'] <= 0 or not isinstance(chunk['metadata'], dict)):
                raise ValueError('chunk page must be a positive integer and metadata an object')
            if chunk['id'] in seen:
                raise ValueError('chunk IDs must be unique')
            seen.add(chunk['id'])
            snapshot.append(deepcopy({key: chunk[key] for key in CORE_FIELDS}))
        self._index = TfidfIndex(snapshot)

    @property
    def vocabulary_size(self):
        return len(self._index.idf)

    def search(self, query: str, *, filters: dict | None = None, top_k: int = 3) -> list[dict]:
        validate_request(query, filters, top_k)
        # _vector is the starter's raw TF * smoothed IDF, followed by L2 norm.
        # Unknown terms are dropped before normalization; an empty vector has no hits.
        vector = self._index._vector(Counter(tokenize(query)))
        if not vector:
            return []
        candidates = []
        for chunk, document_vector in zip(self._index.chunks, self._index.vectors):
            if filters and any(chunk['metadata'].get(key) != value for key, value in filters.items()):
                continue
            score = sum(value * document_vector.get(term, 0) for term, value in vector.items())
            if score > 0:
                candidates.append((score, chunk))
        candidates.sort(key=lambda pair: (-pair[0], pair[1]['id']))
        return [{**deepcopy(chunk), 'score': score, 'rank': rank, 'strategy': 'lexical'}
                for rank, (score, chunk) in enumerate(candidates[:top_k], start=1)]
