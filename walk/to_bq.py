"""Load T03 outputs into BigQuery `hackaton-honory.warsaw_heat.t03_*` (geometries as WGS84 GEOGRAPHY).

usage: python -m walk.to_bq OUT_DIR   (OUT_DIR as written by walk.run and walk.routes)
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

DATASET = "hackaton-honory:warsaw_heat"
STAGE = "gs://hackaton-honory-heat/T03/bq"
TABLES = {  # table: (file, clustering)
    "t03_walk_segments": ("segments.parquet", "segment_id"),
    "t03_walk_nodes": ("nodes.parquet", "node_id"),
    "t03_segment_pixels": ("segment_pixels.parquet", "segment_id"),
    "t03_stop_areas": ("stop_areas.parquet", "stop_id"),
    "t03_test_routes": ("routes.parquet", None),
}


def _meta(path):
    return pq.read_schema(path).metadata or {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    out = Path(ap.parse_args().out_dir)
    with tempfile.TemporaryDirectory() as tmp:
        for table, (name, cluster) in TABLES.items():
            src, dst = out / name, Path(tmp) / name
            if b"geo" in _meta(src):
                df = gpd.read_parquet(src)
                for col in df.columns[df.dtypes == "geometry"]:
                    df[col] = df[col].to_crs("EPSG:4326")
                df.to_parquet(dst, index=False)
            else:
                pd.read_parquet(src).to_parquet(dst, index=False)
            subprocess.run(["gsutil", "-q", "cp", str(dst), f"{STAGE}/{name}"], check=True)
            cmd = ["bq", "load", "--replace", "--source_format=PARQUET", "--parquet_enable_list_inference"]
            if cluster:
                cmd.append(f"--clustering_fields={cluster}")
            subprocess.run(cmd + [f"{DATASET}.{table}", f"{STAGE}/{name}"], check=True)


if __name__ == "__main__":
    main()
