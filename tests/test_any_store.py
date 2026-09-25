"""
Proves the engine works on a store that looks nothing like the demo.

A small made-up store: two lines on two floors, five categories, June,
open 11:00-21:00, stock counted once a day at close, weekly deliveries on
Thursdays, and no loyalty data. Two problems are planted in it:
  - Mens Trouser: every delivery arrives without waists 32 and 34
    (a broken size run, which must be found from daily stock counts alone)
  - Ladies Kurta: the day-11 and day-18 deliveries never arrive (a stockout)
A second, bare version keeps only daily sales totals per category (no
hours, sizes, value, bills, stock or visitors), to check that every feature
either works or says plainly what's missing.

Run from the project folder:  python tests/test_any_store.py
"""

import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import diagnosis  # noqa: E402
import digest  # noqa: E402
import kpi  # noqa: E402
import loyalty  # noqa: E402
import staffing  # noqa: E402
import stock  # noqa: E402
from store import MissingData, StoreDataError, build_store  # noqa: E402

MONTH, LAST_DAY, HOURS = (2026, 6), 18, list(range(11, 21))
DELIVERY_WEEKDAY = 3  # Thursday

# (line, product, floor, monthly target, price, {size: share of demand})
PLAN = [
    ("Mens", "Shirt", "Ground", 300, 1800, {"S": .2, "M": .35, "L": .3, "XL": .15}),
    ("Mens", "Trouser", "Ground", 200, 2200, {"28": .1, "30": .2, "32": .3, "34": .25, "36": .15}),
    ("Mens", "Jacket", "Ground", 40, 5200, {"M": .4, "L": .4, "XL": .2}),
    ("Ladies", "Kurta", "First", 250, 1500, {"XS": .1, "S": .3, "M": .3, "L": .2, "XL": .1}),
    ("Ladies", "Top", "First", 180, 1200, {"S": .35, "M": .4, "L": .25}),
]
SHORT_SIZES = {"Trouser": ["32", "34"]}
MISSED_DELIVERY = {"Kurta": [11, 18]}


def make_tables(seed=7):
    rng = np.random.default_rng(seed)
    days = range(1, LAST_DAY + 1)

    def weekday(d):
        return date(*MONTH, d).weekday()

    def day_factor(d):
        return 1.4 if weekday(d) >= 5 else 0.85

    per_day = {p[1]: p[3] / 30 for p in PLAN}
    par = {p[1]: {s: math.ceil(per_day[p[1]] * 10 * w) for s, w in p[5].items()} for p in PLAN}
    shelf = {k: dict(v) for k, v in par.items()}

    sales, stock_rows, footfall = [], [], []
    for d in days:
        if weekday(d) == DELIVERY_WEEKDAY and d > 1:
            for line, product, *_ in PLAN:
                if d in MISSED_DELIVERY.get(product, []):
                    continue
                for size, level in par[product].items():
                    if size not in SHORT_SIZES.get(product, []):
                        shelf[product][size] = max(shelf[product][size], level)
        for h in HOURS:
            for line, product, floor, target, price, mix in PLAN:
                for size, share in mix.items():
                    want = rng.poisson(per_day[product] * day_factor(d) * share / len(HOURS))
                    sold = min(want, shelf[product][size])
                    shelf[product][size] -= sold
                    sales.append({"date": date(*MONTH, d), "hour": h, "line": line, "product": product,
                                  "size": size, "units": sold, "value": sold * price})
        for line, product, *_ in PLAN:
            for size, units in shelf[product].items():
                stock_rows.append({"date": date(*MONTH, d), "line": line, "product": product,
                                   "size": size, "units": units})
        for floor in ("Ground", "First"):
            normal = sum(per_day[p[1]] for p in PLAN if p[2] == floor) * day_factor(d)
            for h in HOURS:
                footfall.append({"date": date(*MONTH, d), "hour": h, "department": floor,
                                 "visitors": rng.poisson(normal / len(HOURS) / 0.25)})

    sales = pd.DataFrame(sales)
    # Bills: about 1.3 units per bill in each category-hour, split evenly across its size rows.
    per_hour = sales.groupby(["date", "hour", "line", "product"])["units"].transform("sum")
    rows = sales.groupby(["date", "hour", "line", "product"])["units"].transform("size")
    sales["transactions"] = np.ceil(per_hour / 1.3) / rows
    targets = pd.DataFrame([{"line": p[0], "product": p[1], "department": p[2], "target": p[3]}
                            for p in PLAN])
    return targets, sales, pd.DataFrame(stock_rows), pd.DataFrame(footfall)


def full_store():
    targets, sales, stock_df, footfall = make_tables()
    return build_store(targets, sales, stock_df, footfall, name="Test store",
                       delivery_weekday=DELIVERY_WEEKDAY)


def bare_store():
    targets, sales, _, _ = make_tables()
    daily = sales.groupby(["date", "line", "product"], as_index=False)["units"].sum()
    return build_store(targets.drop(columns="department"), daily, name="Bare test store")


def raises(exc, fn):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError(f"expected {exc.__name__}")


