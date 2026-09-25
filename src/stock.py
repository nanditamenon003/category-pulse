"""
Stock logic: what's on the shelf, what came in, and what that means.

get_stock_status() reads what's left right now; get_stock_history() looks
back over recent days and spots deliveries, so the agent can find the cause
of a problem (a missed delivery, a delivery missing key sizes), not just the
symptom. check_size_runs() tells a broken size run apart from a stockout,
and get_last_piece_alerts() flags sizes the moment they drop to one unit.

Stock can be counted every hour (the demo store), once a day, or just once:
each function uses the latest count at or before the moment asked about,
and says which count that was.
"""

from config import (
    BROKEN_RUN_MIN_SHARE,
    CORE_DEPLETED_MAX_UNITS,
    LAST_PIECE_THRESHOLD,
    STOCKOUT_MAX_SHARE,
)
from store import resolve


def _validate(store, category, day, hour):
    if category not in store.categories:
        raise ValueError(f"Unknown category {category!r}")
    if not 1 <= day <= store.today_day:
        raise ValueError(f"day must be between 1 and {store.today_day} (today), got {day}")
    if hour not in store.hours:
        raise ValueError(
            f"hour must be a store hour between {store.hours[0]} and {store.hours[-1]}, got {hour}"
        )
    store.require("stock")


def _remaining_by_size(store, category, day, hour):
    """Units left by size at exactly (day, hour); sizes not in the count have none left."""
    found = store.stock_lookup.get((category, day, hour), {})
    return {size: found.get(size, 0) for size in store.sizes[category]}


def _closing_hour(store, day):
    """The last hour stock was counted on `day`, or None if it wasn't counted."""
    hours = store.stock_hours_by_day.get(day)
    return hours[-1] if hours else None


def get_stock_status(category, day=None, hour=None, store=None):
    """
    Units left on the shelf by size for a category at the end of `hour` on
    `day` (from the latest stock count at or before then), plus which sizes
    are completely out, which of those are core sizes (the ones most
    customers need), and whether the whole category is out.
    """
    store, day, hour = resolve(store, day, hour)
    _validate(store, category, day, hour)

    counted = store.latest_stock_moment(day, hour)
    remaining = (_remaining_by_size(store, category, *counted) if counted
                 else {size: 0 for size in store.sizes[category]})
    core = store.core_sizes[category]
    out_sizes = [s for s, units in remaining.items() if units == 0]

    return {
        "category": category,
        "as_of": {"day": day, "hour": hour},
        "stock_counted_at": {"day": counted[0], "hour": counted[1]} if counted else None,
        "remaining_by_size": remaining,
        "total_remaining": sum(remaining.values()),
        "core_sizes": core,
        "core_units_remaining": sum(remaining[s] for s in core),
        "out_of_stock_sizes": out_sizes,
        "core_sizes_out": [s for s in core if s in out_sizes],
        "is_completely_out": len(out_sizes) == len(remaining),
    }


