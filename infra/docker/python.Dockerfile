FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BSB_PROJECT_ROOT=/app
WORKDIR /app
COPY services/common /app/services/common
COPY packages/contracts /app/packages/contracts
COPY infra/migrations /app/infra/migrations
RUN pip install --no-cache-dir ./services/common
USER 10001
CMD ["uvicorn", "bsb_service.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
