# Công việc 4B — lexical TF-IDF

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã triển khai `LexicalRetriever(chunks)` trong `src/lexical_retrieval.py`,
dùng tokenizer và TF-IDF starter bằng thư viện chuẩn, chạy offline trên 131 chunk B.
Text/query được NFC/casefold và tách bằng Unicode regex `\w+`; không stemming,
stopword hay sửa text gốc. Vocabulary có 2.428 token.

Tôi dùng raw TF, `IDF=ln((1+N)/(1+DF))+1`, chuẩn hóa L2 và cosine. IDF được fit
một lần trên toàn corpus, không tính lại khi lọc. API
`search(query, *, filters=None, top_k=3)` trả core/metadata, score, rank từ 1 và
`strategy='lexical'`; cùng hợp đồng filter với dense. Candidate được lọc trước
cutoff, score giảm dần rồi ID tăng dần; chỉ trả score >0, query không khớp trả `[]`.
Tôi kiểm tra schema/ID trùng và sao chép sâu để dữ liệu trong index không bị đổi.

```python
import json
from pathlib import Path
from src.lexical_retrieval import LexicalRetriever

chunks = json.loads(Path('data/day06/B/chunks.json').read_text())
rows = LexicalRetriever(chunks).search('project phases', top_k=3)
```

Tôi chạy đủ 22 câu dev, không lỗi: Hit@1 **18/20**, Evidence Hit@3 **19/20**.
q06 có evidence hạng 2; q16 thiếu trong top-3. Hai câu thiếu evidence vẫn truy
xuất nhưng không thuộc mẫu số Hit. [Trace, cấu hình và điểm](../artifacts/retrieval/lexical/verified-dev/)
giữ lượt riêng; [báo cáo tích hợp](RETRIEVAL_HYBRID.md) có lượt so sánh chung.

```bash
.venv/bin/python scripts/run_lexical_dev.py run --output artifacts/retrieval/lexical/my-run
.venv/bin/python scripts/run_lexical_dev.py score --output artifacts/retrieval/lexical/my-run
```

Mỗi lượt cần thư mục mới; runner chỉ chạy queries dev không nhãn, score chấm
nhãn dev offline đúng evidence OR. Tôi bổ sung 23 test lexical cho công thức,
filter, dữ liệu bất biến, runner và bộ chấm. Corpus/benchmark giữ nguyên;
chưa chạy benchmark test hoặc đánh giá context/câu trả lời/abstention.
