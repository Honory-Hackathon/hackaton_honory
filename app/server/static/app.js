// heat-app front end (T10). No build step, no accounts.
(() => {
const I18N = {
  pl: {title:"Upał w Śródmieściu", subtitle:"28.06.2026 · UTCI · SOLWEIG", utci:"UTCI", shade:"Cień",
    legend:"Odczuwalna temperatura UTCI", shadeLegend:"cień (SOLWEIG)", planner:"Zaplanuj trasę",
    pickOrigin:"Wskaż start", pickDest:"Wskaż cel", examples:"Przykłady:", earliest:"Wyjście najwcześniej",
    latest:"Dotarcie najpóźniej", plan:"Planuj", clickMap:"Kliknij na mapie…", planning:"Szukam tras…",
    needPts:"Wskaż start i cel (kliknij przycisk, potem mapę).", dose:"dawka", time:"czas", peak:"maks. UTCI",
    shadeShare:"w cieniu", depart:"wyjście", err:"Błąd planera", coolest:"Najchłodniejsza", balanced:"Zrównoważona",
    fastest:"Najszybsza", walk_only:"Tylko pieszo", external:"Zewnętrzna", walk:"pieszo", wait:"czekanie", ride:"przejazd",
    cats:["< 26 °C brak stresu / słaby","26–32 umiarkowany","32–38 silny","38–46 bardzo silny","> 46 ekstremalny"],
    caveat:"Model SOLWEIG nie uwzględnia wieczornego oddawania ciepła przez nawierzchnie: wartości po 21:00 są dolnym oszacowaniem.",
    soon:"wkrótce", pointT:"UTCI w tym miejscu w ciągu dnia", noData:"brak danych w promieniu 15 m (budynek, woda lub poza zasięgiem)",
    night:"noc (słońce pod horyzontem)", router:"planer", coverage:"Zasięg mapy",
    min:"min"},
  en: {title:"Heat in Śródmieście", subtitle:"28 Jun 2026 · UTCI · SOLWEIG", utci:"UTCI", shade:"Shade",
    legend:"Felt temperature UTCI", shadeLegend:"shade (SOLWEIG)", planner:"Plan a trip",
    pickOrigin:"Pick origin", pickDest:"Pick destination", examples:"Examples:", earliest:"Leave no earlier than",
    latest:"Arrive no later than", plan:"Plan", clickMap:"Click the map…", planning:"Searching…",
    needPts:"Pick origin and destination (press the button, then click the map).", dose:"dose", time:"time",
    peak:"peak UTCI", shadeShare:"in shade", depart:"leave", err:"Planner error", coolest:"Coolest",
    balanced:"Balanced", fastest:"Fastest", walk_only:"Walk only", external:"External", walk:"walk", wait:"wait",
    ride:"ride", cats:["< 26 °C no / weak stress","26–32 moderate","32–38 strong","38–46 very strong","> 46 extreme"],
    caveat:"SOLWEIG has no evening heat release from pavement: values after 21:00 are a lower bound.",
    soon:"soon", pointT:"UTCI at this spot through the day", noData:"no walkable data within 15 m (building, water or outside coverage)",
    night:"night (sun below horizon)", router:"router", coverage:"Map coverage", min:"min"},
};
const CAT_COL = ["#78bec8","#fad664","#f07832","#d73027","#6e0a5a"];
const CAT_KEYS = ["<26","26-32","32-38","38-46",">46"];
const KCOL = {coolest:"#1f6fb2", balanced:"#16a085", fastest:"#333", walk_only:"#8e44ad", external:"#7f8c8d"};
const PRESETS = [
  // Demo trips.
  {pl:"pl. Zbawiciela → Uniwersytet, 12:30–14:30", en:"Pl. Zbawiciela → University, 12:30–14:30", o:[21.01864,52.21971], d:[21.01661,52.24007], e:"12:30", l:"14:30"},
  {pl:"Dw. Centralny → Łazienki, 14–15:30", en:"Central Station → Łazienki, 14–15:30", o:[21.00404,52.22887], d:[21.02645,52.21636], e:"14:00", l:"15:30"},
  {pl:"rondo Dmowskiego → pl. Trzech Krzyży, 18:30–20", en:"Dmowskiego roundabout → Pl. Trzech Krzyży, 18:30–20", o:[21.01173,52.22983], d:[21.0234,52.2283], e:"18:30", l:"20:00"},
  {pl:"Kopernik → Hala Mirowska, 15–16:30", en:"Copernicus Centre → Hala Mirowska, 15–16:30", o:[21.0287,52.2419], d:[20.9986,52.2388], e:"15:00", l:"16:30"},
  {pl:"pl. Zamkowy → pl. Zbawiciela, 8–18 (kiedy wyjść?)", en:"Castle Sq. → Pl. Zbawiciela, 8–18 (when to leave?)", o:[21.0138,52.2476], d:[21.01864,52.21971], e:"08:00", l:"18:00"},
];
const q = new URLSearchParams(location.search);
let lang = q.get("lang") || ((navigator.language || "pl").toLowerCase().startsWith("pl") ? "pl" : "en");
const t = k => (I18N[lang][k] ?? k);
const $ = s => document.querySelector(s);

// ---------------------------------------------------------------- map
const map = L.map("map", {zoomControl: false, attributionControl: true}).setView([52.2305, 21.012], innerWidth < 640 ? 13 : 14);
L.control.zoom({position: "bottomright"}).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, className: "basemap",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · UTCI: SOLWEIG, GUGiK LiDAR, IMGW, ERA5'
}).addTo(map);
map.createPane("heat"); map.getPane("heat").style.zIndex = 350;
map.createPane("shade"); map.getPane("shade").style.zIndex = 360;

