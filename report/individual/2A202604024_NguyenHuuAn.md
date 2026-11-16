# Day07 — Retrieval và Context Engineering

Nguyễn Hữu An · MSSV **2A202604024** · Lớp **L3B** · 16/11/2026

## Dữ liệu, model và split

Tôi đã hoàn thành phần cơ bản với **131 chunk B từ 32 trang PDF35–63,65–67** của
*Project Management*, Adrienne Watt, BCcampus. Tôi giữ ID/text/metadata và nguồn
Day06; không ingestion lại, không đổi index. Pgvector/PostgreSQL Day06 dùng
`embeddinggemma-2:740m-mxfp8`, 768 chiều, digest `a4efd357…b3f8189`, prefix query
`task: search result | query: ` và document `title: none | text: ` (giữ dấu cách
cuối). Digest đầy đủ, giấy phép CC BY4.0 và ngoại lệ theo chương/hình nằm trong
[registry nguồn](../../data/day06/B/sources.json) và [.env.example](../../.env.example).
Tôi không chia sẻ lại toàn PDF; cách lấy/đối chiếu snapshot ở registry.

Benchmark v1 có **dev20+2, test mới5+2**. Toàn q01–q20 Day06, gồm q16, là dev;
test cũ đã có kết quả nên không coi là test độc lập. Gold là các evidence thay
thế OR, nguyên văn trong một chunk đúng nguồn/trang. q20 có lịch sử sửa nhãn
riêng; snapshot cũ không đổi. [Manifest và lịch sử](../../benchmark/day07/README.md).

Agent dùng OpenAI Responses/function calling, SDK3.51.0, model cấu hình và
response đều **gpt-6.1-terra**. Model tự tạo query, tìm tiếp hoặc dừng; một tool
`search_corpus` gọi retrieval thật. Nhãn chỉ dùng offline. Tôi đọc/chấm từng
answer, context và citation đối chiếu PDF với AI hỗ trợ; **chưa có người chấm
độc lập**. Không dùng LLM judge hoặc ID citation làm bằng chứng đủ về nội dung.

## Retrieval, context và ba cấu hình dev

Tôi công bố giả thuyết/tiêu chí tại [protocol](../../artifacts/evaluation/dev-protocol.json)
(commit37e44c7) trước API dev đầu. Dense baseline giữ budget bước05; lexical
chỉ thay retriever bằng TF-IDF. Hybrid-context dùng RRF(candidate_k20,rrf_k60),
top3→5, tool6000→12000, context16000→24000, history60000→80000 ký tự Unicode,
thêm refresh_on_full. Đây là nhiều thay đổi cùng lúc, không tách quan hệ nhân quả.

Tôi giữ nguyên chunk cùng ID/nguồn/trang, loại trùng, không cắt mất điều kiện.
Append là baseline; refresh xóa text cũ khỏi history khi lượt mới thiếu chỗ.
History/schema/opaque items đều có giới hạn trước request. Trace tách candidate,
context chuẩn bị và snapshot context thực gửi. Cả ba trần4 model calls,3 search,
2048 output token/call, timeout45s/request, retry0; không đổi model/prompt.

| Dev, mỗi cấu hình đủ22 câu | Hit@1 gốc | Evidence Hit@3 gốc | CER | Grounded /2 | Citation hỗ trợ | Abstain đúng |
|---|---:|---:|---:|---:|---:|---:|
| Baseline dense | 17/20 (85%) | 18/20 (90%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) |
| Lexical TF-IDF | 18/20 (90%) | 19/20 (95%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) |
| Hybrid-context | 19/20 (95%) | 19/20 (95%) | 20/20 (100%) | 2,00 | 20/20 (100%) | 2/2 (100%) |

Hit gốc tái dùng ranking top3 bước04, đã kiểm hash code/corpus/query/filter và
cấu hình tương đương. CER lấy **context cuối thực gửi**, gồm query model viết
lại; không có một ranking pipeline tương đương giả. Câu thiếu evidence không
thuộc mẫu số Hit/CER. Cả ba: context và grounded grade0/1/2=0/0/20; trả lời có
citation20/20; tool-use22/22; từ chối nhầm0/20; lỗi0/22. Mỗi abstention không có
citation là0/0, không áp dụng. [Trace gốc](../../artifacts/retrieval/hybrid/verified-dev/trace.jsonl)
và [quyết định dev](../../artifacts/evaluation/dev-selection.json) dẫn tới từng run/review/metrics.

| Tài nguyên dev | Model calls | Search/query | Input / output token | Tổng giây |
|---|---:|---|---:|---:|
| Dense | 46 | 1×20, 2×2 | 33000 / 3225 | 116,197 |
| Lexical | 45 | 1×21, 2×1 | 33393 / 3189 | 108,321 |
| Hybrid-context | 45 | 1×21, 2×1 | 42152 / 3189 | 117,703 |

Tôi chọn **baseline-dense** theo thứ tự đã công bố: grounded, CER, citation,
abstention, ít lỗi, ít tổng token, rồi thời gian. Chất lượng hòa; tổng36225 token
của dense thấp hơn lexical36582 và hybrid45341. Lexical nhanh hơn trong lượt
này. Hybrid/context lớn chưa cải thiện answer/CER; refresh không kích hoạt trong
trace thật nên chưa có bằng chứng chất lượng riêng cho eviction.

