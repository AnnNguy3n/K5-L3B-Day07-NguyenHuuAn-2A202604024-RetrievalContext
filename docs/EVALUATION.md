# Chạy và kiểm tra đánh giá Day07

[Báo cáo cuối và các bảng kết quả](../report/individual/2A202604024_NguyenHuuAn.md) ·
[Đối chiếu bài nộp](SUBMISSION_AUDIT.md). Tôi chỉ làm phần cơ bản.

## Môi trường và dữ liệu

Chạy từ root Day07. Dùng Python3.14.10, dependencies ghim trong requirements.txt;
[cách cài/kết nối](SETUP_DAY07.md) và [nguồn/tái tạo snapshot](DAY07_DATA_CONTRACT.md).
Điền `.env` riêng theo `.env.example`; shell ưu tiên. Agent benchmark chỉ lấy
model từ OPENAI_MODEL, không tự đổi model. Config thí nghiệm ghi rõ tất cả
AgentConfig, không âm thầm lấy AGENT_* trong .env để thay cấu hình đã khóa.
CLI smoke bước05 vẫn hỗ trợ AGENT_* và chỉ nhận dev/câu tự nhập.

Ollama cần đúng tag/digest/prefix embedding và PostgreSQL có bảng/vector Day06.
Trên máy đã có volume Day06, bật Docker Desktop, chạy
`docker start day6_student_starter-postgres-1` rồi checker; tên container có thể
khác trên máy khác. Không chạy ingestion hoặc rebuild index để chạy Day07.
Máy mới cần khôi phục vector store từ snapshot Day06 trong bản sao riêng và
lab database riêng theo README Day06: `docker compose -f starter/docker-compose.yml
-p day6_student_starter up -d --wait`, `python starter/scripts/check_setup.py`,
rồi `python -m starter.src.run build --label initial` để embed/nạp các chunk đã
xuất. Cấu hình riêng Day06 đọc starter/.env; không chép DSN vào Git. Đây là lệnh
tái tạo trên máy mới, tôi không thực hiện lại build ở Day07. Kiểm export B/chunk
ID/digest trước, trỏ DAY06_PATH và DATABASE_URL Day07 vào bản khôi phục. Snapshot chunk/text được phép chia sẻ đã có trong
repo này; key/DSN và PDF gốc không được commit.

```bash
.venv/bin/python scripts/check_connections.py
.venv/bin/python -m pip check
OPENAI_AGENT_LIVE_TEST=0 DENSE_LIVE_TEST=1 .venv/bin/python -m unittest discover -s tests -q
.venv/bin/python scripts/check_submission.py
```

`check_submission.py` chỉ kiểm/hash/chấm lại offline từ nhãn đã đọc, không gọi
API hay biến unit test thành đánh giá chất lượng. Kiểm nguồn PDF tùy chọn cần
Python có pypdf (trên máy này dùng /opt/miniconda3/envs/main/bin/python3):

```bash
.venv/bin/python scripts/day07_benchmark.py check \
  --day06-root ../K5-L3B-DAY06-NguyenHuuAn-2A202604024-DataIngestion \
  --pdf-python /opt/miniconda3/envs/main/bin/python3
```

## Lượt chạy mới, có dùng API

Tôi đã chạy đủ các lượt cần thiết, không cần lặp API để chấm lại. Nếu tái lập
thí nghiệm, chọn thư mục mới và chấp nhận tính không quyết định của model/API:

```bash
.venv/bin/python scripts/run_agent_benchmark.py --config configs/agent/baseline-dense.json \
  --split dev --output artifacts/evaluation/my-dev-dense
.venv/bin/python scripts/run_agent_benchmark.py --config configs/agent/lexical.json \
  --split dev --output artifacts/evaluation/my-dev-lexical
.venv/bin/python scripts/run_agent_benchmark.py --config configs/agent/hybrid-context.json \
  --split dev --output artifacts/evaluation/my-dev-hybrid
```

Runner chỉ đọc projection có ID/question/filter; không mở labels/cases.
Test yêu cầu khóa đúng fingerprint trước khi mở query/services. Final=baseline,
đã dùng chung một run7 câu, không nhân đôi trace. Lệnh tương ứng (output phải mới):

```bash
.venv/bin/python scripts/run_agent_benchmark.py --config configs/agent/baseline-dense.json \
  --split test --lock artifacts/evaluation/selection-lock.json \
  --output artifacts/evaluation/my-test-baseline-final
.venv/bin/python scripts/run_locked_original_retrieval.py \
  --run artifacts/evaluation/my-test-baseline-final \
  --config configs/agent/baseline-dense.json --lock artifacts/evaluation/selection-lock.json
```

Lệnh sau đo query gốc top3 bằng local retrieval, không gọi model. Không dùng
kết quả test để tinh chỉnh. Khóa cũ/manifest/nhãn không ghi đè; thay runtime/model/
prompt/corpus/config cần thí nghiệm và khóa mới, không gán trace cũ cho cấu hình mới.

