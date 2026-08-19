"""Projde hlavní stránky appky a hlídá, že nic nespadne.

Levný, ale historicky nejužitečnější test: zakomentování tlačítka v hlavičce
umí tiše shodit celý skript (JS sáhne na getElementById(...) => null), stránka
přitom vypadá skoro normálně — jen přestane fungovat označování boltů.
"""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, Checks, watch

PAGES = [
    ("/crops?page=1", "mřížka výřezů"),
    ("/crops?show=bolt", "filtr potvrzené"),
    ("/crops?show=undecided", "filtr nevíme"),
    ("/crops?show=no-bolt", "filtr no-bolt"),
    ("/crops?size=big", "větší náhled"),
]

# Prvky, které na mřížce musí existovat — jinak se skript zastavil dřív, než
# je stihl navěsit, nebo je někdo omylem odstranil z hlavičky.
REQUIRED = ["#setbtn", "#settings", "#viewmodal", ".cell", ".badge-yes", ".badge-no"]


def main():
    c = Checks("test_console")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        for path, label in PAGES:
            pg = b.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            watch(pg, errors)
            pg.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(1500)
            c.check(not errors, f"{label} ({path}) bez chyb", "; ".join(errors[:2]))
            if path.startswith("/crops"):
                missing = pg.evaluate(
                    "sels => sels.filter(s => !document.querySelector(s))", REQUIRED)
                c.check(not missing, f"{label}: prvky na místě", ", ".join(missing))
            pg.close()
        b.close()
    c.done()


if __name__ == "__main__":
    main()
