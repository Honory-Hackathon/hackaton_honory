# router (T09): heat-aware trip planner

Walk + air-conditioned trams and buses on the heat day 2026-06-28, lowest heat dose, best departure time.
Method and results: root [`README.md`](../README.md).

```python
from router import plan, price_path
plan((21.01864, 52.21971), (21.01661, 52.24007), "13:00", "15:00")       # (lon, lat), local HH:MM
price_path([[lon, lat], ...], "13:00")                                      # price a foreign walking line
```

HTTP: `uvicorn router.api:app --port 8080` → `POST /api/plan {origin, destination, earliest, latest[, weights]}`,
`POST /api/price {coords, depart[, weights]}`, `GET /api/health`.

| Env var | Default | Meaning |
|---|---|---|
| `HEAT_SOURCE` | `placeholder` | `placeholder` (fake field from T05 air temperature + segment flags), `stub` (`warsaw_heat.t08_*_stub`), `final` (`warsaw_heat.t08_*`) |
| `ROUTER_BUNDLE` | `gs://hackaton-honory-heat/T09/bundle` | network + stops + stop events bundle (local dir or gs://) |
| `ROUTER_CACHE` | `/tmp/router_bundle` | where a gs:// bundle is downloaded |
| `HEAT_SEGMENT_PATH`, `HEAT_STOP_PATH` | | optional parquet (local/gs://) with the T08 columns, tried before the defaults |
| `HEAT_STRICT` | `0` | `1` = fail if the requested source is missing; otherwise `final` falls back to `stub`, then `placeholder`, with a note in every answer (heat is loaded once at startup: restart / redeploy after T08 lands) |

T08 tables are read from `gs://hackaton-honory-heat/T08/t08_*.parquet` (final) or `T08/stub/t08_*.parquet` (stub) first, then BigQuery `warsaw_heat.t08_*[_stub]`.

Rebuild the bundle (light, seconds): `python -m router.build_bundle --out DIR --upload gs://hackaton-honory-heat/T09/bundle/`
(needs geopandas; the router itself needs only `requirements.txt`).
