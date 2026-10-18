import json, time
from playwright.sync_api import sync_playwright
APP = "https://heat-app-269831067246.europe-central2.run.app/?lang=en"
OUT = "/Users/staszek/warsaw-open-data/screenshots"
res = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for vn, kw in [("desktop", dict(viewport=dict(width=1440, height=900))),
                   ("phone", dict(viewport=dict(width=390, height=844), device_scale_factor=2, is_mobile=True, has_touch=True))]:
        ctx = b.new_context(**kw); pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
        pg.goto(APP, wait_until="networkidle", timeout=90000)
        n = pg.locator("#presets button").count()
        for i in range(n):
            if vn == "phone" and "open" not in (pg.get_attribute("#panel", "class") or ""):
                pg.click("#panel-toggle")
                pg.wait_for_timeout(500)
            label = pg.locator("#presets button").nth(i).inner_text()
            pg.locator("#presets button").nth(i).click()
            pg.wait_for_timeout(400)
            e, l = pg.input_value("#t-e"), pg.input_value("#t-l")
            t0 = time.time()
            with pg.expect_response(lambda r: "/api/plan" in r.url, timeout=60000) as ri:
                pg.click("#go")
            r = ri.value; js = r.json(); rt = round(time.time() - t0, 2)
            pg.wait_for_selector("#cards .card", timeout=30000)
            pg.wait_for_timeout(1500)
            cards = pg.locator("#cards .card").all_inner_texts()
            opts = [{k: o.get(k) for k in ["kind", "depart", "arrive", "duration_min", "dose_Ks", "peak_utci", "shade_share", "minutes_per_category"]} |
                    {"legs": " > ".join((lg["mode"] + (" " + str(lg.get("line")) if lg["mode"] == "ride" else "")) for lg in o["legs"])} for o in js["options"]]
            rec = {"vp": vn, "preset": label, "earliest": e, "latest": l, "http": r.status, "roundtrip_s": rt,
                   "runtime_s": js["query"].get("runtime_s"), "heat_source": js["query"].get("heat_source"), "notes": js.get("notes"),
                   "options": opts, "cards_text": [c.replace("\n", " | ") for c in cards]}
            res.append(rec)
            print(json.dumps(rec, ensure_ascii=False)[:1500], flush=True)
            if vn == "phone" and i == 0:
                pg.locator("#cards .card").first.scroll_into_view_if_needed()
                pg.wait_for_timeout(800)
                pg.screenshot(path=f"{OUT}/app_route_card_phone.png")
                pg.locator("#cards .card").first.screenshot(path=f"{OUT}/app_route_card_phone_crop.png")
            if vn == "desktop" and i == 0:
                pg.screenshot(path=f"{OUT}/app_plan_desktop.png")
        print("ERRS", vn, errs)
        ctx.close()
    b.close()
json.dump(res, open("/Users/staszek/warsaw-open-data/verify/trips.json", "w"), ensure_ascii=False, indent=1)
