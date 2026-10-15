# Excel QA – Hỏi đáp trên bảng tính Excel

Trả lời tự động câu hỏi ngôn ngữ tự nhiên trên file Excel (nhiều sheet, nhiều bảng, tiêu đề phân cấp, ô gộp).

## Cài đặt

```bash
python -m pip install -r requirements.txt      # Python >= 3.9
```

Cấu hình API key **ngoài mã nguồn** (chọn 1 cách):

```bash
# Linux/macOS
export OPENROUTER_API_KEY=sk-or-v1-...
# Windows PowerShell
$env:OPENROUTER_API_KEY="sk-or-v1-..."
# hoặc: copy .env.example thành .env (cùng thư mục main.py hoặc thư mục đang chạy) rồi điền key
```

## Chạy

```bash
python main.py --xlsx_dir <thư_mục_xlsx> --queries <queries.json> --output submission.csv
```

Ví dụ với dữ liệu mẫu: `python main.py --xlsx_dir ../xlsx_files --queries ../queries.json --output submission.csv --log_dir logs`

Tuỳ chọn chính:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--model` | `qwen/qwen3.7-flash` | model trên OpenRouter (hoặc biến `OPENROUTER_MODEL`) |
| `--reasoning` | `off` | `off/low/medium/high/default` – chế độ suy luận của model |
| `--workers` | 8 | số câu xử lý song song |
| `--votes` | 1 | `1` = một lời giải temperature 0, chỉ thử lời giải thứ 2 khi lời giải đầu thất bại (nhanh, ít token). `3` = bỏ phiếu: 2 lời giải song song, khác nhau thì thêm lời giải thứ 3, lấy đa số |
| `--max_retries` | 2 | số lần gửi lỗi (hoặc kết quả sai kiểu/số lượng) lại cho LLM sửa code |
| `--no_direct_fallback` | – | tắt bước dự phòng: khi mọi lần chạy code đều lỗi thì hỏi thẳng LLM đáp án |
| `--max_cost` | 0.08 | USD tối đa mỗi câu (BTC giới hạn 0.10) |
| `--log_dir` | – | ghi log JSON từng câu: code LLM sinh, kết quả, token, thời gian |

Tự chấm: `python evaluate.py --pred submission.csv --gold ../sample_submission.csv --queries ../queries.json`

Xem bảng sau khi chuẩn hoá (đúng nội dung gửi cho LLM): `python table_parser.py file.xlsx`

## Nguyên lý

```
xlsx ──► [1] table_parser  ──► DataFrame phẳng + bản mô tả bảng dạng text
query ─────────────────────► [2] llm: LLM đọc bảng, viết code pandas gán vào `result`
                             [3] executor: chạy code trong môi trường hạn chế ──lỗi / sai kiểu──► gửi lại cho LLM sửa (≤2 lần)
                                 (2 lời giải song song, khác nhau thì thêm lời giải thứ 3 → đa số; hỏng hết → hỏi thẳng đáp án)
                             [4] formatter: định dạng đáp án theo câu "Answer with ..." ──► submission.csv
```

1. **table_parser.py** (không dùng LLM): điền giá trị ô gộp; phân loại từng dòng (header / dữ liệu / nhãn nhóm / ghi chú);
   ghép tiêu đề nhiều tầng thành tên cột `Cha | Con`; tách nhiều bảng trên một sheet; dòng chỉ có nhãn
   (`TOTAL`, `Women`, `MARITAL STATUS`) thành cột `section`; làm sạch chữ (`Con-\nstruction` → `Construction`,
   bỏ chú thích `(1)`, `3/`). DataFrame có **index = số dòng Excel, cột = chữ cái cột Excel** để LLM tham chiếu
   chính xác như trong Excel (`df.loc[31, 'E']`).
   Thích ứng nhiều dạng bảng: bảng toàn chữ (header = dòng in đậm / ô gộp, còn lại là dữ liệu); số lưu dạng chữ
   (`"1,234"`, `"12.5%"`, `"$30"`); hai bảng đặt cạnh nhau (tách theo cột trống); bảng không bắt đầu ở ô A1;
   ô gộp dọc trong vùng dữ liệu (điền giá trị cho mọi dòng); ô công thức không lưu sẵn kết quả (tự tính `=A1`,
   `SUM/AVERAGE/MIN/MAX`, `+ - * /`); bảng rất dài (chỉ gửi 60 dòng đầu + tóm tắt từng cột); sheet lỗi → bảng thô.
2. **llm.py**: prompt gồm bảng dạng text gọn + câu hỏi + kiểu `result` mong đợi (số / bool / danh sách N giá trị);
   LLM chỉ viết code, không tự định dạng. Prompt có quy tắc: chọn đúng bảng/khối, dòng con lồng nhau, dùng sẵn dòng
   Total thay vì tự cộng, công thức hiệu / phần trăm / % thay đổi, khớp nhãn không phân biệt hoa thường.
3. **executor.py**: chạy code với builtins hạn chế (không `open`, chỉ import thư viện tính toán), có timeout;
   chuẩn hoá kết quả (Series/numpy/dict → kiểu Python), kiểm tra rỗng/NaN.
4. **formatter.py** (không dùng LLM): đọc yêu cầu định dạng trong câu hỏi – số nguyên, N chữ số thập phân
   (`two decimal places`, `nearest tenth`, `2 dp`; làm tròn half-up, khử sai số dấu phẩy động), Yes/No, True/False,
   dấu `%`, dấu phẩy hàng nghìn, định dạng ngày (`MM/DD/YYYY`, `Month D, YYYY`...), nhiều giá trị cách nhau `", "`
   hoặc `"; "`; bỏ ký hiệu chú thích khỏi tên. Đồng thời kiểm tra kết quả có đúng kiểu / đủ số giá trị không.

Vì sao thiết kế như vậy: chấm Exact Match nên phép tính và định dạng phải tất định (code), LLM chỉ làm phần
"hiểu câu hỏi → chọn ô/cột". Prompt nhỏ (bảng ~0.5–4k token) → nhanh và rẻ (≪ 0.10 USD/câu).
Các câu chạy song song, mỗi file chỉ đọc một lần.

## Cấu trúc

```
main.py           điểm vào, chạy song song, ghi CSV, thống kê thời gian/token
table_parser.py   bước 1 – chuẩn hoá Excel
llm.py            bước 2 – prompt + gọi OpenRouter (retry khi 429/5xx)
executor.py       bước 3 – chạy code an toàn
formatter.py      bước 4 – định dạng đáp án
evaluate.py       tự chấm Exact Match
```

## Chạy nhanh (cho đồng đội)

1. `git clone` repo này, rồi copy `.env.example` thành `.env` và điền `OPENROUTER_API_KEY` của bạn.
2. Chạy `run_test.bat` (Windows). Dữ liệu mẫu nằm sẵn trong thư mục `data/`.
