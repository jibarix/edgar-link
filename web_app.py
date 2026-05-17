"""
EDGAR Financial Search Tool — Web Interface.

Run:  python web_app.py
Open: http://localhost:5000
"""

import logging
import sys
import os

from flask import Flask, request, jsonify, render_template_string, Response

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from config.settings import LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT
from edgar.company_lookup import search_company, get_company_tickers
from edgar.filing_retrieval import FilingRetrieval
from edgar.xbrl_parser import XBRLParser
from edgar.data_formatter import DataFormatter
from edgar.company_classifier import (
    load_index, query_by_revenue_country, query_by_industry,
    query_by_country_inc, sic_to_industry, INDEX_FILE,
)

# ── Logging ──────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

os.environ['EDGAR_WEB_MODE'] = '1'
app = Flask(__name__)

# Shared instances
_filing = FilingRetrieval()
_parser = XBRLParser()
_company_index = load_index()  # pre-load classification index


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_company_snapshot(cik):
    """Fetch company facts and return a summary dict with key metrics."""
    facts = _filing.get_company_facts(cik)
    if not facts:
        return None

    entity_name = facts.get("entityName", "")

    # Parse a quick IS + BS (latest annual, 1 period) for the summary
    is_data = _parser.parse_company_facts(facts, "IS", "annual", 1)
    bs_data = _parser.parse_company_facts(facts, "BS", "annual", 1)

    def _latest(data, display_name, unit_filter=None):
        if not data or "metrics" not in data:
            return None
        for m in data["metrics"].values():
            if m.get("display_name", "").lower() == display_name.lower():
                if unit_filter and m.get("unit") != unit_filter:
                    continue
                period = data["periods"][0] if data.get("periods") else None
                if period:
                    return m["values"].get(period), m.get("unit", "USD"), period
        return None

    def _fmt(result):
        if result is None:
            return "N/A", ""
        val, unit, period = result
        if val is None:
            return "N/A", period
        if unit in ("USD/shares", "pure"):
            return f"${val:,.2f}", period
        elif unit == "shares":
            return f"{val / 1e6:,.0f}M", period
        else:
            return f"${val / 1e6:,.0f}M", period

    revenue = _latest(is_data, "Net revenue") or _latest(is_data, "Net sales")
    net_income = _latest(is_data, "Net income")
    eps = _latest(is_data, "Diluted", unit_filter="USD/shares")
    gross_margin = _latest(is_data, "Gross margin")
    total_assets = _latest(bs_data, "Total assets")
    total_liab = _latest(bs_data, "Total liabilities")
    equity = _latest(bs_data, "Total shareholders' equity") or _latest(bs_data, "Total equity")

    # Derive period label
    period_raw = ""
    for r in [revenue, net_income, total_assets]:
        if r:
            period_raw = r[2]
            break

    metrics = [
        ("Revenue", *_fmt(revenue)),
        ("Net Income", *_fmt(net_income)),
        ("EPS (Diluted)", *_fmt(eps)),
        ("Gross Margin", *_fmt(gross_margin)),
        ("Total Assets", *_fmt(total_assets)),
        ("Total Liabilities", *_fmt(total_liab)),
        ("Shareholders' Equity", *_fmt(equity)),
    ]

    # Classification data from index
    cik_plain = str(int(cik))  # strip leading zeros for index lookup
    cls = _company_index.get(cik_plain, {})

    return {
        "entity_name": entity_name,
        "cik": cik,
        "period": period_raw,
        "metrics": metrics,
        "industry": cls.get("industry", ""),
        "subindustry": cls.get("subindustry", ""),
        "sic": cls.get("sic", ""),
        "country_inc": cls.get("country_inc", ""),
        "state_inc": cls.get("state_inc", ""),
        "revenue_country": cls.get("revenue_country"),
        "revenue_pct": cls.get("revenue_pct"),
        "geo_breakdown": cls.get("geo_breakdown", {}),
    }


