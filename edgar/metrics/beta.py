"""5-year monthly β + R² regression vs S&P 500.

Produces the standard "5 Year Beta" and "5 Year Beta R-Squared" pair.
β and R² come from the *same* OLS so they're internally consistent —
important downstream, because total-beta uses β/√R² and sourcing the
two from different regressions produces a dimensionally incoherent
ratio.

Methodology:
    r_stock = α + β · r_index + ε
    monthly log returns, 60-month window ending at as_of
    index = ^GSPC (S&P 500), adjusted close
    align by index's date sequence; drop months missing either side

Bottom-up beta chain (unlever → cash-correct → total-beta → relever)
is explicitly out of scope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import log, sqrt

from edgar.market_data.yahoo import MonthlyBars, fetch_monthly_bars, trim_to_window

_INDEX_TICKER = "^GSPC"
_WINDOW_MONTHS = 60
_MIN_OBS = 24  # standard "< 2 years of history → beta not estimated" cutoff


@dataclass
class PeerBeta:
    ticker: str
    n_obs: int
    beta: float | None
    r_squared: float | None
    std_err_beta: float | None
    alpha: float | None
    period_start: str | None
    period_end: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def compute_peer_betas(
    peers: list[str],
    as_of: str | None = None,
    index_ticker: str = _INDEX_TICKER,
) -> list[PeerBeta]:
    """Compute 5Y monthly β + R² for each peer vs the index.

    Returns one `PeerBeta` per peer in input order. Insufficient history
    (<24 monthly returns after alignment) → β/R²/SE/α = None but the row
    is still emitted so callers can see *which* tickers were skipped.
    """
    as_of = as_of or date.today().isoformat()
    index_bars = trim_to_window(
        fetch_monthly_bars(index_ticker, as_of), as_of, months=_WINDOW_MONTHS + 1,
    )
    index_returns = _log_returns(index_bars)

    results: list[PeerBeta] = []
    for ticker in peers:
        try:
            stock_bars = trim_to_window(
                fetch_monthly_bars(ticker, as_of),
                as_of, months=_WINDOW_MONTHS + 1,
            )
            results.append(_regress(ticker, stock_bars, index_returns))
        except Exception as e:
            # Don't poison the whole batch on one ticker; record the failure.
            results.append(PeerBeta(
                ticker=ticker, n_obs=0, beta=None, r_squared=None,
                std_err_beta=None, alpha=None,
                period_start=None, period_end=f"fetch error: {e!r}",
            ))
    return results


def _log_returns(bars: MonthlyBars) -> dict[str, float]:
    """ISO-date -> log return for each bar after the first."""
    out: dict[str, float] = {}
    for i in range(1, len(bars.dates)):
        prev = bars.adjclose[i - 1]
        curr = bars.adjclose[i]
        if prev > 0 and curr > 0:
            out[bars.dates[i]] = log(curr / prev)
    return out


def _regress(ticker: str, stock_bars: MonthlyBars,
             index_returns: dict[str, float]) -> PeerBeta:
    stock_returns = _log_returns(stock_bars)
    # Align on the intersection of return dates.
    common = sorted(set(stock_returns) & set(index_returns))
    if len(common) < _MIN_OBS:
        return PeerBeta(
            ticker=ticker, n_obs=len(common), beta=None, r_squared=None,
            std_err_beta=None, alpha=None,
            period_start=common[0] if common else None,
            period_end=common[-1] if common else None,
        )

    # Cap to the most recent 60 returns (drops anything earlier if Yahoo
    # gave us more headroom than the window).
    common = common[-_WINDOW_MONTHS:]
    y = [stock_returns[d] for d in common]
    x = [index_returns[d] for d in common]
    n = len(common)

    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    sxy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    syy = sum((yi - mean_y) ** 2 for yi in y)
    if sxx == 0 or syy == 0:
        return PeerBeta(
            ticker=ticker, n_obs=n, beta=None, r_squared=None,
            std_err_beta=None, alpha=None,
            period_start=common[0], period_end=common[-1],
        )

    beta = sxy / sxx
    alpha = mean_y - beta * mean_x
    # SS_res = Σ(yi - α - β·xi)² = syy - β·sxy (algebraic identity)
    ss_res = syy - beta * sxy
    r2 = 1.0 - ss_res / syy
    # OLS standard error of β: sqrt(σ²_resid / sxx), σ²_resid = SSR/(n-2)
    sigma2 = ss_res / (n - 2) if n > 2 else 0.0
    se_beta = sqrt(sigma2 / sxx) if sigma2 > 0 and sxx > 0 else 0.0

    return PeerBeta(
        ticker=ticker, n_obs=n, beta=beta, r_squared=r2,
        std_err_beta=se_beta, alpha=alpha,
        period_start=common[0], period_end=common[-1],
    )
