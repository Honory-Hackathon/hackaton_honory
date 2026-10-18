"""T05 Cloud Run task: 2026-06-28 weather forcing for Śródmieście, 33 × 30-min steps → GCS Parquet + BigQuery."""
import io
import json
import zipfile

import numpy as np
import pandas as pd
import pvlib
import requests
from google.cloud import bigquery, storage

BKT = storage.Client().bucket("hackaton-honory-heat")
BQ_TABLE = "hackaton-honory.warsaw_heat.t05_forcing_20260628"
DAY = "2026-06-28"
IMGW = "https://danepubliczne.imgw.pl/data/dane_pomiarowo_obserwacyjne/dane_meteorologiczne"
OKECIE, FILTRY, OBS2 = "352200375", "252200230", "252210170"
# Same sun convention as T02 (shade/sun.py): Śródmieście point, 100 m, refracted elevation at 25 °C.
LAT, LON, ALT = 52.23, 21.01, 100.0

SYNOP_COLS = ("NSP POST ROK MC DZ GG HPOD WHPOD HPON WHPON HPOW WHPOW HTXT POM1 POM2 WID WWID WIDO WWIDO WIDA WWIDA "
              "NOG WNOG KRWR WKRWR FWR WFWR PORW WPORW TEMP WTEMP TTZW WTTZW WENT TWLW CPW WCPW WLGW WWLGW TPTR WTPTR "
              "PPPS WPPPS PPPM WPPPM TECH APP WAPP WO6G WWO6G ROPT WROPT POGB POGU CLCM WCLCM CHCL WCHCL CHLT CHCM WCHCM "
              "CHMT CHCH WCHCH CHHT SGRN WSGRN DEFI WDEFI USLN WUSLN ROSW WROSW PORK WPORK GODP MINP TG05 WTG05 TG10 "
              "WTG10 TG20 WTG20 TG50 WTG50 TG100 WTG100 TMIN WTMIN TMAX WTMAX").split()
KLIMAT_T_COLS = "NSP POST ROK MC DZ GG TEMP WTEMP TTZW WTTZW TWLW WENT WLGW WWLGW DKDK WDKDK FWR WFWR ZOGK WZOGK WID WWID".split()
KLIMAT_D_COLS = "NSP POST ROK MC DZ TMAX WTMAX TMIN WTMIN STD WSTD TMNG WTMNG SUMD WSUMD RODZ WRODZ PKSN WPKSN".split()


def imgw_csv(url, member, cols):
    z = zipfile.ZipFile(io.BytesIO(requests.get(url, timeout=120).content))
    df = pd.read_csv(z.open(member), header=None, encoding="cp1250", dtype=str, keep_default_na=False)
    df = df.iloc[:, : len(cols)]
    df.columns = cols[: df.shape[1]]
    return df


def num(df, col):
    """Numeric column with IMGW status 8 (missing) set to NaN."""
    v = pd.to_numeric(df[col].str.strip(), errors="coerce")
    st = "W" + col
    if st in df:
        v[df[st].str.strip() == "8"] = np.nan
    return v


def es_hpa(t):
    return 6.112 * np.exp(17.62 * t / (243.12 + t))


steps_local = pd.date_range(f"{DAY} 06:00", f"{DAY} 22:00", freq="30min", tz="Europe/Warsaw")
steps = steps_local.tz_convert("UTC")
assert len(steps) == 33
hour_grid = pd.date_range(f"{DAY} 03:00", f"{DAY} 21:00", freq="1h", tz="UTC")

# --- IMGW hourly synop, Warszawa-Okęcie
syn = imgw_csv(f"{IMGW}/terminowe/synop/2026/2026_06_s.zip", "s_t_06_2026.csv", SYNOP_COLS)
syn = syn[(syn.NSP == OKECIE) & (syn.DZ == "28")].copy()
syn.index = pd.to_datetime(DAY + " " + syn.GG + ":00").dt.tz_localize("UTC")
ok = pd.DataFrame({c: num(syn, c) for c in ["TEMP", "CPW", "WLGW", "FWR", "KRWR", "NOG", "PPPS", "USLN"]}).reindex(hour_grid)
assert ok[["TEMP", "CPW", "FWR", "NOG"]].notna().all().all(), ok
u = -ok.FWR * np.sin(np.radians(ok.KRWR))
v = -ok.FWR * np.cos(np.radians(ok.KRWR))

