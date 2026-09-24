"""
Pace and KPI engine.

Answers the question a store manager actually has mid-month: "is each
category on track to hit its monthly target, and what will it take?"
Everything here reads real numbers from the data files — nothing is guessed.
Projections are clearly labelled as projections.
"""

import math
import os

import pandas as pd

from config import (
    CATEGORIES,
    CATEGORY_DEPARTMENT,
    CATEGORY_LINE,
    CATEGORY_PRODUCT,
    DAYS_IN_MONTH,
    DEFAULT_CURRENT_HOUR,
    LAST_YEAR_UNITS,
    LINES,
    MIN_EXPECTED_UNITS_FOR_STATUS,
    MONTHLY_TARGETS,
    NORMAL_VARIATION_Z,
    PACE_THRESHOLD_PCT,
    REQUIRED_RATE_STRETCH,
    STORE_HOURS,
    TODAY_DAY,
    is_busy_day,
    month_calendar,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SALES_PATH = os.path.join(DATA_DIR, "sales.csv")
FOOTFALL_PATH = os.path.join(DATA_DIR, "footfall.csv")


def load_sales_data():
    """Loads the sales data from disk."""
    return pd.read_csv(SALES_PATH)


def load_footfall_data():
    """Loads the footfall (visitor count) data from disk."""
    return pd.read_csv(FOOTFALL_PATH)


def _validate_moment(day, hour):
    if not 1 <= day <= TODAY_DAY:
        raise ValueError(f"day must be between 1 and {TODAY_DAY} (today), got {day}")
    if hour not in STORE_HOURS:
        raise ValueError(
            f"hour must be a store hour between {STORE_HOURS[0]} and {STORE_HOURS[-1]}, got {hour}"
        )


def _as_of(df, day, hour):
    """Rows from the start of the month up to the end of `hour` on `day`."""
    return df[(df["day"] < day) | ((df["day"] == day) & (df["hour"] <= hour))]


def typical_share_of_day_sold(sales_df, day, hour):
    """
    What share of a normal day's units has usually sold by the end of `hour`,
    learned from this month's own history: past days of the same kind as
    `day` (weekend/sale days trade later in the day than weekdays).

    Learned from the data rather than assumed, so it works the same on real
    store exports. Falls back to an even spread if there's no history yet.
    """
    calendar = {d["day"]: d for d in month_calendar()}
    busy = is_busy_day(calendar[day])
    similar_days = [d for d in range(1, day) if is_busy_day(calendar[d]) == busy]

    history = sales_df[sales_df["day"].isin(similar_days)]
    by_hour = history.groupby("hour")["units_sold"].sum().reindex(STORE_HOURS, fill_value=0)
    if by_hour.sum() == 0:
        return (STORE_HOURS.index(hour) + 1) / len(STORE_HOURS)
    return float(by_hour.cumsum()[hour] / by_hour.sum())


def classify_pace(expected, actual):
    """
    Turns expected vs actual units into a status, using three business rules
    from config.py:
      - MIN_EXPECTED_UNITS_FOR_STATUS: too few units expected -> "too_early"
      - PACE_THRESHOLD_PCT: the 15% line for behind / ahead
      - NORMAL_VARIATION_Z: the gap must also be bigger than ordinary
        randomness. Past -15% but within normal variation is "drifting"
        (worth watching, not proven); past +15% within noise is just on pace.
    Returns (status, pct_vs_pace, gap_beyond_normal_variation).
    """
    if expected <= 0:
        return "too_early", 0.0, False

    pct = (actual - expected) / expected * 100
    # Unit sales counts naturally vary by about the square root of the
    # expected number, so a gap is "real" only beyond Z of those swings.
    beyond_noise = abs(actual - expected) > NORMAL_VARIATION_Z * math.sqrt(expected)

    if expected < MIN_EXPECTED_UNITS_FOR_STATUS:
        status = "too_early"
    elif pct < -PACE_THRESHOLD_PCT:
        status = "behind" if beyond_noise else "drifting"
    elif pct > PACE_THRESHOLD_PCT and beyond_noise:
        status = "ahead"
    else:
        status = "on_pace"
    return status, pct, beyond_noise


def get_category_pace(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, category=None, line=None,
                      sales_df=None):
    """
    Month-to-date pace for every category (or one category, or one line), as
    of `hour` on `day`.

    Expected pace = monthly target x share of the month elapsed. Over a whole
    month, busy weekends and quiet weekdays mostly even out, so counting days
    is fair (unlike hours within one day). Today counts as a partial day,
    using how much of a typical day has usually sold by this hour.

    Returns a list of dicts with actuals, the expected pace, a status, and
    clearly-labelled projections for the rest of the month.
    """
    _validate_moment(day, hour)
    if sales_df is None:
        sales_df = load_sales_data()

    days_elapsed = (day - 1) + typical_share_of_day_sold(sales_df, day, hour)
    days_remaining = DAYS_IN_MONTH - days_elapsed
    sold_by_category = _as_of(sales_df, day, hour).groupby("category")["units_sold"].sum()

    selected = [
        c for c in CATEGORIES
        if (category is None or c == category) and (line is None or CATEGORY_LINE[c] == line)
    ]
    if not selected:
        raise ValueError(f"No category matches category={category!r}, line={line!r}")

    results = []
    for cat in selected:
        target = MONTHLY_TARGETS[cat]
        sold = int(sold_by_category.get(cat, 0))
        expected = target * days_elapsed / DAYS_IN_MONTH
        status, pct, beyond_noise = classify_pace(expected, sold)

        actual_per_day = sold / days_elapsed
        still_needed = max(0, target - sold)
        needs_per_day = still_needed / days_remaining if days_remaining > 0 else float(still_needed)
        unlikely = still_needed > 0 and needs_per_day > REQUIRED_RATE_STRETCH * actual_per_day

        results.append({
            "category": cat,
            "line": CATEGORY_LINE[cat],
            "department": CATEGORY_DEPARTMENT[cat],
            "as_of": {"day": day, "hour": hour},
            "monthly_target": target,
            "last_year_units": LAST_YEAR_UNITS[cat],
            "units_sold_so_far": sold,
            "balance_to_do": sold - target,
            "expected_units_by_now": round(expected, 1),
            "pct_vs_pace": round(pct, 1),
            "status": status,
            "gap_beyond_normal_variation": beyond_noise,
            "actual_units_per_day": round(actual_per_day, 1),
            "needed_units_per_day": round(needs_per_day, 1),
            "unlikely_without_action": unlikely,
            # A projection, not a fact: assumes the current daily rate simply
            # continues. It can't know about stockouts or a month-end push.
            "projected_month_end_if_current_rate_continues": round(
                sold + actual_per_day * days_remaining
            ),
        })
    return results


def get_contribution(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, sales_df=None):
    """
    The automated version of the store's contribution report: units and value
    sold month-to-date by line and category, each as a share of the store.

    Line totals are built from their own categories, and the whole report is
    checked before it is returned: line totals must add up to the store
    total, and shares must add up to 100%. A manual spreadsheet can silently
    drop rows from a SUM; this report refuses to.
    """
    _validate_moment(day, hour)
    if sales_df is None:
        sales_df = load_sales_data()

    period = _as_of(sales_df, day, hour)
    by_category = period.groupby("category")[["units_sold", "value"]].sum()
    store_units = int(period["units_sold"].sum())
    store_value = float(period["value"].sum())

    def share(part, whole):
        return part / whole * 100 if whole else 0.0

    lines = []
    for line in LINES:
        categories = []
        for cat in (c for c in CATEGORIES if CATEGORY_LINE[c] == line):
            units = int(by_category["units_sold"].get(cat, 0))
            value = float(by_category["value"].get(cat, 0.0))
            categories.append({
                "category": cat,
                "product_type": CATEGORY_PRODUCT[cat],
                "units": units,
                "value": round(value),
                "units_pct": round(share(units, store_units), 1),
                "value_pct": round(share(value, store_value), 1),
            })
        line_units = sum(c["units"] for c in categories)
        line_value = sum(float(by_category["value"].get(c["category"], 0.0)) for c in categories)
        lines.append({
            "line": line,
            "department": LINES[line],
            "units": line_units,
            "value": round(line_value),
            "units_pct": round(share(line_units, store_units), 1),
            "value_pct": round(share(line_value, store_value), 1),
            "categories": categories,
        })

    total_line_units = sum(l["units"] for l in lines)
    total_line_value = sum(
        float(by_category["value"].get(c["category"], 0.0)) for l in lines for c in l["categories"]
    )
    if total_line_units != store_units or abs(total_line_value - store_value) > 1:
        raise ValueError(
            f"Contribution report doesn't add up: lines total {total_line_units} units / "
            f"{total_line_value:.0f} value vs store {store_units} / {store_value:.0f}"
        )
    unit_share_total = sum(share(l["units"], store_units) for l in lines)
    if store_units and abs(unit_share_total - 100) > 0.01:
        raise ValueError(f"Line shares add up to {unit_share_total:.2f}%, not 100%")

    return {
        "as_of": {"day": day, "hour": hour},
        "store_units": store_units,
        "store_value": round(store_value),
        "checks_passed": ["line totals equal the sum of their categories",
                          "line shares add up to 100%"],
        "lines": lines,
    }


def get_footfall(category=None, hour=None, footfall_df=None):
    """
    Visitor counts. Being rebuilt for per-zone footfall in the next step
    (Phase 3 rebuild); not yet updated for the monthly data.
    """
    raise NotImplementedError("Footfall lookup is being rebuilt for per-zone data (Phase 3).")


def get_conversion_metrics(category, hour, sales_df=None, footfall_df=None):
    """
    Conversion rate and units-per-transaction (UPT). Phase 4 stub — full
    implementation lands in Phase 6d.
    """
    return {
        "category": category,
        "hour": hour,
        "note": "Conversion rate and UPT are not yet available (Phase 6d).",
    }


# --- Verification output -------------------------------------------------------

_STATUS_LABEL = {
    "behind": "BEHIND",
    "drifting": "drifting",
    "on_pace": "on pace",
    "ahead": "AHEAD",
    "too_early": "too early",
}


def _print_pace_table(day, hour):
    results = get_category_pace(day, hour)
    print(f"\nMonth-to-date pace as of day {day}, {hour}:00\n")
    header = (f"{'Line':<7}{'Category':<17}{'Target':>7}{'Sold':>6}{'Balance':>8}"
              f"{'Expected':>9}{'vs pace':>9}{'Per day':>8}{'Needs/day':>10}  Status")
    print(header)
    current_line = None
    for r in results:
        if r["line"] != current_line:
            print("-" * (len(header) + 12))
            current_line = r["line"]
        flag = "  (unlikely without action)" if r["unlikely_without_action"] else ""
        print(
            f"{r['line']:<7}{CATEGORY_PRODUCT[r['category']]:<17}{r['monthly_target']:>7}"
            f"{r['units_sold_so_far']:>6}{r['balance_to_do']:>8}{r['expected_units_by_now']:>9}"
            f"{r['pct_vs_pace']:>8}%{r['actual_units_per_day']:>8}{r['needed_units_per_day']:>10}"
            f"  {_STATUS_LABEL[r['status']]}{flag}"
        )

    print("\nBiggest raw 'balance to do' vs what pace says needs attention:\n")
    by_balance = sorted(results, key=lambda r: r["balance_to_do"])[:5]
    needs_attention = [r for r in results if r["status"] in ("behind", "drifting")]
    needs_attention.sort(key=lambda r: r["pct_vs_pace"])
    print("  Largest balance to do:         " + ", ".join(
        f"{r['category']} ({r['balance_to_do']})" for r in by_balance))
    print("  Behind or drifting on pace:    " + ", ".join(
        f"{r['category']} ({r['pct_vs_pace']:+.0f}%, {r['status']})" for r in needs_attention))


def _print_contribution(day, hour):
    report = get_contribution(day, hour)
    print(f"\nContribution report, month-to-date as of day {day}, {hour}:00\n")
    print(f"{'Line':<8}{'Units':>7}{'Value (INR)':>14}{'Units %':>9}{'Value %':>9}")
    print("-" * 47)
    for l in report["lines"]:
        print(f"{l['line']:<8}{l['units']:>7}{l['value']:>14,}{l['units_pct']:>8}%{l['value_pct']:>8}%")
    print("-" * 47)
    print(f"{'Store':<8}{report['store_units']:>7}{report['store_value']:>14,}")
    print("\nIntegrity checks passed: " + "; ".join(report["checks_passed"]))


if __name__ == "__main__":
    _print_pace_table(TODAY_DAY, DEFAULT_CURRENT_HOUR)
    _print_contribution(TODAY_DAY, DEFAULT_CURRENT_HOUR)
