FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/main.py api/reset.py ./
COPY --from=web /web/dist ./web
ENV ADMIN_DATA=/data BIND=127.0.0.1 PORT=8091
CMD ["sh", "-c", "exec uvicorn main:app --host $BIND --port $PORT --no-access-log"]
