import requests, time
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
                  "Accept": "application/json, text/plain, */*", "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"})
urls = [
 "https://www.bseindia.com/markets/equity/EQReports/bulk_deals.aspx",
 "https://www.bseindia.com/markets/equity/EQReports/block_deals.aspx",
 "https://api.bseindia.com/BseIndiaAPI/api/BulknBlockDeals/w?Flag=1",
 "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w?flag=0",
 "https://api.bseindia.com/BseIndiaAPI/api/SASTData/w?scripcode=500180",
 "https://api.bseindia.com/BseIndiaAPI/api/InsiderTrade15/w?scripcode=500180",
 "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate=20261120&strScrip=500180&strSearch=P&strToDate=20261127&strType=C&subcategory=-1",
 "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active",
 "https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Debtflag=&scripcode=500180&seriesid=",
 "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_20261126_F_0000.CSV",
]
s.get("https://www.bseindia.com/", timeout=30)
for u in urls:
    try:
        r = s.get(u, timeout=30)
        print(r.status_code, len(r.text), u, "|", r.text[:220].replace("\n", " "))
    except Exception as e:
        print("ERR", u, e)
    time.sleep(1)
