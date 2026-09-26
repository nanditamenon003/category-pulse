"""
Pace and KPI engine.

Answers the question a store manager actually has mid-month: "is each
category on track to hit its monthly target, and what will it take?"
Everything here reads real numbers from the store's data — nothing is
guessed. Projections are clearly labelled as projections.

Every function takes a `store` (see store.py); leave it out for the demo
store. `day` and `hour` default to the store's latest day and current hour.
"""

import math

from config import (
    CONVERSION_DROP_PCT,
    DRIFTING_Z,
    MIN_EXPECTED_UNITS_FOR_STATUS,
    MIN_TRANSACTIONS_FOR_READING,
    NORMAL_VARIATION_Z,
    PACE_THRESHOLD_PCT,
    REQUIRED_RATE_STRETCH,
    TRAFFIC_DROP_PCT,
)
from store import resolve


def _validate_moment(store, day, hour):
    if not 1 <= day <= store.today_day:
        raise ValueError(f"day must be between 1 and {store.today_day} (today), got {day}")
    if hour not in store.hours:
        raise ValueError(
            f"hour must be a store hour between {store.hours[0]} and {store.hours[-1]}, got {hour}"
        )


def _as_of(df, day, hour):
    """Rows from the start of the month up to the end of `hour` on `day`."""
    return df[(df["day"] < day) | ((df["day"] == day) & (df["hour"] <= hour))]


def typical_share_of_day_sold(store, day, hour):
    """
    What share of a normal day's units has usually sold by the end of `hour`,
    learned from this month's own history: past days of the same kind as
    `day` (weekend/sale days trade later in the day than weekdays).

    Learned from the data rather than assumed, so it works the same on real
    store exports. Falls back to an even spread if there's no history yet.
    """
    sales = store.sales
    history = sales[sales["day"].isin(_similar_past_days(store, day))]
    by_hour = history.groupby("hour")["units_sold"].sum().reindex(store.hours, fill_value=0)
    if by_hour.sum() == 0:
        return (store.hours.index(hour) + 1) / len(store.hours)
    return float(by_hour.cumsum()[hour] / by_hour.sum())


def classify_pace(expected, actual):
    """
    Turns expected vs actual units into a status, using business rules from
    config.py:
      - MIN_EXPECTED_UNITS_FOR_STATUS: too few units expected -> "too_early"
      - PACE_THRESHOLD_PCT: the 15% line for behind / ahead
      - NORMAL_VARIATION_Z / DRIFTING_Z: the gap must also be bigger than
        ordinary randomness. Past -15% with strong evidence is "behind", with
        some evidence "drifting" (worth watching), otherwise just on pace.
    Returns (status, pct_vs_pace, gap_beyond_normal_variation).
    """
    if expected <= 0:
        return "too_early", 0.0, False

    pct = (actual - expected) / expected * 100
    # Unit sales counts naturally vary by about the square root of the
    # expected number: that's one "normal swing".
    swings = abs(actual - expected) / math.sqrt(expected)
    beyond_noise = swings > NORMAL_VARIATION_Z

    if expected < MIN_EXPECTED_UNITS_FOR_STATUS:
        status = "too_early"
    elif pct < -PACE_THRESHOLD_PCT and beyond_noise:
        status = "behind"
    elif pct < -PACE_THRESHOLD_PCT and swings > DRIFTING_Z:
        status = "drifting"
    elif pct > PACE_THRESHOLD_PCT and beyond_noise:
        status = "ahead"
    else:
        status = "on_pace"
    return status, pct, beyond_noise


