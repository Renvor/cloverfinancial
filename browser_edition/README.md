# Browser edition

A single HTML file (`docs/smarttrade-browser.html`) that runs the whole SmartTrade AI dashboard
inside the browser, with no server: the engine is ported to JavaScript (`engine.js`), the HTTP
API is replaced by an in-page router (`localapi.js`), and the bundled SIMULATED sample data is
embedded. State is kept in the browser's local storage.

`verify.js` replays all 28 sample backtests with the JavaScript engine and compares trades,
P&L, rejections and regimes with the Python engine's results in `reference.json`.

```
python browser_edition/export_sample_data.py   # refresh sample_data.json + reference.json
node browser_edition/verify.js                 # must print 28/28 matches
python browser_edition/build.py                # writes docs/smarttrade-browser.html
```

Limits of the browser edition: no data downloads (web pages cannot call Stooq/CoinGecko), and
storage is per browser. The desktop app (`python -m smarttrade`) has no such limits.
