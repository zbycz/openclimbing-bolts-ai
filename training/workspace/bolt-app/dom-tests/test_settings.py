"""Panel nastavení: vypnutí sliderů, velikost náhledu a dlaždice, jeden sloupec."""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, watch


def sliders_shown(pg):
    return pg.evaluate("""() => {
      const s = document.querySelector('.cell.t-bolt .sliders');
      return s ? getComputedStyle(s).display !== 'none' : null;
    }""")


def main():
    c = Checks("test_settings")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=bolt", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2000)

        c.check(sliders_shown(pg) is True, "slidery jsou zprvu vidět")
        pg.click("#setbtn")
        pg.wait_for_timeout(400)
        c.check(pg.evaluate("document.getElementById('settings').open"),
                "panel nastavení se otevřel")

        # vypnout slidery — bez reloadu
        pg.uncheck("#set-sliders")
        pg.wait_for_timeout(400)
        c.check(sliders_shown(pg) is False, "slidery se schovaly")
        c.check(pg.evaluate("localStorage.getItem('crops_sliders')") == "0",
                "volba uložena")

        # a přežít reload, aniž by problikly
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(1500)
        c.check(sliders_shown(pg) is False, "po reloadu zůstaly schované")
        c.check(pg.evaluate("document.body.classList.contains('no-sliders')"),
                "třída na body je nasazená")

        # jeden sloupec bez reloadu
        pg.click("#setbtn")
        pg.wait_for_timeout(300)
        pg.check("#set-onecol")
        pg.wait_for_timeout(400)
        c.check(pg.evaluate("document.querySelector('.grid').classList.contains('onecol')"),
                "jeden sloupec se zapnul")
        pg.uncheck("#set-onecol")
        pg.wait_for_timeout(300)
        c.check(not pg.evaluate("document.querySelector('.grid').classList.contains('onecol')"),
                "a zase vypnul")

        # velikost dlaždice mění šířku sloupce (přes reload)
        # filtrování je jen CSS, takže první .cell v DOM bývá skrytá (šířka 0)
        VISIBLE_CELL_W = """() => {
          for (const c of document.querySelectorAll('.cell')) {
            const w = c.getBoundingClientRect().width;
            if (w > 0) return Math.round(w);
          }
          return 0;
        }"""
        before = pg.evaluate(VISIBLE_CELL_W)
        pg.select_option("#set-tile", "XL")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        after = pg.evaluate(VISIBLE_CELL_W)
        c.check(after > before + 50, "XL dlaždice je znatelně širší", f"{before} -> {after}")
        c.check("tile=XL" in pg.url, "volba je i v adrese", pg.url[-24:])

        # velikost náhledu
        pg.click("#setbtn")
        pg.wait_for_timeout(300)
        pg.select_option("#set-size", "huge")
        pg.wait_for_load_state("networkidle")
        pg.wait_for_timeout(1200)
        c.check("size=huge" in pg.url, "velikost náhledu se propsala", pg.url[-30:])
        c.check(pg.evaluate("document.getElementById('set-size').value") == "huge",
                "select ukazuje aktuální volbu")
        c.check(sliders_shown(pg) is False, "slidery zůstaly vypnuté i po změně velikosti")

        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()
if __name__ == "__main__":
    main()
