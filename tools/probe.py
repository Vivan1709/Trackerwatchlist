import json, sys, time
sys.path.insert(0, ".")
from collect import NSE
n = NSE()
tests = [
 "/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=01-11-2026&to=24-11-2026",
 "/api/historicalOR/bulk-block-short-deals?optionType=block_deals&from=01-11-2026&to=24-11-2026",
 "/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&symbol=WELCORP&from=26-11-2025&to=24-11-2026",
 "/api/corporates-pit?index=equities&symbol=HDFCBANK&from_date=26-08-2026&to_date=26-11-2026",
 "/api/corporates-pit?index=equities&symbol=HDFCBANK",
 "/api/corporates-pit?index=equities&from_date=01-11-2026&to_date=26-11-2026",
 "/api/corporate-sast-reg29?index=equities&symbol=HDFCBANK",
]
for t in tests:
    try:
        r = n.api(t); d = r.get("data", r) if isinstance(r, dict) else r
        print("OK", t, type(r).__name__, (list(r.keys())[:6] if isinstance(r, dict) else ""), "rows:", len(d) if isinstance(d, list) else "?")
        if isinstance(d, list) and d: print("   ", json.dumps(d[0])[:400])
    except Exception as e:
        print("FAIL", t, str(e)[:200])
    time.sleep(1.5)
