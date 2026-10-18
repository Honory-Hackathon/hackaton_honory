FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "laspy[lazrs]==2.6.1" numpy==2.2.6 scipy==1.15.3 rasterio==1.4.3 pyproj==3.7.1 matplotlib==3.10.3 google-cloud-storage==3.1.0
# Code is fetched from GCS at start so iterations need no rebuild.
CMD ["bash", "-c", "python -c \"from google.cloud import storage; storage.Client().bucket('hackaton-honory-heat').blob('T01/code/' + __import__('os').environ['SCRIPT']).download_to_filename('/tmp/run.py')\" && python /tmp/run.py"]
