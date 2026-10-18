"""HTTP wrapper:  uvicorn router.api:app --port 8080

POST /api/plan   {"origin": [lon, lat], "destination": [lon, lat], "earliest": "HH:MM", "latest": "HH:MM",
                  "weights": optional [[threshold, weight], ...]}  -> plan()
POST /api/price  {"coords": [[lon, lat], ...], "depart": "HH:MM", "weights": optional}  -> price_path()
GET  /api/health
"""
from __future__ import annotations

import json
import math
import os
import threading

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import get_router

app = FastAPI(title="Heat-aware router (T09)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _clean(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):
        return _clean(o.item())
    return o


@app.on_event("startup")
def _warm():
    if os.environ.get("ROUTER_LAZY", "0") != "1":
        threading.Thread(target=get_router, daemon=True).start()


@app.get("/api/health")
def health():
    r = get_router()
    return {"ok": True, "heat_source": r.base_heat.source, "load_s": round(r.load_s, 1)}


@app.post("/api/plan")
def api_plan(q: dict = Body(...)):
    try:
        res = get_router().plan(q["origin"], q["destination"], q.get("earliest", "06:00"), q.get("latest", "22:00"),
                                weights=q.get("weights"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(_clean(res))


@app.post("/api/price")
def api_price(q: dict = Body(...)):
    try:
        res = get_router().price_path(q["coords"], q["depart"], weights=q.get("weights"))
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse(_clean(res))
