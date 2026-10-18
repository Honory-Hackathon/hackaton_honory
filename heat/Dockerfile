FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aggregate.py ./
RUN python -c "import rasterio, shapely, google.cloud.bigquery, db_dtypes; print(rasterio.__version__)"
ENTRYPOINT ["python", "aggregate.py"]
