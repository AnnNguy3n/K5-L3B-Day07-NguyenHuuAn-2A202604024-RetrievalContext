# Báo cáo tiến độ Day07

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

## Công việc 01 — môi trường và kết nối Day06

Tôi đã tạo môi trường Python 3.14.10, ghim dependencies và viết checker kết nối.
Tôi dùng lại model `embeddinggemma-2:740m-mxfp8` (768 chiều) cùng PostgreSQL/pgvector
của Day06. Kiểm tra trực tiếp xác nhận digest khớp, A có 206 vector và B có 131
vector; truy vấn cosine thành công trong transaction chỉ đọc.

Tôi bổ sung 6 test cho cấu hình và các nhánh lỗi của checker; tổng 21/21 test đạt,
`pip check` không phát hiện lỗi dependency. Dữ liệu Day06 được giữ nguyên.
[Kết quả và minh chứng](../../docs/DAY07_CONNECTION_STATUS.md) ·
[Cách chạy](../../docs/SETUP_DAY07.md)

## Công việc 02 — chuẩn hóa dữ liệu Day06

Tôi đã mở rộng converter để nhận JSONL/JSON array và hàng SQL, giữ nguyên ID,
text, nguồn, trang và metadata. Gold chính cùng evidence thay thế được bảo toàn;
truy vấn runtime chỉ chứa ID, câu hỏi và filter. Registry nguồn giữ đường dẫn
tương đối, hash và thông tin giấy phép để mở lại PDF trên máy khác.

Tôi đã xuất 206 chunk A, 131 chunk B và 20 câu có đáp án (15 dev, 5 test).
B có đủ 16/16 evidence dev nằm trọn trong chunk; A có 10/16, còn 6 evidence đi
qua ranh giới chunk nhưng vẫn có trong trang khi ghép theo offset. Kiểm tra SQL
chỉ đọc xác nhận toàn bộ A/B khớp dữ liệu đã chuyển. Tôi tái tạo đầu ra khớp
checksum và chạy đạt 33/33 test; `pip check` không phát hiện lỗi dependency.
[Schema và cách chạy](../../docs/DAY07_DATA_CONTRACT.md) ·
[Minh chứng SQL](../../docs/day07-sql-normalization-check.json)

## Phần tiếp theo

Tôi sẽ hoàn thiện benchmark Day07 ở công việc 03, dùng bộ B và bổ sung câu thiếu
evidence cùng test mới. Test Day06 đã có kết quả phân tích nên cần ghi rõ lịch sử
sử dụng. Hiện tôi chưa chạy retrieval/agent hoặc đánh giá ranking Day07.