def get_category_pace(day=None, hour=None, category=None, line=None, store=None):
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
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)

    days_elapsed = (day - 1) + typical_share_of_day_sold(store, day, hour)
    days_remaining = store.days_in_month - days_elapsed
    sold_by_category = _as_of(store.sales, day, hour).groupby("category")["units_sold"].sum()

    selected = [
        c for c in store.categories
        if (category is None or c == category) and (line is None or store.category_line[c] == line)
    ]
    if not selected:
        raise ValueError(f"No category matches category={category!r}, line={line!r}")

    results = []
    for cat in selected:
        target = store.targets[cat]
        sold = int(sold_by_category.get(cat, 0))
        expected = target * days_elapsed / store.days_in_month
        status, pct, beyond_noise = classify_pace(expected, sold)

        actual_per_day = sold / days_elapsed
        still_needed = max(0, target - sold)
        needs_per_day = still_needed / days_remaining if days_remaining > 0 else float(still_needed)
        unlikely = still_needed > 0 and needs_per_day > REQUIRED_RATE_STRETCH * actual_per_day

        results.append({
            "category": cat,
            "line": store.category_line[cat],
            "department": store.category_department[cat],
            "as_of": {"day": day, "hour": hour},
            "monthly_target": target,
            "last_year_units": store.last_year.get(cat),
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


def get_contribution(day=None, hour=None, store=None):
    """
    The automated version of the store's contribution report: units and value
    sold month-to-date by line and category, each as a share of the store.

    Line totals are built from their own categories, and the whole report is
    checked before it is returned: line totals must add up to the store
    total, and shares must add up to 100%. A manual spreadsheet can silently
    drop rows from a SUM; this report refuses to.
    """
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)

    period = _as_of(store.sales, day, hour)
    by_category = period.groupby("category")[["units_sold", "value"]].sum()
    store_units = int(period["units_sold"].sum())
    store_value = float(period["value"].sum())

    def share(part, whole):
        return part / whole * 100 if whole else 0.0

    lines = []
    for line, department in store.lines.items():
        categories = []
        for cat in store.categories_in(line=line):
            units = int(by_category["units_sold"].get(cat, 0))
            value = float(by_category["value"].get(cat, 0.0))
            categories.append({
                "category": cat,
                "product_type": store.category_product[cat],
                "units": units,
                "value": round(value),
                "units_pct": round(share(units, store_units), 1),
                "value_pct": round(share(value, store_value), 1),
            })
        line_units = sum(c["units"] for c in categories)
        line_value = sum(float(by_category["value"].get(c["category"], 0.0)) for c in categories)
        lines.append({
            "line": line,
            "department": department,
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


def _similar_past_days(store, day):
    """Earlier days this month of the same kind as `day` (busy vs normal)."""
    busy = store.is_busy(day)
    return [d for d in range(1, day) if store.is_busy(d) == busy]


def _zone_for(store, zone, category, line):
    if zone is None and category is not None:
        zone = store.category_department[category]
    if zone is None and line is not None:
        zone = store.lines[line]
    if zone not in store.departments:
        raise ValueError(f"zone must be one of {store.departments} (or give a category/line)")
    return zone


def get_footfall(zone=None, category=None, line=None, day=None, hour=None, store=None):
    """
    Visitors to a floor zone (e.g. Menswear / Womenswear / Kidswear) so far
    today, compared with a typical day of the same kind by the same hour,
    plus the daily totals for the last 7 days. Pass a zone, or a
    category/line to use the zone it sits in.

    Used with pace and conversion to tell a traffic problem (fewer visitors
    than usual) apart from a conversion problem (normal visitors, but sales
    still collapsed — usually stock, sizing, price or service).
    """
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)
    store.require("footfall")
    zone = _zone_for(store, zone, category, line)

    ff = store.footfall[store.footfall["zone"] == zone]
    today = int(ff[(ff["day"] == day) & (ff["hour"] <= hour)]["visitors"].sum())

    similar = _similar_past_days(store, day)
    by_this_hour = ff[ff["day"].isin(similar) & (ff["hour"] <= hour)].groupby("day")["visitors"].sum()
    typical = float(by_this_hour.mean()) if len(by_this_hour) else None

    recent = ff[(ff["day"] < day) & (ff["day"] >= day - 7)].groupby("day")["visitors"].sum()

    return {
        "zone": zone,
        "as_of": {"day": day, "hour": hour},
        "visitors_today_so_far": today,
        "typical_visitors_by_this_hour": round(typical, 1) if typical is not None else None,
        "pct_vs_typical": round((today - typical) / typical * 100, 1) if typical else None,
        "compared_with_days": similar,
        "last_7_days_visitors": {int(d): int(v) for d, v in recent.items()},
    }


def get_today_pace(line=None, day=None, hour=None, store=None):
    """
    Today, hour by hour, for each line (or one line). Tracked at line level,
    not category level, because most single categories sell only a few units
    a day: hour-by-hour pace for them would be mostly noise.

    Today's target for a line = its monthly target phased by how busy today
    is (weekends and sale days carry more of the month). Expected by now =
    today's target x the share of a typical day that has usually sold by this
    hour, learned from this month's history.
    """
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)

    today_share_of_month = store.day_weight(day) / sum(store.day_weights.values())
    share_by_now = typical_share_of_day_sold(store, day, hour)

    sales = store.sales
    today_sales = sales[(sales["day"] == day) & (sales["hour"] <= hour)]
    sold_by_line = today_sales.groupby("line")["units_sold"].sum()

    lines = [line] if line is not None else list(store.lines)
    results = []
    for ln in lines:
        if ln not in store.lines:
            raise ValueError(f"Unknown line {ln!r}; lines are {list(store.lines)}")
        line_target = sum(store.targets[c] for c in store.categories_in(line=ln))
        target_today = line_target * today_share_of_month
        expected = target_today * share_by_now
        sold = int(sold_by_line.get(ln, 0))
        status, pct, beyond_noise = classify_pace(expected, sold)
        results.append({
            "line": ln,
            "department": store.lines[ln],
            "as_of": {"day": day, "hour": hour},
            "target_today": round(target_today, 1),
            "units_sold_today": sold,
            "expected_by_now": round(expected, 1),
            "pct_vs_pace": round(pct, 1),
            "status": status,
            "gap_beyond_normal_variation": beyond_noise,
        })
    return results


def _pct_change(new, old):
    return round((new - old) / old * 100, 1) if old else None


def get_conversion_metrics(category=None, line=None, zone=None, day=None, hour=None, store=None):
    """
    Conversion rate and units per transaction (UPT) for a category, a line or
    a whole floor zone, and a plain reading of whether a slowdown is a
    traffic problem or a conversion problem.

      conversion rate = transactions / visitors to the floor zone x 100
      UPT             = units sold / transactions

    Visitors are counted per zone, so a category's conversion is its share of
    its zone's visitors who bought from it.

    The reading compares "recent" (the last 3 days plus today so far, where a
    current problem shows up) with a "baseline" (the month before the last
    week, before recent problems began). Traffic is judged against what a
    typical day of the same kind (weekday vs weekend/sale day) brought in the
    baseline, so a weekend-heavy stretch isn't mistaken for a traffic change.
    """
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)
    store.require("footfall")
    store.require("transactions")

    if category is not None:
        categories, label = [category], category
        zone = store.category_department[category]
    elif line is not None:
        if line not in store.lines:
            raise ValueError(f"Unknown line {line!r}; lines are {list(store.lines)}")
        categories, label = store.categories_in(line=line), line
        zone = store.lines[line]
    elif zone in store.departments:
        categories, label = store.categories_in(department=zone), zone
    else:
        raise ValueError(f"Give a category, a line, or a zone from {store.departments}")

    units = store.sales[store.sales["category"].isin(categories)]
    bills = store.transactions[store.transactions["category"].isin(categories)]
    visitors = store.footfall[store.footfall["zone"] == zone]

    def in_window(df, first_day, last_day):
        """Rows from first_day..last_day, stopping at `hour` on `day` itself."""
        return df[
            (df["day"] >= first_day) & (df["day"] <= last_day)
            & ((df["day"] < day) | (df["hour"] <= hour))
        ]

    def summarise(first_day, last_day):
        v = int(in_window(visitors, first_day, last_day)["visitors"].sum())
        tx = int(in_window(bills, first_day, last_day)["transactions"].sum())
        u = int(in_window(units, first_day, last_day)["units_sold"].sum())
        return {
            "days": f"{first_day}-{last_day}" if first_day != last_day else str(first_day),
            "zone_visitors": v,
            "transactions": tx,
            "units": u,
            "conversion_rate_pct": round(tx / v * 100, 1) if v else None,
            "units_per_transaction": round(u / tx, 2) if tx else None,
        }

    recent_start = max(1, day - 3)
    baseline_end = max(1, day - 8)
    today = summarise(day, day)
    recent = summarise(recent_start, day)
    baseline = summarise(1, baseline_end)

    # Typical visitors for the recent days, from baseline days of the same kind.
    baseline_days = range(1, baseline_end + 1)
    typical_recent_visitors = 0.0
    for d in range(recent_start, day + 1):
        through = hour if d == day else store.hours[-1]
        same_kind = [b for b in baseline_days if store.is_busy(b) == store.is_busy(d)]
        per_day = visitors[visitors["day"].isin(same_kind) & (visitors["hour"] <= through)]
        typical_recent_visitors += per_day["visitors"].sum() / max(len(same_kind), 1)

    traffic_change = _pct_change(recent["zone_visitors"], typical_recent_visitors)
    conversion_change = _pct_change(recent["conversion_rate_pct"] or 0,
                                    baseline["conversion_rate_pct"] or 0)
    expected_recent_sales = (baseline["conversion_rate_pct"] or 0) / 100 * recent["zone_visitors"]

    # Same rule as pace: a drop counts as a problem only if it's past the
    # percentage line AND bigger than normal randomness for these counts.
    def beyond_noise(expected, actual):
        return expected > 0 and (expected - actual) > NORMAL_VARIATION_Z * math.sqrt(expected)

    traffic_past_line = traffic_change is not None and traffic_change < -TRAFFIC_DROP_PCT
    conversion_past_line = conversion_change is not None and conversion_change < -CONVERSION_DROP_PCT
    traffic_down = traffic_past_line and beyond_noise(typical_recent_visitors, recent["zone_visitors"])
    conversion_down = conversion_past_line and beyond_noise(expected_recent_sales,
                                                            recent["transactions"])

    if expected_recent_sales < MIN_TRANSACTIONS_FOR_READING:
        reading = "too_few_sales_to_judge"
        explanation = "Too few sales recently to tell traffic and conversion apart."
    elif traffic_down and conversion_down:
        reading = "traffic_and_conversion_down"
        explanation = "Fewer visitors than usual, and fewer of them are buying."
    elif traffic_down:
        reading = "traffic_problem"
        explanation = ("Fewer visitors than usual for these days, but those who come still buy "
                       "as usual: a footfall / marketing issue.")
    elif conversion_down:
        reading = "conversion_problem"
        explanation = ("Visitors are coming as usual but fewer are buying: usually stock, "
                       "sizes, price or service rather than marketing.")
    elif traffic_past_line or conversion_past_line:
        reading = "possible_dip"
        explanation = ("Somewhat lower than usual, but within the range normal ups and downs "
                       "could explain. Worth watching, not yet a proven problem.")
    else:
        reading = "normal"
        explanation = "Visitors and conversion are both in their usual range."

    return {
        "subject": label,
        "zone": zone,
        "as_of": {"day": day, "hour": hour},
        "today_so_far": today,
        "recent_last_3_days_and_today": recent,
        "baseline_earlier_this_month": baseline,
        "typical_visitors_for_recent_days": round(typical_recent_visitors),
        "visitors_change_vs_typical_pct": traffic_change,
        "conversion_rate_change_pct": conversion_change,
        "reading": reading,
        "explanation": explanation,
    }