def _generate_report_html(cik, statement_type):
    """Generate an IS or BS report and return the HTML string."""
    facts = _filing.get_company_facts(cik)
    if not facts:
        return None
    entity_name = facts.get("entityName", cik)

    data = _parser.parse_company_facts(facts, statement_type, "annual", 3)
    if not data or not data.get("metrics"):
        return None

    formatter = DataFormatter("html")
    # Build the HTML without writing to disk — reuse _output_html internals
    # Easiest: call format_statement with a temp path, read, delete
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        formatter.format_statement(data, statement_type, entity_name, tmp)
        with open(tmp, "r", encoding="utf-8") as f:
            html = f.read()
        return html
    finally:
        os.unlink(tmp)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(SEARCH_PAGE)


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])

    matches = search_company(q)
    results = []
    seen = set()
    for m in matches[:10]:
        key = m["cik"]
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "cik": m["cik"],
            "name": m["name"],
            "ticker": m["ticker"],
        })
    return jsonify(results)


@app.route("/company/<cik>")
def company_page(cik):
    snapshot = _get_company_snapshot(cik)
    if not snapshot:
        return render_template_string(ERROR_PAGE, msg="Company not found or SEC data unavailable."), 404
    return render_template_string(COMPANY_PAGE, s=snapshot)


@app.route("/api/filters")
def api_filters():
    """Return available filter options derived from the company index."""
    countries_rev = sorted(set(
        v["revenue_country"] for v in _company_index.values() if v.get("revenue_country")
    ))
    countries_inc = sorted(set(
        v["country_inc"] for v in _company_index.values() if v.get("country_inc")
    ))
    industries = sorted(set(
        v["industry"] for v in _company_index.values() if v.get("industry") and v["industry"] != "Unknown"
    ))
    subindustries = sorted(set(
        v["subindustry"] for v in _company_index.values() if v.get("subindustry")
    ))
    return jsonify({"revenue_countries": countries_rev, "inc_countries": countries_inc,
                     "industries": industries, "subindustries": subindustries})


