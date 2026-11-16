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

## 10 — Dev và lựa chọn

Tôi đã công bố [protocol](../artifacts/evaluation/dev-protocol.json) trước lượt
API đầu (commit `37e44c7`). Baseline dense dùng limits bước05; lexical chỉ đổi
retriever; hybrid-context đổi retriever sang RRF20/60, top3→5, tool6000→12000,
context16000→24000, history60000→80000 và refresh_on_full. Model/prompt, corpus,
benchmark, trần call/search/token/timeout giữ nguyên. Giả thuyết và thứ tự tiêu
chí nằm trong protocol; không suy ra tác động riêng khi nhiều yếu tố đổi cùng.

| Cấu hình / run | Hit@1 gốc | Evidence Hit@3 gốc | CER | Grounded /2 | Citation hỗ trợ | Abstain đúng | Input/output token | Tổng giây |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dense / dev-baseline-v1 | 17/20 (85%) | 18/20 (90%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) | 33000/3225 | 116,197 |
| lexical / dev-lexical-v1 | 18/20 (90%) | 19/20 (95%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) | 33393/3189 | 108,321 |
| hybrid-context / dev-hybrid-context-v1 | 19/20 (95%) | 19/20 (95%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) | 42152/3189 | 117,703 |

Cả ba có context/grounded grade0/1/2 = 0/0/20; answered có citation20/20,
tool-use22/22, từ chối nhầm0/20, lỗi0/22. Dense search1×20,2×2 (46 model calls);
lexical và hybrid search1×21,2×1 (45 calls). USD null vì chưa đối soát; cached
và reasoning token được giữ riêng trong metrics. Hit gốc tái dùng trace04
đúng điều kiện/hash; không trình bày ranking này như query tool đã biến đổi.

Tôi chọn **baseline-dense** theo quy tắc thứ tự đã công bố: chất lượng hòa,
baseline dùng tổng36225 token < lexical36582 < hybrid45341. Lexical nhanh hơn
trong một lượt local nhưng timing đứng sau token. Hybrid tăng input, chưa tăng
answer/CER ở bộ này; refresh không được kích hoạt trong các trace thật này.
Không coi số điểm cao của bộ nhỏ là bảo đảm tổng quát. Tôi không tối ưu tiếp.

Hai lỗi dev thực tế (retrieval, không phải lỗi answer):

- **q16**: dense query gốc lấy đoạn execution/closure/life-cycle, thiếu acceptance
  plan trong top3. Giả thuyết từ khóa có thể giúp; lexical và hybrid top3 vẫn
  thiếu evidence q16. Query do model tự tạo thêm “acceptance” đã lấy đúng chunk
  `4d155c…` PDF35 ở cả ba run; CER đạt và grounded=2 sau agent cho cả ba. Không đưa
  đáp án/gold vào input và không tuyên bố RRF đã sửa lỗi top3 này.
- **q19**: dense query gốc xếp stakeholder register/manager/definition thay vì
  đoạn financial return. Lexical/hybrid top3 có đúng PDF62 (0→1 evidence hit);
  query agent dense tự thêm financial return/costs/benefits cũng lấy được
  `629f04…`. Sau agent cả ba đủ CER, grounded=2, nên cải thiện retrieval gốc
  không làm answer tăng trong lượt này.

[Quyết định và bảng máy đọc](../artifacts/evaluation/dev-selection.json).
Mỗi thư mục `artifacts/evaluation/dev-*-v1/` có run/completion, 22 trace riêng,
reviews từng câu/citation có quote/lý do và metrics; không sao chép trace smoke
hoặc trace04 sang các run. Đọc source PDF text và chunk có AI hỗ trợ, chưa có
người chấm độc lập. Test mới chưa chạy và chưa được đọc để chọn cấu hình.

Thời gian là tổng elapsed từng query (gồm API và retrieval), không gồm setup, I/O
và chấm nhãn. Ba run có thời gian thực thi chồng nhau; so sánh latency mang tính
mô tả một lượt, chưa phải thí nghiệm latency được kiểm soát hoặc kiểm định thống kê.

## 11 — Khóa và test

[selection-lock.json](../artifacts/evaluation/selection-lock.json) được tạo lúc
2026-11-16T14:13:46Z (21:13:46 Việt Nam) và push ở commit `18d1c3e` trước lượt
test đầu. Khóa giữ fingerprint baseline/final, model/prompt, code, corpus,
benchmark và80 hash minh chứng dev. Benchmark manifest/labels cũ giữ nguyên.
Test mới trước đó chỉ được kiểm tra lúc xây nhãn, không dùng chạy/chọn hệ thống.

Baseline và final **cùng baseline-dense**, nên dùng chung một run tương đương
`test-baseline-final-v1` cho đủ5+2 câu, không coi là hai lần lặp độc lập.

| Baseline = final (một run) | Kết quả |
|---|---:|
| Hit@1 / Evidence Hit@3 query gốc | 4/5 (80%) / 5/5 (100%) |
| Context Evidence Rate | 5/5 (100%) |
| Context grade0/1/2 | 0/0/5 |
| Grounded grade0/1/2; mean /2 | 0/0/5; 2,00 |
| Citation hỗ trợ / trả lời có citation | 5/5 (100%) / 5/5 (100%) |
| Abstention đúng / từ chối nhầm | 2/2 (100%) / 0/5 (0%) |
| Tool-use / lỗi | 7/7 (100%) / 0/7 (0%) |
| Search distribution / model calls | 1×5, 2×2 / 16 |
| Tổng / mean / median / max giây mỗi query | 37,544 / 5,363 / 4,910 / 6,587 |
| Input / output / cached / reasoning token | 11875 / 958 / 0 / 42 |
| USD | null — chưa đối soát billing/ước tính giá |

Tôi đọc đủ5 answer/citation và2 abstention cùng context thật; đối chiếu text PDF
trang38,43,46,47,61. d07-t06 không điền CPI từ ngoài corpus; literal inventory
CPI/earned value/cost performance index đều không có trong131 chunk, kèm absence
review toàn corpus đã đóng băng. d07-t07 giữ abstain, giải thích không có ngày:
đã xem render PDF63, Figure5.2 có hai ô Communication Date trống; mô tả hình trong
snapshot B khớp. Review có AI hỗ trợ, không có chấm độc lập của con người.

[Trace/review/metrics](../artifacts/evaluation/test-baseline-final-v1/) ·
[Ánh xạ baseline/final](../artifacts/evaluation/test-comparison.json).
Ranking query gốc được đo riêng sau khóa, không dùng query viết lại để giả
Hit@1/Hit@3 gốc. Tôi không chỉnh hệ thống theo kết quả test.

Một lỗi scorer về `relative_to(ROOT)` với input tương đối được sửa bằng chuẩn
hóa đường dẫn trước khi xuất metrics. [Bản ghi hash trước/sau](../artifacts/evaluation/scorer-path-correction.json)
giữ lịch sử khóa; thuật toán chấm không đổi, cả ba metrics dev tính lại giống
hệt từng trường. Không sửa runtime/trace/labels/khóa cũ, không chạy lại API.
