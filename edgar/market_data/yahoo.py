"""Yahoo Finance v8 chart endpoint client.

Pulls monthly adjusted-close history for a ticker over a fixed lookback
window. Used by edgar.metrics.beta for the 5-year monthly β + R²
regression that produces the standard "5 Year Beta" and "5 Year Beta
R-Squared" pair.

Implementation notes:

- HTTP via system `curl` (Windows ships curl.exe in System32 since 1804;
  every modern Linux/macOS has it on PATH). Both Python urllib.request
  and httpx — even with Firefox-mimicking headers — get a hard 429 from
  Yahoo's v8 endpoint, while curl.exe with the exact same headers gets
  200. The difference is the TLS ClientHello fingerprint: Python's
  OpenSSL stack is JA3-blocked, curl's Schannel/native stack isn't.
  Shelling out is the cheapest fix and adds no PyPI dep.
- We pass `--fail-with-body` so curl exits non-zero on HTTP errors and
  we still get the response body for diagnostics.
- 24-hour disk cache at cache/yahoo/{ticker}_{as_of}.json. Keyed by
  as_of so historical reconcile runs against different anchor dates
  stay reproducible without colliding.
- Retries: 3 backoffs (2s/4s/8s). A hard failure raises — silent
  degradation would mask data issues the reconcile script exists to
  surface.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from config.settings import CACHE_DIR

_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Yahoo's edge layer rejects unattended user agents; this string matches
# what a recent desktop Firefox sends and is accepted by v8/chart.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
_TIMEOUT = 30
_RETRY_DELAYS = (2, 4, 8)
_CACHE_TTL = 24 * 60 * 60


@dataclass
class MonthlyBars:
    """Aligned monthly adjusted-close series for one ticker.

    `dates` are ISO-8601 strings (`YYYY-MM-DD`) of the trading day Yahoo
    used as the month bar's anchor — typically the last trading day of
    the month, sometimes the first depending on how the period spans the
    request window. `adjclose` is parallel and never None; rows where
    Yahoo had no observation are dropped before construction.
    """
    ticker: str
    dates: list[str]
    adjclose: list[float]


def fetch_monthly_bars(
    ticker: str,
    as_of: str,
    range_: str = "10y",
    use_cache: bool = True,
) -> MonthlyBars:
    """Return monthly adjusted-close bars for `ticker`, anchored at `as_of`.

    `as_of` is the ISO end-of-window date. Yahoo's `range` is anchored
    at "now", so for any `as_of` in the past we'd lose history from the
    far end after trimming. Defaulting to `range="10y"` gives ample
    headroom for any 5Y window ending up to ~5 years before today, with
    only ~120 rows of payload — caching means we pay once. The
    regression layer trims to the actual window.
    """
    payload = _load_chart(ticker, as_of=as_of, range_=range_, use_cache=use_cache)
    return _bars_from_chart(ticker, payload)


def _cache_path(ticker: str, as_of: str) -> Path:
    safe_ticker = "".join(c if c.isalnum() else "_" for c in ticker)
    safe_as_of = "".join(c if c.isalnum() else "_" for c in as_of)
    return Path(CACHE_DIR) / "yahoo" / f"{safe_ticker}_{safe_as_of}.json"


def _load_chart(
    ticker: str, *, as_of: str, range_: str, use_cache: bool,
) -> dict:
    path = _cache_path(ticker, as_of)
    if use_cache and path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - cached.get("fetched_at", 0) < _CACHE_TTL:
                return cached["chart"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    payload = _http_get_json(ticker, range_=range_)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"fetched_at": time.time(), "chart": payload}, f)
    return payload


def _curl_path() -> str:
    path = shutil.which("curl") or shutil.which("curl.exe")
    if not path:
        raise RuntimeError(
            "system curl not found on PATH; required to bypass Yahoo's "
            "TLS fingerprinting (see module docstring)"
        )
    return path


def _http_get_json(ticker: str, *, range_: str) -> dict:
    """GET v8/chart/{ticker} as JSON via system curl, with retries."""
    qs = urlencode({
        "interval": "1mo",
        "range": range_,
        "includeAdjustedClose": "true",
    })
    url = f"{_CHART_BASE}{ticker}?{qs}"
    curl = _curl_path()

    last_err: Exception | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            proc = subprocess.run(
                [
                    curl, "-sS", "--fail-with-body",
                    "-A", _USER_AGENT,
                    "-H", "Accept: application/json,text/plain,*/*",
                    "-H", "Accept-Language: en-US,en;q=0.9",
                    "--max-time", str(_TIMEOUT),
                    url,
                ],
                capture_output=True, text=True, check=True,
            )
            return json.loads(proc.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
            last_err = e
            if delay is None:
                break
            time.sleep(delay)
    raise RuntimeError(
        f"Yahoo v8 chart fetch failed for {ticker!r} after "
        f"{len(_RETRY_DELAYS) + 1} attempts: {last_err!r}"
    )


def _bars_from_chart(ticker: str, payload: dict) -> MonthlyBars:
    """Extract aligned (date, adjclose) pairs from Yahoo's chart payload.

    Yahoo emits parallel arrays `timestamp` and `indicators.adjclose[0].adjclose`
    (the latter may have `None` entries for halted months). We drop any
    month missing an adjusted close — the regression handles uneven
    series via reindex-and-drop, so silently passing None forward would
    only mask alignment bugs.
    """
    chart = payload.get("chart") or {}
    err = chart.get("error")
    if err:
        raise RuntimeError(f"Yahoo error for {ticker!r}: {err}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo returned empty result for {ticker!r}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    adj_blocks = indicators.get("adjclose") or []
    if not adj_blocks or not timestamps:
        raise RuntimeError(f"Yahoo result missing adjclose for {ticker!r}")
    adj = adj_blocks[0].get("adjclose") or []
    if len(adj) != len(timestamps):
        raise RuntimeError(
            f"Yahoo length mismatch for {ticker!r}: "
            f"{len(timestamps)} timestamps vs {len(adj)} adjclose entries"
        )

    dates: list[str] = []
    closes: list[float] = []
    for ts, px in zip(timestamps, adj):
        if px is None:
            continue
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        dates.append(iso)
        closes.append(float(px))
    return MonthlyBars(ticker=ticker, dates=dates, adjclose=closes)


@dataclass
class SpotQuote:
    """Most recent traded price for a ticker.

    Sourced from the v8 chart endpoint's `meta.regularMarketPrice`,
    which is the latest quote Yahoo has cached at request time.
    `quote_time` is the trade timestamp Yahoo reports.
    """
    ticker: str
    price: float
    currency: str
    quote_time: str  # ISO datetime


def fetch_spot_quote(ticker: str, as_of: str, use_cache: bool = True) -> SpotQuote:
    """Return the latest quote for `ticker`.

    Reuses the same chart payload the 10y/monthly call already cached,
    so this is a free hit when β has already been computed. `as_of` is
    only used as the cache key — the price is always "now-ish" from
    Yahoo's perspective, since the v8 endpoint's meta block doesn't
    carry historical snapshots.
    """
    payload = _load_chart(ticker, as_of=as_of, range_="10y", use_cache=use_cache)
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo returned empty result for {ticker!r}")
    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        raise RuntimeError(f"Yahoo meta missing regularMarketPrice for {ticker!r}")
    ts = meta.get("regularMarketTime")
    quote_time = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if ts else ""
    )
    return SpotQuote(
        ticker=ticker,
        price=float(price),
        currency=str(meta.get("currency") or "USD"),
        quote_time=quote_time,
    )


def trim_to_window(bars: MonthlyBars, as_of: str, months: int = 60) -> MonthlyBars:
    """Trim `bars` to the last `months` observations ending on or before `as_of`.

    Yahoo's `range=5y` is anchored at "now", so historical reconciles
    that ask for an `as_of` in the past need an explicit cut. Returns up
    to `months` rows; fewer is fine — the regression layer enforces a
    minimum sample size.
    """
    cutoff = date.fromisoformat(as_of)
    kept_dates: list[str] = []
    kept_closes: list[float] = []
    for d, c in zip(bars.dates, bars.adjclose):
        if date.fromisoformat(d) > cutoff:
            continue
        kept_dates.append(d)
        kept_closes.append(c)
    if len(kept_dates) > months:
        kept_dates = kept_dates[-months:]
        kept_closes = kept_closes[-months:]
    return MonthlyBars(ticker=bars.ticker, dates=kept_dates, adjclose=kept_closes)