let CFG = null, step = +(q.get("step") ?? 14);
const layers = {utci: null, shade: null};
function frameLayer(kind, s) {
  const c = CFG?.[kind]; if (!c?.meta) return null;
  const m = c.meta, b = m.bounds_wgs84;
  return L.tileLayer(`/frames/${c.source}/${s}/{z}/{x}/{y}.png`, {
    pane: kind === "utci" ? "heat" : "shade", minNativeZoom: m.minzoom, maxNativeZoom: m.maxzoom, maxZoom: 20,
    bounds: L.latLngBounds([b[1], b[0]], [b[3], b[2]]), opacity: kind === "utci" ? 0.85 : 1, keepBuffer: 1,
  });
}
function showStep(s, kinds = ["utci", "shade"]) {
  step = s;
  $("#slider").value = s;
  const lbl = CFG?.utci?.meta?.steps?.[s]?.label_local ?? stepLabel(s);
  $("#tlabel").textContent = lbl;
  $("#tlabel").title = s >= 30 ? t("night") : "";
  for (const k of kinds) {
    const on = $(k === "utci" ? "#tg-utci" : "#tg-shade").checked;
    const old = layers[k];
    if (!on) { if (old) map.removeLayer(old); layers[k] = null; continue; }
    const nl = frameLayer(k, s); if (!nl) continue;
    nl.addTo(map); layers[k] = nl;
    if (old) { let done = false; const rm = () => { if (!done) { done = true; map.removeLayer(old); } };
      nl.once("load", rm); setTimeout(rm, 1500); }
  }
}
const stepLabel = s => { const m = 360 + 30 * s; return `${String(Math.floor(m/60)).padStart(2,"0")}:${String(m%60).padStart(2,"0")}`; };
const stepOf = hhmm => { const [h, m] = hhmm.split(":").map(Number); return Math.max(0, Math.min(32, Math.round((h*60+m-360)/30))); };

$("#slider").addEventListener("input", e => showStep(+e.target.value));
$("#tg-utci").addEventListener("change", () => showStep(step, ["utci"]));
$("#tg-shade").addEventListener("change", () => showStep(step, ["shade"]));
let timer = null;
$("#play").addEventListener("click", () => {
  if (timer) { clearInterval(timer); timer = null; $("#play").textContent = "▶"; return; }
  $("#play").textContent = "❚❚";
  timer = setInterval(() => showStep((step + 1) % 33), 900);
});

