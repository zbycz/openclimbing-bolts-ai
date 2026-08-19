"""Ctrl+kolečko (= pinch na trackpadu) mění poloměr kolečka na desktopu."""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, first_bolt_cell, watch


def main():
    c = Checks("test_wheel")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=bolt", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2500)
        first_bolt_cell(pg)
        pg.wait_for_timeout(400)
        s0 = first_bolt_cell(pg)
        w = s0["wrap"]
        cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] / 2

        def wheel(dy, ctrl):
            pg.evaluate("""([x, y, dy, ctrl]) => {
              const el = document.elementFromPoint(x, y);
              el.dispatchEvent(new WheelEvent('wheel', {
                deltaY: dy, ctrlKey: ctrl, bubbles: true, cancelable: true,
                clientX: x, clientY: y}));
            }""", [cx, cy, dy, ctrl])

        # bez ctrl se nesmí stát nic (stránka musí jít scrollovat)
        wheel(-100, False)
        pg.wait_for_timeout(300)
        s_plain = first_bolt_cell(pg)
        c.check(s_plain["r"] == s0["r"], "kolečko bez Ctrl poloměr nemění",
                f"r {s0['r']} -> {s_plain['r']}")

        # ctrl + nahoru = zvětšit
        for _ in range(5):
            wheel(-40, True)
            pg.wait_for_timeout(60)
        pg.wait_for_timeout(500)
        s_up = first_bolt_cell(pg)
        c.check(s_up["r"] > s0["r"] + 0.4, "Ctrl+kolečko nahoru zvětšuje",
                f"r {s0['r']} -> {s_up['r']}")

        # ctrl + dolů = zmenšit
        for _ in range(8):
            wheel(40, True)
            pg.wait_for_timeout(60)
        pg.wait_for_timeout(500)
        s_dn = first_bolt_cell(pg)
        c.check(s_dn["r"] < s_up["r"] - 0.4, "Ctrl+kolečko dolů zmenšuje",
                f"r {s_up['r']} -> {s_dn['r']}")
        c.check(s_dn["id"] == s0["id"], "pořád tatáž buňka", s_dn["id"])
        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
