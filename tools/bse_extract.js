// Run on https://www.bseindia.com/markets/equity/eqreports/bulk_deals (or block_deals). Returns the deal rows.
await new Promise(r => setTimeout(r, 2500));
[...document.querySelectorAll("tr")].map(r => [...r.children].map(c => c.innerText.trim()))
  .filter(c => c.length === 7 && /^\d/.test(c[0]) && /^\d{6}$/.test(c[1]) && /^[BS]/i.test(c[4]))
