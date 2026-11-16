# Công việc 05 — agent OpenAI API

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã triển khai agent bằng SDK `openai==3.51.0` và Responses/function calling
trong `src/openai_agent.py`. Model smoke là `gpt-6.1-terra`; response xác nhận
cùng model. CLI chọn dense, lexical hoặc hybrid; embedding/index Day06 giữ local.

## Cấu hình và chạy

Tôi dùng Python 3.14.10, dependencies trong `requirements.txt`. Key/model nằm
riêng trong `.env` hoặc shell (shell ưu tiên), không nội suy biến trong key.
API mặc định là `https://api.openai.com/v1`; endpoint khác cần cấu hình rõ bằng
`OPENAI_BASE_URL`. Tôi không ghi key/DSN, reasoning hoặc raw exception vào trace.

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
# Điền OPENAI_API_KEY và OPENAI_MODEL trong .env riêng.
# Bật Ollama/PostgreSQL Day06 khi chọn dense hoặc hybrid.
.venv/bin/python scripts/run_openai_agent.py --query-id q01 --retriever hybrid \
  --output artifacts/agent/my-run/q01.json
```

`--query-id` chỉ nhận ID từ queries dev đã khóa; `--question` nhận câu tự nhập,
cho phép `--filters` theo section/language/document_type. Output phải là file mới
trong `artifacts/agent/`; exit 0 cho answered/abstain, 1 cho error. Câu hỏi và
chunk được chọn sẽ gửi tới OpenAI; nhãn offline và toàn corpus không được gửi.

## Hành vi và giới hạn

Tôi dùng tool strict `search_corpus(query)`, kiểm tra arguments/call_id rồi trả
kết quả đúng call_id. Model tự chọn query, tìm tiếp hoặc kết thúc; phải truy xuất
trước khi trả lời. History giữ output items và reasoning opaque trong RAM để
replay, không lưu chúng vào trace. Mọi request đặt `store=false`; đây không phải
cam kết không lưu giữ dữ liệu của nhà cung cấp.

Mặc định: **4 model call, 3 search, top-3, 2048 output token/call**, reasoning low,
timeout 45 giây/request, retry 0. Budget JSON tính bằng ký tự Unicode: 6000/tool,
16000 tổng tool output, 60000 toàn history/schema. Giới hạn đổi được bằng CLI
hoặc `AGENT_*` trong `.env.example`; timeout không phải deadline toàn câu.

Tôi gửi chunk nguyên vẹn kèm ID/nguồn/trang, loại trùng cơ bản và ghi ID bị bỏ.
Citation phải thuộc context đã gửi và xuất hiện trong answer. Model chủ động
abstain khi thiếu evidence; lỗi API/tool, quá giới hạn, refusal hoặc sai citation
được ghi error. Kiểm tra ID chưa thay thế audit ngữ nghĩa. Thiếu key/model báo
lỗi trước khi mở retriever; không tự đổi model hoặc dùng kết quả giả.

## Kết quả và minh chứng

Hai smoke dùng OpenAI và hybrid Day06 thật, chỉ lấy câu hỏi từ dev:

| Dev ID | Kết quả | Model call / search | Input / output token |
|---|---|---:|---:|
| q01 | answered, citation đúng bốn giai đoạn ở PDF trang 35 | 2 / 1 | 1499 / 137 |
| d07-d21 | abstain vì thiếu tổng ngân sách chính xác | 3 / 2 | 2723 / 109 |

[Trace q01](../artifacts/agent/smoke-v1/q01.json) ·
[Trace d07-d21](../artifacts/agent/smoke-v1/d07-d21.json) lưu context, tool call,
request ID, model, usage và trạng thái. Tôi đọc spot-check context/citation của
hai câu; chưa đánh giá chất lượng chung. Chi phí USD chưa đối soát nên để null.

Tôi bổ sung 23 test cho protocol, giới hạn, lỗi, nhãn/secret và citation. Lượt cuối
có **134 test: 133 đạt, 1 bỏ qua** (live OpenAI unittest); hai smoke OpenAI chạy
riêng qua CLI. [Log test](../artifacts/agent/validation.txt) ·
[Phiên bản dependencies](../artifacts/agent/dependencies.txt).

```bash
# Hồi quy và smoke Day06; không gọi lại OpenAI trả phí.
OPENAI_AGENT_LIVE_TEST=0 DENSE_LIVE_TEST=1 .venv/bin/python -m unittest discover -s tests -q
```

[Tóm tắt hồi quy retrieval](../artifacts/agent/retrieval-regression.json) ghi đủ
22 dev: dense 17/20–18/20, lexical 18/20–19/20, hybrid 19/20–19/20 như bước04.
Snapshot, benchmark và minh chứng 04 giữ nguyên. Tôi chưa chạy agent toàn dev,
7 câu test hoặc tối ưu context sâu. Tham khảo:
[Responses/function calling](https://developers.openai.com/api/docs/guides/function-calling).
