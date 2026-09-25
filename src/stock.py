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
    BROKEN_RUN_MIN_SHARE,
    CATEGORIES,
    CORE_DEPLETED_MAX_UNITS,
    DEFAULT_CURRENT_HOUR,
    DELIVERY_WEEKDAY,
    LAST_PIECE_THRESHOLD,
    STOCKOUT_MAX_SHARE,
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


def _usual_stock_level(stock_df, category, day):
    """
    The category's typical end-of-day stock earlier this month (the median,
    so a few unusual days don't distort it). Learned from the data, the way
    it would be from a real store's stock history.
    """
    closes = stock_df[
        (stock_df["category"] == category)
        & (stock_df["day"] < day)
        & (stock_df["hour"] == STORE_HOURS[-1])
    ].groupby("day")["units_remaining"].sum()
    return float(closes.median()) if len(closes) else None


def check_size_runs(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None):
    """
    Classifies a category's stock health, keeping a broken size run distinct
    from a plain stockout (see the thresholds in config.py):
      - "stockout": almost nothing left in any size
      - "broken_size_run": core sizes gone, but the shelf still looks full
      - "running_out": core sizes gone and overall stock getting low
      - "healthy": core sizes available
    """
    _validate(category, day, hour)
    if stock_df is None:
        stock_df = load_stock_data()

    status = get_stock_status(category, day, hour, stock_df=stock_df)
    remaining = status["remaining_by_size"]
    core = status["core_sizes"]
    usual = _usual_stock_level(stock_df, category, day)
    total = status["total_remaining"]
    share_of_usual = total / usual if usual else None
    core_depleted = all(remaining[s] <= CORE_DEPLETED_MAX_UNITS for s in core)

    if share_of_usual is not None and share_of_usual < STOCKOUT_MAX_SHARE:
        verdict = "stockout"
    elif core_depleted and (share_of_usual is None or share_of_usual >= BROKEN_RUN_MIN_SHARE):
        verdict = "broken_size_run"
    elif core_depleted:
        verdict = "running_out"
    else:
        verdict = "healthy"

    return {
        "category": category,
        "as_of": {"day": day, "hour": hour},
        "verdict": verdict,
        "core_sizes": core,
        "core_remaining_by_size": {s: remaining[s] for s in core},
        "other_sizes_remaining": {s: u for s, u in remaining.items() if s not in core},
        "total_remaining": total,
        "usual_stock_level": round(usual) if usual is not None else None,
        "total_as_pct_of_usual": round(share_of_usual * 100) if share_of_usual is not None else None,
    }


def get_stock_health_report(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None):
    """Every category whose stock isn't healthy right now (for the alert banner)."""
    if stock_df is None:
        stock_df = load_stock_data()
    report = [check_size_runs(c, day, hour, stock_df=stock_df) for c in CATEGORIES]
    return [r for r in report if r["verdict"] != "healthy"]


def get_last_piece_alerts(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None, sales_df=None):
    """
    Every size that dropped to exactly LAST_PIECE_THRESHOLD unit(s) on `day`
    up to `hour`, with the hour it happened. Fires once, at the moment of the
    drop: stock just before an hour's sales (what's left + what sold that
    hour) was above the threshold, and after that hour it is exactly at it.
    Deliveries arrive before opening, so they're already in that "before".
    """
    if stock_df is None:
        stock_df = load_stock_data()
    if sales_df is None:
        from kpi import load_sales_data
        sales_df = load_sales_data()

    keys = ["day", "hour", "category", "size"]
    today = (stock_df["day"] == day) & (stock_df["hour"] <= hour)
    merged = stock_df[today].merge(
        sales_df[(sales_df["day"] == day) & (sales_df["hour"] <= hour)][keys + ["units_sold"]],
        on=keys,
        how="left",
    ).fillna({"units_sold": 0})
    before_hour = merged["units_remaining"] + merged["units_sold"]
    dropped = merged[
        (merged["units_remaining"] == LAST_PIECE_THRESHOLD) & (before_hour > LAST_PIECE_THRESHOLD)
    ]

    return [
        {
            "category": row["category"],
            "line": row["line"],
            "size": str(row["size"]),
            "is_core_size": str(row["size"]) in size_system_for(row["category"])["core"],
            "day": int(row["day"]),
            "hour": int(row["hour"]),
            "units_remaining": LAST_PIECE_THRESHOLD,
        }
        for _, row in dropped.sort_values(["hour", "category"]).iterrows()
    ]