## Test sau khóa

Tôi push [khóa cấu hình](../../artifacts/evaluation/selection-lock.json) ở commit
18d1c3e, lúc21:13:46 ngày16/11/2026, trước lượt test đầu. Khóa giữ model/prompt,
code, corpus/benchmark, cấu hình và80 hash minh chứng dev. Test chỉ từng được
kiểm tra lúc xây nhãn, không dùng để chọn hệ thống. Không chỉnh theo kết quả test.

**Final trùng baseline**: cùng một run thật đủ7 câu được tham chiếu cho cả hai,
không phải hai thí nghiệm độc lập. [Run/review/metrics test](../../artifacts/evaluation/test-baseline-final-v1/)
và [ánh xạ baseline/final](../../artifacts/evaluation/test-comparison.json).

| Baseline = final | Kết quả test |
|---|---:|
| Hit@1 / Evidence Hit@3 query gốc | 4/5 (80%) / 5/5 (100%) |
| CER; context grade0/1/2 | 5/5 (100%); 0/0/5 |
| Grounded grade0/1/2; mean /2 | 0/0/5; 2,00 |
| Citation hỗ trợ / answered có citation | 5/5 (100%) / 5/5 (100%) |
| Abstention đúng / từ chối nhầm | 2/2 (100%) / 0/5 (0%) |
| Tool-use / lỗi | 7/7 (100%) / 0/7 (0%) |
| Model calls / search distribution | 16 / 1×5, 2×2 |
| Input / output token | 11875 / 958 |
| Tổng / mean / median / max giây mỗi query | 37,544 / 5,363 / 4,910 / 6,587 |

Tôi đọc đủ5 answer/citation và2 abstention: giữ đúng điều kiện negative float,
synchronous cùng ví dụ, lowest bid và nhịp update “usually”. Câu CPI không lấy
công thức từ ngoài corpus; Figure5.2 không bịa ngày. Tôi xem render PDF63 xác
nhận hai ô Communication Date trống, khớp mô tả hình đã có trong snapshot B.

## Hai lỗi dev và ba ví dụ

- **q16, lỗi retrieval**: dense query gốc lấy execution/closure/life-cycle, thiếu
  acceptance plan top3. Giả thuyết lexical/hybrid giúp từ khóa: cả hai vẫn miss
  top3. Query agent tự thêm “acceptance” đã lấy đúng PDF35 ở cả ba cấu hình;
  context đủ, grounded2. Không đưa gold vào tool và không nhận RRF sửa lỗi này.
- **q19, lỗi retrieval**: dense gốc lấy stakeholder register/definition thay vì
  financial return. Lexical/hybrid đổi Evidence Hit@3 câu này0→1. Dense agent tự
  viết query thêm financial return/costs/benefits cũng lấy đúng PDF62; cả ba
  answer đạt2. Retrieval gốc tốt hơn chưa làm answer tốt hơn trong lượt này.

Ba ví dụ kiểm tra nhanh: q01 trả lời bốn giai đoạn với nguồn PDF35 tại
[dev baseline/q01](../../artifacts/evaluation/dev-baseline-v1/q01.json);
d07-t06 từ chối CPI không có trong corpus tại
[test/d07-t06](../../artifacts/evaluation/test-baseline-final-v1/d07-t06.json);
q16 là lỗi top3 query gốc tại trace bước04 ở trên, đối chiếu lần khôi phục trong
[dev baseline/q16](../../artifacts/evaluation/dev-baseline-v1/q16.json).

## Tái lập, nguồn và giới hạn

[Cách chạy](../../docs/EVALUATION.md) · [Kết nối Day06](../../docs/SETUP_DAY07.md) ·
[Đối chiếu rubric/checklist](../../docs/SUBMISSION_AUDIT.md).
Tôi chạy hồi quy bằng `OPENAI_AGENT_LIVE_TEST=0 DENSE_LIVE_TEST=1 .venv/bin/python
-m unittest discover -s tests -q`: **143 đạt,1 bỏ qua** trong144 test, có adapter
Day06 thật; không gọi lại OpenAI trả phí. `pip check` đạt; benchmark check xác
nhận26 evidence và snapshot/hash giữ nguyên. [Log](../../artifacts/evaluation/validation.txt).

Chi phí USD ở **mọi cấu hình là null**, chưa đối soát billing/ước tính giá;
không coi là0. Usage provider có cached/reasoning chi tiết trong metrics (đã nằm
trong input/output, không cộng lần nữa). Thời gian tổng cộng elapsed từng query,
không gồm setup/I/O/chấm nhãn; ba lượt dev chồng thời gian thực thi, latency chỉ
mang tính mô tả một lượt. Bộ test nhỏ, cùng một sách và chưa chấm độc lập; điểm
cao không bảo đảm tổng quát. Lỗi giữ trong mẫu số chất lượng theo quy tắc bộ chấm.

Lỗi đường dẫn tương đối của scorer đã được sửa, có
[hash trước/sau](../../artifacts/evaluation/scorer-path-correction.json); ba metrics
dev tính lại giống hệt, runtime/khóa/trace/nhãn không đổi và không lặp API.
Repo được kiểm tra công khai bằng GitHub API không xác thực; không đổi quyền.
**Phần nâng cao: Không thực hiện.**