// ---------------------------------------------------------------- legend, i18n, nav
function renderLegend() {
  $("#lg-rows").innerHTML = I18N[lang].cats.map((c, i) => { const [r, ...d] = c.match(/^([<>]?\s?[\d–]+(?: °C)?)\s(.*)$/).slice(1);
    return `<div class="lg-row"><span class="sw" style="background:${CAT_COL[i]}"></span><b class="rng">${r}</b> <span class="desc">${d.join(" ")}</span></div>`; }).join("");
}
function renderNav() {
  const base = CFG?.heat_views_url || "";
  const views = CFG?.views || [];
  $("#views").innerHTML = views.map(v => base
    ? `<a href="${base}${v.path}?lang=${lang}" target="_blank" rel="noopener">${v[lang]}</a>`
    : `<span title="${t("soon")}">${v[lang]}</span>`).join("");
}
function renderPresets() {
  $("#presets").innerHTML = PRESETS.map((p, i) => `<button data-i="${i}">${p[lang]}</button>`).join("");
  $("#presets").querySelectorAll("button").forEach(b => b.onclick = () => {
    const p = PRESETS[+b.dataset.i]; setPt("o", p.o); setPt("d", p.d);
    if (p.e) $("#t-e").value = p.e; if (p.l) $("#t-l").value = p.l;
    map.fitBounds(L.latLngBounds([[p.o[1], p.o[0]], [p.d[1], p.d[0]]]).pad(0.4));
  });
}
function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    if ((el.id === "o-txt" && pts.o) || (el.id === "d-txt" && pts.d)) return;
    el.textContent = t(el.dataset.i18n);
  });
  $("#lang").textContent = lang === "pl" ? "EN" : "PL";
  document.title = `${t("title")} · ${lang === "pl" ? "planer upału" : "heat planner"}`;
  renderLegend(); renderNav(); renderPresets();
  if (lastPlan) renderCards(lastPlan);
}
$("#lang").addEventListener("click", () => { lang = lang === "pl" ? "en" : "pl"; applyLang(); });
$("#panel-toggle").addEventListener("click", () => $("#panel").classList.toggle("open"));
$("#legend").addEventListener("click", () => $("#legend").classList.toggle("mini"));

// ---------------------------------------------------------------- picking
const pts = {o: null, d: null}, marks = {o: null, d: null};
let picking = null;
const pinIcon = k => L.divIcon({className: "", html: `<span class="pin ${k}" style="width:24px;height:24px;font-size:13px;box-shadow:0 0 0 2px #fff">${k === "o" ? "A" : "B"}</span>`, iconSize: [24, 24], iconAnchor: [12, 12]});
function setPt(k, lonlat) {
  pts[k] = lonlat;
  if (marks[k]) map.removeLayer(marks[k]);
  marks[k] = L.marker([lonlat[1], lonlat[0]], {icon: pinIcon(k), draggable: true}).addTo(map);
  marks[k].on("dragend", e => { const p = e.target.getLatLng(); pts[k] = [+p.lng.toFixed(6), +p.lat.toFixed(6)]; label(k); });
  label(k);
}
function label(k) { $(`#${k}-txt`).textContent = `${pts[k][1].toFixed(4)}, ${pts[k][0].toFixed(4)}`; }
for (const k of ["o", "d"]) $(`#pick-${k}`).addEventListener("click", () => {
  picking = picking === k ? null : k;
  document.querySelectorAll(".pick").forEach(b => b.classList.remove("active"));
  if (picking) { $(`#pick-${k}`).classList.add("active"); $("#status").textContent = t("clickMap");
    if (innerWidth < 640) $("#panel").classList.remove("open"); }
});
map.on("click", e => {
  const ll = [+e.latlng.lng.toFixed(6), +e.latlng.lat.toFixed(6)];
  if (picking) {
    setPt(picking, ll); $(`#pick-${picking}`).classList.remove("active");
    const next = picking === "o" && !pts.d ? "d" : null; picking = null; $("#status").textContent = "";
    if (next) $("#pick-d").click(); else if (innerWidth < 640) $("#panel").classList.add("open");
    return;
  }
  pointSeries(ll);
});

