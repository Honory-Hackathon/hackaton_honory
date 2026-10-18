// Shared helpers for the heat-views pages (T13).
const HV = (() => {
  const N = 33;
  const LABELS = Array.from({ length: N }, (_, k) => {
    const m = 360 + 30 * k;
    return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
  });
  const TS_UTC = LABELS.map((_, k) => Date.UTC(2026, 5, 28, 4, 30 * k));
  const APP_URL = "https://heat-app-269831067246.europe-central2.run.app";

  // UTCI stress categories (spec; T12 manifest utci_categories)
  const CATS = [
    { max: 26, color: "#abd9e9", pl: "brak stresu", en: "no heat stress", range: "< 26" },
    { min: 26, max: 32, color: "#fee090", pl: "umiarkowany", en: "moderate", range: "26–32" },
    { min: 32, max: 38, color: "#f46d43", pl: "silny", en: "strong", range: "32–38" },
    { min: 38, max: 46, color: "#d73027", pl: "bardzo silny", en: "very strong", range: "38–46" },
    { min: 46, color: "#6e0046", pl: "ekstremalny", en: "extreme", range: "> 46" },
  ];
  const catIndex = (u) => (u < 26 ? 0 : u < 32 ? 1 : u < 38 ? 2 : u < 46 ? 3 : 4);

  // T12 UTCI ramp: hard steps at 26/32/38/46, lightness ramp inside each category
  const UTCI_STOPS = [[10, "#313695"], [18, "#4575b4"], [22, "#74add1"], [26, "#abd9e9"], [26.01, "#ffffbf"], [32, "#fee090"],
    [32.01, "#fdae61"], [38, "#f46d43"], [38.01, "#d73027"], [46, "#a50026"], [46.01, "#6e0046"], [54, "#3c003c"]];
  const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  function ramp(stops, v) {
    if (v <= stops[0][0]) return hex(stops[0][1]);
    for (let i = 1; i < stops.length; i++) {
      if (v <= stops[i][0]) {
        const [v0, c0] = stops[i - 1], [v1, c1] = stops[i];
        const t = (v - v0) / (v1 - v0), a = hex(c0), b = hex(c1);
        return [0, 1, 2].map((j) => Math.round(a[j] + t * (b[j] - a[j])));
      }
    }
    return hex(stops[stops.length - 1][1]);
  }
  const utciRGB = (u) => ramp(UTCI_STOPS, u);

  function legendHTML() {
    return '<div class="legend" aria-label="UTCI">' + CATS.map((c, i) =>
      `<div class="sw${i >= 3 ? " dark" : ""}" style="background:${c.color}"><b>${c.range} °C</b><span>${c.pl} / ${c.en}</span></div>`
    ).join("") + "</div>";
  }

  function navHTML(active) {
    const v = [["routes", "Trasy", "Routes"], ["shadows3d", "Cienie 3D", "3D shadows"], ["temperature", "Temperatura i albedo", "Temperature & albedo"]];
    return '<nav class="views">' + v.map(([p, pl, en]) =>
      `<a href="/${p}/" class="${p === active ? "on" : ""}">${pl} · ${en}</a>`).join("") +
      `<a href="${APP_URL}" title="heat-app">Planer ↗</a></nav>`;
  }

  // Time slider with play button and URL hash (?t=HH:MM via #t=)
  function timeControl(el, onChange, initial = 18) {
    el.innerHTML = `<div class="row"><button id="play" aria-label="play">▶</button><span id="time"></span>
      <input id="slider" type="range" min="0" max="${N - 1}" step="1" value="${initial}" aria-label="time / czas"></div>
      <div class="ticks"><span>06:00</span><span>10:00</span><span>14:00</span><span>18:00</span><span>22:00</span></div>`;
    const s = el.querySelector("#slider"), t = el.querySelector("#time"), p = el.querySelector("#play");
    const fromHash = () => { const m = location.hash.match(/t=(\d\d:\d\d)/); const k = m ? LABELS.indexOf(m[1]) : -1; return k >= 0 ? k : null; };
    let step = fromHash() ?? initial, timer = null;
    const set = (k, push = true) => {
      step = k; s.value = k; t.textContent = LABELS[k];
      if (push) history.replaceState(null, "", "#t=" + LABELS[k]);
      onChange(k);
    };
    s.addEventListener("input", () => set(+s.value));
    p.addEventListener("click", () => {
      if (timer) { clearInterval(timer); timer = null; p.textContent = "▶"; return; }
      p.textContent = "❚❚";
      timer = setInterval(() => set((step + 1) % N), 900);
    });
    document.addEventListener("keydown", (e) => {
      if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
      if (e.key === "ArrowRight") set(Math.min(N - 1, step + 1));
      if (e.key === "ArrowLeft") set(Math.max(0, step - 1));
    });
    return { set, get: () => step, start: () => set(step, false) };
  }

  // SOLWEIG has no evening heat release from pavement (spec: known limit)
  const lateNote = (k) => k >= 30 ? '<div class="extra" style="color:#9a3412">Po 21:00 SOLWEIG nie oddaje ciepła nagrzanych nawierzchni: wartości są zaniżone · After 21:00 SOLWEIG has no heat release from warm pavement: values are a lower bound.</div>' : "";
  const isPhone = () => Math.min(window.innerWidth, window.innerHeight) < 600;
  const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? "–" : v.toFixed(d));

  // Preload an array of frame URLs (current first, then outward)
  function framePreloader(urlFor, onLoad) {
    const imgs = new Array(N);
    function get(k) {
      if (!imgs[k]) {
        const im = new Image();
        im.crossOrigin = "anonymous";
        im.decoding = "async";
        im.onload = () => onLoad && onLoad(k, im);
        im.src = urlFor(k);
        imgs[k] = im;
      }
      return imgs[k];
    }
    function around(k, r = 3) { for (let d = 1; d <= r; d++) { if (k + d < N) get(k + d); if (k - d >= 0) get(k - d); } }
    const ready = (k) => { const im = get(k); return im.complete && im.naturalWidth ? im : null; };
    return { get, ready, around, all: () => { for (let k = 0; k < N; k++) get(k); } };
  }

  return { lateNote, N, LABELS, TS_UTC, CATS, catIndex, utciRGB, ramp, hex, legendHTML, navHTML, timeControl, isPhone, fmt, framePreloader, APP_URL };
})();
