# Root Dockerfile — builds Python backend (Railway single-service mode)
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgomp1 curl && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

RUN mkdir -p models

EXPOSE 8003

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