// ---------------------------------------------------------------- point time series
let pointMark = null;
async function pointSeries(ll) {
  if (pointMark) map.removeLayer(pointMark);
  pointMark = L.circleMarker([ll[1], ll[0]], {radius: 6, color: "#000", weight: 2, fillOpacity: 0}).addTo(map);
  $("#point").textContent = "…";
  try {
    const r = await fetch(`/api/point?lon=${ll[0]}&lat=${ll[1]}`); const j = await r.json();
    const u = j.utci;
    if (!u || u.every(v => v == null)) { $("#point").textContent = t("noData"); return; }
    const W = 320, H = 70, lo = 20, hi = 50;
    const x = i => 4 + i * (W - 8) / 32, y = v => H - 4 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo) * (H - 8);
    let bands = [26, 32, 38, 46].map((b, i) => `<line x1="0" x2="${W}" y1="${y(b)}" y2="${y(b)}" stroke="${CAT_COL[i+1]}" stroke-dasharray="3 3"/>`).join("");
    const d = u.map((v, i) => v == null ? "" : `${i && u[i-1] != null ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const sh = (j.shadow || []).map((v, i) => v != null && v < 0.5 && i < 30 ? `<rect x="${x(i)-4}" y="${H-4}" width="8" height="4" fill="#142046"/>` : "").join("");
    const cur = u[step];
    const snap = j.utci_snap_m > 1.5 ? ` <span style="color:#5d6575">(${Math.round(j.utci_snap_m)} m)</span>` : "";
    $("#point").innerHTML = `<b>${t("pointT")}</b>${snap} · ${stepLabel(step)}: <b>${cur == null ? "–" : cur.toFixed(1) + " °C"}</b>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${bands}<path d="${d}" fill="none" stroke="#c0392b" stroke-width="2"/>
      <line x1="${x(step)}" x2="${x(step)}" y1="0" y2="${H}" stroke="#000" stroke-width="1"/>${sh}</svg>
      <div style="display:flex;justify-content:space-between;color:#5d6575"><span>06:00</span><span>14:00</span><span>22:00</span></div>`;
    $("#panel").classList.add("open");
  } catch (e) { $("#point").textContent = String(e); }
}

// ---------------------------------------------------------------- planning
const routeLayer = L.featureGroup().addTo(map);
let lastPlan = null, selected = 0;
$("#go").addEventListener("click", async () => {
  if (!pts.o || !pts.d) { $("#status").textContent = t("needPts"); return; }
  $("#go").disabled = true; $("#status").textContent = t("planning"); $("#cards").innerHTML = ""; $("#notes").innerHTML = "";
  try {
    const r = await fetch("/api/plan", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({origin: pts.o, destination: pts.d, earliest: $("#t-e").value, latest: $("#t-l").value})});
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    lastPlan = await r.json(); selected = 0;
    $("#status").textContent = `${t("router")}: ${lastPlan.router || "?"} · ${lastPlan.elapsed_s ?? "?"} s`;
    renderCards(lastPlan); drawRoutes();
    if (lastPlan.options?.[0]) showStep(stepOf(lastPlan.options[0].depart));
    $("#status").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (e) { $("#status").textContent = `${t("err")}: ${e.message}`; }
  finally { $("#go").disabled = false; }
});
const fmtMin = m => m == null ? "–" : `${Math.round(m)} ${t("min")}`;
function renderCards(p) {
  const opts = p.options || [];
  $("#cards").innerHTML = opts.map((o, i) => {
    const cats = o.minutes_per_category || {}; const tot = CAT_KEYS.reduce((a, k) => a + (cats[k] || 0), 0) || 1;
    const bar = CAT_KEYS.map((k, j) => `<i style="width:${100 * (cats[k] || 0) / tot}%;background:${CAT_COL[j]}" title="${k}: ${(cats[k]||0).toFixed(1)} min"></i>`).join("");
    const legs = (o.legs || []).filter(l => l.mode !== "wait" || true).map(l =>
      l.mode === "ride" ? `🚋 ${l.line ?? ""}` : l.mode === "wait" ? `⏱` : `🚶`).join(" › ");
    return `<div class="card ${i === selected ? "sel" : ""}" style="--k:${KCOL[o.kind] || "#555"}" data-i="${i}">
      <div class="h"><span class="k">${t(o.kind)}</span><span class="tt">${t("depart")} ${o.depart} → ${o.arrive}</span></div>
      <div class="m"><div><b>${o.dose_Ks == null ? "–" : Math.round(o.dose_Ks / 60)}</b><span>${t("dose")} K·min</span></div>
        <div><b>${fmtMin(o.duration_min)}</b><span>${t("time")}</span></div>
        <div><b>${o.peak_utci == null ? "–" : o.peak_utci.toFixed(1) + "°"}</b><span>${t("peak")}</span></div>
        <div><b>${o.shade_share == null ? "–" : Math.round(100 * o.shade_share) + "%"}</b><span>${t("shadeShare")}</span></div></div>
      <div class="bar">${bar}</div><div class="legs">${legs}</div></div>`;
  }).join("");
  $("#cards").querySelectorAll(".card").forEach(c => c.onclick = () => {
    selected = +c.dataset.i; renderCards(lastPlan); drawRoutes();
    showStep(stepOf(lastPlan.options[selected].depart));
  });
  $("#notes").innerHTML = (p.notes || []).map(n => `<div>• ${n}</div>`).join("");
}
function drawRoutes() {
  routeLayer.clearLayers();
  const opts = lastPlan?.options || [];
  opts.forEach((o, i) => { if (i !== selected) drawOption(o, false); });
  if (opts[selected]) drawOption(opts[selected], true);
  if (routeLayer.getLayers().length) map.fitBounds(routeLayer.getBounds().pad(0.25), {paddingBottomRight: innerWidth > 640 ? [380, 0] : [0, innerHeight * 0.45]});
}
function drawOption(o, sel) {
  const col = KCOL[o.kind] || "#555";
  for (const l of o.legs || []) {
    const g = l.geometry; if (!g) continue;
    if (g.type === "Point") {
      if (sel) L.circleMarker([g.coordinates[1], g.coordinates[0]], {radius: 6, color: "#fff", weight: 2, fillColor: col, fillOpacity: 1})
        .bindTooltip(`${t("wait")} ${l.start?.slice(0,5)}–${l.end?.slice(0,5)} ${l.from_stop ?? ""}`).addTo(routeLayer);
      continue;
    }
    const ll = g.coordinates.map(c => [c[1], c[0]]);
    if (sel) L.polyline(ll, {color: "#fff", weight: l.mode === "ride" ? 9 : 8, opacity: 0.9}).addTo(routeLayer);
    L.polyline(ll, {color: col, weight: sel ? (l.mode === "ride" ? 5 : 4.5) : 3, opacity: sel ? 1 : 0.45,
      dashArray: l.mode === "ride" ? "8 7" : null})
      .bindTooltip(`${t(o.kind)} · ${t(l.mode)} ${l.line ?? ""} ${l.start?.slice(0,5)}–${l.end?.slice(0,5)} · ${Math.round((l.dose_Ks || 0) / 60)} K·min`)
      .on("click", ev => { L.DomEvent.stop(ev); selected = (lastPlan.options || []).indexOf(o); renderCards(lastPlan); drawRoutes(); })
      .addTo(routeLayer);
  }
}

// ---------------------------------------------------------------- boot
(async () => {
  applyLang(); if (innerWidth < 640) { $("#panel").classList.remove("open"); $("#legend").classList.add("mini"); }
  try {
    const p = new URLSearchParams(); if (q.get("utci")) p.set("utci", q.get("utci")); if (q.get("shade")) p.set("shade", q.get("shade"));
    CFG = await (await fetch(`/api/config?${p}`)).json();
    if (CFG.utci?.meta) {
      const b = CFG.utci.meta.bounds_wgs84;
      L.rectangle([[b[1], b[0]], [b[3], b[2]]], {color: "#555", weight: 1, dashArray: "4 4", fill: false, interactive: false}).addTo(map);
      if (!q.get("z")) map.fitBounds([[b[1], b[0]], [b[3], b[2]]], {paddingTopLeft: [0, 60], paddingBottomRight: innerWidth > 640 ? [380, 0] : [0, innerHeight * 0.3]});
    }
    $("#lg-src").textContent = CFG.utci?.meta ? `${CFG.utci.source}: ${CFG.utci.meta.note || ""}` : "no frames";
    if (q.get("shadeOn") === "1") $("#tg-shade").checked = true;
  } catch (e) { console.error(e); }
  renderNav(); showStep(step);
  // deep link: ?o=lon,lat&d=lon,lat&e=HH:MM&l=HH:MM&go=1
  const pp = k => (q.get(k) || "").split(",").map(Number);
  if (q.get("o") && q.get("d")) {
    setPt("o", pp("o")); setPt("d", pp("d"));
    if (q.get("e")) $("#t-e").value = q.get("e"); if (q.get("l")) $("#t-l").value = q.get("l");
    if (q.get("go") === "1") { $("#panel").classList.add("open"); $("#go").click(); }
  }
})();
})();
