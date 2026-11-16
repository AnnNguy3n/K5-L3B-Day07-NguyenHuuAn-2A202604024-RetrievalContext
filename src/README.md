# Code Day07

- `dense_retrieval.py`: embedding tương thích Day06 và pgvector chỉ đọc, API search.
- `lexical_retrieval.py`: TF-IDF trên snapshot B, cùng API/filter/top-k.
- `hybrid_retrieval.py`: hợp nhất RRF candidate_k20, rrf_k60, không cộng raw score.
- `openai_agent.py`: OpenAI Responses/function calling, context có budget/dedup,
  citation, model abstention và lỗi phân biệt, trace không chứa reasoning/secret.

[Cách chạy và chấm offline](../docs/EVALUATION.md) · [Báo cáo cuối](../report/individual/2A202604024_NguyenHuuAn.md).
