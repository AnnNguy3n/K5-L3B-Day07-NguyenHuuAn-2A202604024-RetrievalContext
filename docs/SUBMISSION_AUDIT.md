# Đối chiếu bài nộp — Nguyễn Hữu An, 2A202604024, L3B

Tôi chỉ hoàn thành **phần cơ bản**. Đề/rubric/checklist gốc giữ nguyên; các mục
dưới đây dẫn tới minh chứng cuối, không thay yêu cầu chấm. Ngày16/11/2026.

## RUBRIC.md — toàn bộ sáu hàng cơ bản

| Hàng rubric | Minh chứng cụ thể |
|---|---|
| Dữ liệu/benchmark | [Kết nối](day07-connection-check.json), [schema/nguồn/cách tái tạo](DAY07_DATA_CONTRACT.md), [B/chunks và registry](../data/day06/B/), [benchmark v1/manifest/history/audit](../benchmark/day07/). 131 chunk/32 trang, embedding/digest/prefix giữ nguyên;25 có đáp án+4 thiếu evidence; dev20+2/test5+2. |
| Retrieval | [Dense](../src/dense_retrieval.py), [TF-IDF](../src/lexical_retrieval.py), [RRF](../src/hybrid_retrieval.py), [so sánh ba phương pháp gốc](../artifacts/retrieval/hybrid/verified-dev/comparison.json). Cùng top3, nguồn/rank/score/filter và lỗi rõ; dense dùng pgvector thật. |
| Agent/context | [Responses agent](../src/openai_agent.py), [ba config](../configs/agent/), [runner](../scripts/run_agent_benchmark.py), [trace dev dense/q16](../artifacts/evaluation/dev-baseline-v1/q16.json), [test abstention](../artifacts/evaluation/test-baseline-final-v1/d07-t06.json). Tool thật, budget/dedup/history, citation/abstention và stop/error tách biệt. |
| Đánh giá | Mỗi run trong [evaluation](../artifacts/evaluation/) có metrics và reviews từng câu/citation:66 dev+7 test,65 citation đã đọc. [Bộ chấm](../scripts/evaluate_agent.py), [quy tắc](EVALUATION.md), [kiểm lại offline](../artifacts/evaluation/submission-check.json). Đủ Hit@1/Hit@3/CER, grade0/1/2, tool use, citation, abstention, lỗi/thời gian/usage; USD null có lý do. |
| Thử nghiệm/cải thiện | [Protocol trước dev](../artifacts/evaluation/dev-protocol.json), [ba run và quyết định](../artifacts/evaluation/dev-selection.json), [khóa trước test](../artifacts/evaluation/selection-lock.json), [test baseline/final cùng run](../artifacts/evaluation/test-comparison.json). Hai lỗi dev q16/q19, giả thuyết, trước/sau và trade-off trong [báo cáo](../report/individual/2A202604024_NguyenHuuAn.md). |
| Tái lập/báo cáo | [README](../README.md), [cách chạy](EVALUATION.md), [requirements](../requirements.txt), [dependencies](../artifacts/evaluation/dependencies.json), [log144 test](../artifacts/evaluation/validation.txt), [báo cáo cuối](../report/individual/2A202604024_NguyenHuuAn.md), [nguồn/giấy phép](../data/day06/B/sources.json). |

## SUBMISSION.md — checklist cơ bản

- [x] Code agent, ba phương pháp gồm dense, tests và lệnh chạy: src/, tests/, docs/EVALUATION.md.
- [x] Model embedding/vector store, kết nối, dữ liệu được phép chia sẻ/cách tái tạo: .env.example, docs/SETUP_DAY07.md, data/day06/B/sources.json. Key/DSN ở .env riêng, không commit.
- [x] Benchmark đủ25 câu có evidence và4 thiếu evidence, dev/test độc lập theo lịch sử đã công bố.
- [x] Trace từng query/tool, context thật, answer/citation/error/timing/usage/cost: ba dev run và test-baseline-final-v1; tổng73 trace, không nhân bản baseline/final test.
- [x] Baseline+2 cấu hình cải thiện, nhãn/bảng, hai lỗi dev và lý do chọn: protocol/dev-selection/reviews và báo cáo cuối.
- [x] Báo cáo đúng MSSV/họ tên ở report/individual/2A202604024_NguyenHuuAn.md.
- [x] Người chấm đọc được repo: [kiểm tra GitHub không xác thực](../artifacts/evaluation/repository-access.json) trả200/public; không đổi quyền. Không gửi bài qua email/LMS.

Tôi không thực hiện ADVANCED hoặc bonus. Kiểm nguồn có AI hỗ trợ; không nhận đã
có người chấm độc lập. Chi phí tiền chưa đối soát, không báo0 hay bịa giá.

## Kiểm tra cuối và lịch sử commit

`OPENAI_AGENT_LIVE_TEST=0 DENSE_LIVE_TEST=1 .venv/bin/python -m unittest discover
-s tests -q`:143 đạt,1 live OpenAI bỏ qua/144. `pip check` đạt. Benchmark/source
check đủ26 evidence; checker offline tính lại cả bốn metrics giống hệt, kiểm
hash/context/review/limits và khóa trước test. Không lặp OpenAI để chạy hồi quy.

[submission-check.json](../artifacts/evaluation/submission-check.json) và
[benchmark-check.json](../artifacts/evaluation/benchmark-check.json) ghi phạm vi
kiểm thực tế. Scorer sửa path sau test có [hash trước/sau](../artifacts/evaluation/scorer-path-correction.json);
khóa/trace/nhãn cũ giữ nguyên, runtime không đổi, không dùng test để tinh chỉnh.

| Công việc | Commit riêng trên main |
|---|---|
| 06 | f068517 |
| 07 | 45a3288 |
| 08 | ef6551a; sửa projection metadata a0f8a57 |
| 09 | 167acb5 |
| 10 | 1d53001; protocol trước chạy37e44c7 |
| 11 | de39911; khóa trước test18d1c3e |
| 12 | Commit cuối “Hoàn thành công việc 12: hoàn thiện bài nộp”, chứa checklist này; mã chính xác xem git log. |

Các commit được push origin/main theo từng công việc; phiên bản cuối được đối
chiếu HEAD và origin/main sau push. Minh chứng01–05, benchmark/snapshot cũ giữ
nguyên; báo cáo tiến độ được thay bằng báo cáo cuối theo yêu cầu bài nộp.
