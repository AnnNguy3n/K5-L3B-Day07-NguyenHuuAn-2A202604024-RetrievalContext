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

## Công việc 03 — benchmark và bảo vệ test

Tôi đã tạo benchmark v1 gồm 29 câu: dev 20 có đáp án + 2 thiếu evidence, test mới
5 có đáp án + 2 thiếu evidence. Tôi chuyển 20 câu Day06 sang dev và lưu lịch sử
split vì test cũ đã có kết quả công khai. Queries runtime chỉ giữ ID/câu hỏi/filter.

Tôi kiểm chứng 26 evidence với chunk và PDF gốc; q11 giữ hai evidence thay thế.
Gold q20 được rút ngắn trong benchmark mới để nằm trọn một chunk, giữ bản gốc
trong lịch sử. Bốn câu thiếu evidence có ghi chú đọc toàn corpus và quét đủ
131 chunk/32 trang. Nhãn được kiểm chứng có AI hỗ trợ, chưa có người chấm độc lập.

Tôi bổ sung validator và 17 test, tổng 50/50 test đạt; kiểm tra nguồn/hash đạt.
Snapshot Day06/A/B giữ nguyên. Tôi chưa chạy retrieval/agent trên benchmark mới;
test chỉ dùng để xây nhãn, đánh giá hệ thống sau khi khóa cấu hình bằng dev.
[Schema và cách chạy](../../benchmark/day07/README.md) ·
[Minh chứng](../../benchmark/day07/audit.json)

## Công việc 04 — dense, lexical và hybrid retrieval

Tôi đã tích hợp ba phương pháp trên cùng 131 chunk B, benchmark v1, đủ 22 câu dev
và top-3; mỗi phương pháp không có lỗi. Hit@1 / Evidence Hit@3 lần lượt là dense
**17/20 · 18/20**, lexical **18/20 · 19/20**, hybrid RRF **19/20 · 19/20**.
Hai câu thiếu evidence vẫn chạy và được loại khỏi mẫu số Hit. Tổng search tương
ứng khoảng **1,171 s · 0,022 s · 1,214 s**; cả lượt dùng 44 query embedding local,
không gọi LLM, chưa đo chi phí tiền/điện.

Tôi giữ trace, hash và bước chấm offline; **111/111 test code đạt**, gồm adapter thật.
RRF dùng candidate_k=20, rrf_k=60 ban đầu, chưa chứng minh tối ưu; q16 vẫn thiếu
evidence top-3. Tôi chưa đo context/đáp án agent, chưa đánh giá 7 câu test và chưa
có hai cấu hình cải thiện. [Kết quả và cách chạy](../../docs/RETRIEVAL_HYBRID.md) ·
[So sánh có trace/hash](../../artifacts/retrieval/hybrid/verified-dev/comparison.json)

## Công việc 05 — agent OpenAI API

Tôi đã kết nối SDK `openai==3.51.0` với Responses/function calling và ba retriever
thật, giữ embedding/index Day06. Model `gpt-6.1-terra` tự chọn query và lượt tìm;
agent kiểm tra tool/citation, giới hạn call/search/context và ghi lỗi riêng.

Tôi chạy hai smoke dev qua OpenAI thật: q01 trả lời có citation PDF trang 35
(2 call/1 search); d07-d21 từ chối sau 2 search/3 call vì thiếu tổng ngân sách. Context,
request ID và usage được giữ trong trace; không gửi nhãn hoặc lưu key/reasoning.
Chi phí USD chưa đối soát nên để null. Tôi bổ sung 23 test; tổng 134 test có
133 đạt, 1 live OpenAI unittest bỏ qua. Hồi quy retrieval giữ điểm bước 04.
[Thiết lập, minh chứng và giới hạn](../../docs/OPENAI_AGENT.md).

## Phần tiếp theo

Tôi sẽ tối ưu context ở công việc 06. Hai smoke chưa thay thế đánh giá agent
trên toàn dev hoặc audit citation đầy đủ; 7 câu test vẫn chưa được đánh giá.

## Công việc 06 — chọn context

Tôi đã giữ baseline append, thêm refresh khi context cũ chiếm budget, snapshot
context thật theo call và cấu hình tái chạy. Đo packing 22 dev cho thấy 6000 và
12000 ký tự cùng giữ đủ 66 chunk top-3; chưa phải đo chất lượng agent.
[Thiết kế và số đo](../../docs/EVALUATION.md). Test agent: 23 đạt, 1 live bỏ qua.

## Công việc 07 — citation và abstention

Tôi đã kiểm tra citation bị eviction phải thành error, giữ tài liệu là dữ liệu,
và đọc đối chiếu hai smoke với PDF gốc. Nhãn hỗ trợ citation và lý do ở
[review smoke](../../artifacts/evaluation/smoke-citation-review.json); đây chưa
phải đánh giá toàn dev/test. Test agent: 25 đạt, 1 live bỏ qua.

## Công việc 08 — runner và trace

Tôi đã thêm runner theo config/split, trace ranking và context thực gửi theo
request, fingerprint/hash và guard khóa test trước khi mở query. File mới bắt
buộc, retry/resume không tự động; lỗi được giữ. 29 test liên quan đạt, 1 live bỏ
qua. [Lệnh và schema](../../docs/EVALUATION.md).

## Công việc 09 — metrics và nhãn

Tôi đã hoàn thiện bộ chấm offline, schema review từng câu/citation và quy tắc
mẫu số/lỗi. Gold OR/source/page được kiểm tra độc lập với nhãn ngữ nghĩa; CER
lấy context đã gửi cuối. 32 test liên quan đạt, 1 live bỏ qua. Nhãn toàn dev/test
được lưu theo run ở bước10–11, không suy từ hai smoke. [Quy tắc](../../docs/EVALUATION.md).
