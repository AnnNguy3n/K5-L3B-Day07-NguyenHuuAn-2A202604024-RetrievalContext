# Công việc 04 — so sánh ba phương pháp retrieval

Nguyễn Hữu An · 2A202604024 · L3B · 16/11/2026

Tôi đã tích hợp dense, lexical và hybrid trên cùng 131 chunk B, benchmark v1,
22 query dev và cutoff top-3. Tôi chạy lại ba phương pháp trong cùng lượt với
query/filter gốc; toàn bộ truy vấn thành công. Hai câu thiếu evidence vẫn chạy
nhưng không thuộc mẫu số 20 câu có đáp án.

| Phương pháp | Hit@1 | Evidence Hit@3 | Lỗi | Tổng search |
|---|---:|---:|---:|---:|
| Dense | 17/20 (85%) | 18/20 (90%) | 0/22 | 1,171 s |
| Lexical | 18/20 (90%) | 19/20 (95%) | 0/22 | 0,022 s |
| Hybrid RRF | 19/20 (95%) | 19/20 (95%) | 0/22 | 1,214 s |

Tôi dùng `HybridRetriever(dense, lexical, candidate_k=20, rrf_k=60)` và API
`search(query, *, filters=None, top_k=3)`. Hai nhánh nhận cùng query/filter,
lấy tối đa 20 candidate; score fusion là tổng `1/(60+rank_nhánh)`, rank từ 1.
Tôi không cộng raw score dense/lexical. Kết quả loại ID trùng, hòa điểm theo ID,
giữ nguồn/trang/metadata cùng component ranks/scores; `strategy='hybrid'`.

Tôi kiểm tra core của cùng ID và hợp nhất metadata bổ sung từ SQL/JSON. Trường
chung mâu thuẫn, ranking sai hoặc nhánh lỗi đều báo lỗi; nhánh rỗng hợp lệ.
Caller quản lý adapter dense và gọi `close()` khi xong. Tham số 20/60 là lựa
chọn ban đầu, chưa chứng minh tối ưu. API từng nhánh: [dense](RETRIEVAL_DENSE.md),
[lexical](RETRIEVAL_LEXICAL.md); code hybrid nằm trong `src/hybrid_retrieval.py`.

## Chạy và minh chứng

Chạy từ root Day07 khi dịch vụ Day06 hoạt động; dùng thư mục output mới.

```bash
.venv/bin/python scripts/run_hybrid_dev.py run --output artifacts/retrieval/hybrid/my-run
.venv/bin/python scripts/run_hybrid_dev.py score --output artifacts/retrieval/hybrid/my-run
DENSE_LIVE_TEST=1 .venv/bin/python -m unittest discover -s tests -q
```

Runner hỗ trợ `--candidate-k`, `--rrf-k`. Lệnh run chỉ đọc queries dev; score
chạy offline, kiểm tra hash, nguồn/trang/metadata, ranking nhánh, top-3 baseline
và tính lại RRF trước khi chấm gold OR. Tôi lưu minh chứng của lượt chung tại:

- [comparison.json](../artifacts/retrieval/hybrid/verified-dev/comparison.json): điểm từng câu của ba phương pháp, thời gian/chi phí và hash.
- [trace.jsonl](../artifacts/retrieval/hybrid/verified-dev/trace.jsonl): 22 query, candidate từng nhánh và baseline, kết quả fusion và lỗi.
- [summary.json](../artifacts/retrieval/hybrid/verified-dev/summary.json): cấu hình, hash code/corpus/benchmark và trạng thái dịch vụ.
- [validation.txt](../artifacts/retrieval/hybrid/validation.txt): **111/111 test đạt**, gồm smoke thật và 21 test hybrid mới.

## Giới hạn

Hybrid còn thiếu evidence top-3 cho q16 và bằng lexical ở Hit@3. Tôi chưa chỉnh
theo các lỗi này, chưa đo context/câu trả lời/abstention, chưa chạy 7 câu test
hoặc chốt hai cấu hình cải thiện. Nhãn vẫn chưa có người chấm độc lập.

Thời gian là một lượt local, thứ tự cố định, chưa tính đọc/ghi file hay chấm
điểm. Preflight dense 131,680 ms và build lexical 8,200 ms đo riêng. Lượt so sánh
có 44 query embedding local, thêm probe constructor; không gọi LLM/API trả phí.
Tiền, điện và token chưa đo. Các lần đầu bị sandbox chặn localhost đã được chạy
lại với quyền phù hợp; lượt verified và smoke cuối đều đạt. Tôi giữ nguyên
Day06, snapshot A/B và benchmark; chưa triển khai agent thật trong công việc 04.
