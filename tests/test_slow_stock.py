"""
Checks the stock warnings against what actually happened next on the Sample Store.

  - Slow stock: what's flagged really does stay slow. The following week,
    flagged stock sells a much smaller share of its pieces than stock in
    general, and the overstocked blazers come first (most money tied up).
  - "At risk of running out before the next delivery" is honest: the sizes it
    flags sell out far more often than other sizes, but most still last, which
    is why the page calls it a watch list, not a certainty.
  - Nothing is flagged before there are two weeks of sales, and a store
    without stock counts gets a plain "not in your data".

(Predicting the day each size sells out was also tried, and dropped: at a few
pieces per size per week it did no better than guessing the average. See the
note in stock.py.)

Run from the project folder:  python tests/test_slow_stock.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
warnings.filterwarnings("ignore")

import stock  # noqa: E402
from store import MissingData, build_store, demo_store  # noqa: E402
from test_any_store import make_tables  # noqa: E402


def sold_share_next_week(d, start, category, sizes=None):
    """Share of the pieces on hand at the close of `start` that sold over the next 7 days."""
    close = d.hours[-1]
    on_hand = stock.get_stock_status(category, start, close, store=d)["remaining_by_size"]
    sizes = sizes or list(on_hand)
    pieces = sum(on_hand[s] for s in sizes)
    rows = d.sales[(d.sales["category"] == category) & (d.sales["day"] > start)
                   & (d.sales["day"] <= start + 7) & d.sales["size"].isin(sizes)]
    return rows["units_sold"].sum(), pieces


def test_slow_stock_stays_slow():
    d = demo_store()
    close = d.hours[-1]
    report = stock.get_slow_stock(hour=close, store=d)
    first = report["slow"][0]
    assert first["category"] == "Men Formal Blazers" and first["whole_category"], first
    assert first["value_tied_up"] > 250_000 and first["replace_with"], first

    flagged_sold = flagged_pieces = all_sold = all_pieces = 0
    for start in (14, 17):
        for item in stock.get_slow_stock(day=start, hour=close, store=d)["slow"]:
            sizes = None if item["whole_category"] else item["sizes_not_selling"]
            sold, pieces = sold_share_next_week(d, start, item["category"], sizes)
            flagged_sold, flagged_pieces = flagged_sold + sold, flagged_pieces + pieces
        for category in d.categories:
            sold, pieces = sold_share_next_week(d, start, category)
            all_sold, all_pieces = all_sold + sold, all_pieces + pieces
    flagged, overall = flagged_sold / flagged_pieces, all_sold / all_pieces
    assert flagged < overall / 2, (flagged, overall)
    print(f"Slow stock: the week after it was flagged, it sold {flagged:.0%} of its pieces; stock in general "
          f"sold {overall:.0%}.")
    print(f"  Top of the list: {first['category']}, {first['units']} pieces, Rs {first['value_tied_up']:,} "
          f"tied up, about {first['weeks_of_stock']:.0f} weeks' worth. Space could go to {first['replace_with']}.")


def test_at_risk_is_a_fair_watch_list():
    d = demo_store()
    close = d.hours[-1]
    counts = {True: [0, 0], False: [0, 0]}  # flagged -> [sold out, total]
    for start in range(3, d.today_day - 1):
        nxt = start + next(k for k in range(1, 8) if d.weekday(start + k) == d.delivery_weekday)
        if nxt - 1 > d.today_day:
            continue
        for category in d.categories:
            for s in stock.get_days_of_cover(category, start, close, store=d)["sizes"]:
                if s["units_remaining"] <= 0:
                    continue
                out = any(d.stock_lookup.get((category, day, close), {}).get(s["size"], 0) == 0
                          for day in range(start + 1, nxt))
                counts[s["likely_out_before_next_delivery"]][0] += out
                counts[s["likely_out_before_next_delivery"]][1] += 1
    flagged = counts[True][0] / counts[True][1]
    others = counts[False][0] / counts[False][1]
    assert flagged > 5 * others and flagged < 0.5, (flagged, others)
    print(f"\nAt risk of running out: {flagged:.0%} of flagged sizes sold out before the next delivery, "
          f"against {others:.1%} of the rest: a watch list, not a certainty.")


def test_early_month_and_missing_stock():
    targets, sales, stock_df, _ = make_tables()
    early = build_store(targets, sales[sales["date"].map(lambda x: x.day) <= 10],
                        stock_df[stock_df["date"].map(lambda x: x.day) <= 10])
    report = stock.get_slow_stock(store=early)
    assert not report["enough_history"] and report["slow"] == []
    try:
        stock.get_slow_stock(store=build_store(targets, sales))
    except MissingData:
        pass
    else:
        raise AssertionError("expected MissingData")
    print("\nBefore two weeks of sales nothing is flagged; without stock counts it says so plainly.")


if __name__ == "__main__":
    test_slow_stock_stays_slow()
    test_at_risk_is_a_fair_watch_list()
    test_early_month_and_missing_stock()
    print("\nAll checks passed.")
