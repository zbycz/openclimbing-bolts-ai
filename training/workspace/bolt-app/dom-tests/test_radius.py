"""Poloměr jde vytáhnout výš, než dovoloval starý strop 30."""
from playwright.sync_api import sync_playwright

from dt_util import BASE_URL, DESKTOP, Checks, first_bolt_cell, watch


def main():
    c = Checks("test_radius")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(**DESKTOP)
        errors = []
        watch(pg, errors)
        pg.goto(f"{BASE_URL}/crops?show=bolt&size=huge", wait_until="networkidle",
                timeout=60000)
        pg.wait_for_timeout(2500)
        first_bolt_cell(pg)
        pg.wait_for_timeout(400)
        s0 = first_bolt_cell(pg)

        lim = pg.evaluate("""() => {
          const i = document.querySelector('.cell.t-bolt .s-r');
          return {max: parseFloat(i.max), min: parseFloat(i.min),
                  step: parseFloat(i.step)};
        }""")
        c.check(lim["max"] >= 100, "strop slideru je aspoň 100", str(lim["max"]))
        c.check(lim["step"] <= 0.1, "krok je jemný", str(lim["step"]))

        # vytáhni poloměr nad starý strop a nech uložit
        pg.evaluate("""() => {
          const c = document.querySelector('.cell.t-bolt');
          const i = c.querySelector('.s-r');
          i.value = '62.5';
          i.dispatchEvent(new Event('input', {bubbles: true}));
        }""")
        pg.wait_for_timeout(900)
        s1 = first_bolt_cell(pg)
        c.check(s1["r"] > 30, "poloměr přes starý strop drží", f"r = {s1['r']}")

        # a kružnice v SVG opravdu vyrostla
        rr = pg.evaluate("""() => {
          const c = document.querySelector('.cell.t-bolt');
          return parseFloat(c.querySelector('.ovl circle').getAttribute('r'));
        }""")
        c.check(rr > 30, "kružnice se překreslila na velký poloměr", f"svg r = {rr}")

        # přežije to reload (uložilo se do DB)
        pg.reload(wait_until="networkidle")
        pg.wait_for_timeout(1500)
        s2 = first_bolt_cell(pg)
        c.check(s2["r"] > 30, "po reloadu je hodnota z DB", f"r = {s2['r']}")
        c.check(not errors, "žádné chyby v konzoli", "; ".join(errors[:3]))
        b.close()
    c.done()


if __name__ == "__main__":
    main()
