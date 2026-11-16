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
