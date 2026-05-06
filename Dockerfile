# Stage 1 — build React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
ARG VITE_WS_URL=""
ENV VITE_WS_URL=$VITE_WS_URL
RUN npm run build

# Stage 2 — Python backend + bundled frontend
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgomp1 curl && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
RUN mkdir -p models

# Copy built React app into /app/static
COPY --from=frontend-builder /app/dist /app/static

EXPOSE 8003

CMD ["/bin/sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8003} --workers 1"]
