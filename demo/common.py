"""Shared helpers for the T11 demo scripts: trips, GCS cache, dose weights, UTCI categories."""
import json
import os
from pathlib import Path

BUCKET = "hackaton-honory-heat"
PREFIX = "T11"
HERE = Path(__file__).resolve().parent
TRIPS_PATH = str(HERE / "demo_trips.json")
OUT_DIR = HERE.parent / "reports" / "T11"

STEP0_LOCAL_MIN = 6 * 60  # step 0 = 06:00 CEST
STEP_MIN = 30
N_STEPS = 33

# Dose weights g(U) = sum_k w_k * max(0, U - t_k). Spec §7 working weights first.
WEIGHTS = {
    "spec": [(26, 1.0), (32, 1.0), (38, 2.0)],
    "linear26": [(26, 1.0)],
    "strong_only": [(32, 1.0)],
    "steep": [(26, 1.0), (32, 2.0), (38, 4.0)],
    "very_strong_only": [(38, 1.0)],
}
CATEGORIES = ["<26", "26-32", "32-38", "38-46", ">46"]


def g_of_u(u, weights):
    import numpy as np
    u = np.asarray(u, dtype=float)
    out = np.zeros_like(u)
    for t, w in weights:
        out += w * np.maximum(0.0, u - t)
    return out


def load_trips(path=TRIPS_PATH):
    with open(path) as f:
        return json.load(f)["trips"]


def step_of(hhmm):
    h, m = map(int, hhmm.split(":")[:2])
    return (h * 60 + m - STEP0_LOCAL_MIN) / STEP_MIN


def _client():
    from google.cloud import storage
    return storage.Client(project="hackaton-honory")


def gcs_read_json(name):
    b = _client().bucket(BUCKET).blob(f"{PREFIX}/{name}")
    if not b.exists():
        return None
    return json.loads(b.download_as_bytes())


def gcs_write_json(name, obj):
    _client().bucket(BUCKET).blob(f"{PREFIX}/{name}").upload_from_string(
        json.dumps(obj, ensure_ascii=False), content_type="application/json")


def gcs_upload_file(local, name):
    _client().bucket(BUCKET).blob(f"{PREFIX}/{name}").upload_from_filename(str(local))
    return f"gs://{BUCKET}/{PREFIX}/{name}"
