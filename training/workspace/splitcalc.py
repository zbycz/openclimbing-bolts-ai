import zipfile, json, random, sys
sys.path.insert(0, ".")
from slugify import slug_filename

old = json.loads(zipfile.ZipFile("/tmp/bp_v8.zip").read("points.json"))
new = json.load(open("data/bolt-points/points.json"))["photos"]

# exactly how each kernel built its split
of = sorted(old.keys()); random.Random(0).shuffle(of)
old_val = set(of[:max(1, int(len(of) * 0.10))])
old_train = set(of[len(old_val):])

nf = sorted(new.keys()); random.Random(0).shuffle(nf)
new_val = set(nf[:max(1, int(len(nf) * 0.10))])

print("old: %d photos, %d val, %d train" % (len(of), len(old_val), len(old_train)))
print("new: %d photos, %d val" % (len(nf), len(new_val)))

old_train_slugs = {slug_filename(n) for n in old_train}
old_all_slugs = {slug_filename(n) for n in old.keys()}

clean = sorted(s for s in new_val if s not in old_train_slugs)
never_seen = sorted(s for s in new_val if s not in old_all_slugs)
tainted = sorted(s for s in new_val if s in old_train_slugs)

nb = lambda ss: sum(len(new[s]["bolts"]) for s in ss)
ng = lambda ss: sum(len(new[s]["negatives"]) for s in ss)
print()
print("new val photos the old model TRAINED on (unfair to new): %d  (%d bolts)"
      % (len(tainted), nb(tainted)))
print("new val photos the old model never trained on          : %d  (%d bolts, %d negatives)"
      % (len(clean), nb(clean), ng(clean)))
print("   of which entirely absent from the old dataset       : %d  (%d bolts)"
      % (len(never_seen), nb(never_seen)))
json.dump({"clean": clean, "tainted": tainted, "never_seen": never_seen},
          open("data/bolt-points/eval_split.json", "w"), indent=1)
print("\nwritten: data/bolt-points/eval_split.json")
