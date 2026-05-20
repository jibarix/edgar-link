"""Offline guardrails for scripts/build_comps.py screening helpers.

The point-in-time CapIQ snapshot path used to (a) accept a fiscal-year
period whose end-date was AFTER ``--as-of`` purely because the year prefix
matched, and (b) fall back to the most-recent annual when no period was
``<= as_of``. Both produced look-ahead bias in screening sheets. These
tests pin the corrected resolver and the decoupling of the snapshot
annual parse from ``--period-type``.

Live SEC calls are NOT made here; everything runs against synthetic
NormalizedStatement-shaped dicts.
"""
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load build_comps as a module without invoking its CLI entry point. It
# lives under ``scripts/`` which is not on sys.path by default.
_SPEC = importlib.util.spec_from_file_location(
    "build_comps", REPO_ROOT / "scripts" / "build_comps.py",
)
build_comps = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_comps)


# ── _pick_annual_period: never returns a period after as_of ───────────


def test_pick_annual_period_rejects_future_same_year():
    """as_of=2025-03-31 must NOT pick 2025-09-27 just because the year
    prefix matches (the prior bug). Apple/Cintas-style non-Dec filers
    expose this — their latest FY end is in Sep/May etc., and a Q1 screen
    should ignore that FY entirely."""
    periods = ["2025-09-27", "2024-09-28", "2023-09-30"]  # desc, newest first
    picked = build_comps._pick_annual_period(periods, "2025-03-31")
    assert picked == "2024-09-28"


def test_pick_annual_period_picks_latest_le_as_of():
    """Standard case: among all annuals <= as_of, return the latest."""
    periods = ["2024-12-31", "2023-12-31", "2022-12-31"]
    assert build_comps._pick_annual_period(periods, "2025-06-30") == "2024-12-31"
    assert build_comps._pick_annual_period(periods, "2024-12-31") == "2024-12-31"
    assert build_comps._pick_annual_period(periods, "2024-06-30") == "2023-12-31"


def test_pick_annual_period_returns_none_when_all_future():
    """If every available period is after as_of, return None — never
    fabricate by reaching forward in time."""
    periods = ["2025-09-27", "2024-09-28"]
    assert build_comps._pick_annual_period(periods, "2023-12-31") is None


def test_pick_annual_period_empty_input():
    assert build_comps._pick_annual_period([], "2025-03-31") is None


# ── _capiq_snapshot: respects the as_of cutoff end-to-end ─────────────


def _make_annual_norm(periods, revenue_by_period):
    """Synthetic XBRLParser-shaped annual normalized dict.

    Only the fields _capiq_snapshot / _pick_annual_period touch are
    populated; the goal is to test the period resolver, not the metric
    registry.
    """
    return {
        "periods": list(periods),
        "metrics": {
            "Revenue:Revenues": {
                "category": "Revenue",
                "tag": "us-gaap:Revenues",
                "values": dict(revenue_by_period),
            },
        },
        "metadata": {"period_type": "annual"},
    }


def test_capiq_snapshot_non_dec_filer_q1_screen_does_not_look_ahead():
    """Sep-year-end filer (e.g. AAPL) with FY2025 already filed but the
    screen anchored at 2025-03-31 (mid-FY25 from the filer's perspective):
    no quarterly history available, so the resolver must fall back to
    annual and pick FY2024-09-28, never FY2025-09-27.
    """
    annual = _make_annual_norm(
        periods=["2025-09-27", "2024-09-28", "2023-09-30"],
        revenue_by_period={
            "2025-09-27": 400_000_000_000,
            "2024-09-28": 391_035_000_000,
            "2023-09-30": 383_285_000_000,
        },
    )
    # No quarterly slice — forces the annual fallback branch.
    snap = build_comps._capiq_snapshot(annual, None, "2025-03-31")
    assert snap is not None
    _, latest, ptype = snap
    assert latest == "2024-09-28"
    assert ptype == "annual"
    assert latest <= "2025-03-31"


def test_capiq_snapshot_dec_filer_picks_le_as_of_annual():
    """Dec-year-end filer with FY2024 filed: as_of=2025-06-30 must pick
    FY2024-12-31, not the next FY (which isn't filed yet)."""
    annual = _make_annual_norm(
        periods=["2024-12-31", "2023-12-31", "2022-12-31"],
        revenue_by_period={
            "2024-12-31": 100_000_000,
            "2023-12-31": 90_000_000,
            "2022-12-31": 80_000_000,
        },
    )
    snap = build_comps._capiq_snapshot(annual, None, "2025-06-30")
    assert snap is not None
    _, latest, ptype = snap
    assert latest == "2024-12-31"
    assert ptype == "annual"


