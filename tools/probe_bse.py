"""One-off probe: which BSE endpoints answer from GitHub Actions."""
import requests, json
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
                  "Accept": "application/json, text/plain, */*", "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"})
urls = [
 "https://www.bseindia.com/markets/equity/EQReports/bulk_deals.aspx",
 "https://www.bseindia.com/markets/equity/EQReports/block_deals.aspx",
 "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w?flag=1",
 "https://api.bseindia.com/BseIndiaAPI/api/BlockDealData/w?flag=1",
 "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active",
 "https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w?quotetype=EQ&scripcode=513228&seriesid=",
]
out = {}
for u in urls:
    try:
        r = s.get(u, timeout=40)
        t = r.text
        out[u] = {"status": r.status_code, "len": len(t), "head": t[:300]}
        if "aspx" in u:
            import re
            out[u]["tables"] = re.findall(r'<table[^>]*id="([^"]+)"', t)[:10]
            i = t.find("Client Name"); out[u]["around"] = re.sub(r"\s+", " ", t[i-400:i+1500]) if i > 0 else ""
    except Exception as e:
        out[u] = {"error": str(e)}
open("data/bse_probe.json", "w").write(json.dumps(out, indent=1))
print(json.dumps(out, indent=1)[:4000])
