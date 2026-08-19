"""Klik na popisek výřezu otevře prohlížeč fotky v modálu a nikam neodroluje."""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, watch

# Popisek, který je právě vidět. pg.click() by si k prvku sám odscrolloval a
# měřený posun by pak byl jeho, ne aplikace.
VISIBLE_CAP = """() => {
  for (const a of document.querySelectorAll('.cell .cap')) {
    const r = a.getBoundingClientRect();
    if (r.top > 60 && r.bottom < window.innerHeight - 20)
      return {x: r.x + r.width / 2, y: r.y + r.height / 2};
  }
  return null;
}"""

MODAL_STATE = """() => {
  const vm = document.getElementById('viewmodal');
  return {open: vm.open,
          src: document.getElementById('vm-frame').getAttribute('src'),
          title: document.getElementById('vm-title').textContent,
          openHref: document.getElementById('vm-open').href,
          scrollY: window.scrollY};
}"""

INNER_STATE = """() => {
  const w = document.getElementById('vm-frame').contentWindow;
  return {scrollY: w.scrollY, hash: w.location.hash,
          imgs: w.document.querySelectorAll('#overview img').length,
          crops: w.document.querySelectorAll('.crop').length};
}"""


def main():
    c = Checks("test_modal")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?page=2", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2000)

        pg.evaluate("window.scrollTo(0, 900)")
        pg.wait_for_timeout(400)
        target = pg.evaluate(VISIBLE_CAP)
        assert target, "nenašel jsem viditelný popisek"
        before = pg.evaluate("window.scrollY")
        url_before = pg.url

        pg.mouse.click(target["x"], target["y"])
        pg.wait_for_timeout(2500)

        st = pg.evaluate(MODAL_STATE)
        c.check(st["open"], "modál se otevřel")
        c.check("/view?" in (st["src"] or ""), "iframe míří na prohlížeč",
                str(st["src"])[:60])
        c.check(bool(st["title"]), "modál má titulek", st["title"][:40])
        c.check("/view?" in st["openHref"], "odkaz do nového okna funguje")
        c.check(st["scrollY"] == before, "mřížka pod modálem neodrolovala",
                f"{before} -> {st['scrollY']}")
        c.check(pg.url == url_before, "zůstali jsme na mřížce", pg.url[-40:])

        pg.frame_locator("#vm-frame").locator("#overview img").first.wait_for(timeout=20000)
        pg.wait_for_timeout(1500)
        inner = pg.evaluate(INNER_STATE)
        c.check(inner["imgs"] == 1, "fotka v modálu se vykreslila")
        c.check(inner["crops"] > 0, "výřezy v modálu", f"{inner['crops']} ks")
        c.check(inner["scrollY"] == 0, "prohlížeč v modálu zůstal nahoře",
                f"scrollY {inner['scrollY']}, hash {inner['hash']}")

        pg.click("#vm-close")
        pg.wait_for_timeout(500)
        after = pg.evaluate("""() => ({
          open: document.getElementById('viewmodal').open,
          src: document.getElementById('vm-frame').getAttribute('src')})""")
        c.check(not after["open"], "modál se zavřel")
        c.check(after["src"] == "about:blank", "iframe uvolněn", str(after["src"]))
        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
