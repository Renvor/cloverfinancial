"""Assemble the browser edition: one HTML page with the dashboard, the JS engine, the local API and the sample data."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "smarttrade" / "web"
HERE = Path(__file__).resolve().parent

css = (ROOT / "styles.css").read_text()
app = (ROOT / "app.js").read_text()
engine = (HERE / "engine.js").read_text()
localapi = (HERE / "localapi.js").read_text()
data = (HERE / "sample_data.json").read_text()
index = (ROOT / "index.html").read_text()

# Route the dashboard's api() helper to the in-page API instead of fetch().
old = app[app.index("  async function api(path, opts = {}) {"): app.index("  function toast(msg")]
app = app.replace(old, "  async function api(path, opts = {}) { return window.localApi(path, opts); }\n")
assert "fetch(" not in app

body = index[index.index("<body>") + len("<body>"): index.index("</body>")]
body = body.replace('<script src="/static/app.js"></script>', "")
body = body.replace('<button class="link" id="glossary-btn">Glossary</button>',
                    '<button class="link" id="glossary-btn">Glossary</button>\n        <div class="pill pill-blue">Browser edition</div>')

extra_css = """
/* Browser edition: dark-first single theme, phone layout */
:root { color-scheme: dark; }
html, body { background: var(--bg); }
.content { padding: 16px 16px 60px; }
@media (max-width: 900px) {
  .sidebar { padding: 10px 12px; gap: 8px; }
  .brand { margin-bottom: 4px; width: 100%; }
  nav a { padding: 7px 10px; font-size: 13px; }
  .topbar { padding: 10px 16px; }
  .page-title { font-size: 17px; }
  .content { padding: 14px 16px 60px; }
  .card { padding: 12px 14px; }
  .stat { font-size: 22px; }
  table { font-size: 12px; }
  th, td { padding: 6px 5px; }
  .card:has(> table), .card:has(> details > table) { overflow-x: auto; }
  details table, .card > table { min-width: 560px; }
  .kv { grid-template-columns: 1fr; }
  .row > label.field, .row > input, .row > select { width: 100%; }
  .row > button.btn { width: 100%; }
  .candidate .head { flex-direction: column; }
}
svg.chart text { fill: #8b98a8; }
"""

html = f"""<title>SmartTrade AI</title>
<style>
{css}
{extra_css}
</style>
{body}
<script>window.SAMPLE_DATA = {data};</script>
<script>
{engine}
</script>
<script>
{localapi}
</script>
<script>
{app}
</script>
"""
out = HERE.parent / "docs" / "smarttrade-browser.html"
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB)")
