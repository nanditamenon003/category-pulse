"""
Month-end range: where the month is likely to land, as a range rather than
one number, and the chance of reaching the target.

Simple statistics, not a trained model, so every part can be explained:
  - the likely finish is the pace projection: sales so far, plus the rest of
    the month at the same rate, with busier days (weekends, sale days)
    weighted as the store's own data shows
  - the width comes from how much this store's daily sales actually bounce
    around that rate, plus the uncertainty in the rate itself (a rate learned
    from a few days is less certain than one learned from three weeks)
  - the range covers the middle 80% of outcomes: roughly 8 months in 10 like
    this one would finish inside it
It can't know about a stockout next week or a month-end push, so it's always
labelled as a projection. Figures are in the store's measure (units or rupees).
"""

import math

from kpi import _validate_moment, typical_share_of_day_sold
from store import resolve

# The middle 80% of a normal distribution: 1.28 standard deviations each side.
RANGE_Z = 1.2816
MIN_DAYS_FOR_RANGE = 5


# How to say what the month is measured against: most stores have targets, but a
# store without them is judged against last year or its own early pace.
GOAL_PHRASE = {"target": "keeps the month on course for its target",
               "last year": "keeps the month on course to match last year",
               "own pace": "keeps up the pace the month started with"}
CHANCE_PHRASE = {"target": "chance of target", "last year": "chance of beating last year",
                 "own pace": "chance of keeping the early pace"}


def yardstick_of(store, categories):
    """'target', 'last year' or 'own pace': what these categories are measured against, taken together."""
    sources = {store.target_source.get(store.measure, {}).get(c) or "target" for c in categories}
    return next(iter(sources)) if sources in ({"last year"}, {"own pace"}) else "target"


def _normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def month_end_range(daily, weights_done, share_today, weight_today, weight_after, sold_today=0.0):
    """
    The core calculation, on plain numbers (so it can be tested on its own):
      daily         sales on each complete day so far
      weights_done  how busy each of those days is (same order)
      share_today   how much of today's usual trading is done (0 if today hasn't started, or is complete)
      weight_today  today's busy-ness (0 if today is already among the complete days)
      weight_after  total busy-ness of the days after today
      sold_today    sales so far today (0 if today is complete or not started)
    Returns (likely, low, high, standard deviation) for the month's total, or None with too few days.
    """
    if len(daily) < MIN_DAYS_FOR_RANGE:
        return None
    sold = sum(daily) + sold_today
    w_done = sum(weights_done) + weight_today * share_today
    w_rest = weight_today * (1 - share_today) + weight_after
    rate = sold / w_done if w_done else 0.0
    likely = sold + rate * w_rest
    if rate <= 0:
        return likely, sold, sold, 0.0
    # How much days bounce around the rate, compared with pure chance (1 = pure chance).
    day_rate = sum(daily) / sum(weights_done)
    spread = sum((s - day_rate * w) ** 2 / (day_rate * w) for s, w in zip(daily, weights_done) if w > 0)
    spread = max(1.0, spread / max(len(daily) - 1, 1)) if day_rate > 0 else 1.0
    # The rest of the month's days, plus the uncertainty in the rate itself.
    variance = spread * rate * w_rest * (1 + w_rest / w_done)
    sd = math.sqrt(variance)
    # With only a few days to learn the wobble from, it's easy to underestimate: widen a little
    # (the usual small-sample correction, which fades as the days add up).
    z = RANGE_Z + (RANGE_Z ** 3 + RANGE_Z) / (4 * (len(daily) - 1))
    return likely, max(sold, likely - z * sd), likely + z * sd, sd


