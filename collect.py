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

import json, os
import re
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
            body = r.text.lstrip("\ufeff \r\n\t")
            if r.status_code == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    last = body[:160].replace("\n", " ")
            time.sleep(4 * (attempt + 1))
            if attempt == 1:
                self.s.get(self.base + "/", timeout=30)
        raise RuntimeError(f"{path}: HTTP {r.status_code} {locals().get('last', '')}")

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
        if load(path, {}).get("rows") != rows:
            save(path, {"rows": rows, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            notes.append(f"{d}: {len(rows)} deals")
        if d not in index["days"]:
            index["days"].append(d)
    if not days:
        notes.append("no deals published (holiday?)")


def backfill(nse: NSE, index: dict, notes: list, days_back: int = 365) -> None:
    """Fill past trading days from NSE's historical bulk/block deal reports, 30 days per request."""
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=days_back)
    added = {}
    while end > start:
        frm = max(start, end - timedelta(days=29))
        for kind, path in (("U", "bulk-deals"), ("K", "block-deals")):
            try:
                data = nse.api(f"/api/historical/{path}?from={frm:%d-%m-%Y}&to={end:%d-%m-%Y}")
            except Exception as e:
                notes.append(f"backfill {path} {frm}: {e}")
                continue
            for r in (data.get("data") if isinstance(data, dict) else data) or []:
                try:
                    d = iso(r["BD_DT_DATE"])
                    row = [r["BD_SYMBOL"].strip(), (r.get("BD_SCRIP_NAME") or "").strip(), " ".join(str(r["BD_CLIENT_NAME"]).split()),
                           "B" if str(r["BD_BUY_SELL"]).upper().startswith("B") else "S", int(float(r["BD_QTY_TRD"])), float(r["BD_TP_WATP"]), kind]
                except (KeyError, ValueError, TypeError):
                    continue
                if row[0] and row[2] and row[4] > 0 and row[5] > 0:
                    added.setdefault(d, []).append(row)
            time.sleep(2)
        end = frm - timedelta(days=1)
    for d, rows in added.items():
        path = DATA / "days" / f"{d}.json"
        if not path.exists():
            save(path, {"rows": rows, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "historical"})
        if d not in index["days"]:
            index["days"].append(d)
    notes.append(f"backfill: {len(added)} days, {sum(map(len, added.values()))} deals")
    if added:
        index["backfilled"] = True


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _date(x):
    """NSE dates come as 25-Nov-2026, 25-NOV-2026 12:00:00 or 2026-11-25."""
    s = str(x or "").strip()
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    try:
        return iso(s.split()[0])
    except Exception:
        return None


def collect_deal_history(nse: NSE, symbols: list, index: dict, notes: list) -> None:
    """Every bulk and block deal in each portfolio company over the last year (NSE's history endpoint, per symbol), once a day."""
    from datetime import date, timedelta
    done = index.setdefault("histAt", {})
    frm, to = date.today() - timedelta(days=365), date.today()
    for sym in symbols:
        if done.get(sym) == str(to):
            continue
        add, failed = {}, False
        for kind, opt in (("U", "bulk_deals"), ("K", "block_deals")):
            try:
                res = nse.api(f"/api/historicalOR/bulk-block-short-deals?optionType={opt}&symbol={sym}&from={frm:%d-%m-%Y}&to={to:%d-%m-%Y}")
            except Exception as e:
                failed = True
                notes.append(f"history {sym} {opt} failed: {str(e)[:60]}")
                continue
            for r in res.get("data") or []:
                try:
                    row = [r["BD_SYMBOL"].strip(), (r.get("BD_SCRIP_NAME") or "").strip(), " ".join(str(r["BD_CLIENT_NAME"]).split()),
                           "B" if str(r["BD_BUY_SELL"]).upper().startswith("B") else "S", int(float(r["BD_QTY_TRD"])), float(r["BD_TP_WATP"]), kind]
                    add.setdefault(iso(r["BD_DT_DATE"]), []).append(row)
                except (KeyError, ValueError, TypeError):
                    continue
            time.sleep(1)
        rows = sorted([[day] + r for day, rs in add.items() for r in rs], key=lambda r: r[0], reverse=True)
        n = len(rows)
        if not failed or rows:
            save(DATA / "deals" / f"{sym}.json", {"rows": rows, "at": str(to)})
        if not failed:
            done[sym] = str(to)
        notes.append(f"history {sym}: {n} deals in a year")


def collect_latest_filings(nse: NSE, notes: list) -> None:
    """Newest insider trades and 5%+ stake filings across the whole market, merged into each company's file as they appear."""
    by = {}
    for kind, path in (("Insider", "corporates-pit"), ("SAST", "corporate-sast-reg29")):
        try:
            res = nse.api(f"/api/{path}?index=equities")
        except Exception as e:
            notes.append(f"latest {kind} failed: {str(e)[:60]}")
            continue
        for r in (res.get("data", []) if isinstance(res, dict) else res) or []:
            sym = str(r.get("symbol") or "").strip().upper()
            if not sym:
                continue
            if kind == "Insider":
                qty, val = _num(r.get("secAcq")), _num(r.get("secVal"))
                row = {"src": "Insider", "date": _date(r.get("acqfromDt") or r.get("date")), "reported": _date(r.get("intimDt") or r.get("date")),
                       "who": " ".join(str(r.get("acqName") or "").split()), "cat": r.get("personCategory") or "",
                       "type": r.get("tdpTransactionType") or "", "mode": r.get("acqMode") or "", "sec": r.get("secType") or "",
                       "qty": qty, "value": val, "price": round(val / qty, 2) if qty and val else None,
                       "before": _num(r.get("befAcqSharesPer")), "after": _num(r.get("afterAcqSharesPer"))}
            else:
                acq, sale = _num(r.get("noOfShareAcq")) or 0, _num(r.get("noOfShareSale")) or 0
                row = {"src": "SAST", "date": _date(r.get("acquirerDate") or r.get("date") or r.get("timestamp")), "reported": _date(r.get("timestamp")),
                       "who": " ".join(str(r.get("acquirerName") or "").split()), "cat": r.get("promoterType") or "",
                       "type": r.get("acqSaleType") or ("Sale" if sale > acq else "Acquisition"), "mode": r.get("acquisitionMode") or "",
                       "sec": "", "qty": (acq or sale) or None, "value": None, "price": None, "before": None, "after": _num(r.get("totAftShare"))}
            if row["date"] and row["who"]:
                by.setdefault(sym, []).append(row)
        time.sleep(1)
    k = lambda r: (r["src"], r["date"], r["who"], r["qty"], r["type"])
    changed = 0
    for sym, rows in by.items():
        path = DATA / "txns" / f"{sym}.json"
        old = load(path, {})
        have = {k(r) for r in old.get("rows", [])}
        new = [r for r in rows if k(r) not in have]
        if not new:
            continue
        merged = sorted(new + old.get("rows", []), key=lambda r: r["date"], reverse=True)[:400]
        save(path, {**old, "rows": merged, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        if (DATA / "co" / f"{sym}.json").exists():
            build_co(sym)
        changed += 1
    notes.append(f"latest filings: {sum(map(len, by.values()))} seen, {changed} companies updated")


def merge_bse(nse: NSE, index: dict, notes: list) -> None:
    """BSE bulk/block deals (read on the owner's computer into data/bse/raw) rebuilt into the same day and company files as NSE's.
    Rebuilt from scratch on every run, so a better NSE/BSE match fixes earlier rows by itself."""
    import csv, io
    from datetime import date, timedelta
    raw = sorted((DATA / "bse" / "raw").glob("*.json")) if (DATA / "bse" / "raw").exists() else []
    if not raw:
        return
    isin_of, name_of = {}, {}
    for back in range(0, 8):
        d = date.today() - timedelta(days=back)
        try:
            r = requests.get(f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV", timeout=60,
                             headers={"User-Agent": HEADERS["User-Agent"], "Referer": "https://www.bseindia.com/"})
            r.raise_for_status()
        except Exception:
            continue
        text = r.content.decode("utf-8-sig", "replace")
        if not text.startswith("TradDt") or text.count("\n") < 500:
            continue   # not published yet for that day (BSE answers with a web page)
        for row in csv.DictReader(io.StringIO(text)):
            code = str(row.get("FinInstrmId") or "").strip()
            if code:
                isin_of[code], name_of[code] = str(row.get("ISIN") or "").strip(), str(row.get("FinInstrmNm") or "").strip()
        if isin_of:
            break
    if not isin_of:
        notes.append("BSE merge waiting: BSE price file unavailable")
        return
    nse_of = load(DATA / "isin.json", {})
    nse_syms = {c[0] for c in load(DATA / "companies.json", [])}
    bse_only, per_sym, total, matched = {}, {}, 0, 0
    for f in raw:
        path = DATA / "days" / f"{f.stem}.json"
        cur = load(path, {"rows": []})
        rows = [r for r in cur["rows"] if r[-1] not in ("BU", "BK")]
        for code, ticker, client, side, qty, price, kind in load(f, {}).get("rows", []):
            sym = nse_of.get(isin_of.get(code, ""), "")
            name = name_of.get(code) or ticker
            if sym:
                matched += 1
            else:
                sym = ticker if ticker not in nse_syms else f"{ticker}-BSE"
                bse_only[sym] = name
            row = [sym, name, client, side, qty, price, kind]
            if row not in rows:
                rows.append(row)
                per_sym.setdefault(sym, []).append([f.stem] + row)
                total += 1
        cur["rows"] = rows
        cur.setdefault("fetched", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        save(path, cur)
        index["days"].append(f.stem)
    for co in (DATA / "co").glob("*.json"):
        c = load(co, {})
        old = c.get("d", [])
        new = [r for r in old if r[-1] not in ("BU", "BK")] + per_sym.get(co.stem, [])
        if new != old:
            c["d"] = sorted(new, key=lambda r: r[0], reverse=True)[:150]
            save(co, c)
    save(DATA / "bse" / "companies.json", sorted([s, n] for s, n in bse_only.items()))
    notes.append(f"BSE deals: {total}, {matched} matched to NSE companies")


def collect_txns(nse: NSE, symbols: list, index: dict, notes: list, light: bool = False) -> None:
    """Insider (SEBI PIT) and substantial-acquisition (SAST Reg 29) disclosures, last 365 days, per company."""
    from datetime import date, timedelta
    for sym in symbols:
        old = load(DATA / "txns" / f"{sym}.json", {})
        full = not old or (old.get("fullAt", "")[:10] != str(date.today()))
        windows, end = [], date.today()
        for _ in range(0 if light else 4 if full else 1):
            windows.append((end - timedelta(days=90), end))
            end -= timedelta(days=91)
        rows, errs, seen = [], [], set()
        for frm, to in windows + [(None, None)]:
            for kind, path in (("Insider", "corporates-pit"), ("SAST", "corporate-sast-reg29")):
                try:
                    q = f"&from_date={frm:%d-%m-%Y}&to_date={to:%d-%m-%Y}" if frm else ""
                    res = nse.api(f"/api/{path}?index=equities&symbol={sym}{q}")
                except Exception as e:
                    errs.append(f"{kind} {frm}: {str(e)[:60]}")
                    continue
                for r in (res.get("data", []) if isinstance(res, dict) else res) or []:
                    key = json.dumps(r, sort_keys=True, default=str)
                    if key in seen:
                        continue
                    seen.add(key)
                    if kind == "Insider":
                        qty, val = _num(r.get("secAcq")), _num(r.get("secVal"))
                        rows.append({"src": "Insider", "date": _date(r.get("acqfromDt") or r.get("date")), "reported": _date(r.get("intimDt") or r.get("date")),
                                     "who": " ".join(str(r.get("acqName") or "").split()), "cat": r.get("personCategory") or "",
                                     "type": r.get("tdpTransactionType") or "", "mode": r.get("acqMode") or "", "sec": r.get("secType") or "",
                                     "qty": qty, "value": val, "price": round(val / qty, 2) if qty and val else None,
                                     "before": _num(r.get("befAcqSharesPer")), "after": _num(r.get("afterAcqSharesPer"))})
                    else:
                        acq, sale = _num(r.get("noOfShareAcq")) or 0, _num(r.get("noOfShareSale")) or 0
                        rows.append({"src": "SAST", "date": _date(r.get("acquirerDate") or r.get("date") or r.get("timestamp")), "reported": _date(r.get("timestamp")),
                                     "who": " ".join(str(r.get("acquirerName") or "").split()), "cat": r.get("promoterType") or "",
                                     "type": r.get("acqSaleType") or ("Sale" if sale > acq else "Acquisition"), "mode": r.get("acquisitionMode") or "",
                                     "sec": "", "qty": (acq or sale) or None, "value": None, "price": None,
                                     "before": None, "after": _num(r.get("totAftShare"))})
                time.sleep(0.8)
        rows = [r for r in rows if r["date"] and r["who"]]
        if not full:  # keep older rows from the last full fetch
            k = lambda r: (r["src"], r["date"], r["who"], r["qty"], r["type"])
            have = {k(r) for r in rows}
            rows += [r for r in old.get("rows", []) if k(r) not in have]
        rows.sort(key=lambda r: r["date"], reverse=True)
        rows = rows[:400]
        if (rows or len(errs) < 8) and rows != old.get("rows"):
            save(DATA / "txns" / f"{sym}.json", {"rows": rows, "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                 "fullAt": datetime.now(timezone.utc).isoformat(timespec="seconds") if full else old.get("fullAt")})
            if sym not in index.setdefault("txns", []):
                index["txns"].append(sym)
        notes.append(f"txns {sym}: {len(rows)}" + (f" ({len(errs)} failed requests: {errs[0]})" if errs else ""))


def parse_xbrl(xml_text) -> dict:
    """Named 1%+ holders, plus the filing's own category totals (every *_ContextI percentage) and shareholder count."""
    root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    facts, totals, counts = {}, {}, {}
    for el in root.iter():
        ref = el.get("contextRef")
        if not ref or el.text is None:
            continue
        name = el.tag.rsplit("}", 1)[-1]
        text = el.text.strip()
        if name == "ShareholdingAsAPercentageOfTotalNumberOfShares" and ref.endswith("_ContextI"):
            try:
                totals[ref[:-len("_ContextI")]] = round(float(text) * 100, 4)
            except ValueError:
                pass
        if name in ("NumberOfShareholders", "NumberOfShareHolders") and ref.endswith("_ContextI"):
            try:
                counts[ref[:-len("_ContextI")]] = int(float(text))
            except ValueError:
                pass
        f = facts.setdefault(ref[2:] if ref.startswith("D_") else ref, {})
        if name == "NameOfTheShareholder":
            f["h"] = " ".join(text.split())
        elif name == "ShareholdingAsAPercentageOfTotalNumberOfShares" and "p" not in f:
            try:
                f["p"] = float(text)
            except ValueError:
                pass
    rows = [[f["h"], round(f["p"] * 100, 2), key.split("_Context")[0]]
            for key, f in facts.items() if "h" in f and "p" in f and 1 <= f["p"] * 100 <= 100]
    total_holders = max(counts.values()) if counts else None
    return {"rows": sorted(rows, key=lambda r: -r[1]), "totals": totals, "shareholders": total_holders}


def portfolio_symbols() -> list:
    only = [s.strip().upper() for s in os.environ.get("ONLY_SYMBOLS", "").split(",") if s.strip()]
    return only or [s.strip().upper() for s in load(ROOT / "portfolio.json", {}).get("symbols", []) if s.strip()]


def collect_holders(nse: NSE, index: dict, notes: list, symbols: list = None) -> None:
    symbols = portfolio_symbols() if symbols is None else symbols
    done = 0
    for sym in symbols:
        if done >= MAX_HOLDER_SYMBOLS:
            notes.append("holder limit reached; rest next run")
            break
        path = DATA / "holders" / f"{sym}.json"
        stored = load(path, {"q": []})
        try:
            filings = nse.api(f"/api/corporate-share-holdings-master?index=equities&symbol={sym}")
            seen, uniq = set(), []
            for f in [f for f in (filings if isinstance(filings, list) else filings.get("data", [])) if f.get("xbrl")]:
                p = iso(f["date"])
                if p not in seen:          # first listed = latest submission for that quarter (revisions included)
                    seen.add(p)
                    uniq.append(f)
            filings = sorted(uniq, key=lambda f: iso(f["date"]), reverse=True)[:5]
            if not filings:
                continue
            latest = iso(filings[0]["date"])
            if (stored["q"] and stored["q"][0].get("period") == latest and "totals" in stored["q"][0]
                    and len(stored["q"]) >= min(5, len(filings))):
                continue
            have = {q["period"]: q for q in stored["q"]}
            quarters = []
            for f in filings:
                period = iso(f["date"])
                if period in have and "totals" in have[period]:
                    quarters.append(have[period])
                    continue
                parsed = parse_xbrl(nse.text(f["xbrl"]))
                try:
                    promoter = float(f.get("pr_and_prgrp"))
                except (TypeError, ValueError):
                    promoter = None
                quarters.append({"period": period, "filed": f.get("broadcastDate"), "promoter": promoter, "public": _num(f.get("public_val")),
                                 "rows": parsed["rows"], "totals": parsed["totals"], "shareholders": parsed["shareholders"]})
                time.sleep(1)
            save(path, {"q": quarters})
            if sym not in index["holders"]:
                index["holders"].append(sym)
            notes.append(f"holders {sym} {latest}")
            done += 1
        except Exception as e:  # keep going for other symbols
            notes.append(f"holders {sym} failed: {e}")
        time.sleep(1)


def collect_companies(nse: NSE, index: dict, notes: list) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if index.get("companiesAt") == today and (DATA / "isin.json").exists():
        return
    import csv, io
    host = nse.base.replace("://www.", "://nsearchives.")
    rows = list(csv.reader(io.StringIO(nse.text(host + "/content/equities/EQUITY_L.csv"))))
    out, isins = [], {}
    for r in rows[1:]:
        if len(r) >= 2 and r[0].strip():
            name = r[1].strip()
            for suffix in (" Limited", " Ltd.", " Ltd"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
            out.append([r[0].strip(), name])
            if len(r) > 6 and r[6].strip():
                isins[r[6].strip()] = r[0].strip()
    if len(out) > 1500:
        save(DATA / "companies.json", out)
        save(DATA / "isin.json", isins)
        index["companiesAt"] = today
        notes.append(f"company list: {len(out)}")


def collect_prices(nse: NSE, symbols: list, notes: list) -> None:
    """Live quote when NSE allows it; otherwise the official closing price from NSE's daily bhavcopy."""
    from datetime import date, timedelta
    prices, missing = load(DATA / "prices.json", {}), []
    for sym in symbols:
        try:
            nse.s.get(f"{nse.base}/get-quotes/equity?symbol={sym}", timeout=30)
            old = nse.s.headers.get("Referer")
            nse.s.headers["Referer"] = f"{nse.base}/get-quotes/equity?symbol={sym}"
            try:
                q = nse.api(f"/api/quote-equity?symbol={sym}")
            finally:
                nse.s.headers["Referer"] = old
            p = q.get("priceInfo", {})
            hl = p.get("weekHighLow", {}) or {}
            prices[sym] = {"price": p.get("lastPrice"), "change": p.get("change"), "pct": p.get("pChange"),
                           "prev": p.get("previousClose"), "high52": hl.get("max"), "low52": hl.get("min"),
                           "time": q.get("metadata", {}).get("lastUpdateTime") or "", "live": True}
        except Exception:
            missing.append(sym)
        time.sleep(0.6)
    if missing:
        import csv, io
        host = nse.base.replace("://www.", "://nsearchives.")
        for back in range(0, 7):
            d = date.today() - timedelta(days=back)
            try:
                text = nse.text(f"{host}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
            except Exception:
                continue
            rows = {r[0].strip(): r for r in csv.reader(io.StringIO(text)) if len(r) > 8 and r[1].strip() == "EQ"}
            for sym in missing:
                r = rows.get(sym)
                if r:
                    close, prev = _num(r[8]), _num(r[3])
                    old = prices.get(sym, {})
                    prices[sym] = {"price": close, "prev": prev, "change": round(close - prev, 2) if close and prev else None,
                                   "pct": round((close / prev - 1) * 100, 2) if close and prev else None,
                                   "high52": old.get("high52"), "low52": old.get("low52"),
                                   "time": f"close {d:%d %b %Y}", "live": False}
            notes.append(f"prices: {len(symbols) - len(missing)} live, {len(missing)} from {d:%d %b} close")
            break
        else:
            notes.append(f"prices failed for {', '.join(missing)}")
    save(DATA / "prices.json", prices)


ALLOT = ("allot", "preferential", "qip", "qualified institution", "warrant", "rights issue", "private placement", "esop", "employee stock")


def collect_corporate(nse: NSE, symbols: list, index: dict, notes: list) -> None:
    """Promoter pledges and share allotments (preferential issues, QIPs, warrants), per company, once a day."""
    from datetime import date, timedelta
    today = str(date.today())
    for sym in symbols:
        path = DATA / "corp" / f"{sym}.json"
        old = load(path, {})
        if old.get("day") == today:
            continue
        out = {"day": today, "pledge": old.get("pledge", []), "allot": old.get("allot", []), "errors": []}
        try:
            res = nse.api(f"/api/corporate-pledgedata?index=equities&symbol={sym}")
            items = res.get("data", []) if isinstance(res, dict) else res
            if items and "pledge_keys" not in index:
                index["pledge_keys"] = sorted(items[0].keys())
            out["pledge"] = [{k: r.get(k) for k in r if k not in ("anex", "attachment", "xbrl")} for r in (items or [])][:12]
        except Exception as e:
            out["errors"].append(f"pledge: {str(e)[:80]}")
        time.sleep(0.8)
        try:
            frm, to = date.today() - timedelta(days=730), date.today()
            res = nse.api(f"/api/corporate-announcements?index=equities&symbol={sym}&from_date={frm:%d-%m-%Y}&to_date={to:%d-%m-%Y}")
            items = res.get("data", []) if isinstance(res, dict) else res
            keep = []
            for r in items or []:
                text = " ".join(str(r.get(k) or "") for k in ("desc", "attchmntText", "smIndustry", "subject"))
                low = text.lower()
                if (re.search(r"\b(allot|allotment|allotted|preferential|qip|qualified institution\w*|warrants?|rights issue|private placement|esop|esos|employee stock)\b", low)
                        and not re.search(r"postal ballot|newspaper|scrutini", low)):
                    keep.append({"date": _date(r.get("an_dt") or r.get("sort_date") or r.get("dt")), "subject": r.get("desc") or "",
                                 "text": str(r.get("attchmntText") or "")[:600], "file": r.get("attchmntFile") or ""})
            out["allot"] = sorted(keep, key=lambda r: r["date"] or "", reverse=True)[:40]
        except Exception as e:
            out["errors"].append(f"announcements: {str(e)[:80]}")
        time.sleep(0.8)
        if out["pledge"] or out["allot"] or not out["errors"]:
            save(path, out)
            if sym not in index.setdefault("corp", []):
                index["corp"].append(sym)
        notes.append(f"corp {sym}: {len(out['pledge'])} pledge rows, {len(out['allot'])} allotments" + (f" ({'; '.join(out['errors'])})" if out["errors"] else ""))


def build_co(sym: str, price: dict = None) -> None:
    """Everything the website shows for one company, in one small file."""
    h = load(DATA / "holders" / f"{sym}.json", {}).get("q", [])
    t = load(DATA / "txns" / f"{sym}.json", {}).get("rows", [])
    c = load(DATA / "corp" / f"{sym}.json", {})
    d = load(DATA / "deals" / f"{sym}.json", {}).get("rows", [])
    old = load(DATA / "co" / f"{sym}.json", {})
    save(DATA / "co" / f"{sym}.json", {"h": h, "t": t[:60], "c": {"pledge": c.get("pledge", [])[:2], "allot": c.get("allot", [])[:15]},
                                       "d": d[:150], "p": price or old.get("p"), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def bhav_all(nse: NSE) -> dict:
    """Latest official close for every NSE equity, from the daily bhavcopy, with traded value for ranking."""
    import csv, io
    from datetime import date, timedelta
    host = nse.base.replace("://www.", "://nsearchives.")
    for back in range(0, 7):
        d = date.today() - timedelta(days=back)
        try:
            text = nse.text(f"{host}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
        except Exception:
            continue
        out = {}
        for r in csv.reader(io.StringIO(text)):
            if len(r) > 11 and r[1].strip() == "EQ":
                close, prev = _num(r[8]), _num(r[3])
                out[r[0].strip()] = {"price": close, "prev": prev, "change": round(close - prev, 2) if close and prev else None,
                                     "pct": round((close / prev - 1) * 100, 2) if close and prev else None,
                                     "time": f"close {d:%d %b %Y}", "live": False, "tv": _num(r[11]) or 0}
        if out:
            return out
    return {}


def cover(shard: int, shards: int, limit: int) -> int:
    """Background: keep every NSE company collected, most traded first, refreshing each one weekly."""
    from datetime import timedelta
    nse = NSE()
    bhav = bhav_all(nse)
    ranked = sorted(bhav, key=lambda s: -bhav[s]["tv"])
    mine = [s for i, s in enumerate(ranked) if i % shards == shard]
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    todo = [s for s in mine if load(DATA / "co" / f"{s}.json", {}).get("at", "") < week][:limit]
    notes, index = [], {"days": [], "holders": []}
    started = time.time()
    for s in todo:
        if time.time() - started > 17 * 60:
            break
        try:
            collect_holders(nse, index, notes, [s])
            collect_deal_history(nse, [s], {}, notes)
            collect_txns(nse, [s], index, notes, light=True)
            collect_corporate(nse, [s], index, notes)
            p = dict(bhav.get(s, {})); p.pop("tv", None)
            build_co(s, p)
        except Exception as e:
            notes.append(f"{s} failed: {e}")
    if len(todo) >= limit or time.time() - started > 17 * 60:
        Path("/tmp/more").write_text("1")   # more companies left: the workflow starts another pass
    print(f"shard {shard}: {len(todo)} to do, notes: {notes[-5:]}")
    return 0


def main() -> int:
    index = load(DATA / "index.json", {"days": [], "holders": []})
    notes, ok = [], True
    try:
        nse = NSE()
        quick = bool(os.environ.get("ONLY_SYMBOLS", "").strip())   # a company was just added: fetch only that, fast
        if not quick:
            collect_deals(nse, index, notes)
            try:
                merge_bse(nse, index, notes)
            except Exception as e:
                notes.append(f"BSE merge failed: {e}")
            try:
                collect_latest_filings(nse, notes)
            except Exception as e:
                notes.append(f"latest filings failed: {e}")
            try:
                collect_companies(nse, index, notes)
            except Exception as e:
                notes.append(f"company list failed: {e}")
        index["backfilled"] = True
        collect_holders(nse, index, notes)
        syms = portfolio_symbols()
        collect_deal_history(nse, syms[:MAX_HOLDER_SYMBOLS], index, notes)
        collect_txns(nse, syms[:MAX_HOLDER_SYMBOLS], index, notes)
        collect_prices(nse, syms[:MAX_HOLDER_SYMBOLS], notes)
        collect_corporate(nse, syms[:MAX_HOLDER_SYMBOLS], index, notes)
        prices = load(DATA / "prices.json", {})
        for s in syms[:MAX_HOLDER_SYMBOLS]:
            build_co(s, prices.get(s))
    except Exception as e:
        ok = False
        notes.append(f"failed: {e}")
    index["days"] = sorted(set(index["days"]))
    index["holders"] = sorted(set(index["holders"]))
    index["txns"] = sorted(set(index.get("txns", [])))
    index["corp"] = sorted(set(index.get("corp", [])))
    index.update(lastRun=datetime.now(timezone.utc).isoformat(timespec="seconds"), ok=ok, note="; ".join(notes)[:500])
    save(DATA / "index.json", index)
    print(json.dumps({"ok": ok, "notes": notes}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cover":
        sys.exit(cover(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
    sys.exit(main())
