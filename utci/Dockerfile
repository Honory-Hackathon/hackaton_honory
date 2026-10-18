FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY field.py ./
RUN python -c "import rasterio, numpy, pandas; from google.cloud import storage; print(rasterio.__version__, rasterio.__gdal_version__)"
ENTRYPOINT ["python", "field.py"]
