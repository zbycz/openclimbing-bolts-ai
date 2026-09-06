"""Tlačítko na stažení databáze dole na stránce s výřezy.

Stahování jde přes SQLite backup API, ne prostým přečtením souboru — appka do
DB píše i během stahování a syrová kopie by mohla odejít rozepsaná uprostřed
transakce. Test proto nekouká jen na to, že něco přišlo, ale otevře stažený
soubor jako SQLite a spustí na něm integrity_check.
"""
import os
import sqlite3
import tempfile

from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, Checks, watch


def main():
    c = Checks("test_download")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page()
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops", wait_until="networkidle", timeout=60000)

        btn = pg.query_selector('a.btn[href="/download-db"]')
        c.check(btn is not None, "tlačítko na stažení DB je na stránce")
        if btn:
            box = btn.bounding_box()
            grid = pg.query_selector(".grid").bounding_box()
            c.check(box["y"] > grid["y"] + grid["height"],
                    "je až pod mřížkou výřezů",
                    f"y {int(box['y'])} > konec mřížky {int(grid['y'] + grid['height'])}")

        r = pg.request.get(f"{BASE_URL}/download-db")
        c.check(r.status == 200, "/download-db vrací 200", str(r.status))
        cd = r.headers.get("content-disposition", "")
        c.check("attachment" in cd and cd.endswith('.sqlite"'),
                "posílá se jako soubor ke stažení", cd)
        body = r.body()
        c.check(body[:15] == b"SQLite format 3", "je to opravdu SQLite",
                repr(body[:15]))

        fd, tmp = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(body)
            db = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            ok = db.execute("pragma integrity_check").fetchone()[0]
            c.check(ok == "ok", "stažená kopie projde integrity_check", ok)
            n = db.execute("select count(*) from crop_labels").fetchone()[0]
            c.check(n > 0, "crop_labels v kopii nejsou prázdné", f"{n} řádků")
            live = pg.request.get(f"{BASE_URL}/crops.json").json()
            c.check(len(live) > 0 and n >= len(live),
                    "kopie má aspoň tolik výřezů, kolik appka ukazuje",
                    f"DB {n} vs /crops.json {len(live)}")
            db.close()
        finally:
            os.unlink(tmp)

        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
