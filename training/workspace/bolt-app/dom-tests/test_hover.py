"""Hover nad levou/pravou polovinou náhledu zvýrazní odpovídající odznak.

Jen na nepotvrzených výřezech — u potvrzeného boltu jsou zóny vypnuté a
odznaky se klikají přímo.
"""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, watch


def op(pg, sel):
    return float(pg.evaluate(
        "s => getComputedStyle(document.querySelector(s)).opacity", sel))


def main():
    c = Checks("test_hover")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=undecided", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2000)

        box = pg.evaluate("""() => {
          const c = document.querySelector('.cell.t-undecided');
          c.scrollIntoView({block: 'center'});
          const w = c.querySelector('.imgwrap').getBoundingClientRect();
          return {x: w.x, y: w.y, w: w.width, h: w.height};
        }""")
        pg.wait_for_timeout(400)
        box = pg.evaluate("""() => {
          const w = document.querySelector('.cell.t-undecided .imgwrap').getBoundingClientRect();
          return {x: w.x, y: w.y, w: w.width, h: w.height};
        }""")
        yes = ".cell.t-undecided .badge-yes"
        no = ".cell.t-undecided .badge-no"

        # myš na levou polovinu
        pg.mouse.move(box["x"] + box["w"] * 0.2, box["y"] + box["h"] / 2)
        pg.wait_for_timeout(350)
        ly, ln = op(pg, yes), op(pg, no)
        c.check(ly > 0.9 and ln < 0.3, "vlevo svítí ✓ a ✕ zhasíná",
                f"yes {ly:.2f} / no {ln:.2f}")

        # myš na pravou polovinu
        pg.mouse.move(box["x"] + box["w"] * 0.8, box["y"] + box["h"] / 2)
        pg.wait_for_timeout(350)
        ry, rn = op(pg, yes), op(pg, no)
        c.check(rn > 0.9 and ry < 0.3, "vpravo svítí ✕ a ✓ zhasíná",
                f"yes {ry:.2f} / no {rn:.2f}")

        # mimo dlaždici jsou obě schované
        pg.mouse.move(5, 5)
        pg.wait_for_timeout(350)
        disp = pg.evaluate(
            "s => getComputedStyle(document.querySelector(s)).display", yes)
        c.check(disp == "none", "mimo dlaždici jsou odznaky schované", disp)
        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
