# Đánh giá agent Day07

Nguyễn Hữu An · 2A202604024 · L3B

## 06 — Context

Tôi giữ chunk nguyên vẹn, ID/nguồn/trang, loại trùng ID và text; budget là số ký
 tự Unicode của JSON, gồm wrapper. `context_chars` giới hạn tổng tool output
trong history; `history_chars` kiểm tra cả question, output, opaque reasoning,
instructions và schema trước mỗi request. Không nén/cắt mất điều kiện trong chunk.

Baseline `configs/agent/baseline-dense.json` giữ cách append và budget mặc định
của bước05, dùng dense để đo riêng tác động chuyển sang lexical/hybrid.
`refresh_on_full` xóa source text cũ khỏi function outputs trong history khi
lượt mới không đủ chỗ, giữ call ID/protocol rồi ưu tiên evidence lượt mới.
Citation cuối chỉ được dùng context còn trong request. Lỗi history/context
limit vẫn là error. Context chunks được chụp theo từng model call; chỉ call
có `delivery_confirmed=true` mới chứng minh đã gửi thành công.

Phép đo đóng gói offline trên **22 dev**, dùng ranking hybrid query gốc bước04:
budget 3000 chọn 55/66 chunk, bỏ 11; 6000 và 12000 đều chọn 66/66, tổng 61746 ký tự.
Đây là đo packing, **chưa phải CER hay context đã gửi model**; không suy ra budget
lớn hơn tốt hơn. [Số đo](../artifacts/evaluation/context-packing-dev.json).
Tác động answer/context sẽ được đo agent thật trên toàn dev ở bước10.
Tôi đã thêm test xác minh evidence cũ thực sự biến mất khỏi request sau refresh.

## 07 — Citation và abstention

Tôi giữ ba trạng thái answered/abstain/error. Agent bắt buộc retrieval trước
final; model tự từ chối khi thiếu evidence. Provider/tool/protocol/call limit
và citation sai là error, không được tính là từ chối đúng. Citation ID phải duy
nhất, đúng danh sách inline, còn trong context đã gửi; source/page lấy từ chunk.
Tool text chỉ là dữ liệu dưới instructions cố định, kể cả chứa prompt injection.
Test offline kiểm tra cơ chế này, không chứng minh model luôn kháng injection.

Tôi đã đọc answer/context của hai smoke lịch sử và text PDF gốc trang35–36:
q01 có đủ bốn giai đoạn đúng thứ tự; d07-d21 từ chối đúng số USD không được cung
cấp. [Review từng citation](../artifacts/evaluation/smoke-citation-review.json).
Review này không thay full-dev/test; mọi citation các lượt đó phải có nhãn ngữ
nghĩa riêng. Không dùng ID hợp lệ hoặc LLM judge để kết luận được nguồn hỗ trợ.
Test agent sau bước này: 25 đạt, 1 live bỏ qua, không gọi lại API trả phí.

## 08 — Runner và trace

```bash
.venv/bin/python scripts/run_agent_benchmark.py --config configs/agent/baseline-dense.json \
  --split dev --output artifacts/evaluation/dev-baseline-v1
# Test bắt buộc --lock artifacts/evaluation/selection-lock.json (tạo ở bước11).
```

Runner chỉ mở projections runtime; không đọc cases/gold. Mỗi run có fingerprint
cấu hình đã resolve OPENAI_MODEL, embedding/digest/prefix, prompt/schema, code,
SDK/Python, corpus/benchmark và hash query projection. Mỗi query lưu query gốc,
query tool, filter/nguồn filter, ranking đầy đủ top-k, output được chọn, context
snapshot từng request, delivery flag, answer/citation, stop/error, thời gian,
usage và cost null có lý do. Không log opaque reasoning hoặc raw provider error.
Các snapshot context theo call giúp xác minh CER cuối, không đồng nhất candidate
hoặc tool output vừa chuẩn bị với context đã gửi. Query nhiều lượt không có
một ranking pipeline giả; ranking từng call được giữ riêng.

Mỗi run/file phải mới; không resume hay tự retry lỗi. SDK retry=0 trong cả ba
config. Mỗi attempt hoàn thành có số1; start marker còn lại nếu tiến trình bị
ngắt. Không chạy lại âm thầm hoặc bỏ lỗi khỏi mẫu số. Trần mỗi query: 4 model
calls, 3 searches, 2048 output token/call, 45 giây/request. Chạy đủ split một
lần; không tự chạy lại live trong unittest. Guard tests xác minh khóa được kiểm
tra trước khi mở test/services, nhãn bị chặn và file không bị ghi đè.

## 09 — Bộ chấm và nhãn

```bash
.venv/bin/python scripts/evaluate_agent.py artifacts/evaluation/dev-baseline-v1 \
  --output artifacts/evaluation/dev-baseline-v1/metrics.json
```

Mỗi run có `reviews.json`: query_id, trace_sha256, context_grade 0/1/2,
grounded_grade 0/1/2 (null cho câu thiếu evidence), citation_reviews từng ID với
supports_answer/reason/source_quote nguyên văn trong context, abstention_correct
và reason/source_check. Review do đọc nội dung có AI hỗ trợ, không phải người
chấm độc lập. Smoke q01 được chấm context2/grounded2; d07-d21 abstention đúng;
full-dev/test sẽ có review riêng cho từng run, không ngoại suy từ smoke.

Gold OR: một evidence nguyên văn trọn chunk, đúng source_id/page. Hit@1 và
Evidence Hit@3 dùng query gốc/cutoff chung, không tính câu thiếu evidence.
Dev tái dùng trace bước04 khi hash code retrieval, corpus/benchmark và query
không đổi. CER tính từ snapshot cuối có delivery_confirmed, không từ candidate
hoặc context mới chuẩn bị; có thể khác Hit@3 vì query viết lại/nhiều lượt/top-5.
Context grade2 cần đọc đủ điều kiện và nguồn; có gold exact chưa tự quyết định
answer/citation đúng. Grade1 là một phần, 0 thiếu evidence; câu có đáp án bị từ
chối hoặc lỗi có grounded0. Citation cần hỗ trợ các claim gắn với nó, không chỉ
trùng ID. Câu thiếu evidence chỉ đúng khi thực sự model abstain, không kèm số
liệu/khẳng định trả lời thiếu căn cứ.

Tất cả query có trong kết quả. Lỗi giữ grounded0, abstention sai cho câu thiếu
evidence; CER vẫn xét context request cuối đã xác nhận (nếu có), retrieval lỗi
hit0. Tool-use chia số query thành công; lỗi và phân bố search báo trên toàn
split. Citation 0/0 có rate=null, tức không áp dụng. Có n/N và tỷ lệ cho metric;
điểm grounded báo phân bố/mean trên mẫu có đáp án. Thời gian từng query và tổng,
usage theo provider; tổng usage null nếu bất kỳ call chưa đo, cost null có lý do.
Unit tests xác minh OR/source/page, context chưa gửi, mẫu số và lỗi; nhãn chất
lượng phải đọc thực tế, không được thay bằng unit test.
