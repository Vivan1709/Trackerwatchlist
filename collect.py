"""
Nightly NSE collector for Big Money Tracker.

Runs on GitHub Actions. Writes:
  data/days/YYYY-MM-DD.json   bulk + block deals for that trading day
  data/holders/SYMBOL.json    named 1%+ shareholders from the latest two quarterly filings
  data/index.json             what exists, when it last ran, and any problems
Reads:
  portfolio.json              {"symbols": ["TCS", ...]}  (kept in sync from the website)

Deal row format: [symbol, company, client, "B"|"S", qty, price, "U" bulk | "K" block]
Holder row format: [name, pct, filing group]
"""

import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MONTHS = {m: f"{i:02d}" for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/report-detail/display-bulk-and-block-deals",
}
MAX_HOLDER_SYMBOLS = 60


def iso(d: str) -> str:
    dd, mm, yy = str(d).split("-")[:3]
    return f"{yy[:4]}-{MONTHS[mm[:3].upper()]}-{int(dd):02d}"


def load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.base = "https://www.nseindia.com"
        r = self.s.get(self.base + "/report-detail/display-bulk-and-block-deals", timeout=30)
        self.base = f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"   # NSE may redirect to nseindia.in
        time.sleep(2)

    def api(self, path: str):
        for attempt in range(3):
            r = self.s.get(self.base + path, timeout=30)
            if r.status_code == 200 and r.text.lstrip().startswith(("{", "[")):
                return r.json()
            time.sleep(4 * (attempt + 1))
            if attempt == 1:
                self.s.get(self.base + "/", timeout=30)
        raise RuntimeError(f"{path}: HTTP {r.status_code}")

    def text(self, url: str) -> str:
        r = self.s.get(url, timeout=60)
        r.raise_for_status()
        return r.text


def collect_deals(nse: NSE, index: dict, notes: list) -> None:
    snap = nse.api("/api/snapshot-capital-market-largedeal")
    days = {}
    for key, kind in (("BULK_DEALS_DATA", "U"), ("BLOCK_DEALS_DATA", "K")):
        for r in snap.get(key) or []:
            try:
                row = [r["symbol"].strip(), (r.get("name") or "").strip(), " ".join(str(r["clientName"]).split()),
                       "B" if str(r["buySell"]).upper().startswith("B") else "S", int(float(r["qty"])), float(r["watp"]), kind]
            except (KeyError, ValueError, TypeError):
                continue
            if row[0] and row[2] and row[4] > 0 and row[5] > 0:
                days.setdefault(iso(r.get("date") or snap["as_on_date"]), []).append(row)
    for d, rows in days.items():
        path = DATA / "days" / f"{d}.json"
        if not path.exists():
            save(path, {"rows": rows, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            notes.append(f"{d}: {len(rows)} deals")
        if d not in index["days"]:
            index["days"].append(d)
    if not days:
        notes.append("no deals published (holiday?)")


def parse_xbrl(xml_text: str) -> list:
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    facts = {}
    for el in root.iter():
        ref = el.get("contextRef")
        if not ref:
            continue
        name = el.tag.rsplit("}", 1)[-1]
        f = facts.setdefault(ref[2:] if ref.startswith("D_") else ref, {})
        if name == "NameOfTheShareholder" and el.text:
            f["h"] = " ".join(el.text.split())
        elif name == "ShareholdingAsAPercentageOfTotalNumberOfShares" and "p" not in f and el.text:
            try:
                f["p"] = float(el.text)
            except ValueError:
                pass
    rows = [[f["h"], round(f["p"] * 100, 2), key.split("_Context")[0]]
            for key, f in facts.items() if "h" in f and "p" in f and 1 <= f["p"] * 100 <= 100]
    return sorted(rows, key=lambda r: -r[1])


def collect_holders(nse: NSE, index: dict, notes: list) -> None:
    symbols = [s.strip().upper() for s in load(ROOT / "portfolio.json", {}).get("symbols", []) if s.strip()]
    done = 0
    for sym in symbols:
        if done >= MAX_HOLDER_SYMBOLS:
            notes.append("holder limit reached; rest next run")
            break
        path = DATA / "holders" / f"{sym}.json"
        stored = load(path, {"q": []})
        try:
            filings = nse.api(f"/api/corporate-share-holdings-master?index=equities&symbol={sym}")
            filings = [f for f in (filings if isinstance(filings, list) else filings.get("data", [])) if f.get("xbrl")][:2]
            if not filings:
                continue
            latest = iso(filings[0]["date"])
            if stored["q"] and stored["q"][0].get("period") == latest:
                continue
            have = {q["period"]: q for q in stored["q"]}
            quarters = []
            for f in filings:
                period = iso(f["date"])
                if period in have:
                    quarters.append(have[period])
                    continue
                rows = parse_xbrl(nse.text(f["xbrl"]))
                try:
                    promoter = float(f.get("pr_and_prgrp"))
                except (TypeError, ValueError):
                    promoter = None
                quarters.append({"period": period, "filed": f.get("broadcastDate"), "promoter": promoter, "rows": rows})
                time.sleep(1)
            save(path, {"q": quarters})
            if sym not in index["holders"]:
                index["holders"].append(sym)
            notes.append(f"holders {sym} {latest}")
            done += 1
        except Exception as e:  # keep going for other symbols
            notes.append(f"holders {sym} failed: {e}")
        time.sleep(1)


def main() -> int:
    index = load(DATA / "index.json", {"days": [], "holders": []})
    notes, ok = [], True
    try:
        nse = NSE()
        collect_deals(nse, index, notes)
        collect_holders(nse, index, notes)
    except Exception as e:
        ok = False
        notes.append(f"failed: {e}")
    index["days"] = sorted(set(index["days"]))
    index["holders"] = sorted(set(index["holders"]))
    index.update(lastRun=datetime.now(timezone.utc).isoformat(timespec="seconds"), ok=ok, note="; ".join(notes)[:500])
    save(DATA / "index.json", index)
    print(json.dumps({"ok": ok, "notes": notes}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
