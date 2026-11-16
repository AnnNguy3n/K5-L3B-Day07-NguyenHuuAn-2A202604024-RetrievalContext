# Thiết lập môi trường Day07

Tôi dùng Python 3.14.10, các phiên bản thư viện trong `requirements.txt` và
embedding/vector store đã tạo ở Day06. Các lệnh dưới đây chạy từ gốc repo Day07.

## Môi trường

```bash
# Trên máy khác, thay bằng interpreter Python >=3.11 đã cài.
/opt/miniconda3/envs/main/bin/python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Tôi dùng `.venv/bin/python` vì `python3` mặc định trên máy là Python 3.9.6.
Phiên bản đã kiểm thử được ghi trong `.python-version`.

## Kết nối Day06

```bash
test -f .env || (umask 077; cp .env.example .env)
chmod 600 .env
```

Tôi cấu hình các giá trị sau trong `.env`:

- `DAY06_PATH`: đường dẫn tới repo Day06, mặc định là thư mục cùng cấp với Day07.
- `EMBEDDING_ENDPOINT`: `http://127.0.0.1:11434`.
- Model, digest và số chiều: giữ các giá trị Day06 đã ghi trong `.env.example`.
- Prefix query/document: giữ nguyên khoảng trắng cuối trong chuỗi có dấu ngoặc kép.
- `DATABASE_URL`: thông tin kết nối riêng tới database `day6_lab`, cổng local `55433`.
- Schema/bảng: `public.day6_chunks`; C dùng chung dữ liệu B.

Biến môi trường shell được ưu tiên hơn `.env`, sau đó tới `.env.example`.
`.env` và `.venv/` được Git bỏ qua; mẫu cấu hình không chứa mật khẩu.
Trước khi kiểm tra live, tôi cần bật dịch vụ embedding và PostgreSQL Day06 đã có.

## Kiểm tra

```bash
# Đọc file Day06, chưa xác minh dịch vụ đang chạy.
.venv/bin/python scripts/check_connections.py --offline

# Kiểm tra embedding và truy vấn database chỉ đọc.
.venv/bin/python scripts/check_connections.py

# Test code và kiểm tra dependencies.
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

Checker trả JSON và exit 0 khi các kiểm tra được yêu cầu đều đạt. Tôi đối chiếu
artifact với model/digest/prefix, thử hai vector thật và kiểm tra số hàng, chiều,
digest cùng toán tử cosine trong database. Checker không chạy ingestion hoặc
benchmark; lỗi kết nối được ghi riêng, không thay bằng vector giả.

Nếu thiếu `DATABASE_URL`, tôi bổ sung vào `.env`. Nếu bị từ chối kết nối hoặc
hết thời gian, tôi kiểm tra dịch vụ, endpoint và quyền truy cập localhost trước;
không rebuild Day06 để xử lý lỗi kết nối. Sai digest/chiều cần đối chiếu lại cấu hình.

[Kết quả công việc 01](DAY07_CONNECTION_STATUS.md) · [Minh chứng live](day07-connection-check.json)

## Model agent — công việc 05

Tôi bổ sung `openai==3.51.0`, đã kiểm tra trên Python 3.14.10. Key/model được
điền riêng trong `.env` hoặc shell; embedding/index Day06 giữ local. Lệnh chạy,
giới hạn và hai trace smoke OpenAI thật nằm tại [hướng dẫn agent](OPENAI_AGENT.md).
