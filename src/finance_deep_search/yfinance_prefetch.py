#!/usr/bin/env python
"""
Pre-fetch yfinance financial data for Vietnamese banks.

This script runs with CPython + yfinance and saves structured financial
data as JSON so the research agent can read it from the filesystem.

Usage:
    uvx --python 3.12 --with yfinance python yfinance_prefetch.py VCB ./output
"""

import json
import math
import sys
from pathlib import Path


def safe_val(v, divisor=1):
    """Convert a value to float divided by divisor, or None if NaN/None."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f / divisor, 2)
    except (TypeError, ValueError):
        return None


def fetch_and_save(ticker: str, output_path: str) -> str:
    """Fetch yfinance data and save to JSON. Returns the output file path."""
    import yfinance as yf  # noqa: PLC0415

    symbol = f"{ticker}.VN"
    print(f"[yfinance_prefetch] Fetching {symbol}...")

    t = yf.Ticker(symbol)
    div = 1_000_000_000  # absolute VND -> tỷ đồng

    # ── Income Statement ──────────────────────────────────────────────────────
    inc = t.income_stmt  # columns = dates (newest first)
    inc_data = {}
    if not inc.empty:
        for col in inc.columns:
            year = str(col.year)
            inc_data[year] = {
                row: safe_val(inc.at[row, col], div)
                for row in inc.index
                if safe_val(inc.at[row, col], div) is not None
            }

    # ── Balance Sheet ─────────────────────────────────────────────────────────
    bs = t.balance_sheet
    bs_data = {}
    if not bs.empty:
        for col in bs.columns:
            year = str(col.year)
            bs_data[year] = {
                row: safe_val(bs.at[row, col], div)
                for row in bs.index
                if safe_val(bs.at[row, col], div) is not None
            }

    # ── Cash Flow ─────────────────────────────────────────────────────────────
    cf = t.cashflow
    cf_data = {}
    if not cf.empty:
        for col in cf.columns:
            year = str(col.year)
            cf_data[year] = {
                row: safe_val(cf.at[row, col], div)
                for row in cf.index
                if safe_val(cf.at[row, col], div) is not None
            }

    # ── Ticker Info (market data) ─────────────────────────────────────────────
    try:
        info = t.info
    except Exception:
        info = {}

    result = {
        "ticker": ticker,
        "symbol": symbol,
        "units": "tỷ đồng VND (values already divided by 1,000,000,000)",
        "note": "All monetary values are in tỷ đồng (billions VND). Columns are fiscal year ends.",
        "income_statement": inc_data,
        "balance_sheet": bs_data,
        "cash_flow": cf_data,
        "market_data": {
            "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
            "marketCap_ty_dong": safe_val(info.get("marketCap"), div),
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "bookValue": info.get("bookValue"),
            "priceToBook": info.get("priceToBook"),
            "trailingEps": info.get("trailingEps"),
            "forwardEps": info.get("forwardEps"),
            "profitMargins": info.get("profitMargins"),
            "dividendRate": info.get("dividendRate"),
            "currency": info.get("currency", "VND"),
        },
    }

    out_path = Path(output_path) / f"yfinance_{ticker}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[yfinance_prefetch] Saved to {out_path}")
    return str(out_path)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python yfinance_prefetch.py <TICKER> <OUTPUT_DIR>")
        sys.exit(1)
    ticker_arg = sys.argv[1]
    output_dir = sys.argv[2]
    fetch_and_save(ticker_arg, output_dir)
