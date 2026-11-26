# Big Money Tracker data

Collects NSE bulk and block deals and quarterly shareholding filings every weeknight on GitHub Actions (10:10 PM IST, with a 7:10 AM retry). A Claude scheduled task then copies new data into the Big Money Tracker website.

- `collect.py`: the collector
- `portfolio.json`: the companies to fetch shareholders for (synced from the website)
- `data/days/`: one file per trading day of deals
- `data/holders/`: one file per company, the latest two quarterly filings
- `data/index.json`: last run time, status and notes

Run it by hand: Actions tab, "Nightly NSE collection", Run workflow.
