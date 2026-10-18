import json, sys, time
from playwright.sync_api import sync_playwright
APP = "https://heat-app-269831067246.europe-central2.run.app"
VIEWS = "https://heat-views-269831067246.europe-central2.run.app"
PAGES = {"app": APP + "/", "routes": VIEWS + "/routes/", "shadows3d": VIEWS + "/shadows3d/", "temperature": VIEWS + "/temperature/"}
VP = {"desktop": dict(width=1440, height=900), "phone": dict(width=390, height=844)}
OUT = "/Users/staszek/warsaw-open-data/screenshots"
res = {}
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"])
    for name, url in PAGES.items():
        for vn, vp in VP.items():
            kw = dict(viewport=vp)
            if vn == "phone":
                kw.update(device_scale_factor=2, is_mobile=True, has_touch=True)
            ctx = b.new_context(**kw)
            pg = ctx.new_page()
            errs, fails = [], []
            pg.on("console", lambda m, errs=errs: errs.append(m.text[:200]) if m.type == "error" else None)
            pg.on("pageerror", lambda e, errs=errs: errs.append("PAGEERROR " + str(e)[:200]))
            pg.on("response", lambda r, fails=fails: fails.append(f"{r.status} {r.url[:120]}") if r.status >= 400 else None)
            t0 = time.time()
            st = pg.goto(url, wait_until="networkidle", timeout=90000).status
            pg.wait_for_timeout(2500)
            r = {"status": st, "load_s": round(time.time() - t0, 1)}
            r["overflow"] = pg.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
            # elements wider than viewport
            r["wide_elems"] = pg.evaluate("""() => { const w = document.documentElement.clientWidth; const o=[];
              for (const e of document.querySelectorAll('body *')) { const r=e.getBoundingClientRect(); const cs=getComputedStyle(e);
                if (cs.display==='none'||cs.visibility==='hidden'||r.width===0) continue;
                if (r.right > w + 1 && !e.closest('.leaflet-container, canvas, .maplibregl-map, .mapboxgl-map, #map')) o.push((e.id||e.className||e.tagName).toString().slice(0,40)+':'+Math.round(r.right)); }
              return o.slice(0,10); }""")
            sl = pg.query_selector("#slider")
            if sl:
                vals = []
                mx = sl.get_attribute("max")
                for v in [0, 16, 32]:
                    pg.evaluate("v => { const s=document.querySelector('#slider'); s.value=v; s.dispatchEvent(new Event('input',{bubbles:true})); s.dispatchEvent(new Event('change',{bubbles:true})); }", v)
                    pg.wait_for_timeout(1200)
                    lab = pg.evaluate("""() => { const c=['#tlabel','.tlabel','#time','#tl','.time-label']; for (const s of c){const e=document.querySelector(s); if(e) return e.textContent.trim();} 
                       const s=document.querySelector('#slider'); return (s.parentElement.parentElement||s.parentElement).textContent.trim().slice(0,80); }""")
                    vals.append((v, lab))
                r["slider"] = {"max": mx, "labels": vals}
                pg.evaluate("v => { const s=document.querySelector('#slider'); s.value=v; s.dispatchEvent(new Event('input',{bubbles:true})); s.dispatchEvent(new Event('change',{bubbles:true}));}", 14)
                pg.wait_for_timeout(2000)
            else:
                r["slider"] = None
            txt = pg.evaluate("document.body.innerText")
            r["has_pl"] = any(w in txt for w in ["brak stresu", "umiarkowany", "Cień", "czas", "silny", "Zaplanuj", "Planuj"])
            r["has_en"] = any(w in txt.lower() for w in ["heat stress", "strong", "shade", "time", "moderate"])
            r["legend_text"] = [w for w in ["26", "32", "38", "46"] if w in txt]
            pg.screenshot(path=f"{OUT}/{name}_{vn}.png")
            r["console_errors"] = errs[:8]
            r["http_fail"] = fails[:8]
            res[f"{name}_{vn}"] = r
            print(name, vn, json.dumps(r, ensure_ascii=False)[:900], flush=True)
            ctx.close()
    b.close()
json.dump(res, open("/Users/staszek/warsaw-open-data/verify/pages.json", "w"), ensure_ascii=False, indent=1)
