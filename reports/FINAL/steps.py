import json
from playwright.sync_api import sync_playwright
APP = "https://heat-app-269831067246.europe-central2.run.app/?lang=pl"
V = "https://heat-views-269831067246.europe-central2.run.app"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--use-gl=swiftshader","--enable-webgl","--ignore-gpu-blocklist"])
    for name, url in [("app", APP), ("routes", V+"/routes/"), ("shadows3d", V+"/shadows3d/"), ("temperature", V+"/temperature/")]:
        pg = b.new_page(viewport=dict(width=1440, height=900))
        ok, bad, errs = [0], [], []
        pg.on("response", lambda r: ok.__setitem__(0, ok[0]+1) if r.status < 400 else bad.append(f"{r.status} {r.url[-80:]}"))
        pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
        pg.goto(url, wait_until="networkidle", timeout=90000)
        labels = []
        for s in range(33):
            pg.evaluate("v => { const s=document.querySelector('#slider'); s.value=v; s.dispatchEvent(new Event('input',{bubbles:true})); s.dispatchEvent(new Event('change',{bubbles:true})); }", s)
            pg.wait_for_timeout(350)
        pg.wait_for_timeout(3000)
        txt = pg.evaluate("document.body.innerText")
        print(name, "ok_resp", ok[0], "bad", len(bad), bad[:5], "errs", errs[:3], "PL:", any(w in txt for w in ["Zaplanuj","Planuj","brak stresu","Cień","Odczuwalna"]))
        if name == "app":
            pg.set_viewport_size(dict(width=390, height=844)); pg.wait_for_timeout(1500)
            pg.screenshot(path="/Users/staszek/warsaw-open-data/screenshots/app_pl_phone.png")
        pg.close()
    b.close()
