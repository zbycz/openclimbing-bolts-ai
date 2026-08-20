"""Dotykové ovládání zeleného kolečka na stránce s výřezy.

Kontroluje tři věci, které jinak nejde ověřit jinak než skutečným prstem:
  1. tažení jedním prstem odkudkoliv nad náhledem posune kolečko o TOTÉŽ dx/dy
  2. krátký tap pořád skočí středem na místo dotyku
  3. dva prsty (pinch) mění poloměr

Pozor na rohy náhledu: v levém/pravém horním rohu sedí odznaky ✓ a ✕ (na
mobilu 60x60 px) a tap do nich bolt odznačí — testy se jim vyhýbají.
"""
import sys

from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, MOBILE, Checks, first_bolt_cell, touch_sender, watch

# Náhled je OUTV viewBox jednotek široký a SC jednotek je 1 původní pixel,
# takže OUTV/SC původních pixelů na celou šířku. Drženo stejně jako server.
OUTV, SC = 200, 5
SHIFT = 40          # o kolik px displeje táhneme prstem
HOLD_WAIT = 450     # HOLD_MS v appce je 320; držíme s rezervou
TAP_PX = 10         # práh tapu v appce


def main():
    c = Checks("test_touch")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**MOBILE)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=bolt", wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2500)

        s0 = first_bolt_cell(pg)
        if not s0:
            sys.exit("  na stránce není žádný potvrzený bolt — nemám co testovat")
        pg.wait_for_timeout(500)
        s0 = first_bolt_cell(pg)
        touch = touch_sender(pg)
        w = s0["wrap"]

        c.check(s0["touchAction"] == "pan-y", "potvrzený náhled má touch-action:pan-y",
                s0["touchAction"])

        # 1. tažení z levého horního rohu, tedy daleko od kolečka
        sx, sy = w["x"] + 12, w["y"] + 12
        touch("touchStart", [(sx, sy)])
        pg.wait_for_timeout(HOLD_WAIT)      # podržet, aby se tažení nabilo
        for i in range(1, 9):
            touch("touchMove", [(sx + SHIFT * i / 8, sy + SHIFT * i / 8)])
            pg.wait_for_timeout(35)
        touch("touchEnd", [])
        pg.wait_for_timeout(700)
        s1 = first_bolt_cell(pg)

        expected = SHIFT * OUTV / w["w"] / SC
        got_x, got_y = s1["dx"] - s0["dx"], s1["dy"] - s0["dy"]
        c.check(s1["id"] == s0["id"], "pořád tatáž buňka", s1["id"])
        c.check(abs(got_x - expected) < 1.0 and abs(got_y - expected) < 1.0,
                "tažení z rohu posunulo kolečko o stejné dx/dy",
                f"čekáno {expected:.2f}, dx {got_x:.2f} dy {got_y:.2f}")

        # během tažení smí být vidět jediná kružnice a nesmí být vybarvená
        touch("touchStart", [(w["x"] + 12, w["y"] + 12)])
        pg.wait_for_timeout(HOLD_WAIT)
        touch("touchMove", [(w["x"] + 40, w["y"] + 40)])
        pg.wait_for_timeout(120)
        vis = pg.evaluate("""() => {
          const cell = document.querySelector('.cell.t-bolt');
          const circles = cell.querySelectorAll('.ovl circle');
          const cs = getComputedStyle(circles[0]);
          return {n: circles.length,
                  fill: cs.fill,
                  dragging: cell.querySelector('.ovl').classList.contains('dragging'),
                  extras: cell.querySelectorAll('.grab, .gactive').length};
        }""")
        touch("touchEnd", [])
        pg.wait_for_timeout(500)
        c.check(vis["n"] == 1, "při tažení je jedna kružnice", f"{vis['n']} ks")
        c.check(vis["extras"] == 0, "žádný druhý kruh navíc", f"{vis['extras']} prvků")
        c.check("none" in vis["fill"] or "rgba(0, 0, 0, 0)" in vis["fill"],
                "střed není vybarvený", vis["fill"])
        c.check(vis["dragging"], "tažení je znát na kružnici (třída dragging)")

        # 2. krátký tap dole uprostřed (mimo odznaky) skočí středem na dotyk
        touch("touchStart", [(w["x"] + w["w"] * 0.5, w["y"] + w["h"] * 0.8)])
        pg.wait_for_timeout(60)
        touch("touchEnd", [])
        pg.wait_for_timeout(700)
        s2 = first_bolt_cell(pg)
        c.check(s2["id"] == s0["id"], "tap netrefil odznak", s2["id"])
        c.check(abs(s2["dy"] - s1["dy"]) > 2 or abs(s2["dx"] - s1["dx"]) > 2,
                "tap přemístil střed", f"dx {s2['dx']} dy {s2['dy']}")

        # 3. pinch od sebe zvětší poloměr
        cx, cy = w["x"] + w["w"] / 2, w["y"] + w["h"] * 0.7
        touch("touchStart", [(cx - 20, cy), (cx + 20, cy)])
        pg.wait_for_timeout(120)
        for i in range(1, 7):
            d = 20 + 7 * i
            touch("touchMove", [(cx - d, cy), (cx + d, cy)])
            pg.wait_for_timeout(35)
        touch("touchEnd", [])
        pg.wait_for_timeout(700)
        s3 = first_bolt_cell(pg)
        c.check(s3["r"] > s2["r"] + 0.4, "pinch zvětšil poloměr",
                f"r {s2['r']} -> {s3['r']}")

        # 4. rychlé přejetí prstem přes výřez = scroll stránky, bod stojí
        s_before = first_bolt_cell(pg)
        fx, fy = w["x"] + w["w"] * 0.5, w["y"] + w["h"] * 0.5
        touch("touchStart", [(fx, fy)])
        for i in range(1, 7):           # hned se hýbat, žádné podržení
            touch("touchMove", [(fx, fy - 12 * i)])
            pg.wait_for_timeout(20)
        touch("touchEnd", [])
        pg.wait_for_timeout(700)
        s_after = first_bolt_cell(pg)
        c.check(s_after["dx"] == s_before["dx"] and s_after["dy"] == s_before["dy"],
                "rychlé přejetí (scroll) bodem nehne",
                f"({s_before['dx']}, {s_before['dy']}) -> ({s_after['dx']}, {s_after['dy']})")
        c.check(s_after["touchAction"] == "pan-y",
                "náhled nechává svislý scroll prohlížeči", s_after["touchAction"])

        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
