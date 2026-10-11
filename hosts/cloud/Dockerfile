# The cloud gateway: the same three public model ids as every node's own gateway, answered by clouds.
# Build context is the repo root (compose.yaml here sets it), so the code is the one of hosts/mac and services/image.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2 httpx==0.28.1 pillow==11.2.1 python-multipart==0.0.20
COPY hosts/mac/gateway.py ./gateway.py
COPY services/image/app.py ./image/app.py
COPY services/image/workflows ./image/workflows
