"""Tažení kolečka myší na desktopu.

Dotykové ovládání se dodělávalo později a leží na stejné buňce, takže tenhle
test hlídá hlavně to, že si dotykové prvky (.grab) neukradly myš.
"""
import sys

from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, first_bolt_cell, watch


def main():
    c = Checks("test_mouse")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=bolt", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2500)

        s0 = first_bolt_cell(pg)
        if not s0:
            sys.exit("  na stránce není žádný potvrzený bolt — nemám co testovat")
        pg.wait_for_timeout(400)
        s0 = first_bolt_cell(pg)
        w = s0["wrap"]

        grab_pe = pg.evaluate(
            "getComputedStyle(document.querySelector('.cell.t-bolt .grab')).pointerEvents")
        c.check(grab_pe == "none", "dotykový terč na desktopu nebere myš", grab_pe)

        cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2
        pg.mouse.move(cx, cy)
        pg.mouse.down()
        for i in range(1, 6):
            pg.mouse.move(cx + 4 * i, cy + 3 * i)
            pg.wait_for_timeout(40)
        pg.mouse.up()
        pg.wait_for_timeout(700)

        s1 = first_bolt_cell(pg)
        c.check(s1["id"] == s0["id"], "pořád tatáž buňka", s1["id"])
        c.check(abs(s1["dx"] - s0["dx"]) > 0.5 or abs(s1["dy"] - s0["dy"]) > 0.5,
                "tažení myší posunulo kolečko",
                f"dx {s0['dx']}->{s1['dx']} dy {s0['dy']}->{s1['dy']}")
        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
