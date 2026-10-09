FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY workflows ./workflows
ENV BIND=127.0.0.1 PORT=8102
CMD ["sh", "-c", "exec uvicorn app:app --host $BIND --port $PORT --no-access-log"]