@app.route("/api/browse")
def api_browse():
    """Return companies matching filter criteria."""
    rev = request.args.get("rev_country", "").strip()
    inc = request.args.get("inc_country", "").strip()
    ind = request.args.get("industry", "").strip()
    subind = request.args.get("subindustry", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 50

    results = _company_index
    if rev:
        results = {k: v for k, v in results.items() if v.get("revenue_country") == rev}
    if inc:
        results = {k: v for k, v in results.items() if v.get("country_inc") == inc}
    if ind:
        results = {k: v for k, v in results.items() if v.get("industry") == ind}
    if subind:
        results = {k: v for k, v in results.items() if v.get("subindustry") == subind}

    sorted_items = sorted(results.items(), key=lambda x: x[1]["name"])
    total = len(sorted_items)
    start = (page - 1) * per_page
    page_items = sorted_items[start:start + per_page]

    rows = []
    for cik, info in page_items:
        rows.append({
            "cik": str(cik).zfill(10),
            "name": info["name"],
            "sic": info.get("sic", ""),
            "industry": info.get("industry", ""),
            "subindustry": info.get("subindustry", ""),
            "country_inc": info.get("country_inc", ""),
            "revenue_country": info.get("revenue_country"),
            "revenue_pct": info.get("revenue_pct"),
        })
    return jsonify({"total": total, "page": page, "per_page": per_page, "results": rows})


@app.route("/browse")
def browse_page():
    return render_template_string(BROWSE_PAGE)


@app.route("/company/<cik>/<stmt>")
def report_page(cik, stmt):
    stmt = stmt.upper()
    if stmt not in ("IS", "BS", "CF"):
        return "Invalid statement type", 400
    html = _generate_report_html(cik, stmt)
    if not html:
        return render_template_string(ERROR_PAGE, msg="Could not generate report."), 500
    # Inject a "Back" link at the top
    back_link = f'<a href="/company/{cik}" style="position:fixed;top:12px;right:24px;z-index:99;background:#343a40;color:#fff;padding:6px 16px;border-radius:6px;text-decoration:none;font-size:0.85rem">&larr; Back to summary</a>'
    html = html.replace("<body>", f"<body>{back_link}", 1)
    return Response(html, content_type="text/html")


# ── Templates ────────────────────────────────────────────────────────────────

SEARCH_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EDGAR Financial Search</title>
<style>
  :root { --bg:#f8f9fa; --card:#fff; --border:#dee2e6; --text:#212529; --muted:#6c757d; --accent:#0d6efd; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); }
  .hero {
    display:flex; flex-direction:column; align-items:center; justify-content:center;
    min-height:100vh; padding:2rem;
  }
  .hero h1 { font-size:2rem; font-weight:700; margin-bottom:0.25rem; }
  .hero .sub { color:var(--muted); margin-bottom:2rem; font-size:0.95rem; }
  .browse-link { margin-top:2rem; font-size:0.9rem; }
  .browse-link a { color:var(--accent); text-decoration:none; }
  .browse-link a:hover { text-decoration:underline; }
  .search-wrap { position:relative; width:100%; max-width:560px; }
  .search-wrap input {
    width:100%; padding:0.85rem 1.2rem; font-size:1.05rem;
    border:2px solid var(--border); border-radius:12px; outline:none;
    transition:border-color .2s;
  }
  .search-wrap input:focus { border-color:var(--accent); }
  .results {
    position:absolute; top:calc(100% + 4px); left:0; right:0;
    background:var(--card); border:1px solid var(--border); border-radius:10px;
    box-shadow:0 8px 24px rgba(0,0,0,.1); max-height:360px; overflow-y:auto;
    display:none; z-index:10;
  }
  .results.open { display:block; }
  .results a {
    display:flex; justify-content:space-between; align-items:center;
    padding:0.75rem 1.2rem; text-decoration:none; color:var(--text);
    border-bottom:1px solid #f0f0f0; transition:background .15s;
  }
  .results a:last-child { border-bottom:none; }
  .results a:hover, .results a.active { background:#e7f1ff; }
  .results .name { font-weight:500; }
  .results .ticker { color:var(--accent); font-weight:600; font-size:0.85rem; }
  .results .empty { padding:1rem; text-align:center; color:var(--muted); }
  .spinner { display:none; position:absolute; right:16px; top:50%; transform:translateY(-50%); }
  .spinner.on { display:block; }
  .spinner::after {
    content:''; display:block; width:18px; height:18px;
    border:2px solid var(--border); border-top-color:var(--accent);
    border-radius:50%; animation:spin .6s linear infinite;
  }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<div class="hero">
  <h1>EDGAR Financial Search</h1>
  <div class="sub">Search any US public company by name or ticker</div>
  <div class="search-wrap">
    <input id="q" type="text" placeholder="e.g. Apple, MSFT, Tesla..." autocomplete="off" autofocus>
    <div class="spinner" id="spin"></div>
    <div class="results" id="res"></div>
  </div>
  <div class="browse-link">or <a href="/browse">browse companies by industry, country &amp; revenue source</a></div>
</div>
<script>
const q=document.getElementById('q'), res=document.getElementById('res'), spin=document.getElementById('spin');
let timer=null, active=-1, items=[];

q.addEventListener('input',()=>{
  clearTimeout(timer);
  const v=q.value.trim();
  if(v.length<1){res.classList.remove('open');return;}
  timer=setTimeout(()=>doSearch(v),250);
});

q.addEventListener('keydown',e=>{
  if(!res.classList.contains('open'))return;
  if(e.key==='ArrowDown'){e.preventDefault();active=Math.min(active+1,items.length-1);highlight();}
  else if(e.key==='ArrowUp'){e.preventDefault();active=Math.max(active-1,0);highlight();}
  else if(e.key==='Enter'&&active>=0){e.preventDefault();items[active]?.click();}
  else if(e.key==='Escape'){res.classList.remove('open');}
});

async function doSearch(v){
  spin.classList.add('on');
  try{
    const r=await fetch('/api/search?q='+encodeURIComponent(v));
    const data=await r.json();
    active=-1;
    if(!data.length){res.innerHTML='<div class="empty">No matches found</div>';res.classList.add('open');return;}
    res.innerHTML=data.map(c=>
      `<a href="/company/${c.cik}"><span class="name">${esc(c.name)}</span><span class="ticker">${esc(c.ticker)}</span></a>`
    ).join('');
    items=[...res.querySelectorAll('a')];
    res.classList.add('open');
  }catch(e){console.error(e);}
  finally{spin.classList.remove('on');}
}
function highlight(){items.forEach((a,i)=>a.classList.toggle('active',i===active));}
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}
document.addEventListener('click',e=>{if(!e.target.closest('.search-wrap'))res.classList.remove('open');});
</script>
</body>
</html>"""

COMPANY_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ s.entity_name }} — EDGAR Financial</title>
<style>
  :root { --bg:#f8f9fa; --card:#fff; --border:#dee2e6; --text:#212529; --muted:#6c757d; --accent:#0d6efd; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); padding:2rem; }
  .container { max-width:900px; margin:0 auto; }
  .back { color:var(--accent); text-decoration:none; font-size:0.9rem; display:inline-block; margin-bottom:1rem; }
  .back:hover { text-decoration:underline; }
  .company-header {
    background:var(--card); border:1px solid var(--border); border-radius:12px;
    padding:2rem; margin-bottom:1.5rem;
  }
  .company-header h1 { font-size:1.8rem; font-weight:700; margin-bottom:0.25rem; }
  .company-header .meta { color:var(--muted); font-size:0.9rem; }
  .badges { display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:0.75rem; }
  .badge {
    display:inline-block; padding:0.3rem 0.75rem; border-radius:6px;
    font-size:0.78rem; font-weight:600; letter-spacing:.02em;
  }
  .badge-industry { background:#e8f5e9; color:#2e7d32; }
  .badge-inc { background:#e3f2fd; color:#1565c0; }
  .badge-rev { background:#fff3e0; color:#e65100; }
  .geo-bar { margin-top:1.5rem; margin-bottom:2rem; }
  .geo-bar h3 { font-size:0.9rem; margin-bottom:0.5rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .geo-bar-row { display:flex; align-items:center; margin-bottom:0.35rem; font-size:0.85rem; }
  .geo-bar-label { width:160px; text-align:right; padding-right:0.75rem; color:var(--text); font-weight:500; }
  .geo-bar-track { flex:1; height:20px; background:#e9ecef; border-radius:4px; overflow:hidden; }
  .geo-bar-fill { height:100%; border-radius:4px; background:var(--accent); transition:width .3s; }
  .geo-bar-pct { width:50px; padding-left:0.5rem; color:var(--muted); font-size:0.8rem; }
  .grid {
    display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));
    gap:1rem; margin-bottom:2rem;
  }
  .metric-card {
    background:var(--card); border:1px solid var(--border); border-radius:10px;
    padding:1.25rem 1.5rem;
  }
  .metric-card .label { font-size:0.8rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-bottom:0.3rem; }
  .metric-card .value { font-size:1.35rem; font-weight:700; }
  .metric-card .value.na { color:var(--muted); font-size:1rem; }
  .actions { display:flex; gap:1rem; flex-wrap:wrap; }
  .actions a {
    display:inline-flex; align-items:center; gap:0.5rem;
    padding:0.75rem 1.5rem; border-radius:8px; font-weight:600;
    text-decoration:none; font-size:0.95rem; transition:opacity .2s;
  }
  .actions a:hover { opacity:.85; }
  .btn-is { background:#198754; color:#fff; }
  .btn-bs { background:#0d6efd; color:#fff; }
  .btn-cf { background:#6f42c1; color:#fff; }
</style>
</head>
<body>
<div class="container">
  <a class="back" href="/">&larr; Back to search</a>
  <div class="company-header">
    <h1>{{ s.entity_name }}</h1>
    <div class="meta">CIK: {{ s.cik }}{% if s.country_inc %} &middot; Incorporated in {{ s.country_inc }}{% if s.state_inc %} ({{ s.state_inc }}){% endif %}{% endif %}{% if s.period %} &middot; Latest annual period: {{ s.period }}{% endif %}</div>
    <div class="badges">
      {% if s.industry %}<span class="badge badge-industry">{{ s.industry }}{% if s.subindustry %} &rsaquo; {{ s.subindustry }}{% endif %}</span>{% endif %}
      {% if s.country_inc %}<span class="badge badge-inc">Inc: {{ s.country_inc }}{% if s.state_inc %} ({{ s.state_inc }}){% endif %}</span>{% endif %}
      {% if s.revenue_country %}<span class="badge badge-rev">Rev: {{ s.revenue_country }} ({{ s.revenue_pct }}%)</span>{% endif %}
    </div>
  </div>
  <div class="grid">
    {% for label, value, _ in s.metrics %}
    <div class="metric-card">
      <div class="label">{{ label }}</div>
      <div class="value{% if value == 'N/A' %} na{% endif %}">{{ value }}</div>
    </div>
    {% endfor %}
  </div>
  {% if s.geo_breakdown %}
  <div class="geo-bar">
    <h3>Revenue by Geography</h3>
    {% for geo, pct in s.geo_breakdown|dictsort(by='value', reverse=true) %}
    <div class="geo-bar-row">
      <div class="geo-bar-label">{{ geo }}</div>
      <div class="geo-bar-track"><div class="geo-bar-fill" style="width:{{ pct }}%"></div></div>
      <div class="geo-bar-pct">{{ pct }}%</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  <div class="actions">
    <a class="btn-is" href="/company/{{ s.cik }}/is">Income Statement (3Y)</a>
    <a class="btn-bs" href="/company/{{ s.cik }}/bs">Balance Sheet (3Y)</a>
    <a class="btn-cf" href="/company/{{ s.cik }}/cf">Cash Flow Statement (3Y)</a>
  </div>
</div>
</body>
</html>"""

BROWSE_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Browse Companies — EDGAR Financial</title>
<style>
  :root { --bg:#f8f9fa; --card:#fff; --border:#dee2e6; --text:#212529; --muted:#6c757d; --accent:#0d6efd; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:var(--bg); color:var(--text); padding:2rem; }
  .container { max-width:1100px; margin:0 auto; }
  .back { color:var(--accent); text-decoration:none; font-size:0.9rem; display:inline-block; margin-bottom:1rem; }
  .back:hover { text-decoration:underline; }
  h1 { font-size:1.6rem; font-weight:700; margin-bottom:1.5rem; }
  .filters {
    display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.5rem;
    background:var(--card); border:1px solid var(--border); border-radius:10px; padding:1.25rem;
  }
  .filter-group { display:flex; flex-direction:column; min-width:200px; flex:1; }
  .filter-group label { font-size:0.78rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-bottom:0.3rem; font-weight:600; }
  .filter-group select {
    padding:0.5rem 0.75rem; font-size:0.9rem; border:1px solid var(--border);
    border-radius:6px; background:#fff; color:var(--text); cursor:pointer;
  }
  .summary { color:var(--muted); font-size:0.85rem; margin-bottom:0.75rem; }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:10px; overflow:hidden; }
  th { background:#f1f3f5; text-align:left; padding:0.65rem 1rem; font-size:0.78rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-weight:600; }
  td { padding:0.65rem 1rem; border-top:1px solid #f0f0f0; font-size:0.9rem; }
  tr:hover td { background:#f8f9ff; }
  td a { color:var(--accent); text-decoration:none; font-weight:500; }
  td a:hover { text-decoration:underline; }
  .tag { display:inline-block; padding:0.15rem 0.5rem; border-radius:4px; font-size:0.75rem; font-weight:600; }
  .tag-rev { background:#fff3e0; color:#e65100; }
  .tag-inc { background:#e3f2fd; color:#1565c0; }
  .pager { display:flex; gap:0.5rem; justify-content:center; margin-top:1.25rem; }
  .pager button {
    padding:0.5rem 1rem; border:1px solid var(--border); border-radius:6px;
    background:var(--card); cursor:pointer; font-size:0.85rem; color:var(--text);
  }
  .pager button:hover { background:#e7f1ff; }
  .pager button:disabled { opacity:.4; cursor:default; }
  .pager .page-info { padding:0.5rem 0.75rem; font-size:0.85rem; color:var(--muted); }
  .spinner-sm { display:none; margin-left:0.5rem; }
  .spinner-sm.on { display:inline-block; }
  .spinner-sm::after {
    content:''; display:inline-block; width:14px; height:14px;
    border:2px solid var(--border); border-top-color:var(--accent);
    border-radius:50%; animation:spin .6s linear infinite; vertical-align:middle;
  }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <a class="back" href="/">&larr; Back to search</a>
  <h1>Browse Companies</h1>
  <div class="filters">
    <div class="filter-group">
      <label>Revenue Country (50% rule)</label>
      <select id="fRev"><option value="">All</option></select>
    </div>
    <div class="filter-group">
      <label>Country of Incorporation</label>
      <select id="fInc"><option value="">All</option></select>
    </div>
    <div class="filter-group">
      <label>Industry</label>
      <select id="fInd"><option value="">All</option></select>
    </div>
    <div class="filter-group">
      <label>Sub-Industry</label>
      <select id="fSub"><option value="">All</option></select>
    </div>
  </div>
  <div class="summary" id="summary"></div>
  <span class="spinner-sm" id="spin"></span>
  <table>
    <thead><tr><th>Company</th><th>SIC</th><th>Sub-Industry</th><th>Inc.</th><th>Rev. Country</th></tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <div class="pager">
    <button id="prev" disabled>&laquo; Prev</button>
    <span class="page-info" id="pageInfo"></span>
    <button id="next" disabled>Next &raquo;</button>
  </div>
</div>
<script>
const fRev=document.getElementById('fRev'), fInc=document.getElementById('fInc'),
      fInd=document.getElementById('fInd'), fSub=document.getElementById('fSub'), tbody=document.getElementById('tbody'),
      summary=document.getElementById('summary'), spin=document.getElementById('spin'),
      prevBtn=document.getElementById('prev'), nextBtn=document.getElementById('next'),
      pageInfo=document.getElementById('pageInfo');
let page=1, totalPages=1;

// Load filter options
fetch('/api/filters').then(r=>r.json()).then(d=>{
  d.revenue_countries.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;fRev.appendChild(o);});
  d.inc_countries.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;fInc.appendChild(o);});
  d.industries.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;fInd.appendChild(o);});
  d.subindustries.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;fSub.appendChild(o);});
  browse();
});

