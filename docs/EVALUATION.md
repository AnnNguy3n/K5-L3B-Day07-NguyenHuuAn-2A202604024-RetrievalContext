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
