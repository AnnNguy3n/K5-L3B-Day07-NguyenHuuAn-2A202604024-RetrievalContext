# Công việc 01 — kết quả kiểm tra

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã tạo môi trường Day07 và kết nối lại embedding, vector store của Day06.
Lần kiểm tra được lưu lúc 16:48 ngày 16/11/2026 (UTC+7) trong
[JSON kết quả](day07-connection-check.json).

| Hạng mục | Kết quả |
|---|---|
| Môi trường | Python 3.14.10; dependencies đã ghim, `pip check` đạt |
| Embedding | `embeddinggemma-2:740m-mxfp8`; digest khớp Day06; hai vector query/document đều 768 chiều, hữu hạn và khác 0 |
| Vector store | PostgreSQL 16.16, pgvector 0.8.7; A có 206 vector, B có 131 vector; đúng digest/chiều, không NULL, ID không trùng |
| Truy vấn | Toán tử cosine chạy được trên A/B trong transaction chỉ đọc |
| Kiểm thử | 21/21 test đạt, gồm 15 test starter và 6 test mới cho checker |

Tôi giữ nguyên dữ liệu Day06 và chưa chạy benchmark ở bước này. Kết quả trên
xác nhận kết nối; chưa đánh giá chất lượng retrieval hoặc câu trả lời của agent.
Cách tái lập nằm trong [hướng dẫn thiết lập](SETUP_DAY07.md).