# --- Verification output -------------------------------------------------------

_STATUS_LABEL = {
    "behind": "BEHIND",
    "drifting": "drifting",
    "on_pace": "on pace",
    "ahead": "AHEAD",
    "too_early": "too early",
}


def _print_pace_table(store):
    results = get_category_pace(store=store)
    day, hour = store.today_day, store.default_hour
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
            f"{r['line']:<7}{store.category_product[r['category']]:<17}{r['monthly_target']:>7}"
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


def _print_contribution(store):
    report = get_contribution(store=store)
    print(f"\nContribution report, month-to-date as of day {store.today_day}, {store.default_hour}:00\n")
    print(f"{'Line':<8}{'Units':>7}{'Value (INR)':>14}{'Units %':>9}{'Value %':>9}")
    print("-" * 47)
    for l in report["lines"]:
        print(f"{l['line']:<8}{l['units']:>7}{l['value']:>14,}{l['units_pct']:>8}%{l['value_pct']:>8}%")
    print("-" * 47)
    print(f"{'Store':<8}{report['store_units']:>7}{report['store_value']:>14,}")
    print("\nIntegrity checks passed: " + "; ".join(report["checks_passed"]))


def _print_today_by_line(store, hours):
    print(f"\nToday (day {store.today_day}) by line — status as the day goes on\n")
    snapshots = {h: {r["line"]: r for r in get_today_pace(hour=h, store=store)} for h in hours}
    print(f"{'Line':<8}" + "".join(f"{f'{h}:00':>22}" for h in hours))
    for ln in store.lines:
        cells = []
        for h in hours:
            r = snapshots[h][ln]
            cells.append(f"{r['units_sold_today']}/{r['expected_by_now']:.0f} {_STATUS_LABEL[r['status']]}")
        print(f"{ln:<8}" + "".join(f"{c:>22}" for c in cells))
    print("  (cells show units sold today / expected by then, and status)")


