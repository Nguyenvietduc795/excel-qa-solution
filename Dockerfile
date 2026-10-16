# Excel QA - anh Docker chay tren Linux amd64 (khong can GPU)
FROM --platform=linux/amd64 python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    OPENROUTER_MODEL=qwen/qwen3.7-flash

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py llm.py executor.py formatter.py table_parser.py evaluate.py entrypoint.py README.md ./

# Khoa API KHONG nam trong anh: truyen luc chay bang -e OPENROUTER_API_KEY=...
# Tham so (neu co) duoc chuyen cho main.py, vd: --xlsx_dir /data/xlsx_files --queries /data/queries.json --output /output/submission.csv
# Khong tham so: tu tim queries.json trong /data, /input, ... (xem entrypoint.py)
ENTRYPOINT ["python", "/app/entrypoint.py"]
