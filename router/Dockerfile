# Standalone router service (T09 reference deployment; T10 vendors the package into heat-app instead).
# Build from the repo root:  gcloud builds submit --config router/cloudbuild.yaml .
FROM python:3.12-slim
WORKDIR /app
COPY router/requirements.txt /app/router/requirements.txt
RUN pip install --no-cache-dir -r /app/router/requirements.txt
COPY router /app/router
ENV HEAT_SOURCE=placeholder PYTHONUNBUFFERED=1
CMD exec uvicorn router.api:app --host 0.0.0.0 --port ${PORT:-8080}