# --- IMGW climate stations: 06/12/18 UTC terms and daily Tmax/Tmin
kt = imgw_csv(f"{IMGW}/terminowe/klimat/2026/2026_06_k.zip", "k_t_06_2026.csv", KLIMAT_T_COLS)
kd = imgw_csv(f"{IMGW}/dobowe/klimat/2026/2026_06_k.zip", "k_d_06_2026.csv", KLIMAT_D_COLS)
terms, daily = {}, {}
for st in (OBS2, FILTRY):
    t = kt[(kt.NSP == st) & (kt.DZ == "28")]
    terms[st] = pd.Series(num(t, "TEMP").to_numpy(), index=pd.to_datetime(DAY + " " + t.GG + ":00").dt.tz_localize("UTC"))
    d = kd[(kd.NSP == st) & (kd.DZ == "28")]
    daily[st] = {"tmax": float(num(d, "TMAX").iloc[0]), "tmin": float(num(d, "TMIN").iloc[0]), "name": d.POST.iloc[0]}
    assert terms[st].notna().all() and len(terms[st]) == 3, terms[st]


def at_steps(series):
    """Hourly values at full hours → 30-min steps: exact at :00, mean of neighbours at :30."""
    s = series.reindex(series.index.union(steps)).interpolate(method="time")
    return s.reindex(steps).to_numpy()


def offset_at_steps(st):
    """Station − Okęcie offset at the 06/12/18 UTC terms, linear in time between, held constant outside."""
    d = terms[st] - ok.TEMP.reindex(terms[st].index)
    x = (steps - steps[0]).total_seconds()
    xp = (d.index - steps[0]).total_seconds()
    return np.interp(x, xp, d.to_numpy()), d


ta_ok = at_steps(ok.TEMP)
off_obs2, d_obs2 = offset_at_steps(OBS2)
off_filtry, d_filtry = offset_at_steps(FILTRY)
ta = ta_ok + off_obs2
e = at_steps(ok.CPW)
rh = 100 * e / es_hpa(ta)
wind = at_steps(ok.FWR)
wdir = (np.degrees(np.arctan2(-at_steps(u), -at_steps(v))) + 360) % 360
cloud_okta = at_steps(ok.NOG)

# --- ERA5 via Open-Meteo archive: radiation + cloud cover
om = requests.get("https://archive-api.open-meteo.com/v1/archive", timeout=60, params={
    "latitude": LAT, "longitude": LON, "start_date": DAY, "end_date": DAY, "models": "era5", "timezone": "GMT",
    "wind_speed_unit": "ms", "hourly": ",".join([
        "shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
        "shortwave_radiation_instant", "direct_normal_irradiance_instant", "diffuse_radiation_instant",
        "cloud_cover", "temperature_2m", "wind_speed_10m"])}).json()
era = pd.DataFrame(om["hourly"])
era.index = pd.to_datetime(era.pop("time")).dt.tz_localize("UTC")
sp = pvlib.solarposition.get_solarposition(steps_local, LAT, LON, altitude=ALT, temperature=25.0)
elev = sp["apparent_elevation"].to_numpy()
azim = sp["azimuth"].to_numpy()

# ERA5 instantaneous GHI/DHI at full hours → clear-sky index (pvlib Ineichen) interpolated to the 30-min steps;
# DNI then follows from closure GHI = DHI + DNI·sin(elev) (ERA5 instantaneous DNI where the sun is below 5°).
loc = pvlib.location.Location(LAT, LON, altitude=ALT)
cs_hours = loc.get_clearsky(era.index)
cs_steps = loc.get_clearsky(steps)
rad = {}
for var, cs_col in [("shortwave_radiation", "ghi"), ("diffuse_radiation", "dhi"), ("direct_normal_irradiance", "dni")]:
    k = (era[var + "_instant"] / cs_hours[cs_col]).where(cs_hours[cs_col] > 5, 0.0)
    rad[var] = at_steps(k) * cs_steps[cs_col].to_numpy()
sin_e = np.sin(np.radians(np.clip(elev, 0, 90)))
dni = np.where(elev >= 5, (rad["shortwave_radiation"] - rad["diffuse_radiation"]) / np.maximum(sin_e, 1e-6),
               rad["direct_normal_irradiance"])
rad["direct_normal_irradiance"] = np.where(elev > 0, np.maximum(dni, 0), 0.0)
for var in rad:
    rad[var] = np.round(np.where(elev > -0.833, rad[var], 0.0), 1)
cloud_era = at_steps(era.cloud_cover)

# Optional incoming longwave: Brutsaert clear-sky emissivity, Bolz cloud factor with Okęcie oktas.
tk = ta + 273.15
ldown = 1.24 * (e / tk) ** (1 / 7) * (1 + 0.22 * (cloud_okta / 8) ** 2) * 5.670374e-8 * tk**4

