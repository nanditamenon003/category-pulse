"""
Checks Step 3: the month-end range, what to request per size, tomorrow's
plan and the WhatsApp report.

  - The range is honest: on thousands of simulated months (ordinary ones,
    and bumpier ones), about 8 in 10 finish inside the "middle 80%" range.
  - On the Sample Store, the planted problems come out as they should: the
    sold-out and broken-size categories are unlikely to reach target, and
    their missing sizes are the first things to request.
  - The plan and the report work on a bare store too (no stock or visitor
    counts, no targets), saying less rather than guessing.

Run from the project folder:  python tests/test_plan.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import digest  # noqa: E402
import forecast  # noqa: E402
import plan  # noqa: E402
import stock  # noqa: E402
from store import MissingData, build_store, demo_store  # noqa: E402
from test_any_store import make_tables  # noqa: E402

WEEK = [0.8, 0.8, 0.85, 0.9, 1.1, 1.5, 1.4]  # Monday ... Sunday


def coverage(runs, day, rate, bumpy, seed):
    """Share of simulated 30-day months that finish inside the range made on `day`."""
    rng = np.random.default_rng(seed)
    weights = [WEEK[d % 7] for d in range(30)]
    inside = 0
    for _ in range(runs):
        # Bumpy months: each day's rate itself wobbles (weather, events), on top of chance.
        wobble = rng.gamma(4, 1 / 4, 30) if bumpy else np.ones(30)
        sales = rng.poisson(rate * np.array(weights) * wobble)
        likely, low, high, _ = forecast.month_end_range(
            list(sales[:day]), weights[:day], 0.0, 0.0, sum(weights[day:]))
        inside += low <= sales.sum() <= high
    return inside / runs


def test_range_is_honest():
    results = {(bumpy, day): coverage(2000, day, 12, bumpy, seed=day + bumpy)
               for bumpy in (False, True) for day in (8, 15, 24)}
    for (bumpy, day), share in results.items():
        assert 0.72 <= share <= 0.88, (bumpy, day, share)
    print("Month-end range, 2,000 simulated months each: share finishing inside the middle-80% range")
    for (bumpy, day), share in results.items():
        print(f"  {'bumpy' if bumpy else 'ordinary'} months, range made on day {day}: {share:.0%}")


def test_sample_store():
    d = demo_store()
    close = d.hours[-1]
    ranges = {c: forecast.get_month_end_range(category=c, hour=close, store=d)
              for c in ("Women Tops", "Men Casual Trousers", "Little Boys Tops")}
    assert ranges["Women Tops"]["chance_of_target_pct"] <= 10 and "caution" in ranges["Women Tops"]
    assert ranges["Men Casual Trousers"]["chance_of_target_pct"] <= 10
    assert ranges["Little Boys Tops"]["chance_of_target_pct"] >= 90
    for r in ranges.values():
        assert r["low"] <= r["likely"] <= r["high"]

    requests = stock.get_request_quantities(hour=close, store=d)
    first_two = [c["category"] for c in requests["categories"][:2]]
    assert set(first_two) == {"Women Tops", "Men Casual Trousers"}, first_two
    chinos = next(c for c in requests["categories"] if c["category"] == "Men Casual Trousers")
    assert {s["size"] for s in chinos["sizes"]} >= {"32", "34"} and chinos["core_sizes_out"] == ["32", "34"]

    p = plan.get_plan(store=d)
    assert [f["category"] for f in p["focus"][:2]] == ["Women Tops", "Men Casual Trousers"]
    assert p["goal"]["needs"] > 0 and p["people"] and p["stock"]["urgent"]
    report = digest.whatsapp_report(store=d)
    assert len(report) < 1000 and "*Behind:*" in report and "Women Tops" in report
    print("\nSample Store: Women Tops (sold out) and chinos (core sizes gone) are unlikely to reach target,")
    print(f"  Little Boys Tops is very likely to; chinos request: {chinos['text']}.")
    print(f"  WhatsApp report: {len(report)} characters; plan message: {len(plan.plan_message(p, d))}.")


def test_bare_store():
    """Daily sales only, no targets: the plan and report still work and say less."""
    _, sales, _, _ = make_tables()
    daily = sales.groupby(["date", "line", "product"], as_index=False)["units"].sum()
    s = build_store(None, daily)
    p = plan.get_plan(store=s)
    assert p["stock"] is None and p["people"] is None
    try:
        stock.get_request_quantities(store=s)
    except MissingData:
        pass
    else:
        raise AssertionError("expected MissingData")
    early = build_store(None, daily[pd.to_datetime(daily["date"]).dt.day <= 6])
    report = digest.whatsapp_report(store=early)
    assert "no targets to judge by yet" in report, report
    assert forecast.get_month_end_range(store=early)["likely"] is not None  # six full days is enough
    last = plan.get_plan(day=s.days_in_month, store=s) if s.today_day == s.days_in_month else {"month_over": True}
    assert last["month_over"]
    print("\nA bare store (daily sales, no targets): the plan leaves out stock and people, and the early-")
    print("  month report says there are no targets to judge by yet.")


if __name__ == "__main__":
    test_range_is_honest()
    test_sample_store()
    test_bare_store()
    print("\nAll checks passed.")
