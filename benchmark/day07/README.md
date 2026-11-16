# Benchmark Day07 v1.0.0

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã tạo 29 câu trên corpus B cố định: 131 chunk, 32 trang PDF vật lý 35–63,
65–67. Tôi chỉ xây và kiểm chứng nhãn offline, chưa chạy retrieval/agent.

| Loại câu | Dev | Test mới |
|---|---:|---:|
| Có đáp án | 20 | 5 |
| Thiếu evidence | 2 | 2 |
| Tổng | 22 | 7 |

## Schema và cách chấm

- `cases.json`: nhãn offline gồm ID, split, câu hỏi, `answerable`, nguồn gốc,
  intent và ghi chú kiểm chứng. Câu có đáp án có `expected_answer` và `gold`
  gồm nguồn/trang/evidence; các gold thay thế nhau (OR), không phải multi-hop.
- Câu thiếu evidence có `expected_behavior: search_and_abstain`, `absence_reason`
  và `absence_check`, không có gold/đáp án giả. Phạm vi absence chỉ là corpus đã index.
- `queries.dev.json`, `queries.test.json`: chỉ ID, câu hỏi, filter nếu có. Nhãn,
  audit, review và lịch sử không đưa vào model/retrieval tool.

Tôi tính hit khi candidate đúng nguồn, đúng trang và chứa trọn nguyên văn một
evidence OR. Hit@1, Evidence Hit@3 và Context Evidence Rate có mẫu số là số câu
có đáp án. Context phải thực sự chứa evidence cùng nguồn/trang sau cắt budget;
không chỉ có ID trong candidate. Không ghép evidence rời qua chunk. Khi đo sẽ
báo `n/N`, tỷ lệ, cutoff, budget và cấu hình; chấm câu trả lời/citation/abstention
riêng theo [BENCHMARK.md](../../BENCHMARK.md).

## Kiểm chứng và giới hạn

Tôi chuyển q01–q20 Day06 sang dev, giữ ánh xạ trong [history.json](history.json)
vì test cũ đã có kết quả/phân tích công khai. Test mới có mục tiêu đáp án khác dev.
[review.json](review.json) lưu nhận xét 154 cặp dev–test và hash cases; validator
kiểm tra trùng câu/intent/đáp án/evidence, Jaccard >=0.65 hoặc SequenceMatcher >=0.82.
Heuristic và nhận xét AI chưa thay thế việc chấm ngữ nghĩa độc lập.

Tôi đối chiếu 26 evidence với chunk và đúng PDF snapshot, đọc 32 trang bằng pypdf
và kiểm tra bản render trang 40/54/63. Hai evidence q11 giữ nguyên. Gold q20 cũ
qua ranh giới chunk B nên tôi rút phần mở đầu, giữ điều kiện và đáp án; gold gốc
lưu tại `provenance.label_revision.original_gold`. Snapshot A/B không thay đổi.

Bốn câu thiếu evidence có ghi chú đọc toàn corpus và inventory quét 131 chunk/
32 trang. [audit.json](audit.json) giữ đối chiếu nguồn và kết quả quét; từ khóa
không tự chứng minh absence. Nhãn có AI hỗ trợ kiểm chứng, chưa có người chấm
độc lập; không có câu đang chờ xác nhận nguồn trong hồ sơ hiện tại. Nguồn, ghi
công và ngoại lệ giấy phép giữ tại [sources.json](../../data/day06/B/sources.json).

## Bảo vệ test và tái lập

Tôi chỉ dùng dev để chỉnh hệ thống. Trước đánh giá test ở công việc 11 cần khóa
cấu hình, tiêu chí chọn và hash code/corpus/benchmark; lưu trace mọi lần chạy.
Nếu dùng test để chỉnh, chuyển test đó sang dev, tạo test mới và tăng phiên bản.
Benchmark này chưa có lượt đánh giá test; cùng corpus và có AI biên soạn nên
không phải bộ test mù từ người chấm ngoài.

Chạy từ gốc Day07, không cần model/DB. Đổi `PDF_PYTHON` trên máy khác bằng Python
có cùng phiên bản pypdf ghi trong audit; shell không tự đọc `.env`.

```bash
DAY06_PATH="../K5-L3B-DAY06-NguyenHuuAn-2A202604024-DataIngestion"
PDF_PYTHON="/Users/annnguy3n/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"

# Kiểm tra nguồn/hash, không ghi file hoặc đánh giá retrieval.
.venv/bin/python scripts/day07_benchmark.py check \
  --day06-root "$DAY06_PATH" --pdf-python "$PDF_PYTHON"

# Chỉ tái sinh queries/audit/manifest trong giai đoạn biên soạn nhãn.
.venv/bin/python scripts/day07_benchmark.py build \
  --day06-root "$DAY06_PATH" --pdf-python "$PDF_PYTHON"

.venv/bin/python -m unittest discover -s tests -v
```

`cases.json`, `review.json`, `history.json` là đầu vào đã biên soạn. `check` đối
chiếu hash khóa và đọc lại nguồn; `build` tái sinh đầu ra sau khi kiểm tra đạt,
không dùng để che thay đổi sau đánh giá. Sửa cases phải rà lại nguồn/review hash.
[manifest.json](manifest.json) ghi phiên bản/hash dữ liệu, code, chính sách và
snapshot; hash manifest sẽ đưa vào khóa cấu hình sau. Công cụ trả exit 2 khi lỗi.
Tổng **50/50 test code đạt**, gồm 17 test mới. Starter baseline chỉ nhận câu có
đáp án; bước retrieval cần xử lý mixed cases đúng mẫu số.