def _deliveries_received(store, category, up_to_day):
    """
    Deliveries aren't recorded separately, so they're inferred: if a day's
    first stock count shows more than the previous day closed with (after
    adding back what sold before that count), the difference was delivered
    overnight.
    """
    core = store.core_sizes[category]
    sales = store.sales[store.sales["category"] == category]

    deliveries = []
    for d in range(2, up_to_day + 1):  # day 1 is the month's opening stock
        prev_hour, hours_today = _closing_hour(store, d - 1), store.stock_hours_by_day.get(d)
        if prev_hour is None or not hours_today:
            continue
        first_count = hours_today[0]
        prev_close = _remaining_by_size(store, category, d - 1, prev_hour)
        at_first_count = _remaining_by_size(store, category, d, first_count)
        sold = sales[(sales["day"] == d) & (sales["hour"] <= first_count)]
        sold_before_count = sold.groupby("size")["units_sold"].sum().to_dict()
        received = {
            s: at_first_count[s] + sold_before_count.get(s, 0) - prev_close[s]
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


def get_stock_history(category, day=None, hour=None, days_back=10, store=None):
    """
    The recent stock story for a category:
      - end-of-day stock (total and core sizes) for the last `days_back` days
      - every delivery received this month, with the sizes it contained
      - scheduled delivery days (the store's weekly delivery day) on which
        nothing arrived for this category
    This is how the agent finds the cause behind a stockout or broken size
    run, not just the symptom.
    """
    store, day, hour = resolve(store, day, hour)
    _validate(store, category, day, hour)

    core = store.core_sizes[category]
    daily = []
    for d in range(max(1, day - days_back + 1), day + 1):
        hours = [h for h in store.stock_hours_by_day.get(d, []) if d < day or h <= hour]
        if not hours:
            continue  # no stock count that day
        remaining = _remaining_by_size(store, category, d, hours[-1])
        daily.append({
            "day": d,
            "as_of_hour": hours[-1],
            "total_remaining": sum(remaining.values()),
            "core_units_remaining": sum(remaining[s] for s in core),
        })

    deliveries = _deliveries_received(store, category, day)
    delivered_days = {d["day"] for d in deliveries}
    scheduled_days = [] if store.delivery_weekday is None else [
        d for d in range(2, day + 1) if store.weekday(d) == store.delivery_weekday
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


def _usual_stock_level(store, category, day):
    """
    The category's typical end-of-day stock earlier this month (the median,
    so a few unusual days don't distort it). Learned from the data, the way
    it would be from a real store's stock history. None without history.
    """
    closes = []
    for d in range(1, day):
        close = _closing_hour(store, d)
        if close is not None:
            closes.append(sum(store.stock_lookup.get((category, d, close), {}).values()))
    if not closes:
        return None
    closes.sort()
    middle = len(closes) // 2
    return float(closes[middle] if len(closes) % 2 else (closes[middle - 1] + closes[middle]) / 2)


def check_size_runs(category, day=None, hour=None, store=None):
    """
    Classifies a category's stock health, keeping a broken size run distinct
    from a plain stockout (see the thresholds in config.py):
      - "stockout": almost nothing left in any size
      - "broken_size_run": core sizes gone, but the shelf still looks full
      - "running_out": core sizes gone and overall stock getting low
      - "healthy": core sizes available
    Without earlier stock counts to learn the usual level from, it judges by
    sizes alone: every size down to its last unit is a stockout, and core
    sizes gone while other sizes still have stock is a broken size run.
    """
    store, day, hour = resolve(store, day, hour)
    _validate(store, category, day, hour)

    status = get_stock_status(category, day, hour, store=store)
    remaining = status["remaining_by_size"]
    core = status["core_sizes"]
    usual = _usual_stock_level(store, category, day)
    total = status["total_remaining"]
    share_of_usual = total / usual if usual else None
    # With no size data there are no core sizes, so no size run to break.
    core_depleted = bool(core) and all(remaining[s] <= CORE_DEPLETED_MAX_UNITS for s in core)

    if usual is None:
        others_in_stock = any(u > CORE_DEPLETED_MAX_UNITS for s, u in remaining.items() if s not in core)
        if all(u <= CORE_DEPLETED_MAX_UNITS for u in remaining.values()):
            verdict = "stockout"
        elif core_depleted and others_in_stock:
            verdict = "broken_size_run"
        else:
            verdict = "healthy"
    elif share_of_usual < STOCKOUT_MAX_SHARE:
        verdict = "stockout"
    elif core_depleted and share_of_usual >= BROKEN_RUN_MIN_SHARE:
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


def get_stock_health_report(day=None, hour=None, store=None):
    """Every category whose stock isn't healthy right now (for the alert banner)."""
    store, day, hour = resolve(store, day, hour)
    report = [check_size_runs(c, day, hour, store=store) for c in store.categories]
    return [r for r in report if r["verdict"] != "healthy"]


def suggest_supply_action(category, day=None, hour=None, health=None, store=None):
    """
    What to do about supply for a category that's out or has a broken size
    run, stated only as far as the delivery history actually shows it.
    Returns None if its stock is healthy.
    """
    store, day, hour = resolve(store, day, hour)
    if health is None:
        health = check_size_runs(category, day, hour, store=store)
    if health["verdict"] == "healthy":
        return None

    history = get_stock_history(category, day, hour, store=store)
    last = history["deliveries_received"][-1] if history["deliveries_received"] else None
    missed = history["scheduled_deliveries_not_received"]

    if health["verdict"] == "broken_size_run":
        sizes = " and ".join(health["core_sizes"])
        if last and all(s in last["core_sizes_missing"] for s in health["core_sizes"]):
            why = f"the last delivery, on day {last['day']}, came without them"
        elif last:
            why = f"they've sold through since the last delivery, on day {last['day']}"
        elif store.has_stock_history:
            why = "no delivery has arrived this month"
        else:
            return f"Request a transfer of sizes {sizes} from a nearby store."
        return f"Request a transfer of sizes {sizes} from a nearby store ({why})."

    if missed:
        days = " and ".join(f"day {d}" for d in missed)
        return (f"Chase the delivery due on {days}, which never arrived, or request an "
                f"inter-store transfer.")
    return "Request a replenishment or an inter-store transfer."


def get_last_piece_alerts(day=None, hour=None, store=None):
    """
    Every size that dropped to exactly LAST_PIECE_THRESHOLD unit(s) on `day`
    up to `hour`, with the hour it happened. Fires once, at the moment of the
    drop: stock just before that count (what's left + what sold since the
    previous count that day) was above the threshold, and at the count it is
    exactly at it. Deliveries arrive before opening, so they're already in
    that "before".
    """
    store, day, hour = resolve(store, day, hour)
    store.require("stock")

    counts_today = [h for h in store.stock_hours_by_day.get(day, []) if h <= hour]
    today = store.sales[(store.sales["day"] == day) & (store.sales["hour"] <= hour)]
    sold = {}  # (category, size) -> {hour: units sold}
    for (c, s, sold_hour), units_sold in today.groupby(["category", "size", "hour"])["units_sold"].sum().items():
        sold.setdefault((c, s), {})[sold_hour] = units_sold

    alerts = []
    for category in store.categories:
        previous = None
        for h in counts_today:
            remaining = _remaining_by_size(store, category, day, h)
            for size, units in remaining.items():
                sold_since = sum(u for sold_hour, u in sold.get((category, size), {}).items()
                                 if (previous is None or sold_hour > previous) and sold_hour <= h)
                if units == LAST_PIECE_THRESHOLD and units + sold_since > LAST_PIECE_THRESHOLD:
                    alerts.append({
                        "category": category,
                        "line": store.category_line[category],
                        "size": size,
                        "is_core_size": size in store.core_sizes[category],
                        "day": day,
                        "hour": h,
                        "units_remaining": LAST_PIECE_THRESHOLD,
                    })
            previous = h
    return sorted(alerts, key=lambda a: (a["hour"], a["category"]))


def get_days_of_cover(category, day=None, hour=None, store=None):
    """
    A projection, not a fact: roughly how many days each size will last if it
    keeps selling at its average rate over the last 7 full days. Flags sizes
    likely to run out before the next scheduled delivery, so the team can act
    before the last piece, not at it.
    """
    store, day, hour = resolve(store, day, hour)
    _validate(store, category, day, hour)

    remaining = get_stock_status(category, day, hour, store=store)["remaining_by_size"]
    sales = store.sales
    window = sales[(sales["category"] == category) & (sales["day"] >= day - 7) & (sales["day"] < day)]
    days_in_window = min(7, day - 1)
    daily_rate = window.groupby("size")["units_sold"].sum() / max(days_in_window, 1)

    next_delivery = None if store.delivery_weekday is None else next(
        (d for d in range(day + 1, store.days_in_month + 1)
         if store.weekday(d) == store.delivery_weekday), None)
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
            # Only sizes that still have stock can "run out"; empty ones already have.
            "likely_out_before_next_delivery": (
                units > 0 and cover is not None and days_to_delivery is not None
                and cover < days_to_delivery
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

def _print_status(store, category):
    s = get_stock_status(category, store=store)
    sizes = "  ".join(f"{size}:{units}" for size, units in s["remaining_by_size"].items())
    print(f"\n{category} — stock now (day {store.today_day}, {store.default_hour}:00)")
    print(f"  {sizes}")
    print(f"  total {s['total_remaining']}, core ({'/'.join(s['core_sizes'])}) "
          f"{s['core_units_remaining']}, core sizes out: {s['core_sizes_out'] or 'none'}, "
          f"completely out: {s['is_completely_out']}")


def _print_history(store, category):
    h = get_stock_history(category, store=store)
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


def _print_stock_health(store):
    print(f"\nStock health, day {store.today_day} {store.default_hour}:00 (categories that aren't healthy)\n")
    for r in get_stock_health_report(store=store):
        core = ", ".join(f"{s}:{u}" for s, u in r["core_remaining_by_size"].items())
        print(f"  {r['category']:<24} {r['verdict']:<16} core [{core}]  total {r['total_remaining']} "
              f"({r['total_as_pct_of_usual']}% of usual {r['usual_stock_level']})")
    healthy = check_size_runs("TJM Denim Bottom", store=store)
    print(f"  (control) TJM Denim Bottom: {healthy['verdict']}")


def _print_last_piece_alerts(store):
    alerts = get_last_piece_alerts(store=store)
    print(f"\nLast-piece alerts today (day {store.today_day}) up to {store.default_hour}:00: {len(alerts)}\n")
    for a in alerts:
        core = "  <- core size" if a["is_core_size"] else ""
        print(f"  {a['hour']}:00  {a['category']:<24} size {a['size']:<5} 1 left{core}")


def _print_days_of_cover(store, category):
    c = get_days_of_cover(category, store=store)
    print(f"\n{category} — days of cover (projection), next delivery day "
          f"{c['next_scheduled_delivery_day']}")
    for s in c["sizes"]:
        cover = s["projected_days_of_cover"]
        cover_text = f"{cover} days" if cover is not None else "no recent sales"
        warn = "  <- likely out before delivery" if s["likely_out_before_next_delivery"] else ""
        print(f"  {s['size']:<5} {s['units_remaining']:>3} left, "
              f"{s['avg_units_sold_per_day_last_7_days']}/day -> {cover_text}{warn}")


if __name__ == "__main__":
    demo, _, _ = resolve()
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_status(demo, cat)
    for cat in ("Womens Knit Top", "THM Non Denim Bottom", "TJM Denim Bottom"):
        _print_history(demo, cat)
    _print_stock_health(demo)
    _print_last_piece_alerts(demo)
    _print_days_of_cover(demo, "THM Polo")
