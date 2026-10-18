"""Heat sources for the router: per-segment and per-stop UTCI / dose rate on the 33 steps.

HEAT_SOURCE = placeholder | stub | final
  placeholder  computed here from the T05 air temperature and the segment flags (no T08 needed)
  stub         gs://.../T08/stub/t08_*.parquet, else warsaw_heat.t08_segment_heat_stub / t08_stop_heat_stub
  final        gs://hackaton-honory-heat/T08/t08_*.parquet, else warsaw_heat.t08_segment_heat / t08_stop_heat

Overrides: HEAT_SEGMENT_PATH / HEAT_STOP_PATH (local or gs:// parquet with the T08 columns).
"""
from __future__ import annotations

import io
import logging
import os
import zlib

import numpy as np
import pandas as pd

log = logging.getLogger("router.heat")

N_STEPS = 33
STEP0_S = 6 * 3600  # 06:00 local
STEP_S = 1800
DEFAULT_WEIGHTS = ((26.0, 1.0), (32.0, 1.0), (38.0, 2.0))
INDOOR_UTCI = 24.0  # underground / indoor passages: outdoor UTCI does not apply, assume comfortable
RIDE_UTCI = 26.0    # air-conditioned vehicle (spec §7)

BQ_PROJECT = "hackaton-honory"
BQ_DATASET = "warsaw_heat"
GCS_T08 = "gs://hackaton-honory-heat/T08"


def parse_weights(weights) -> tuple[tuple[float, float], ...]:
    """Dose weights g(U) = sum_k w_k * max(0, U - th_k).

    Accepts None (spec default), a list of [threshold, weight] pairs, or a dict
    {"thresholds": [...], "weights": [...]}.
    """
    if weights is None:
        return DEFAULT_WEIGHTS
    if isinstance(weights, dict):
        th, w = weights.get("thresholds"), weights.get("weights")
        return tuple((float(a), float(b)) for a, b in zip(th, w))
    return tuple((float(a), float(b)) for a, b in weights)


def dose_rate(u: np.ndarray, weights=DEFAULT_WEIGHTS) -> np.ndarray:
    u = np.asarray(u, dtype=np.float64)
    g = np.zeros_like(u)
    for th, w in weights:
        g += w * np.maximum(0.0, u - th)
    return g


class HeatField:
    """Arrays [33, n] for segments (edge order of the bundle) and stops (stop order of the bundle)."""

    def __init__(self, seg_utci, seg_p90, seg_g, seg_shade, stop_utci, stop_g, source: str, notes=None):
        self.seg_utci = seg_utci
        self.seg_p90 = seg_p90
        self.seg_g = seg_g
        self.seg_shade = seg_shade
        self.stop_utci = stop_utci
        self.stop_g = stop_g
        self.source = source
        self.notes = list(notes or [])

    def reweighted(self, weights) -> "HeatField":
        """Recompute g from UTCI with custom weights (T08's g, if any, is replaced)."""
        return HeatField(self.seg_utci, self.seg_p90, dose_rate(self.seg_utci, weights), self.seg_shade,
                         self.stop_utci, dose_rate(self.stop_utci, weights), self.source + "+weights",
                         self.notes + ["custom dose weights: g recomputed from mean UTCI per segment/stop"])


# ---------------------------------------------------------------- placeholder

def _hash_unit(ids) -> np.ndarray:
    return np.array([zlib.crc32(str(i).encode()) / 0xFFFFFFFF for i in ids])


def placeholder(edges: pd.DataFrame, stops: pd.DataFrame, forcing: pd.DataFrame | None) -> HeatField:
    """Clearly fake field with the right shape: UTCI = Ta + radiant load x exposure.

    Exposure depends on segment flags (park paths shadier, covered fully shaded) plus a
    deterministic per-segment jitter, so the router has something to trade off. Not physics.
    """
    if forcing is not None and len(forcing) == N_STEPS:
        ta = forcing.sort_values("step").ta_c.to_numpy(float)
        sun = np.clip(forcing.sort_values("step").ghi_wm2.fillna(0).to_numpy(float) / 850.0, 0, 1.2)
    else:
        h = 6 + np.arange(N_STEPS) / 2
        ta = 22 + 14 * np.clip(np.sin((h - 6) / 16 * np.pi), 0, 1)
        sun = np.clip(np.sin((h - 5) / 16.5 * np.pi), 0, 1)
    jitter = _hash_unit(edges.segment_id.values)
    expo = np.full(len(edges), 0.85) + 0.3 * (jitter - 0.5)
    kind = edges.kind.astype(str).values
    mc = edges.mask_category.astype(str).values
    park = (kind == "park_path") | (mc == "park_path")
    expo[park] = 0.35 + 0.4 * (jitter[park] - 0.5)
    expo[edges.covered.values.astype(bool)] = 0.0
    expo = np.clip(expo, 0, 1)
    load = 8.0 * sun  # K of extra UTCI in full sun
    utci = ta[:, None] + load[:, None] * expo[None, :] - 1.0
    indoor = edges.indoor.values.astype(bool)
    utci[:, indoor] = INDOOR_UTCI
    shade = np.broadcast_to(1.0 - expo, utci.shape).copy()
    shade[:, indoor] = 1.0
    shade[sun <= 0.02, :] = 1.0
    p90 = utci + 1.5 * (expo > 0.2)
    sexpo = np.where(stops.shelter.astype(object).fillna(False).astype(bool).values, 0.4, 0.8)
    sutci = ta[:, None] + load[:, None] * sexpo[None, :] - 1.0
    return HeatField(utci, p90, dose_rate(utci), shade, sutci, dose_rate(sutci), "placeholder",
                     ["HEAT_SOURCE=placeholder: UTCI = air temperature + fake radiant load by segment flags; "
                      "NOT the SOLWEIG field, numbers are for testing only"])


