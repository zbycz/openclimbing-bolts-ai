import csv

def load(p):
    rows = list(csv.DictReader(open(p)))
    k = {c: [h for h in rows[0] if c in h][0] for c in
         ("mAP50(B)", "mAP50-95(B)", "precision(B)", "recall(B)")}
    ke = [h for h in rows[0] if h.strip() == "epoch"][0]
    kt = [h for h in rows[0] if h.strip() == "time"][0]
    out = []
    for r in rows:
        out.append(dict(epoch=int(float(r[ke])), t=float(r[kt]),
                        m50=float(r[k["mAP50(B)"]]), m95=float(r[k["mAP50-95(B)"]]),
                        p=float(r[k["precision(B)"]]), rc=float(r[k["recall(B)"]])))
    return out

new = load("data/model/results.csv")
old = load("data/model-v1-2026-07-16/results.csv")

for name, rs in (("previous (2026-07-16)", old), ("this run (2026-09-06)", new)):
    b = max(rs, key=lambda r: r["m50"])
    last = rs[-1]
    print("%s" % name)
    print("   epochs run      : %d" % last["epoch"])
    print("   wall time       : %.2f h  (%.0f s/epoch)"
          % (last["t"] / 3600, last["t"] / last["epoch"]))
    print("   best mAP50      : %.3f  at epoch %d" % (b["m50"], b["epoch"]))
    print("   at that epoch   : mAP50-95 %.3f  P %.3f  R %.3f" % (b["m95"], b["p"], b["rc"]))
    print("   final epoch     : mAP50 %.3f  mAP50-95 %.3f" % (last["m50"], last["m95"]))
    print()
