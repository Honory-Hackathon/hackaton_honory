# heat-app (T10). Build context = repo root, so router/ (T09) is vendored when it exists.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY app/server/requirements.txt /tmp/app-req.txt
RUN pip install --no-cache-dir -r /tmp/app-req.txt
COPY . /tmp/src
RUN mkdir -p /srv/app && cp -r /tmp/src/app/server /srv/app/server \
 && if [ -d /tmp/src/router ]; then cp -r /tmp/src/router /srv/router; fi \
 && if [ -f /srv/router/requirements.txt ]; then pip install --no-cache-dir -r /srv/router/requirements.txt; fi \
 && rm -rf /tmp/src
WORKDIR /srv/app/server
ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75
