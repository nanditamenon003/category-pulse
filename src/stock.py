"""
Stock logic: what's on the shelf, what came in, and what that means.

get_stock_status() reads what's left right now; get_stock_history() looks
back over recent days and spots deliveries, so the agent can find the cause
of a problem (a missed delivery, a delivery missing key sizes), not just the
symptom. check_size_runs() becomes real broken-size-run detection in Phase
6e; last-piece alerts are added in Phase 6a.
"""

import os

import pandas as pd

from config import (
    CATEGORIES,
    DEFAULT_CURRENT_HOUR,
    DELIVERY_WEEKDAY,
    STORE_HOURS,
    TODAY_DAY,
    month_calendar,
    size_system_for,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STOCK_PATH = os.path.join(DATA_DIR, "stock.csv")


def load_stock_data():
    """Loads the remaining-stock data from disk."""
    return pd.read_csv(STOCK_PATH)


def _validate(category, day, hour):
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}")
    if not 1 <= day <= TODAY_DAY:
        raise ValueError(f"day must be between 1 and {TODAY_DAY} (today), got {day}")
    if hour not in STORE_HOURS:
        raise ValueError(
            f"hour must be a store hour between {STORE_HOURS[0]} and {STORE_HOURS[-1]}, got {hour}"
        )


def _remaining_by_size(stock_df, category, day, hour):
    rows = stock_df[
        (stock_df["category"] == category) & (stock_df["day"] == day) & (stock_df["hour"] == hour)
    ]
    found = dict(zip(rows["size"].astype(str), rows["units_remaining"].astype(int)))
    return {size: found.get(size, 0) for size in size_system_for(category)["sizes"]}


def get_stock_status(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None):
    """
    Units left on the shelf by size for a category at the end of `hour` on
    `day`, plus which sizes are completely out, which of those are core sizes
    (the ones most customers need), and whether the whole category is out.
    """
    _validate(category, day, hour)
    if stock_df is None:
        stock_df = load_stock_data()

    remaining = _remaining_by_size(stock_df, category, day, hour)
    core = size_system_for(category)["core"]
    out_sizes = [s for s, units in remaining.items() if units == 0]

    return {
        "category": category,
        "as_of": {"day": day, "hour": hour},
        "remaining_by_size": remaining,
        "total_remaining": sum(remaining.values()),
        "core_sizes": core,
        "core_units_remaining": sum(remaining[s] for s in core),
        "out_of_stock_sizes": out_sizes,
        "core_sizes_out": [s for s in core if s in out_sizes],
        "is_completely_out": len(out_sizes) == len(remaining),
    }


def _deliveries_received(category, up_to_day, stock_df, sales_df):
    """
    Deliveries aren't recorded separately, so they're inferred: if a day
    opened with more stock than the previous day closed with (after allowing
    for the first hour's sales), the difference was delivered overnight.
    """
    core = size_system_for(category)["core"]
    first_hour, last_hour = STORE_HOURS[0], STORE_HOURS[-1]
    cat_sales = sales_df[(sales_df["category"] == category) & (sales_df["hour"] == first_hour)]

    deliveries = []
    for d in range(2, up_to_day + 1):  # day 1 is the month's opening stock
        prev_close = _remaining_by_size(stock_df, category, d - 1, last_hour)
        after_first_hour = _remaining_by_size(stock_df, category, d, first_hour)
        sold = cat_sales[cat_sales["day"] == d]
        sold_first_hour = dict(zip(sold["size"].astype(str), sold["units_sold"].astype(int)))
        received = {
            s: after_first_hour[s] + sold_first_hour.get(s, 0) - prev_close[s]
            for s in prev_close
        }
        if any(units > 0 for units in received.values()):
            deliveries.append({
                "day": d,
                "units_received": sum(max(0, u) for u in received.values()),
                "by_size": {s: max(0, u) for s, u in received.items()},
                "core_sizes_missing": [s for s in core if received[s] <= 0],
            })
    return deliveries


def get_stock_history(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, days_back=10,
                      stock_df=None, sales_df=None):
    """
    The recent stock story for a category:
      - end-of-day stock (total and core sizes) for the last `days_back` days
      - every delivery received this month, with the sizes it contained
      - scheduled delivery days (the store's weekly delivery day) on which
        nothing arrived for this category
    This is how the agent finds the cause behind a stockout or broken size
    run, not just the symptom.
    """
    _validate(category, day, hour)
    if stock_df is None:
        stock_df = load_stock_data()
    if sales_df is None:
        from kpi import load_sales_data
        sales_df = load_sales_data()

    core = size_system_for(category)["core"]
    daily = []
    for d in range(max(1, day - days_back + 1), day + 1):
        close_hour = hour if d == day else STORE_HOURS[-1]
        remaining = _remaining_by_size(stock_df, category, d, close_hour)
        daily.append({
            "day": d,
            "as_of_hour": close_hour,
            "total_remaining": sum(remaining.values()),
            "core_units_remaining": sum(remaining[s] for s in core),
        })

    deliveries = _deliveries_received(category, day, stock_df, sales_df)
    delivered_days = {d["day"] for d in deliveries}
    scheduled_days = [
        d["day"] for d in month_calendar()[1:day] if d["weekday"] == DELIVERY_WEEKDAY
    ]

    return {
        "category": category,
        "as_of": {"day": day, "hour": hour},
        "core_sizes": core,
        "daily_stock": daily,
        "deliveries_received": deliveries,
        "last_delivery_day": deliveries[-1]["day"] if deliveries else None,
        "scheduled_deliveries_not_received": [d for d in scheduled_days if d not in delivered_days],
    }


def check_size_runs(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None):
    """
    Broken-size-run detection. Phase 4 stub — full implementation lands in
    Phase 6e, where it flags core sizes being out even though total stock
    still looks adequate, as a cause distinct from a plain stockout.
    """
    return {
        "category": category,
        "note": "Broken size run detection is not yet available (Phase 6e).",
    }


# --- Verification output -------------------------------------------------------

def _print_status(category):
    s = get_stock_status(category)
    sizes = "  ".join(f"{size}:{units}" for size, units in s["remaining_by_size"].items())
    print(f"\n{category} — stock now (day {TODAY_DAY}, {DEFAULT_CURRENT_HOUR}:00)")
    print(f"  {sizes}")
    print(f"  total {s['total_remaining']}, core ({'/'.join(s['core_sizes'])}) "
          f"{s['core_units_remaining']}, core sizes out: {s['core_sizes_out'] or 'none'}, "
          f"completely out: {s['is_completely_out']}")


def _print_history(category):
    h = get_stock_history(category)
    print(f"\n{category} — last 10 days of stock")
    print("  day   total   core")
    for row in h["daily_stock"]:
        print(f"  {row['day']:>3}{row['total_remaining']:>8}{row['core_units_remaining']:>7}")
    for d in h["deliveries_received"]:
        missing = f", core sizes missing: {', '.join(d['core_sizes_missing'])}" \
            if d["core_sizes_missing"] else ""
        print(f"  delivery on day {d['day']}: {d['units_received']} units {d['by_size']}{missing}")
    print(f"  last delivery: day {h['last_delivery_day']}; "
          f"scheduled deliveries not received: {h['scheduled_deliveries_not_received'] or 'none'}")


if __name__ == "__main__":
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_status(cat)
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_history(cat)
