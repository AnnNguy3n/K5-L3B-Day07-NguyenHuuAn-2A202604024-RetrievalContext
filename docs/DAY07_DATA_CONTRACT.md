# Công việc 02 — dữ liệu Day06 → Day07

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã chuyển dữ liệu Day06 sang JSON array UTF-8 tại `data/day06/A/` và
`data/day06/B/`, giữ nguyên snapshot Day06. Converter chạy offline bằng
`.venv/bin/python`; môi trường đã kiểm tra là Python 3.14.10.

## Schema và nguồn

| Thành phần | Quy tắc |
|---|---|
| Chunk | `chunk_id → id`, `document_id → source_id`; giữ nguyên `text`, `source`, `page`, toàn bộ `metadata` và trường bổ sung |
| Hàng SQL | `normalize_sql_row()` lấy nguồn/trang/document ID từ metadata; giữ score, strategy và digest nếu có |
| Trang | Số nguyên dương, trang PDF vật lý 1-based; không dùng số trang in |
| Nhãn offline | `questions.json` giữ ID, split, câu hỏi, đáp án, filter; gold chính và `accepted_alternatives` thành danh sách `gold` thay thế (OR) |
| Truy vấn runtime | `queries.dev.json`, `queries.test.json` chỉ có `id`, `question`, `filter` nếu có; không chứa gold/đáp án |
| Nguồn | `sources.json` giữ locator tương đối, hash snapshot, trang, ghi công và giấy phép từ manifest Day06 |

Tôi dùng `resolve_source_path(source, day06_root, registry)` để mở PDF từ root
Day06, thay vì mở chuỗi `source` tương đối theo CWD Day07. Locator phải nằm trong
root; converter kiểm tra hash PDF và trang theo manifest. Metadata được sao chép
sâu; alias mâu thuẫn, trường thiếu/sai kiểu, ID trùng hoặc nguồn sai đều báo lỗi.
Filter chỉ sao chép từ đầu vào, không suy ra từ gold. Adapter không tạo/sửa vector.

Nguồn là *Project Management*, Adrienne Watt, BCcampus; corpus gồm 32 trang PDF
35–63 và 65–67. Thông tin giấy phép, ghi công và ngoại lệ nằm trong `sources.json`.

## Tái tạo

Chạy từ gốc Day07; đổi `DAY06_PATH` theo vị trí repo trên máy khác.

```bash
DAY06_PATH="../K5-L3B-DAY06-NguyenHuuAn-2A202604024-DataIngestion"

.venv/bin/python starter/convert_day6.py \
  --day06-root "$DAY06_PATH" \
  --chunks "$DAY06_PATH/starter/results/chunks_B.jsonl" \
  --questions "$DAY06_PATH/benchmark/questions.jsonl" \
  --expected-chunks 131 --strict-dev --output data/day06/B

.venv/bin/python starter/convert_day6.py \
  --day06-root "$DAY06_PATH" \
  --chunks "$DAY06_PATH/starter/results/chunks_A.jsonl" \
  --questions "$DAY06_PATH/benchmark/questions.jsonl" \
  --expected-chunks 206 --output data/day06/A

.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check

# Tùy chọn: đối chiếu SQL chỉ đọc khi PostgreSQL Day06 đang chạy.
.venv/bin/python scripts/check_day07_sql_contract.py \
  --output docs/day07-sql-normalization-check.json
```

CLI nhận JSONL/array, BOM UTF-8; có `--chunk-format`, `--question-format` để chọn
rõ định dạng. `--manifest` mặc định là `DAY06_ROOT/data/manifest.json`; lỗi trả
exit 2. `--strict-dev` yêu cầu mọi evidence dev nằm trọn trong chunk. Output phải
ngoài Day06; các file cùng tên bị ghi đè. Mỗi bộ có 6 file: chunks, questions,
queries dev/test, sources và validation. `validation.json` lưu hash và đối chiếu dev.

## Kết quả và giới hạn

| Kiểm tra | B | A |
|---|---:|---:|
| Chunk / ID duy nhất | 131 / 131 | 206 / 206 |
| ID, text, metadata so với export | Giữ nguyên | Giữ nguyên |
| Câu hỏi | 15 dev, 5 test | 15 dev, 5 test |
| Evidence dev nằm trọn trong chunk | 16/16 | 10/16 |
| Hàng SQL khớp ID/text/nguồn/trang/metadata | 131/131 | 206/206 |

Tôi giữ được evidence thay thế của q11. Sáu evidence A của q01/q04/q05/q07/q08/q09
đi qua ranh giới chunk; ghép theo offset vẫn tìm được trích dẫn, nên tôi giữ
nguyên nhãn và không bật strict cho A. Bộ B đạt strict và được dùng cho bước sau.
Đây là đối chiếu dữ liệu, không phải điểm retrieval.

Tổng **33/33 test đạt**, gồm 12 test mới cho định dạng, SQL, alternatives, filter,
truy vấn không nhãn, nguồn và các nhánh lỗi. Xem
[B/validation.json](../data/day06/B/validation.json),
[A/validation.json](../data/day06/A/validation.json) và
[minh chứng SQL chỉ đọc](day07-sql-normalization-check.json).
Nhãn test mới được chuyển schema, chưa đối chiếu evidence với corpus hoặc chạy
hệ thống trên test. CLI hiện chưa nhận câu thiếu evidence hay multi-hop bắt buộc
nhiều đoạn; cần bổ sung schema ở bước benchmark. Converter không thay snapshot
Day06; kiểm tra kết nối gần nhất của bước 02 nằm trong
[JSON kết nối](day07-task02-connection-check.json).
