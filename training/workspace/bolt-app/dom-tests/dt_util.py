"""Sdílené pomůcky pro DOM testy bolt-appky.

Testy mění geometrii boltů (posouvají kolečko, mění poloměr) a ukládají ji
přes /api/crop-geom. Proti ostré databázi se tedy pouštět NESMÍ — od toho je
run.sh, který zvedne izolovanou instanci nad kopií DB.
"""
import os
import sys

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002")
LIVE_PORT = os.environ.get("BOLT_LIVE_PORT", "8001")

if f":{LIVE_PORT}" in BASE_URL and not os.environ.get("BOLT_ALLOW_LIVE"):
    sys.exit(
        f"ODMÍTNUTO: BASE_URL míří na :{LIVE_PORT}, což je ostrá instance.\n"
        f"Testy přepisují geometrii boltů v DB. Pusť je přes dom-tests/run.sh,\n"
        f"který si udělá kopii DB, nebo si vynuť BOLT_ALLOW_LIVE=1."
    )

MOBILE = {"viewport": {"width": 390, "height": 844},
          "is_mobile": True, "has_touch": True}
DESKTOP = {"viewport": {"width": 1400, "height": 900}}


class Checks:
    """Posbírá výsledky a na konci rozhodne o návratovém kódu."""

    def __init__(self, title):
        self.title = title
        self.failed = []

    def check(self, ok, label, detail=""):
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            self.failed.append(label)
        return ok

    def done(self):
        if self.failed:
            print(f"  => {self.title}: {len(self.failed)} FAILED: {', '.join(self.failed)}")
            sys.exit(1)
        print(f"  => {self.title}: OK")


def watch(page, errors):
    """Zaznamenávej chyby stránky, ať nezůstanou nepovšimnuté."""
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.on("requestfailed",
            lambda r: errors.append(f"requestfailed: {r.url}"))
    page.on("response", lambda r: errors.append(f"HTTP {r.status}: {r.url}")
            if r.status >= 400 else None)


def touch_sender(page):
    """Vrátí funkci pro posílání skutečných touch eventů přes CDP.

    Playwright neumí multi-touch, takže se jde přímo na DevTools protokol.
    """
    cdp = page.context.new_cdp_session(page)

    def send(kind, points):
        cdp.send("Input.dispatchTouchEvent", {
            "type": kind,
            "touchPoints": [{"x": x, "y": y, "id": i}
                            for i, (x, y) in enumerate(points)],
        })
    return send


def first_bolt_cell(page):
    """Geometrie první potvrzené buňky. None, když na stránce žádná není."""
    return page.evaluate("""() => {
      const c = document.querySelector('.cell.t-bolt');
      if (!c) return null;
      c.scrollIntoView({block: 'center'});
      const w = c.querySelector('.imgwrap').getBoundingClientRect();
      return {
        id: c.id,
        r: parseFloat(c.querySelector('.s-r').value),
        dx: parseFloat(c.querySelector('.s-x').value),
        dy: parseFloat(c.querySelector('.s-y').value),
        wrap: {x: w.x, y: w.y, w: w.width, h: w.height},
        touchAction: getComputedStyle(c.querySelector('.imgwrap')).touchAction,
      };
    }""")