def test_full_store():
    s = full_store()
    assert (s.month_name, s.days_in_month, s.today_day, s.hours) == ("June", 30, 18, HOURS)
    assert s.departments == ["Ground", "First"] and s.default_hour == 20
    assert s.core_sizes["Mens Trouser"] == ["32", "34"], s.core_sizes["Mens Trouser"]
    assert s.has_stock_history and s.has_visitor_hours and s.has_transactions and not s.has_loyalty

    pace = {p["category"]: p for p in kpi.get_category_pace(store=s)}
    kpi.get_contribution(store=s)  # checks its own totals
    kpi.get_today_pace(store=s)

    trousers = diagnosis.diagnose("Mens Trouser", store=s)
    kurta = diagnosis.diagnose("Ladies Kurta", store=s)
    assert trousers["cause"] == "broken_size_run", trousers
    assert kurta["cause"] == "stockout", kurta
    assert sum("never arrived" in e for e in kurta["evidence"]) == 2, kurta["evidence"]

    history = stock.get_stock_history("Mens Trouser", store=s)
    assert [d["day"] for d in history["deliveries_received"]] == [4, 11, 18]
    assert all(d["core_sizes_missing"] == ["32", "34"] for d in history["deliveries_received"])

    ideas = {i["category"]: i for i in loyalty.get_cross_sell_ideas(store=s)}
    assert ideas["Ladies Kurta"]["type"] == "substitute" and ideas["Ladies Kurta"]["lead_tier"] is None
    raises(MissingData, lambda: loyalty.get_tier_playbook("Mens Shirt", store=s))

    rec = staffing.get_staffing_recommendation(store=s)
    assert rec["weekday"] == "Friday" and set(rec["suggested_floor_split_at_busiest_hour_pct"]) == {"Ground", "First"}
    text = digest.generate_digest(store=s)
    assert "Close of day 18" in text and "Mens Trouser" in text and "Ladies Kurta" in text

    print("Full test store (June, 11:00-21:00, stock counted daily, no loyalty data)")
    for cat, p in pace.items():
        d = diagnosis.diagnose(cat, store=s)
        print(f"  {cat:<14} {p['units_sold_so_far']:>4} of {p['monthly_target']:<4} "
              f"{p['pct_vs_pace']:+5.0f}%  {p['status']:<9} cause: {d['cause']}")
    print(f"  learned core sizes: " + ", ".join(f"{c.split()[1]} {'/'.join(v)}"
                                                 for c, v in s.core_sizes.items()))
    print(f"  staffing for day 19 ({rec['weekday']}): busiest hour {rec['store_busiest_hour']}:00, "
          f"split {rec['suggested_floor_split_at_busiest_hour_pct']}")
    print("\n  End-of-day summary:\n" + "\n".join("    " + p for p in text.split("\n\n")))


def test_bare_store():
    s = bare_store()
    assert not (s.hourly or s.has_sizes or s.has_value or s.has_transactions
                or s.has_stock or s.has_footfall or s.has_loyalty)
    assert s.departments == ["Whole store"]

    pace = kpi.get_category_pace(store=s)
    assert sum(p["units_sold_so_far"] for p in pace) > 0
    kpi.get_contribution(store=s)
    kpi.get_today_pace(store=s)
    missing = [raises(MissingData, fn) for fn in (
        lambda: stock.get_stock_status("Mens Shirt", store=s),
        lambda: kpi.get_conversion_metrics(category="Mens Shirt", store=s),
        lambda: staffing.get_staffing_recommendation(store=s),
    )]
    behind = [d for d in diagnosis.diagnose_store(store=s) if d["status"] == "behind"]
    assert behind and all(any("no stock counts" in e for e in d["evidence"]) for d in behind)
    text = digest.generate_digest(store=s)

    print("\nBare test store (daily sales totals only)")
    for p in pace:
        print(f"  {p['category']:<14} {p['units_sold_so_far']:>4} of {p['monthly_target']:<4} "
              f"{p['pct_vs_pace']:+5.0f}%  {p['status']}")
    print("  what it says when data is missing:")
    for m in missing:
        print(f"    - {m}")
    print(f"  a diagnosis: {behind[0]['category']}: " + " ".join(behind[0]["evidence"][1:]))
    print("\n  End-of-day summary:\n" + "\n".join("    " + p for p in text.split("\n\n")))


def test_bad_tables():
    targets, sales, stock_df, _ = make_tables()
    two_months = sales.copy()
    two_months.loc[0, "date"] = date(2026, 7, 1)
    no_target = sales.copy()
    no_target.loc[0, "product"] = "Socks"
    text_units = sales.copy().astype({"units": object})
    text_units.loc[0, "units"] = "five"
    late_stock = stock_df.copy()
    late_stock.loc[0, "date"] = date(2026, 6, 25)
    messages = [
        raises(StoreDataError, lambda: build_store(targets, two_months)),
        raises(StoreDataError, lambda: build_store(targets, no_target)),
        raises(StoreDataError, lambda: build_store(targets, text_units)),
        raises(StoreDataError, lambda: build_store(targets, sales, late_stock)),
        raises(StoreDataError, lambda: build_store(targets.drop(columns="target"), sales)),
    ]
    print("\nTables it refuses, and what it says:")
    for m in messages:
        print(f"  - {m}")


if __name__ == "__main__":
    test_full_store()
    test_bare_store()
    test_bad_tables()
    print("\nAll checks passed.")