def get_days_of_cover(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, stock_df=None,
                      sales_df=None):
    """
    A projection, not a fact: roughly how many days each size will last if it
    keeps selling at its average rate over the last 7 full days. Flags sizes
    likely to run out before the next scheduled delivery, so the team can act
    before the last piece, not at it.
    """
    _validate(category, day, hour)
    if stock_df is None:
        stock_df = load_stock_data()
    if sales_df is None:
        from kpi import load_sales_data
        sales_df = load_sales_data()

    remaining = get_stock_status(category, day, hour, stock_df=stock_df)["remaining_by_size"]
    window = sales_df[
        (sales_df["category"] == category) & (sales_df["day"] >= day - 7) & (sales_df["day"] < day)
    ]
    days_in_window = min(7, day - 1)
    daily_rate = window.groupby(window["size"].astype(str))["units_sold"].sum() / max(days_in_window, 1)

    next_delivery = next(
        (d["day"] for d in month_calendar()[day:] if d["weekday"] == DELIVERY_WEEKDAY), None
    )
    days_to_delivery = (next_delivery - day) if next_delivery else None

    sizes = []
    for size, units in remaining.items():
        rate = float(daily_rate.get(size, 0.0))
        cover = units / rate if rate > 0 else None
        sizes.append({
            "size": size,
            "units_remaining": units,
            "avg_units_sold_per_day_last_7_days": round(rate, 1),
            "projected_days_of_cover": round(cover, 1) if cover is not None else None,
            "likely_out_before_next_delivery": (
                cover is not None and days_to_delivery is not None and cover < days_to_delivery
            ),
        })

    return {
        "category": category,
        "as_of": {"day": day, "hour": hour},
        "next_scheduled_delivery_day": next_delivery,
        "note": "Projection: assumes each size keeps selling at its last-7-day average.",
        "sizes": sizes,
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


def _print_stock_health():
    print(f"\nStock health, day {TODAY_DAY} {DEFAULT_CURRENT_HOUR}:00 (categories that aren't healthy)\n")
    for r in get_stock_health_report():
        core = ", ".join(f"{s}:{u}" for s, u in r["core_remaining_by_size"].items())
        print(f"  {r['category']:<24} {r['verdict']:<16} core [{core}]  total {r['total_remaining']} "
              f"({r['total_as_pct_of_usual']}% of usual {r['usual_stock_level']})")
    healthy = check_size_runs("TJM Denim Bottom")
    print(f"  (control) TJM Denim Bottom: {healthy['verdict']}")


def _print_last_piece_alerts():
    alerts = get_last_piece_alerts()
    print(f"\nLast-piece alerts today (day {TODAY_DAY}) up to {DEFAULT_CURRENT_HOUR}:00: {len(alerts)}\n")
    for a in alerts:
        core = "  <- core size" if a["is_core_size"] else ""
        print(f"  {a['hour']}:00  {a['category']:<24} size {a['size']:<5} 1 left{core}")


def _print_days_of_cover(category):
    c = get_days_of_cover(category)
    print(f"\n{category} — days of cover (projection), next delivery day "
          f"{c['next_scheduled_delivery_day']}")
    for s in c["sizes"]:
        cover = s["projected_days_of_cover"]
        cover_text = f"{cover} days" if cover is not None else "no recent sales"
        warn = "  <- likely out before delivery" if s["likely_out_before_next_delivery"] else ""
        print(f"  {s['size']:<5} {s['units_remaining']:>3} left, "
              f"{s['avg_units_sold_per_day_last_7_days']}/day -> {cover_text}{warn}")


if __name__ == "__main__":
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_status(cat)
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_history(cat)
    _print_stock_health()
    _print_last_piece_alerts()
    _print_days_of_cover("THM Polo")
