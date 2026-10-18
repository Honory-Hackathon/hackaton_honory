FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 WORK=/tmp/t12
RUN apt-get update && apt-get install -y --no-install-recommends osmium-tool libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
RUN python -c "import rasterio, geopandas, osmium, skimage, google.cloud.bigquery; print(rasterio.__version__)"
ENTRYPOINT ["python", "run.py"]
