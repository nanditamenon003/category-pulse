"""
Checks the files that used to be accepted and then gave a confident wrong answer:

  - today's report on its own: added to the saved month, not replacing it
  - a month-to-date summary: read as totals, with no made-up figure for "today"
  - stock names that don't match sales: asked about, not called sold out
  - month-first dates: read that way, and said so
  - a festival stretch: the plan expects more, and typing it in changes the plan
  - a rupee figure in the units box: questioned
  - a small targets file (Store | Month | Target): used, for the right month
  - the classic target-vs-achieved sheet, with no sales file at all
  - a file for part of the month: judged on its own days, not called behind

Run from the project folder:  python tests/test_wrong_answers.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import digest  # noqa: E402
import forecast  # noqa: E402
import kpi  # noqa: E402
import plan  # noqa: E402
import smart_import  # noqa: E402
import stock  # noqa: E402
import upload  # noqa: E402

CATEGORIES = ("Polos", "Shirts", "Chinos", "Kurtas")
TARGETS = pd.DataFrame({"Category": CATEGORIES, "Target": [130, 130, 130, 130]})


def sales(days=range(1, 21), seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in days:
        for c in CATEGORIES:
            for _ in range(int(rng.integers(1, 6))):
                q = int(rng.integers(1, 3))
                rows.append({"Date": f"{d:02d}/06/2026", "Category": c, "Size": str(rng.choice(["S", "M", "L"])),
                             "Qty": q, "Net Amt": q * 1500})
    return pd.DataFrame(rows)


def csv(df):
    return df.to_csv(index=False).encode()


def sold_by_category(store):
    return {p["category"]: p["units_sold_so_far"] for p in kpi.get_category_pace(store=store)}


def saved_month(frame, *extra):
    tables, _ = smart_import.find_tables([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS))] + list(extra))
    return upload.build(tables)[2]


def test_a_days_report_joins_the_saved_month():
    everything = sales()
    earlier = saved_month(everything[everything["Date"] != "20/06/2026"])
    today = everything[everything["Date"] == "20/06/2026"]
    store, _ = upload.read_upload([("today.csv", csv(today))], {"one_date_means": "day"}, earlier=earlier)
    assert store.today_day == 20
    assert int(store.sales["units_sold"].sum()) == int(everything["Qty"].sum()), "every day is there"
    assert store.targets, "targets carried over from the saved month"
    assert store.first_day == 1 and not store.summary_only
    assert any("Added to your saved June" in w for w in store.warnings), store.warnings
    # Sending the same day again replaces it, rather than counting it twice.
    again, _ = upload.read_upload([("today.csv", csv(today))], {"one_date_means": "day"},
                                  earlier=upload.build(smart_import.find_tables(
                                      [("sales.csv", csv(everything)), ("targets.csv", csv(TARGETS))])[0])[2])
    assert int(again.sales["units_sold"].sum()) == int(everything["Qty"].sum())
    print("A day's report joins the saved month, and sending it twice doesn't count it twice.")


def test_a_new_month_starts_afresh():
    earlier = saved_month(sales())
    july = sales(range(1, 4)).assign(Date=lambda d: d["Date"].str.replace("/06/", "/07/"))
    store, _ = upload.read_upload([("july.csv", csv(july))], earlier=earlier)
    assert store.month_name == "July" and not any(store.targets.values())
    assert int(store.sales["units_sold"].sum()) == int(july["Qty"].sum())
    print("A file for a new month starts afresh.")


def test_month_to_date_summary():
    mine = sales()
    totals = mine.groupby("Category", as_index=False)[["Qty", "Net Amt"]].sum().assign(Date="20/06/2026")
    tables, _ = smart_import.find_tables([("sales.csv", csv(totals)), ("targets.csv", csv(TARGETS))])
    question = smart_import.summary_question(tables)
    assert question == {"as_of": pd.Timestamp("2026-06-20"), "guess": "mtd", "can_be_day": True}, question
    store = upload.build(tables)[0]
    assert store.summary_only and store.today_day == 20
    assert any("month-to-date" in w for w in store.warnings), store.warnings
    # Month totals stay exactly right, whichever way the days were shared.
    want = totals.set_index("Category")["Qty"].to_dict()
    assert sold_by_category(store) == want, sold_by_category(store)
    report = digest.whatsapp_report(store=store)
    assert "*Today:*" not in report, report
    assert "likely" in report or "Month" in report
    assert store.sales["units_sold"].sum() == totals["Qty"].sum()
    print(f"A month-to-date summary is read as totals, with no 'today' line ({int(totals['Qty'].sum())} units).")


def test_month_to_date_with_no_date_asks():
    totals = sales().groupby("Category", as_index=False)[["Qty"]].sum()
    tables, _ = smart_import.find_tables([("sales.csv", csv(totals)), ("targets.csv", csv(TARGETS))])
    assert any("no date" in p for p in smart_import.check_choices(tables))
    store = upload.build(tables, {"as_of": pd.Timestamp("2026-06-20")})[0]
    assert store.summary_only and store.today_day == 20
    print("Totals with no date: it asks which date they run up to.")


def test_saved_summary_reads_back_the_same():
    totals = sales().groupby("Category", as_index=False)[["Qty"]].sum().assign(Date="20/06/2026")
    tables, _ = smart_import.find_tables([("sales.csv", csv(totals)), ("targets.csv", csv(TARGETS))])
    store, _, keep = upload.build(tables)
    again, _ = upload.read_upload(keep)
    assert again.summary_only and sold_by_category(again) == sold_by_category(store)
    # And a saved single day isn't mistaken for totals when it's opened again.
    one_day = sales(range(10, 11))
    tables, _ = smart_import.find_tables([("sales.csv", csv(one_day)), ("targets.csv", csv(TARGETS))])
    _, _, keep = upload.build(tables, {"one_date_means": "day"})
    assert not upload.read_upload(keep)[0].summary_only
    print("What's saved reads back the same, totals as totals and a single day as a single day.")


def test_a_real_sales_file_is_not_asked_about():
    tables, _ = smart_import.find_tables([("sales.csv", csv(sales()))])
    assert smart_import.summary_question(tables) is None
    print("A file with many dates is never asked about.")


def test_stock_names_that_do_not_match():
    frame = sales()
    wrong = pd.DataFrame([{"Category": "X" + c, "Size": z, "SOH": 5} for c in CATEGORIES for z in "SML"])
    store, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS)), ("stock.csv", csv(wrong))])
    assert any("nothing is marked as sold out" in w for w in store.warnings), store.warnings
    assert not stock.get_stock_health_report(store=store) if store.has_stock else True

    # Some match, some don't: the rest are "not counted", never "sold out".
    partly = pd.DataFrame([{"Category": c, "Size": z, "SOH": 5} for c in CATEGORIES[:2] for z in "SML"]
                          + [{"Category": "XChinos", "Size": "M", "SOH": 5}])
    store, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS)), ("stock.csv", csv(partly))])
    assert store.uncounted == {"Chinos", "Kurtas"}, store.uncounted
    assert any("Chinos" in w for w in store.warnings), store.warnings
    named = {r["category"] for r in stock.get_stock_health_report(store=store)}
    assert not named & store.uncounted, named
    asked = {c["category"] for c in stock.get_request_quantities(store=store)["categories"]}
    assert not asked & store.uncounted, asked
    assert stock.check_size_runs("Chinos", store=store)["verdict"] == "not_counted"
    assert not store.stock_known("Chinos") and store.stock_known("Polos")
    print("Stock names that don't match: a warning, and nothing is called sold out.")

    # A near spelling is simply matched, and said.
    close = pd.DataFrame([{"Category": c.upper()[:-1], "Size": z, "SOH": 5} for c in CATEGORIES for z in "SML"])
    store, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS)), ("stock.csv", csv(close))])
    assert store.has_stock and not store.uncounted, store.warnings
    print("A near spelling (POLO for Polos) is matched.")


def test_month_first_dates():
    frame = sales()
    us = frame.assign(Date=frame["Date"].map(lambda x: f"{x[3:5]}/{x[:2]}/{x[6:]}"))
    store, _ = upload.read_upload([("sales.csv", csv(us))])
    assert store.month_name == "June" and store.today_day == 20
    assert int(store.sales["units_sold"].sum()) == int(frame["Qty"].sum())
    assert any("month first" in w for w in store.warnings), store.warnings
    # An ordinary Indian day-first file isn't touched.
    store, _ = upload.read_upload([("sales.csv", csv(frame))])
    assert store.month_name == "June" and not any("month first" in w for w in store.warnings)
    print("Month-first dates are spotted and said; day-first stays as it was.")


def test_a_few_rows_from_the_latest_month_are_flagged():
    frame = pd.concat([sales(), sales(range(1, 2)).assign(Date="01/07/2026")])
    store, _ = upload.read_upload([("sales.csv", csv(frame))])
    assert store.month_name == "July" and any("sales rows are from" in w for w in store.warnings), store.warnings
    print("A stray date in the next month is flagged.")


def test_festival_days_raise_the_plan():
    frame = sales(range(1, 21))
    plain, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS))])
    festive, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS))],
                                    {"sale_days": smart_import.sale_day_numbers("21-30"), "sale_uplift": 3.0})
    assert smart_import.sale_day_numbers("20-22, 27") == [20, 21, 22, 27]
    assert festive.day_weights[25] >= 2.5 * plain.day_weights[25]
    before = sum(p["expected_units_by_now"] for p in kpi.get_category_pace(store=plain))
    after = sum(p["expected_units_by_now"] for p in kpi.get_category_pace(store=festive))
    assert after < 0.7 * before, (before, after)
    print(f"A festival stretch ahead lowers what's expected by now ({before:.0f} to {after:.0f}).")


def test_a_rupee_figure_in_the_units_box():
    store, _ = upload.read_upload([("sales.csv", csv(sales()))], {"store_target_units": 5000000})
    assert any("Please check your" in w for w in store.warnings), store.warnings
    print("A rupee figure typed as a units target is questioned.")


def test_small_targets_files():
    frame = sales()
    for name, targets in (("targets.csv", pd.DataFrame({"Store": ["Mine"], "Month": ["June"], "Target": [520]})),
                          ("targets.csv", pd.DataFrame({"Month": ["May 2026", "Jun 2026"], "Target": [9, 520]})),
                          ("target.csv", pd.DataFrame({"Target": [520]}))):
        store, _ = upload.read_upload([("sales.csv", csv(frame)), (name, csv(targets))])
        assert sum(store.targets.values()) == 520, (targets, store.targets)
    store, _ = upload.read_upload([("sales.csv", csv(frame)),
                                   ("targets.csv", csv(pd.DataFrame({"Month": ["May"], "Target": [9]})))])
    assert any("no row for June" in w for w in store.warnings), store.warnings
    print("A small targets file is used, for the right month.")


def test_target_vs_achieved_sheet():
    sheet = pd.DataFrame({"Category": CATEGORIES, "LY": [100, 100, 100, 100], "Target": [130, 130, 130, 130],
                          "Sold": [80, 70, 60, 50], "Balance": [50, 60, 70, 80]})
    tables, _ = smart_import.find_tables([("store sheet.csv", csv(sheet))])
    assert [t["role"] for t in tables] == ["targets"], tables[0]["role"]
    assert any("no date" in p for p in smart_import.check_choices(tables))
    store = upload.build(tables, {"as_of": pd.Timestamp("2026-06-20")})[0]
    assert store.summary_only and sold_by_category(store) == dict(zip(CATEGORIES, [80, 70, 60, 50]))
    assert sum(store.targets.values()) == 520
    print("The classic target / sold / balance sheet works on its own.")


def test_a_file_for_part_of_the_month():
    frame = sales(range(10, 21))
    store, _ = upload.read_upload([("sales.csv", csv(frame)), ("targets.csv", csv(TARGETS))])
    assert store.first_day == 10 and any("start on day 10" in w for w in store.warnings)
    whole, _ = upload.read_upload([("sales.csv", csv(sales())), ("targets.csv", csv(TARGETS))])
    # Judged on its own days: it isn't "behind" because the first nine days aren't in the file.
    assert not [p for p in kpi.get_category_pace(store=store) if p["status"] == "behind"]
    end = forecast.get_month_end_range(store=store)
    full = forecast.get_month_end_range(store=whole)
    assert abs(end["likely"] - full["likely"]) < 0.25 * full["likely"], (end["likely"], full["likely"])
    assert forecast.get_month_path(store=store)["partial"]
    assert "Days 10 to 20 only" in digest.whatsapp_report(store=store)
    assert plan.get_plan(store=store)
    print(f"A file from day 10: not called behind, month-end {end['likely']} (whole month says {full['likely']}).")


def test_csv_oddities_do_not_crash():
    text = csv(sales()).decode()
    store, _ = upload.read_upload([("sales.csv", text.encode("utf-16"))])
    assert store.today_day == 20
    print("A UTF-16 CSV (a common Excel export) is read.")


if __name__ == "__main__":
    test_a_days_report_joins_the_saved_month()
    test_a_new_month_starts_afresh()
    test_month_to_date_summary()
    test_month_to_date_with_no_date_asks()
    test_saved_summary_reads_back_the_same()
    test_a_real_sales_file_is_not_asked_about()
    test_stock_names_that_do_not_match()
    test_month_first_dates()
    test_a_few_rows_from_the_latest_month_are_flagged()
    test_festival_days_raise_the_plan()
    test_a_rupee_figure_in_the_units_box()
    test_small_targets_files()
    test_target_vs_achieved_sheet()
    test_a_file_for_part_of_the_month()
    test_csv_oddities_do_not_crash()
    print("\nAll checks passed.")
