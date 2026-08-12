"""Přehledový prohlížeč /view.

/view kdysi mlčky nefungoval: bootoval se fetchem statických data.json a
crops.json, které už nikdo negeneroval, takže Promise.all spadl na parsování
404 těla a stránka zůstala prázdná — bez jediné viditelné stopy kromě chyby
v konzoli. Test tedy hlídá, že se opravdu něco vykreslí, ne jen že přišlo 200.

Druhá věc: výřezy pod přehledovkou mají být zhruba 1:1, aby nebyly rozmazané.
"""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, watch


def main():
    c = Checks("test_view")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1600, "height": 1000})
        errors = []
        watch(pg, errors)

        # data.json a crops.json se generují z DB, musí být validní JSON
        for path in ("/data.json", "/crops.json"):
            r = pg.request.get(f"{BASE_URL}{path}")
            ok = r.status == 200 and isinstance(r.json(), list) and len(r.json()) > 0
            c.check(ok, f"{path} vrací neprázdné pole", f"HTTP {r.status}")

        data = pg.request.get(f"{BASE_URL}/data.json").json()
        item = data[0]
        pg.goto(f"{BASE_URL}/view?file={item['file']}", wait_until="networkidle",
                timeout=60000)
        pg.wait_for_timeout(4000)

        info = pg.evaluate("""() => {
          const ov = document.getElementById('overview');
          const cv = document.querySelector('.crop canvas');
          const grid = document.getElementById('crops');
          const r = cv ? cv.getBoundingClientRect() : null;
          return {
            overviewImgs: ov.querySelectorAll('img').length,
            svgRects: document.querySelectorAll('#overview svg rect').length,
            crops: document.querySelectorAll('.crop').length,
            canvasBacking: cv ? cv.width : 0,
            canvasCss: r ? Math.round(r.width) : 0,
            gridWidth: Math.round(grid.getBoundingClientRect().width),
            winWidth: window.innerWidth,
            title: (document.getElementById('title') || {}).textContent,
          };
        }""")

        c.check(info["overviewImgs"] == 1, "přehledový obrázek se vykreslil")
        c.check(info["svgRects"] > 0, "značky boltů v SVG", f"{info['svgRects']} ks")
        c.check(info["crops"] > 0, "výřezy pod přehledovkou", f"{info['crops']} ks")
        c.check(bool(info["title"]), "nadpis vyplněn", str(info["title"]))

        ratio = info["canvasBacking"] / max(info["canvasCss"], 1)
        c.check(0.8 <= ratio <= 1.25, "výřezy jsou zhruba 1:1 (neinterpolují se)",
                f"{info['canvasBacking']}px do {info['canvasCss']}px = {ratio:.2f}")
        c.check(info["gridWidth"] >= info["winWidth"] - 4,
                "mřížka výřezů jde přes celou šířku",
                f"{info['gridWidth']} z {info['winWidth']}")

        c.check(not errors, "žádné chyby v konzoli ani spadlé requesty",
                "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
