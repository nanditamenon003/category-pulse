"""
Stock logic: what's left on the shelf, and what that means.

Phase 4 builds get_stock_status(), a straightforward read of remaining
stock. check_size_runs() is a stub here — it becomes real broken-size-run
detection in Phase 6e, and last-piece alerts are added in Phase 6a.
"""

import os

import pandas as pd

from config import SIZES

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STOCK_PATH = os.path.join(DATA_DIR, "stock.csv")


def load_stock_data():
    """Loads the simulated remaining-stock data from disk."""
    return pd.read_csv(STOCK_PATH)


def get_stock_status(category, hour=None, stock_df=None):
    """
    Remaining units by size for a category, as of the given hour (defaults
    to the latest hour in the data), plus which sizes are completely out
    (stockout_sizes) and whether the whole category is out (is_stockout).
    """
    if stock_df is None:
        stock_df = load_stock_data()

    if hour is None:
        hour = stock_df["hour"].max()

    rows = stock_df[(stock_df["category"] == category) & (stock_df["hour"] == hour)]

    if rows.empty:
        return {
            "category": category,
            "hour": int(hour),
            "error": "No stock data available for this category/hour.",
        }

    remaining_by_size = {
        row["size"]: int(row["units_remaining"]) for _, row in rows.iterrows()
    }
    # Keep a stable, size-ordered view (XS..XXL) rather than CSV row order.
    remaining_by_size = {s: remaining_by_size.get(s, 0) for s in SIZES}

    stockout_sizes = [s for s, units in remaining_by_size.items() if units == 0]
    total_remaining = sum(remaining_by_size.values())

    return {
        "category": category,
        "hour": int(hour),
        "remaining_by_size": remaining_by_size,
        "total_remaining": total_remaining,
        "stockout_sizes": stockout_sizes,
        "is_stockout": len(stockout_sizes) == len(remaining_by_size),
    }


def check_size_runs(category, hour=None, stock_df=None):
    """
    Broken-size-run detection. This is a Phase 4 stub — full implementation
    lands in Phase 6e, where it flags core sizes (M, L) being depleted even
    though total remaining stock still looks adequate, as a cause distinct
    from a plain stockout.
    """
    return {
        "category": category,
        "note": "Broken size run detection is not yet available (Phase 6e).",
    }


if __name__ == "__main__":
    for category in ["Womenswear", "Chinos", "Jeans"]:
        print(f"\n{category} stock status at hour 16:")
        print(get_stock_status(category, hour=16))