fRev.addEventListener('change',()=>{page=1;browse();});
fInc.addEventListener('change',()=>{page=1;browse();});
fInd.addEventListener('change',()=>{page=1;browse();});
fSub.addEventListener('change',()=>{page=1;browse();});
prevBtn.addEventListener('click',()=>{if(page>1){page--;browse();}});
nextBtn.addEventListener('click',()=>{if(page<totalPages){page++;browse();}});

function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

async function browse(){
  spin.classList.add('on');
  const params=new URLSearchParams();
  if(fRev.value)params.set('rev_country',fRev.value);
  if(fInc.value)params.set('inc_country',fInc.value);
  if(fInd.value)params.set('industry',fInd.value);
  if(fSub.value)params.set('subindustry',fSub.value);
  params.set('page',page);
  try{
    const r=await fetch('/api/browse?'+params);
    const d=await r.json();
    totalPages=Math.max(1,Math.ceil(d.total/d.per_page));
    summary.textContent=`${d.total} companies found`;
    pageInfo.textContent=`Page ${d.page} of ${totalPages}`;
    prevBtn.disabled=page<=1;
    nextBtn.disabled=page>=totalPages;
    if(!d.results.length){
      tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:#6c757d;padding:2rem">No companies match these filters</td></tr>';
      return;
    }
    tbody.innerHTML=d.results.map(c=>{
      const rev=c.revenue_country?`<span class="tag tag-rev">${esc(c.revenue_country)} ${c.revenue_pct}%</span>`:'—';
      const inc=c.country_inc?`<span class="tag tag-inc">${esc(c.country_inc)}</span>`:'—';
      return `<tr>
        <td><a href="/company/${c.cik}">${esc(c.name)}</a></td>
        <td>${esc(c.sic)}</td>
        <td>${esc(c.subindustry||c.industry)}</td>
        <td>${inc}</td>
        <td>${rev}</td>
      </tr>`;
    }).join('');
  }catch(e){console.error(e);}
  finally{spin.classList.remove('on');}
}
</script>
</body>
</html>"""

ERROR_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Error — EDGAR Financial</title>
<style>
  body { font-family:-apple-system,sans-serif; display:flex; align-items:center; justify-content:center; min-height:100vh; background:#f8f9fa; }
  .box { text-align:center; }
  .box h1 { font-size:1.5rem; margin-bottom:.5rem; }
  .box p { color:#6c757d; }
  .box a { color:#0d6efd; }
</style>
</head>
<body><div class="box"><h1>Something went wrong</h1><p>{{ msg }}</p><p style="margin-top:1rem"><a href="/">Back to search</a></p></div></body>
</html>"""


if __name__ == "__main__":
    print("\n  EDGAR Financial Search Tool")
    print("  http://localhost:5000\n")
    app.run(debug=True, port=5000)