def get_month_end_range(category=None, line=None, day=None, hour=None, store=None, categories=None):
    """
    Where a category, a line, a list of `categories` or the whole store (none
    given) is likely to finish the month: likely, low and high (the middle
    80%), the target and the chance of reaching it. None for "likely" when
    it's too early to say.
    """
    store, day, hour = resolve(store, day, hour)
    _validate_moment(store, day, hour)
    if categories is not None:
        categories, subject = list(categories), "Categories with a target"
    elif category is not None:
        categories, subject = [category], category
    elif line is not None:
        categories, subject = store.categories_in(line=line), line
    else:
        categories, subject = list(store.categories), "Whole store"
    if not categories or any(c not in store.categories for c in categories):
        raise ValueError(f"Unknown category or line: {category or line!r}")

    column = store.sold_column
    rows = store.sales[store.sales["category"].isin(categories)]
    by_day = rows[rows["day"] <= day].groupby("day")[column].sum()
    complete = hour >= store.hours[-1]
    last_full = day if complete else day - 1
    days = list(range(1, last_full + 1))
    daily = [float(by_day.get(d, 0.0)) for d in days]
    weights = store.day_weights
    sold_today = 0.0 if complete else float(rows[(rows["day"] == day) & (rows["hour"] <= hour)][column].sum())
    share = 0.0 if complete else typical_share_of_day_sold(store, day, hour)
    after = sum(weights[d] for d in range(day + 1, store.days_in_month + 1))
    result = month_end_range(daily, [weights[d] for d in days], share,
                             0.0 if complete else weights[day], after, sold_today)

    targets = [store.active_targets.get(c) for c in categories]
    target = sum(t for t in targets if t) if all(targets) else None
    sold = sum(daily) + sold_today
    finished = day >= store.days_in_month and complete
    out = {
        "subject": subject,
        "as_of": {"day": day, "hour": hour},
        "measure": store.measure,
        "sold_so_far": round(sold),
        "target": target,
        "month_finished": finished,
        "yardstick": yardstick_of(store, categories),
        "based_on_days": len(days),
        "note": ("A projection: the likely finish at the current rate, and the range roughly 8 "
                 "months in 10 like this one would finish inside, given how much this store's "
                 "daily sales usually vary. It can't know about future stockouts or promotions."),
    }
    if finished:
        return out | {"likely": round(sold), "low": round(sold), "high": round(sold),
                      "chance_of_target_pct": (100 if target and sold >= target else 0) if target else None}
    if result is None:
        return out | {"likely": None, "low": None, "high": None, "chance_of_target_pct": None,
                      "note": f"Too early for a range: it needs at least {MIN_DAYS_FOR_RANGE} full days of sales."}
    likely, low, high, sd = result
    if category is not None and store.has_stock:
        import stock  # here, not at the top: stock's own checks are only needed for one category

        verdict = stock.check_size_runs(category, day, hour, store=store)["verdict"]
        if verdict in ("stockout", "broken_size_run", "running_out"):
            out["caution"] = ("It's short of stock right now, and the range assumes it keeps selling "
                              "as it has: it will finish lower unless stock arrives.")
    chance = None
    if target:
        chance = 100.0 if sd == 0 and likely >= target else 0.0 if sd == 0 else \
            100 * (1 - _normal_cdf((target - likely) / sd))
        chance = int(min(99, max(1, 5 * round(chance / 5)))) if 0 < chance < 100 else int(chance)
    return out | {"likely": round(likely), "low": round(low), "high": round(high),
                  "chance_of_target_pct": chance}


def get_month_path(day=None, hour=None, store=None):
    """
    The month so far, for the "Month so far" chart: running sales to date and
    the path to target (both for the categories with something to judge them
    by), and where the month is likely to land. None if no category has one.
    """
    store, day, hour = resolve(store, day, hour)
    judged = [c for c in store.categories if store.active_targets.get(c)]
    if not judged:
        return None
    rows = store.sales[store.sales["category"].isin(judged)]
    rows = rows[(rows["day"] < day) | ((rows["day"] == day) & (rows["hour"] <= hour))]
    by_day = rows.groupby("day")[store.sold_column].sum()
    sold, running = [], 0.0
    for d in range(1, day + 1):
        running += float(by_day.get(d, 0.0))
        sold.append(round(running))
    target = sum(store.active_targets[c] for c in judged)
    weights = store.day_weights
    total_weight = sum(weights.values())
    plan, done = [], 0.0
    for d in range(1, store.days_in_month + 1):
        done += weights[d]
        plan.append(round(target * done / total_weight))
    return {"sold": sold, "plan": plan, "target": target, "measure": store.measure,
            "yardstick": yardstick_of(store, judged),
            "range": get_month_end_range(day=day, hour=hour, store=store, categories=judged)}


def chance_words(pct):
    """A chance as people say it."""
    if pct is None:
        return None
    if pct >= 90:
        return "very likely"
    if pct >= 65:
        return "likely"
    if pct >= 35:
        return "about even"
    if pct >= 10:
        return "unlikely"
    return "very unlikely"


if __name__ == "__main__":
    demo, _, _ = resolve()
    print(f"Month-end range as of day {demo.today_day}, {demo.default_hour}:00 (middle 80%):\n")
    for subject in [None] + list(demo.lines)[:3] + ["Men Casual Trousers", "Women Tops", "Little Boys Tops"]:
        kwargs = {"line": subject} if subject in demo.lines else {"category": subject}
        r = get_month_end_range(**kwargs, store=demo)
        print(f"  {r['subject']:<22} sold {r['sold_so_far']:>6,}  likely {r['likely']:>6,} "
              f"({r['low']:,} to {r['high']:,})  target {r['target']:>6,}  "
              f"chance {r['chance_of_target_pct']}% ({chance_words(r['chance_of_target_pct'])})")
    r = get_month_end_range(store=demo.for_measure("value"))
    print(f"\n  In rupees, whole store: likely {demo.for_measure('value').amount(r['likely'])} "
          f"({demo.for_measure('value').amount(r['low'])} to {demo.for_measure('value').amount(r['high'])}), "
          f"chance {r['chance_of_target_pct']}%")
