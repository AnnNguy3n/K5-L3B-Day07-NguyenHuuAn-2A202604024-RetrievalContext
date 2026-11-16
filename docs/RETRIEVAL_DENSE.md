# Công việc 4A — dense retrieval

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã triển khai `DenseRetriever(config)` trong `src/dense_retrieval.py`, dùng
embedding `embeddinggemma-2:740m-mxfp8` 768 chiều và 131 vector B của Day06.
Tôi giữ prefix/digest, đối chiếu corpus với SQL và tìm exact cosine trong
transaction chỉ đọc, repeatable-read. Dữ liệu Day06 không thay đổi.

API `search(query, *, filters=None, top_k=3)` trả ID, text, nguồn, trang,
metadata, score, rank từ 1 và `strategy='dense'`. Filter nhận
`section/language/document_type`, khớp chính xác trước top-k; hòa điểm theo ID.
Tôi chặn tham số/vector sai, digest lệch và lỗi API/DB; không tạo kết quả giả.
Caller gọi `close()` khi dùng xong. Ví dụ khởi tạo:

```python
from pathlib import Path
from scripts.check_connections import load_config
from src.dense_retrieval import DenseRetriever

retriever = DenseRetriever(load_config(Path('.env')))
try:
    rows = retriever.search('project phases', top_k=3)
finally:
    retriever.close()
```

Tôi chạy đủ 22 câu dev, không lỗi: Hit@1 **17/20**, Evidence Hit@3 **18/20**.
q15 có evidence hạng 2; q16/q19 thiếu trong top-3. Hai câu thiếu evidence không
thuộc mẫu số Hit. [Trace, cấu hình và điểm](../artifacts/retrieval/dense/verified-dev/)
giữ nguyên bằng chứng của lượt riêng; [so sánh chung](RETRIEVAL_HYBRID.md) dùng
lượt chạy lại cùng điều kiện cho cả ba phương pháp.

Chạy từ root Day07 khi dịch vụ Day06 hoạt động:

```bash
.venv/bin/python scripts/run_dense_dev.py run --output artifacts/retrieval/dense/my-run
.venv/bin/python scripts/run_dense_dev.py score --output artifacts/retrieval/dense/my-run
```

Mỗi lượt cần thư mục mới. Retriever chỉ nhận query/filter; lệnh score chọn nhãn
dev để chấm offline đúng nguồn/trang và evidence OR. Tôi thêm 17 test dense;
kết quả hồi quy chung và smoke thật nằm trong [báo cáo tích hợp](RETRIEVAL_HYBRID.md).
Tôi chưa chạy benchmark test hoặc đánh giá câu trả lời/abstention.
