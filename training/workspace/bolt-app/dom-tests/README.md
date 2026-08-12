# dom-tests

Testy, které pouštějí **skutečný headless Chromium** proti běžící labelovací
appce a koukají, co se v ní opravdu stane. Nejde o unit testy — celá appka je
jeden `server.py`, který skládá HTML a JavaScript do f-stringů, takže jediné
místo, kde se dá ověřit, že se ta stránka chová správně, je prohlížeč.

Chytají hlavně chyby, které jsou jinak tiché:

* JS sáhne na prvek, který v hlavičce už není, spadne na `null` a **odejde
  s ním zbytek skriptu** — stránka vypadá skoro normálně, ale přestane fungovat
  označování boltů;
* `/view` se bootuje fetchem `data.json`, a když ten soubor chybí, `r.json()`
  hodí SyntaxError, `Promise.all` se odmítne a **nevykreslí se vůbec nic**;
* dotyková gesta — jestli prst opravdu táhne kolečko a dva prsty mění poloměr
  se nedá zjistit jinak než tím, že se ta gesta pošlou.

## Spuštění

```bash
./run.sh                 # všechny testy
./run.sh test_view.py    # jeden
```

`run.sh` si udělá **kopii databáze**, zvedne nad ní druhou instanci `server.py`
na portu 8002, pustí testy a zase po sobě uklidí.

> Testy posouvají kolečka a ukládají geometrii přes `/api/crop-geom`, takže
> proti ostré DB nemají co dělat. `dt_util.py` proto odmítne běžet, když
> `BASE_URL` míří na port 8001 (přebít jde přes `BOLT_ALLOW_LIVE=1`, ale je to
> skoro vždy chyba — jednou už to přepsalo geometrii reálného boltu).

## Co je potřeba

```bash
pip install --user playwright
playwright install chromium
sudo playwright install-deps chromium   # systémové knihovny
```

Na aarch64 v LXD kontejneru vyžaduje `libatk1.0-0t64`, `libatk-bridge2.0-0t64`,
`libcups2t64`, `libasound2t64`, `libgbm1`, `libcairo2`, `libpango-1.0-0`,
`libxcomposite1`, `libxdamage1`, `libxfixes3`, `libxrandr2`, `libatspi2.0-0t64`.

## Soubory

| soubor | co ověřuje |
|---|---|
| `test_console.py` | hlavní stránky se načtou bez chyb v konzoli a klíčové prvky jsou v DOM |
| `test_touch.py` | prst táhne kolečko o stejné dx/dy, tap přemístí střed, pinch mění poloměr |
| `test_mouse.py` | tažení myší na desktopu funguje a dotykový terč mu nekrade události |
| `test_view.py` | `/view` se vykreslí (obrázek, značky, výřezy) a výřezy jsou ~1:1 |
| `dt_util.py` | společné pomůcky + pojistka proti běhu na ostré DB |
| `jsparse.js` | naparsuje doručený JS (bun/node), odhalí rozbitou syntaxi po zakomentování |

`jsparse.js` je volitelný, potřebuje `bun` nebo `node`:

```bash
curl -s http://127.0.0.1:8002/crops > /tmp/p.html && bun jsparse.js /tmp/p.html
```

## Poznámky k psaní dalších testů

* Po `touchStart` nech **~80 ms** před prvním `touchMove`. Bez toho stihne
  prohlížeč doručit pohyb dřív, než se chytí pointer capture, a gesto vyšumí —
  vypadá to jako že funkce nefunguje, přitom je vadný test.
* V levém a pravém horním rohu náhledu sedí odznaky ✓ a ✕ (na mobilu 60×60 px).
  Tap do nich bolt odznačí a `document.querySelector('.cell.t-bolt')` pak vrátí
  **jinou buňku** — proto si testy hlídají `id` buňky mezi kroky.
* Playwright neumí multi-touch; pinch se posílá přes CDP
  (`Input.dispatchTouchEvent` se dvěma body), viz `touch_sender()`.