## Context, trace và giới hạn

Tôi giữ whole chunks có ID/source/page, loại trùng ID/text. Budget là ký tự Unicode
của tool JSON gồm wrapper, và tổng history/schema/instructions kiểm trước request.
Append giữ baseline; refresh_on_full xóa text evidence cũ khỏi function outputs
khi lượt mới thiếu chỗ, giữ call IDs/protocol rồi repack. Chỉ citation còn trong
snapshot đã gửi được phép trả. Test xác minh eviction thực sự xóa text khỏi request;
chưa có eviction trong benchmark thật. Đo packing offline22 dev ở
[context-packing-dev.json](../artifacts/evaluation/context-packing-dev.json):3000
ký tự bỏ11/66 chunk;6000 và12000 không bỏ. Đây không phải CER hoặc chất lượng model.

Mỗi run có fingerprint: model resolve/SDK/Python/endpoint, prompt/schema, code,
config, corpus/benchmark và protocol. Trace từng query giữ query gốc/biến đổi,
filter/nguồn filter, top-k ranking và selected tool JSON, snapshot context mỗi
call/delivery flag, answer/citation, stop/error, timing/usage/cost. Candidate hoặc
output vừa chuẩn bị không đồng nghĩa context đã gửi. Opaque reasoning chỉ replay
trong RAM, không ghi trace; không log key/DSN/Authorization/raw provider errors.

Mỗi run/file bắt buộc mới; không resume hay tự retry. Lỗi giữ nguyên; start marker
còn nếu tiến trình bị ngắt, completed trace có attempt1 và thời gian. Trần mỗi câu:
4 model calls,3 searches,2048 output token/call,45s/request,retry0. Error gồm API,
tool, call/history/context limit, refusal, citation/protocol sai; không tính là
abstention đúng. Model có thể tự abstain sau retrieval thiếu evidence. Tài liệu
là dữ liệu dưới instructions cố định; test injection kiểm tra protocol, không
chứng minh kháng mọi tấn công.

## Review và metrics offline

Mỗi `reviews.json` ghi method/reviewer, trace_sha256, context_grade/grounded_grade
0/1/2, reason/source_check, từng citation với supports_answer/reason/source_quote
nguyên văn trong context. Abstention correctness là bool cho câu thiếu evidence;
context/grounded null ở các câu đó. Tôi đọc answer, context, full citation chunk
và PDF gốc bằng AI hỗ trợ, chưa có người chấm độc lập. Không tự gán nhãn chỉ vì
ID hợp lệ hoặc gold xuất hiện.

Gold OR: đúng source_id/page và nguyên văn trong một chunk. Hit@1/Hit@3 dùng cùng
query gốc/cutoff, chỉ20 dev/5 test có đáp án. Dev tái dùng ranking bước04 khi hash
code retrieval/corpus/benchmark/query/filter/config không đổi; trace có đường dẫn
nguồn/hash. Query agent viết lại/nhiều lượt giữ ranking từng call, không giả một
ranking pipeline. CER lấy context request cuối delivery_confirmed, có thể khác
Hit@3. Grade2 đủ điều kiện/nguồn,1 một phần,0 thiếu; answered thiếu căn cứ hoặc
answerable abstain/error có grounded0.

Citation cần vừa có trong context vừa hỗ trợ các claim/điều kiện được gắn nguồn.
Câu thiếu evidence chỉ abstention đúng khi model thực sự từ chối, không thêm đáp
án vô căn cứ. Citation0/0 có rate=null (N/A); báo riêng answered có citation.
Tất cả query giữ trong kết quả; retrieval lỗi hit0, grounded lỗi0, missing-evidence
lỗi không tính abstain đúng. CER vẫn xét last confirmed context kể cả khi error.
Tool-use chia successful queries, lỗi và search distribution báo trên toàn split.
Usage tổng null nếu có call chưa đo; USD luôn null khi chưa có billing/giá, không
coi là0. Thời gian là elapsed mỗi query gồm API/retrieval, không gồm setup/I/O/review.

```bash
# Cần đọc/gán nhãn reviews.json trước. Output mới, không ghi đè metrics lịch sử.
.venv/bin/python scripts/evaluate_agent.py artifacts/evaluation/dev-baseline-v1 \
  --output /tmp/day07-recheck-dense.json
```

[Protocol](../artifacts/evaluation/dev-protocol.json),
[quyết định dev](../artifacts/evaluation/dev-selection.json),
[khóa trước test](../artifacts/evaluation/selection-lock.json),
[test chung baseline/final](../artifacts/evaluation/test-comparison.json).
Khóa được push trước test ở18d1c3e. Một sửa đường dẫn tương đối scorer sau test
có [hash riêng](../artifacts/evaluation/scorer-path-correction.json); thuật toán
không đổi, ba bảng dev tính lại giống hệt, không thay runtime/nhãn/trace hay lặp API.
