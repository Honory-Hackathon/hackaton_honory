FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
# import check (fails the build if the geospatial stack is broken)
RUN python -c "import solweig, solweig.io, rasterio, pythermalcomfort, pvlib; print(solweig.__version__, rasterio.__version__)"
ENTRYPOINT ["python", "-m", "run"]