# ---------------------------------------------------------------- T08 tables

def _read_parquet(path: str) -> pd.DataFrame:
    if path.startswith("gs://"):
        from google.cloud import storage
        b, _, k = path[5:].partition("/")
        data = storage.Client(project=BQ_PROJECT).bucket(b).blob(k).download_as_bytes()
        return pd.read_parquet(io.BytesIO(data))
    return pd.read_parquet(path)


def _read_bq(table: str, cols: str) -> pd.DataFrame:
    from google.cloud import bigquery
    q = f"select {cols} from `{BQ_PROJECT}.{BQ_DATASET}.{table}`"
    return bigquery.Client(project=BQ_PROJECT).query(q).to_dataframe()


def _load_table(name: str, cols: str, env_path: str | None) -> pd.DataFrame:
    """T08 writes final tables to T08/t08_<x>.parquet and stand-ins to T08/stub/t08_<x>.parquet."""
    tried = []
    gcs = (f"{GCS_T08}/stub/{name[:-5]}.parquet" if name.endswith("_stub") else f"{GCS_T08}/{name}.parquet")
    for loader, arg in ([(_read_parquet, env_path)] if env_path else []) + [
        (_read_parquet, gcs),
        (_read_bq, name),
    ]:
        try:
            df = loader(arg) if loader is _read_parquet else loader(arg, cols)
            log.info("heat table %s from %s (%d rows)", name, arg, len(df))
            return df
        except Exception as exc:  # try the next source
            tried.append(f"{arg}: {type(exc).__name__}: {str(exc)[:120]}")
    raise RuntimeError(f"heat table {name} not found; tried " + " | ".join(tried))


def from_frames(edges: pd.DataFrame, stops: pd.DataFrame, seg: pd.DataFrame, stp: pd.DataFrame,
                source: str) -> HeatField:
    """Turn T08-shaped frames into arrays. Missing segments: indoor -> comfortable, others -> step median."""
    notes = []
    eidx = pd.Series(np.arange(len(edges)), index=edges.segment_id.values)
    seg = seg[seg.segment_id.isin(eidx.index)]
    ei = eidx[seg.segment_id.values].values
    st = seg.step.to_numpy(int)
    shape = (N_STEPS, len(edges))

    def fill(col, default=np.nan):
        a = np.full(shape, default, dtype=np.float64)
        if col in seg:
            a[st, ei] = seg[col].to_numpy(float)
        return a

    utci = fill("utci_mean")
    p90 = fill("utci_p90")
    g = fill("g")
    shade = fill("shade_share")
    indoor = edges.indoor.values.astype(bool)
    miss = np.isnan(utci)
    n_miss_out = int((miss[:, ~indoor].any(axis=0)).sum())
    if n_miss_out:
        notes.append(f"{n_miss_out} outdoor segments without heat values: step median used")
    med_u = np.nanmedian(np.where(indoor[None, :], np.nan, utci), axis=1)
    med_g = np.nanmedian(np.where(indoor[None, :], np.nan, g), axis=1)
    for a, m in ((utci, med_u), (p90, med_u), (g, med_g)):
        bad = np.isnan(a)
        a[bad] = np.broadcast_to(m[:, None], shape)[bad]
    utci[:, indoor & miss.all(axis=0)] = INDOOR_UTCI
    p90[:, indoor & miss.all(axis=0)] = INDOOR_UTCI
    g[:, indoor & miss.all(axis=0)] = 0.0
    shade[np.isnan(shade)] = 0.0
    shade[:, indoor] = 1.0
    if np.isnan(g).any():  # g column absent
        g = dose_rate(utci)

    sidx = pd.Series(np.arange(len(stops)), index=stops.stop_id.astype(str).values)
    stp = stp.copy()
    stp["stop_id"] = stp.stop_id.astype(str)
    stp = stp[stp.stop_id.isin(sidx.index)]
    si = sidx[stp.stop_id.values].values
    sst = stp.step.to_numpy(int)
    su = np.full((N_STEPS, len(stops)), np.nan)
    sg = np.full((N_STEPS, len(stops)), np.nan)
    su[sst, si] = stp.utci.to_numpy(float)
    if "g" in stp:
        sg[sst, si] = stp.g.to_numpy(float)
    n_ms = int(np.isnan(su).all(axis=0).sum())
    if n_ms:
        notes.append(f"{n_ms} stops without heat values: step median of stops used")
    mu = np.nanmedian(su, axis=1)
    bad = np.isnan(su)
    su[bad] = np.broadcast_to(mu[:, None], su.shape)[bad]
    bad = np.isnan(sg)
    sg[bad] = dose_rate(su)[bad]
    return HeatField(utci, p90, g, shade, su, sg, source, notes)


def load(source: str, edges: pd.DataFrame, stops: pd.DataFrame, forcing: pd.DataFrame | None) -> HeatField:
    source = (source or "placeholder").lower()
    if source == "placeholder":
        return placeholder(edges, stops, forcing)
    suffix = "_stub" if source == "stub" else ""
    seg = _load_table(f"t08_segment_heat{suffix}", "segment_id, step, utci_mean, utci_p90, g, shade_share",
                      os.environ.get("HEAT_SEGMENT_PATH"))
    stp = _load_table(f"t08_stop_heat{suffix}", "stop_id, step, utci, g", os.environ.get("HEAT_STOP_PATH"))
    return from_frames(edges, stops, seg, stp, source)
