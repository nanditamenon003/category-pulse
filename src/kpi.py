"""
Pace and KPI engine.

Answers the core question a store manager cares about: "is each category on
track right now?" Everything here reads real numbers from data/sales.csv —
nothing is estimated or guessed.
"""

import os

import pandas as pd

from config import (
    CATEGORIES,
    CATEGORY_TARGETS,
    PACE_THRESHOLD_PCT,
    STORE_OPEN_HOUR,
    TOTAL_STORE_HOURS,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SALES_PATH = os.path.join(DATA_DIR, "sales.csv")


def load_sales_data():
    """Loads the simulated sales data from disk."""
    return pd.read_csv(SALES_PATH)


def _hours_elapsed(hour):
    """
    Number of complete store hours from opening through the given hour,
    inclusive. E.g. at hour=16 (the 16:00-17:00 slot), hours 10..16 have
    all completed selling, so 7 hours have elapsed.
    """
    return hour - STORE_OPEN_HOUR + 1


def get_category_pace(hour, category=None, sales_df=None):
    """
    Computes pace/status for one category (if `category` is given) or all
    categories, as of the given hour.

    For each category, returns:
      - units_sold_so_far: actual units sold from opening through `hour`
      - expected_units_by_now: what the daily target implies should have
        sold by this point in the day, assuming even pacing
      - pct_vs_pace: how far actual is from expected, as a percentage
        (positive = ahead, negative = behind)
      - status: "behind", "ahead", or "on_pace" — see PACE_THRESHOLD_PCT
        in config.py for the business rule behind this classification

    Returns a list of dicts (one per category), so it serializes cleanly
    as a tool result for the Claude agent in Phase 4.
    """
    if sales_df is None:
        sales_df = load_sales_data()

    hours_elapsed = _hours_elapsed(hour)
    fraction_of_day = hours_elapsed / TOTAL_STORE_HOURS

    categories_to_check = [category] if category else CATEGORIES
    results = []

    for cat in categories_to_check:
        if cat not in CATEGORY_TARGETS:
            continue

        target = CATEGORY_TARGETS[cat]
        expected_units_by_now = target * fraction_of_day

        sold_so_far = sales_df[
            (sales_df["category"] == cat) & (sales_df["hour"] <= hour)
        ]["units_sold"].sum()

        # Expected units is never exactly zero this early in the day, so
        # division is safe; guarded anyway in case hour is before opening.
        if expected_units_by_now > 0:
            pct_vs_pace = (sold_so_far - expected_units_by_now) / expected_units_by_now * 100
        else:
            pct_vs_pace = 0.0

        # Business rule (config.PACE_THRESHOLD_PCT): more than 15% under
        # pace is "behind", more than 15% over is "ahead", otherwise
        # "on_pace". Chosen because a category more than 15% behind by
        # mid-afternoon is unlikely to recover without intervention.
        if pct_vs_pace < -PACE_THRESHOLD_PCT:
            status = "behind"
        elif pct_vs_pace > PACE_THRESHOLD_PCT:
            status = "ahead"
        else:
            status = "on_pace"

        results.append(
            {
                "category": cat,
                "hour": hour,
                "units_sold_so_far": int(sold_so_far),
                "expected_units_by_now": round(expected_units_by_now, 1),
                "pct_vs_pace": round(pct_vs_pace, 1),
                "status": status,
            }
        )

    return results


def _print_verification(hour):
    results = get_category_pace(hour)
    print(f"\nPace status at hour {hour}:00\n")
    header = f"{'Category':<14}{'Sold':>8}{'Expected':>10}{'% vs pace':>12}{'Status':>12}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r['category']:<14}{r['units_sold_so_far']:>8}"
            f"{r['expected_units_by_now']:>10}{r['pct_vs_pace']:>11}%"
            f"{r['status']:>12}"
        )

    print(
        "\nCheck: Womenswear should read 'behind'; Kidswear should read 'ahead'."
    )


if __name__ == "__main__":
    _print_verification(16)