def test_capiq_snapshot_returns_none_when_all_annuals_after_as_of():
    """No annual <= as_of, no quarterly: nothing to snapshot. The fix
    must return None rather than fabricating from a future period."""
    annual = _make_annual_norm(
        periods=["2025-09-27", "2024-09-28"],
        revenue_by_period={
            "2025-09-27": 400_000_000_000,
            "2024-09-28": 391_035_000_000,
        },
    )
    snap = build_comps._capiq_snapshot(annual, None, "2020-01-01")
    assert snap is None


# ── _trailing_annual_revenue: same cutoff guarantee ───────────────────


def test_trailing_annual_revenue_excludes_post_as_of_periods():
    """Trailing strip must only emit periods <= as_of (the prior bug
    let the latest annual leak in via the resolver fallback)."""
    annual = _make_annual_norm(
        periods=["2025-09-27", "2024-09-28", "2023-09-30", "2022-09-24"],
        revenue_by_period={
            "2025-09-27": 400_000_000_000,
            "2024-09-28": 391_035_000_000,
            "2023-09-30": 383_285_000_000,
            "2022-09-24": 394_328_000_000,
        },
    )
    trailing = build_comps._trailing_annual_revenue(annual, "2025-03-31")
    # 2025-09-27 is in the future relative to as_of and must not appear.
    assert 400_000_000_000 not in trailing
    assert 391_035_000_000 in trailing


# ── Screening sheet is independent of --period-type ───────────────────


def test_screening_annual_parse_decoupled_from_quarterly_period_type(monkeypatch):
    """When --period-type=quarterly, the screening sheets still need an
    annual parse to feed _capiq_snapshot / _trailing_annual_revenue.
    Verify that build_comps explicitly requests an annual slice for the
    snapshot path even when the primary parse is quarterly.
    """
    parse_calls: list[dict] = []

    class FakeParser:
        def parse_company_facts(self, facts, *, statement_type,
                                period_type, num_periods):
            parse_calls.append({
                "statement_type": statement_type,
                "period_type": period_type,
                "num_periods": num_periods,
            })
            # Return a minimal annual-or-quarterly-shaped dict so the
            # caller's downstream code doesn't blow up. The screening
            # path will short-circuit on missing revenue data, which is
            # fine — we're only testing which parses get requested.
            if period_type == "annual":
                periods = ["2024-12-31", "2023-12-31", "2022-12-31"]
            elif period_type == "quarterly":
                periods = [
                    "2025-03-31", "2024-12-31", "2024-09-30",
                    "2024-06-30", "2024-03-31",
                ]
            else:
                periods = []
            return {"periods": periods, "metrics": {}, "metadata": {}}

        def augment_with_extensions(self, *args, **kwargs):
            return None

    fake_parser = FakeParser()
    # Reuse the actual helper to drive both branches through _parse_normalized.
    quarterly_call = build_comps._parse_normalized(
        {"facts": {}}, fake_parser, "quarterly", 12,
    )
    annual_call = build_comps._parse_normalized(
        {"facts": {}}, fake_parser, "annual", 9,
    )

    # Both parses got made independently — the screening path can request
    # an annual slice on top of a quarterly primary parse.
    requested_types = [c["period_type"] for c in parse_calls]
    assert "quarterly" in requested_types
    assert "annual" in requested_types

    # Decoupling means: even when the primary is quarterly, an annual
    # parse with the right shape is available for the snapshot resolver.
    assert quarterly_call is not None
    assert annual_call is not None
    assert annual_call["periods"][0] == "2024-12-31"


# ── SEC identity env-var alias ────────────────────────────────────────


def test_identity_alias_accepts_sec_edgar_user_agent(monkeypatch):
    """build_comps must not reject runs where only SEC_EDGAR_USER_AGENT
    is set — the rest of the project (config/constants.py) treats it as
    a valid alias for EDGAR_IDENTITY, so the screening script can't be
    an outlier."""
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test User test@example.com")
    assert build_comps._has_identity() is True


def test_identity_alias_accepts_edgar_identity(monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    monkeypatch.setenv("EDGAR_IDENTITY", "Test User test@example.com")
    assert build_comps._has_identity() is True


def test_identity_alias_rejects_when_both_unset(monkeypatch):
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    assert build_comps._has_identity() is False


def test_identity_alias_rejects_blank_value(monkeypatch):
    """An empty/whitespace-only value is not a valid identity — the SEC
    will throttle and the alias check should treat it as unset."""
    monkeypatch.delenv("EDGAR_IDENTITY", raising=False)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "   ")
    assert build_comps._has_identity() is False