def _print_footfall(store):
    print(f"\nFootfall today vs a typical day of the same kind, by {store.default_hour}:00\n")
    for zone in store.departments:
        f = get_footfall(zone=zone, store=store)
        print(f"  {zone:<11} today {f['visitors_today_so_far']:>4}   typical "
              f"{f['typical_visitors_by_this_hour']:>6}   ({f['pct_vs_typical']:+.0f}%)")


def _print_conversion(store):
    print("\nTraffic vs conversion: last 3 days + today, compared with the first half of the month\n")
    print(f"{'Subject':<24}{'Visitors vs typical':>21}{'Conversion':>20}{'UPT':>14}   Reading")
    subjects = [
        {"category": "Women Tops"}, {"zone": "Womenswear"},
        {"category": "Men Casual Trousers"}, {"category": "Men Denim Jeans"},
        {"category": "Little Boys Tops"}, {"category": "Men Formal Blazers"},
    ]
    for kwargs in subjects:
        m = get_conversion_metrics(**kwargs, store=store)
        b, r = m["baseline_earlier_this_month"], m["recent_last_3_days_and_today"]
        print(
            f"{m['subject']:<24}{r['zone_visitors']:>6} vs {m['typical_visitors_for_recent_days']:<4}"
            f"({m['visitors_change_vs_typical_pct']:+.0f}%)"
            f"{b['conversion_rate_pct']:>8}% -> {r['conversion_rate_pct']:<5}%"
            f"{b['units_per_transaction'] or 0:>7} -> {r['units_per_transaction'] or 0:<5}"
            f"  {m['reading']}"
        )


if __name__ == "__main__":
    demo, _, _ = resolve()
    _print_pace_table(demo)
    _print_contribution(demo)
    _print_today_by_line(demo, [11, 14, 16, 19])
    _print_footfall(demo)
    _print_conversion(demo)
