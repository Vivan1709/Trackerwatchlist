"""Save BSE bulk/block deals read from bseindia.com (on the user's computer) into data/bse/raw/<date>.json.
Usage: python tools/bse_save.py bulk.json block.json   (each a JSON list of 7-cell rows)"""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
MONTHS = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6, JUL=7, AUG=8, SEP=9, OCT=10, NOV=11, DEC=12)
def day(s):
    s = s.strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m: return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    m = re.match(r"(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -](\d{2,4})", s)
    if m: y = int(m[3]); return f"{y + 2000 if y < 100 else y}-{MONTHS[m[2].upper()]:02d}-{int(m[1]):02d}"
num = lambda s: float(s.replace(",", "")) if s.replace(",", "").replace(".", "").isdigit() else None
out = {}
for path, kind in zip(sys.argv[1:3], ("BU", "BK")):
    for c in json.load(open(path)):
        d, q, p = day(c[0]), num(c[5]), num(c[6])
        if d and q and p:
            out.setdefault(d, []).append([c[1], c[2], " ".join(c[3].split()), "B" if c[4].upper().startswith("B") else "S", int(q), p, kind])
root = Path(__file__).resolve().parent.parent / "data" / "bse" / "raw"
root.mkdir(parents=True, exist_ok=True)
for d, rows in out.items():
    f = root / f"{d}.json"
    old = json.loads(f.read_text()) if f.exists() else {"rows": []}
    have = {json.dumps(r) for r in old["rows"]}
    old["rows"] += [r for r in rows if json.dumps(r) not in have]
    old["fetched"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    f.write_text(json.dumps(old, separators=(",", ":")))
    print(d, len(old["rows"]), "rows")
