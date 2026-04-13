"""
Deterministic financial metric calculations for CFA report generation.
"""

from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _safe_pct(numerator: float | None, denominator: float | None) -> float | None:
    ratio = _safe_div(numerator, denominator)
    if ratio is None:
        return None
    return ratio * 100.0


def _safe_yoy(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def _round(value: float | None, ndigits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, ndigits)


def _rating(blended_fv: float | None, current_price: float | None) -> str | None:
    if blended_fv is None or current_price is None or current_price <= 0:
        return None
    if blended_fv > current_price * 1.15:
        return "OUTPERFORM"
    if blended_fv < current_price * 0.85:
        return "UNDERPERFORM"
    return "MARKET PERFORM"


def _peer_target_pb(roe_percent: float | None) -> float | None:
    if roe_percent is None:
        return None
    if roe_percent > 20.0:
        return 3.0
    if roe_percent >= 15.0:
        return 2.0
    return 1.25


def _scenario_pb(roe_percent: float | None, g_percent: float, ke_percent: float) -> float | None:
    if roe_percent is None:
        return None
    denom = ke_percent - g_percent
    if denom <= 0:
        return None
    return (roe_percent - g_percent) / denom


def _financial_value(research_data: dict[str, Any], section: str, field: str, fiscal_year: str) -> float | None:
    return _to_float(
        (research_data.get("financials", {}) or {})
        .get(section, {})
        .get(field, {})
        .get(fiscal_year)
    )


def compute_cfa_metrics(
    research_data: dict[str, Any],
    market_data: dict[str, Any],
    assumptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assumptions = assumptions or {}
    ke_percent = _to_float(assumptions.get("ke_percent")) or 13.0
    g_percent = _to_float(assumptions.get("g_percent")) or 8.0

    fy = ["FY2022", "FY2023", "FY2024"]
    base: dict[str, Any] = {}

    for year in fy:
        nii = _financial_value(research_data, "income_statement", "net_interest_income", year)
        toi = _financial_value(research_data, "income_statement", "total_operating_income", year)
        opex = _financial_value(research_data, "income_statement", "operating_expenses", year)
        pbt = _financial_value(research_data, "income_statement", "pre_tax_profit", year)
        tax = _financial_value(research_data, "income_statement", "income_tax", year)
        npat = _financial_value(research_data, "income_statement", "net_profit", year)
        assets = _financial_value(research_data, "balance_sheet", "total_assets", year)
        equity = _financial_value(research_data, "balance_sheet", "equity", year)

        ppop = None
        if toi is not None and opex is not None:
            ppop = toi - opex

        nim_percent = _safe_pct(nii, assets)
        cir_percent = _safe_pct(opex, toi)
        roe_percent = _safe_pct(npat, equity)
        roa_percent = _safe_pct(npat, assets)
        net_margin_percent = _safe_pct(npat, toi)
        asset_turnover = _safe_div(toi, assets)
        equity_multiplier = _safe_div(assets, equity)

        base[year] = {
            "nii": _round(nii),
            "toi": _round(toi),
            "opex": _round(opex),
            "ppop": _round(ppop),
            "pbt": _round(pbt),
            "tax": _round(tax),
            "npat": _round(npat),
            "assets": _round(assets),
            "equity": _round(equity),
            "nim_percent": _round(nim_percent),
            "cir_percent": _round(cir_percent),
            "roe_percent": _round(roe_percent),
            "roa_percent": _round(roa_percent),
            "net_margin_percent": _round(net_margin_percent),
            "asset_turnover": _round(asset_turnover, 6),
            "equity_multiplier": _round(equity_multiplier, 4),
        }

    yoy = {
        "FY2023": {
            "nii_yoy_percent": _round(_safe_yoy(base["FY2023"]["nii"], base["FY2022"]["nii"])),
            "toi_yoy_percent": _round(_safe_yoy(base["FY2023"]["toi"], base["FY2022"]["toi"])),
            "npat_yoy_percent": _round(_safe_yoy(base["FY2023"]["npat"], base["FY2022"]["npat"])),
            "ppop_yoy_percent": _round(_safe_yoy(base["FY2023"]["ppop"], base["FY2022"]["ppop"])),
            "pbt_yoy_percent": _round(_safe_yoy(base["FY2023"]["pbt"], base["FY2022"]["pbt"])),
        },
        "FY2024": {
            "nii_yoy_percent": _round(_safe_yoy(base["FY2024"]["nii"], base["FY2023"]["nii"])),
            "toi_yoy_percent": _round(_safe_yoy(base["FY2024"]["toi"], base["FY2023"]["toi"])),
            "npat_yoy_percent": _round(_safe_yoy(base["FY2024"]["npat"], base["FY2023"]["npat"])),
            "ppop_yoy_percent": _round(_safe_yoy(base["FY2024"]["ppop"], base["FY2023"]["ppop"])),
            "pbt_yoy_percent": _round(_safe_yoy(base["FY2024"]["pbt"], base["FY2023"]["pbt"])),
        },
    }

    fy2024 = base["FY2024"]
    net_margin_decimal = _safe_div(fy2024["net_margin_percent"], 100.0)
    dupont_roe_percent = None
    if (
        net_margin_decimal is not None
        and fy2024["asset_turnover"] is not None
        and fy2024["equity_multiplier"] is not None
    ):
        dupont_roe_percent = (
            net_margin_decimal * fy2024["asset_turnover"] * fy2024["equity_multiplier"] * 100.0
        )

    current_price = _to_float(market_data.get("currentPrice"))
    bvps = _to_float(market_data.get("bookValue"))
    current_pb = _to_float(market_data.get("priceToBook"))
    if current_pb is None:
        current_pb = _safe_div(current_price, bvps)

    roe_2024 = fy2024["roe_percent"]
    target_pb = _peer_target_pb(roe_2024)
    pb_fair_value = target_pb * bvps if target_pb is not None and bvps is not None else None

    justified_pb = _scenario_pb(roe_2024, g_percent, ke_percent)
    gordon_fair_value = justified_pb * bvps if justified_pb is not None and bvps is not None else None
    blended_fair_value = None
    if pb_fair_value is not None and gordon_fair_value is not None:
        blended_fair_value = (pb_fair_value + gordon_fair_value) / 2.0

    blended_upside_percent = _safe_pct(
        None if blended_fair_value is None or current_price is None else blended_fair_value - current_price,
        current_price,
    )
    gordon_upside_percent = _safe_pct(
        None if gordon_fair_value is None or current_price is None else gordon_fair_value - current_price,
        current_price,
    )

    bear_roe = roe_2024 * 0.85 if roe_2024 is not None else None
    bull_roe = roe_2024 * 1.15 if roe_2024 is not None else None
    bear_pb = _scenario_pb(bear_roe, 6.0, ke_percent)
    bull_pb = _scenario_pb(bull_roe, 10.0, ke_percent)
    bear_fv = bear_pb * bvps if bear_pb is not None and bvps is not None else None
    bull_fv = bull_pb * bvps if bull_pb is not None and bvps is not None else None
    bear_upside_percent = _safe_pct(
        None if bear_fv is None or current_price is None else bear_fv - current_price,
        current_price,
    )
    bull_upside_percent = _safe_pct(
        None if bull_fv is None or current_price is None else bull_fv - current_price,
        current_price,
    )

    sensitivity: dict[str, dict[str, float | None]] = {}
    for ke in (12.0, 13.0, 14.0):
        row: dict[str, float | None] = {}
        for g in (6.0, 8.0, 10.0):
            row[f"g_{int(g)}"] = _round(_scenario_pb(roe_2024, g, ke), 4)
        sensitivity[f"ke_{int(ke)}"] = row

    sanity_errors: list[str] = []
    sanity_warnings: list[str] = []

    nim_2024 = fy2024["nim_percent"]
    if nim_2024 is None:
        sanity_warnings.append("FY2024 NIM could not be computed.")
    elif not (1.5 <= nim_2024 <= 5.0):
        sanity_errors.append(f"NIM out of expected range: {nim_2024}%")

    at_2024 = fy2024["asset_turnover"]
    if at_2024 is None:
        sanity_warnings.append("FY2024 Asset Turnover could not be computed.")
    elif not (0.02 <= at_2024 <= 0.06):
        sanity_errors.append(f"Asset Turnover out of expected range: {at_2024}")

    em_2024 = fy2024["equity_multiplier"]
    if em_2024 is None:
        sanity_warnings.append("FY2024 Equity Multiplier could not be computed.")
    elif not (8.0 <= em_2024 <= 15.0):
        sanity_errors.append(f"Equity Multiplier out of expected range: {em_2024}x")

    if dupont_roe_percent is None or roe_2024 is None:
        sanity_warnings.append("DuPont ROE verification skipped due to missing inputs.")
    elif abs(dupont_roe_percent - roe_2024) > 0.5:
        sanity_errors.append(
            f"DuPont mismatch: computed {round(dupont_roe_percent, 4)}% vs ROE {roe_2024}%"
        )

    if ke_percent <= g_percent:
        sanity_errors.append(f"Invalid assumptions: ke_percent ({ke_percent}) must be > g_percent ({g_percent}).")
    if justified_pb is not None and justified_pb <= 0:
        sanity_warnings.append(f"Justified P/B is non-positive: {round(justified_pb, 4)}")

    return {
        "assumptions": {
            "ke_percent": ke_percent,
            "g_percent": g_percent,
        },
        "base_metrics": base,
        "yoy_metrics": yoy,
        "dupont": {
            "fy2024": {
                "net_margin_percent": _round(fy2024["net_margin_percent"]),
                "asset_turnover": _round(fy2024["asset_turnover"], 6),
                "equity_multiplier": _round(fy2024["equity_multiplier"]),
                "roe_percent": _round(roe_2024),
                "dupont_roe_percent": _round(dupont_roe_percent),
            }
        },
        "valuation": {
            "current_price": _round(current_price),
            "book_value_per_share": _round(bvps),
            "current_pb": _round(current_pb),
            "peer_target_pb": _round(target_pb),
            "pb_fair_value": _round(pb_fair_value),
            "justified_pb": _round(justified_pb),
            "gordon_fair_value": _round(gordon_fair_value),
            "blended_fair_value": _round(blended_fair_value),
            "blended_upside_percent": _round(blended_upside_percent),
            "gordon_upside_percent": _round(gordon_upside_percent),
            "rating": _rating(blended_fair_value, current_price),
            "market_data": {
                "market_cap_ty_dong": _to_float(market_data.get("marketCap_ty_dong")),
                "trailing_pe": _to_float(market_data.get("trailingPE")),
                "trailing_eps": _to_float(market_data.get("trailingEps")),
            },
        },
        "scenarios": {
            "bear": {
                "roe_percent": _round(bear_roe),
                "g_percent": 6.0,
                "ke_percent": ke_percent,
                "justified_pb": _round(bear_pb),
                "fair_value": _round(bear_fv),
                "upside_percent": _round(bear_upside_percent),
            },
            "base": {
                "roe_percent": _round(roe_2024),
                "g_percent": g_percent,
                "ke_percent": ke_percent,
                "justified_pb": _round(justified_pb),
                "fair_value": _round(gordon_fair_value),
                "upside_percent": _round(gordon_upside_percent),
            },
            "bull": {
                "roe_percent": _round(bull_roe),
                "g_percent": 10.0,
                "ke_percent": ke_percent,
                "justified_pb": _round(bull_pb),
                "fair_value": _round(bull_fv),
                "upside_percent": _round(bull_upside_percent),
            },
        },
        "sensitivity_justified_pb": sensitivity,
        "sanity_checks": {
            "passed": len(sanity_errors) == 0,
            "errors": sanity_errors,
            "warnings": sanity_warnings,
        },
    }