SRC_TA = "IMGW synop Warszawa-Okęcie 12375 hourly TEMP + offset (Warszawa-Obserwatorium II − Okęcie) from IMGW klimat 06/12/18 UTC terms"
df = pd.DataFrame({
    "step": np.arange(33),
    "ts_utc": steps,
    "local_label": steps_local.strftime("%H:%M CEST"),
    "ta_c": np.round(ta, 2),
    "ta_src": [SRC_TA + (" (:00 obs)" if t.minute == 0 else " (:30 = mean of adjacent hours)") for t in steps],
    "ta_offset_k": np.round(off_obs2, 2),
    "ta_okecie_c": np.round(ta_ok, 2),
    "ta_filtry_adj_c": np.round(ta_ok + off_filtry, 2),
    "rh_pct": np.round(rh, 1),
    "vp_hpa": np.round(e, 2),
    "rh_src": "IMGW synop Okęcie vapour pressure CPW (assumed spatially uniform) with ta_c, Magnus formula",
    "wind_10m_ms": np.round(wind, 2),
    "wind_10m_utci_ms": np.round(np.maximum(wind, 0.5), 2),
    "wind_dir_deg": np.round(wdir, 0),
    "wind_src": "IMGW synop Okęcie FWR/KRWR at 10 m (1 m/s resolution); :30 = mean of adjacent hours",
    "ghi_wm2": rad["shortwave_radiation"],
    "dni_wm2": rad["direct_normal_irradiance"],
    "dhi_wm2": rad["diffuse_radiation"],
    "rad_src": [f"ERA5 via Open-Meteo archive (cell {om['latitude']:.2f}N {om['longitude']:.2f}E) instantaneous GHI/DHI, "
                + ("as is" if t.minute == 0 else "clear-sky index (pvlib Ineichen) interpolated from adjacent hours")
                + "; DNI = (GHI-DHI)/sin(elev)" for t in steps],
    "sun_elev_deg": np.round(elev, 3),
    "sun_azim_deg": np.round(azim, 3),
    "sun_src": f"pvlib NREL SPA, {LAT}N {LON}E {ALT:.0f} m, refracted elevation (T02 convention)",
    "cloud_okta": np.round(cloud_okta, 1),
    "cloud_era5_pct": np.round(cloud_era, 0),
    "cloud_src": "IMGW synop Okęcie NOG (oktas); ERA5 total cloud cover",
    "ldown_wm2": np.round(ldown, 1),
    "ldown_src": "Brutsaert (1975) clear-sky emissivity from vp_hpa, ta_c × Bolz (1+0.22 n²), n = cloud_okta/8",
})
assert df.drop(columns=[]).notna().all().all() and len(df) == 33

checks = {
    "obs2_minus_okecie_at_terms_k": {str(k): round(float(x), 2) for k, x in d_obs2.items()},
    "filtry_minus_okecie_at_terms_k": {str(k): round(float(x), 2) for k, x in d_filtry.items()},
    "daily": {**{daily[s]["name"]: {k: v for k, v in daily[s].items() if k != "name"} for s in daily},
              "WARSZAWA-OKĘCIE synop TMAX 06-18Z": float(num(syn, "TMAX").max())},
    "ta_c_max": float(df.ta_c.max()), "ta_okecie_max": float(df.ta_okecie_c.max()),
    "ta_filtry_adj_max": float(df.ta_filtry_adj_c.max()),
    "ghi_closure_max_abs_wm2": float(np.nanmax(np.abs(df.ghi_wm2 - df.dhi_wm2 - df.dni_wm2 * sin_e))),
    "okecie_sunshine_fraction_04_19Z": ok.USLN.loc["2026-06-28 04:00Z":"2026-06-28 19:00Z"].round(2).tolist(),
    "era5_temperature_2m_at_steps": np.round(at_steps(era.temperature_2m), 1).tolist(),
    "era5_wind_10m_at_steps": np.round(at_steps(era.wind_speed_10m), 2).tolist(),
    "open_meteo_cell": [om["latitude"], om["longitude"]],
    "era5_instant_hourly_utc": era[["shortwave_radiation_instant", "diffuse_radiation_instant",
                                    "direct_normal_irradiance_instant"]].round(1).astype(float).to_dict("list"),
}

buf = io.BytesIO()
df.to_parquet(buf, index=False)
BKT.blob("T05/forcing/forcing_20260628.parquet").upload_from_string(buf.getvalue())
BKT.blob("T05/forcing/forcing_20260628.csv").upload_from_string(df.to_csv(index=False))
BKT.blob("T05/forcing/checks.json").upload_from_string(json.dumps(checks, indent=1, ensure_ascii=False))
bq = bigquery.Client(project="hackaton-honory")
bq.load_table_from_dataframe(df, BQ_TABLE, job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")).result()
print(df.drop(columns=[c for c in df if c.endswith("_src")]).to_string())
print(json.dumps(checks, indent=1, ensure_ascii=False))
