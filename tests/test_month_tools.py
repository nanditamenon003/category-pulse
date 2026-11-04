"""
Checks the three month tools:

  - festivals saved by date: only this month's days count, each with its own
    uplift, they stay saved for the next month, and bad dates are explained
  - last month: read from the same file or the saved month, compared over the
    same dates, and left out when it wouldn't be a fair comparison
  - pairings: the manager's own list replaces the usual rule, but a category
    that's sold out or slipping is still never suggested

Run from the project folder:  python tests/test_month_tools.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import digest  # noqa: E402
import kpi  # noqa: E402
import loyalty  # noqa: E402
import plan  # noqa: E402
import smart_import  # noqa: E402
import upload  # noqa: E402
from store import demo_store  # noqa: E402

CATEGORIES = ("Polos", "Shirts", "Chinos", "Kurtas")
TARGETS = pd.DataFrame({"Category": CATEGORIES, "Target": [130, 130, 130, 130]})


def sales(month=6, days=range(1, 21), per=3, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame([{"Date": f"{d:02d}/{month:02d}/2026", "Category": c, "Qty": int(rng.integers(1, per + 1)),
                          "Net Amt": 1500} for d in days for c in CATEGORIES])


def csv(df):
    return df.to_csv(index=False).encode()


def build(frame, extras=None, earlier=None):
    tables, _ = smart_import.find_tables([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS))])
    return upload.build(tables, extras, earlier)


DIWALI = {"name": "Diwali", "start": "2026-06-25", "end": "2026-07-05", "uplift": 3.0}


def test_festivals():
    plain_store = build(sales())[0]
    store, _, keep = build(sales(), {"busy_periods": [DIWALI]})
    assert store.occasions == {d: "Diwali" for d in range(25, 31)}, store.occasions
    assert set(store.occasions) <= store.sale_days and store.is_busy(26)
    assert abs(store.day_weights[26] / plain_store.day_weights[26] - 3) < 0.01
    expected = [sum(p["expected_units_by_now"] for p in kpi.get_category_pace(store=s)) for s in (plain_store, store)]
    assert expected[1] < expected[0]
    print(f"A festival ahead lowers what's expected by now ({expected[0]:.0f} to {expected[1]:.0f}).")

    # It's saved, and it carries on into the next month, for the days that fall there.
    assert upload.read_upload(keep)[0].busy_periods == [DIWALI]
    july = upload.read_upload([("july.csv", csv(sales(7, range(1, 4))))], {"busy_periods": [DIWALI]}, earlier=keep)[0]
    assert july.occasions == {d: "Diwali" for d in range(1, 6)}, july.occasions
    assert plan.get_plan(store=july)["occasion"] == "Diwali"
    print("Festivals stay saved, and a festival across two months counts in each.")

    good, problems = smart_import.clean_busy_periods([
        {"name": "Sale", "start": pd.Timestamp("2026-06-02"), "end": pd.Timestamp("2026-06-04"), "uplift": float("nan")},
        {"name": None, "start": pd.NaT, "end": pd.NaT, "uplift": float("nan")},
        {"name": "Backwards", "start": pd.Timestamp("2026-06-09"), "end": pd.Timestamp("2026-06-04"), "uplift": 2.0},
        {"name": "No end", "start": pd.Timestamp("2026-06-09"), "end": pd.NaT, "uplift": 2.0},
    ])
    assert good == [{"name": "Sale", "start": "2026-06-02", "end": "2026-06-04", "uplift": None}]
    assert len(problems) == 2 and "ends before" in problems[0] and "both" in problems[1], problems
    print("A blank row is ignored, and bad dates are explained.")


def test_saved_changes_rebuild():
    _, _, keep = build(sales())
    pairings = {"Polos": {"with": ["Kurtas"], "instead": []}}
    changed = smart_import.with_settings(keep, busy_periods=[DIWALI], pairings=pairings)
    changed = smart_import.with_settings(changed, busy_periods=[])  # and changing it again replaces it
    store = upload.read_upload(changed)[0]
    assert store.pairings == pairings and store.busy_periods == []
    assert int(store.sales["units_sold"].sum()) == int(sales()["Qty"].sum())
    print("Changing a setting keeps the sales, and replaces what was there.")


def test_last_month_from_the_same_file():
    may, june = sales(5, range(1, 32), seed=2), sales()
    store, _, keep = build(pd.concat([may, june]))
    assert store.month_name == "June" and store.has_last_month
    assert int(store.sales["units_sold"].sum()) == int(june["Qty"].sum()), "only June counts as June"
    versus = kpi.get_last_month(store=store)
    want_then = may[may["Date"].str[:2].astype(int) <= 20]["Qty"].sum()
    assert versus["month"] == "May" and versus["sold_last_month"] == want_then, versus
    assert versus["sold_this_month"] == june["Qty"].sum() and versus["last_month_total"] == may["Qty"].sum()
    one = kpi.get_last_month(category="Polos", store=store)
    assert one["sold_last_month"] == may[(may["Category"] == "Polos") & (may["Date"].str[:2].astype(int) <= 20)]["Qty"].sum()
    assert f"*Against May:*" in digest.whatsapp_report(store=store)
    print(f"Against May, same dates: {versus['sold_this_month']} vs {versus['sold_last_month']} ({versus['pct_change']:+.0f}%).")

    # What's saved reads back the same, with no second-guessing of the older rows.
    again = upload.read_upload(keep)[0]
    assert kpi.get_last_month(store=again) == versus
    assert again.month_name == "June" and not any("sales rows are from" in w for w in again.warnings), again.warnings
    print("It reads back the same when saved.")


def test_last_month_from_the_saved_month():
    _, _, june = build(sales(6, range(1, 31)))
    july = upload.read_upload([("july.csv", csv(sales(7, range(1, 6))))], earlier=june)[0]
    versus = kpi.get_last_month(store=july)
    assert versus["month"] == "June" and versus["to_day"] == 5, versus
    print("A new month keeps the saved month to compare with.")

    # A day's report added within the month keeps it too, and doesn't double anything.
    july_files = upload.build(smart_import.find_tables([("july.csv", csv(sales(7, range(1, 6))))])[0], None, june)[2]
    day_six = sales(7, range(6, 7))
    later = upload.read_upload([("day6.csv", csv(day_six))], {"one_date_means": "day"}, earlier=july_files)[0]
    assert later.today_day == 6 and later.has_last_month
    assert kpi.get_last_month(store=later)["to_day"] == 6
    assert int(later.sales["units_sold"].sum()) == int(sales(7, range(1, 6))["Qty"].sum() + day_six["Qty"].sum())
    print("A day's report added later keeps it, and nothing is counted twice.")

    # A file with a different month two back has nothing fair to compare with.
    august = upload.read_upload([("aug.csv", csv(sales(8, range(1, 6))))], earlier=june)[0]
    assert not august.has_last_month


def test_an_unfair_comparison_is_left_out():
    few_days = pd.concat([sales(5, range(25, 32)), sales()])
    store = build(few_days)[0]
    assert kpi.get_last_month(store=store) is None
    report = digest.whatsapp_report(store=store)
    assert "Against May" not in report
    print("With only the end of last month, the dates don't line up, so nothing is compared.")

    assert kpi.get_last_month(store=build(sales())[0]) is None
    print("With no last month, nothing is shown.")


def test_a_part_day_is_not_compared():
    demo = demo_store()
    at_close = kpi.get_last_month(store=demo, hour=demo.hours[-1])
    mid_day = kpi.get_last_month(store=demo, hour=12)
    assert at_close["to_day"] == demo.today_day and mid_day["to_day"] == demo.today_day - 1
    print(f"Sample Store against {at_close['month']}: {at_close['sold_this_month']} vs {at_close['sold_last_month']} "
          f"({at_close['pct_change']:+.0f}%); part-way through a day, only complete days are compared.")


def test_pairings():
    demo = demo_store()
    tops = "Women Tops"
    usual = loyalty.get_cross_sell_ideas(store=demo)
    idea = next(i for i in usual if i["category"] == tops)
    assert idea["partner"], idea
    # A manager's choice replaces the rule, even across floors.
    chosen = next(p["category"] for p in kpi.get_category_pace(store=demo)
                  if p["status"] in ("on_pace", "ahead") and demo.category_department[p["category"]] != "Womenswear"
                  and p["category"] not in [r["category"] for r in __import__("stock").get_stock_health_report(store=demo)])
    mine = demo.with_pairings({tops: {"instead": [chosen]}})
    assert mine.id != demo.id
    assert next(i for i in loyalty.get_cross_sell_ideas(store=mine) if i["category"] == tops)["partner"] == chosen
    assert loyalty.suggestions_for(mine, tops, "instead") == [chosen]
    print(f"A manager can walk {tops} shoppers to {chosen} instead of {idea['partner']}.")

    # Still never a suggestion that's behind.
    behind = next(p["category"] for p in kpi.get_category_pace(store=demo) if p["status"] == "behind" and p["category"] != tops)
    blocked = demo.with_pairings({tops: {"instead": [behind]}})
    assert next(i for i in loyalty.get_cross_sell_ideas(store=blocked) if i["category"] == tops)["partner"] is None
    print("A category that's behind itself is still not suggested.")

    # An empty list means no suggestion; other categories keep the usual rule.
    none = demo.with_pairings({tops: {"instead": []}})
    assert next(i for i in loyalty.get_cross_sell_ideas(store=none) if i["category"] == tops)["partner"] is None
    assert loyalty.suggestions_for(none, "Men Casual Trousers", "with")
    # The manager's list reaches the plan and the report through the same ideas.
    assert plan.plan_message(plan.get_plan(store=mine), mine).count(chosen)
    print("An empty list means no suggestion, and the plan uses the manager's choice.")


def test_no_pairing_rule_for_own_names_can_be_filled():
    store = build(sales())[0]
    assert loyalty.suggestions_for(store, "Kurtas", "with") == []  # no rule knows "Kurtas"
    _, _, keep = build(sales(), {"pairings": {"Kurtas": {"with": ["Chinos"]}}})
    assert loyalty.suggestions_for(upload.read_upload(keep)[0], "Kurtas", "with") == ["Chinos"]
    print("A category no rule knows (Kurtas) can be given its own suggestions, and they stay saved.")


if __name__ == "__main__":
    test_festivals()
    test_saved_changes_rebuild()
    test_last_month_from_the_same_file()
    test_last_month_from_the_saved_month()
    test_an_unfair_comparison_is_left_out()
    test_a_part_day_is_not_compared()
    test_pairings()
    test_no_pairing_rule_for_own_names_can_be_filled()
    print("\nAll checks passed.")
