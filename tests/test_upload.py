"""
Checks the "Your data" upload path end to end.

  - The sample file (the demo month written into the template) reads back
    as the same store: identical pace, stock problems, diagnoses,
    contribution and deliveries as the demo at the close of the day.
  - CSV files with loosely named columns ("Qty", day-first dates) work.
  - The empty template and broken files are refused with a plain message.

Run from the project folder:  python tests/test_upload.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import diagnosis  # noqa: E402
import kpi  # noqa: E402
import stock  # noqa: E402
import upload  # noqa: E402
from store import StoreDataError, demo_store  # noqa: E402
from test_any_store import make_tables  # noqa: E402


def test_sample_round_trip():
    s, notes = upload.read_upload([("sample.xlsx", upload.sample_bytes())])
    d = demo_store()
    close = d.hours[-1]
    assert notes == [], notes
    assert (s.month_name, s.today_day, s.hours, s.default_hour) == ("May", 24, d.hours, close)
    assert (s.categories, s.sizes, s.core_sizes) == (d.categories, d.sizes, d.core_sizes)
    assert (set(s.sale_days), s.delivery_weekday) == (set(d.sale_days), d.delivery_weekday)
    assert all(on for _, on, _ in upload.feature_checklist(s))

    assert kpi.get_category_pace(store=s) == kpi.get_category_pace(hour=close, store=d)
    assert kpi.get_contribution(store=s) == kpi.get_contribution(hour=close, store=d)
    health = [(r["category"], r["verdict"]) for r in stock.get_stock_health_report(store=s)]
    assert health == [(r["category"], r["verdict"])
                      for r in stock.get_stock_health_report(hour=close, store=d)]
    causes = {x["category"]: x["cause"] for x in diagnosis.diagnose_store(store=s)}
    assert causes == {x["category"]: x["cause"] for x in diagnosis.diagnose_store(hour=close, store=d)}
    chinos = stock.get_stock_history("Men Casual Trousers", store=s)["deliveries_received"]
    assert [(x["day"], x["core_sizes_missing"]) for x in chinos] == [(4, ["32", "34"]), (11, ["32", "34"]),
                                                                       (18, ["32", "34"])]
    print(f"Sample file ({len(upload.sample_bytes()) // 1024} KB) reads back as the demo store at close:")
    print(f"  pace, contribution, stock problems {health}, and all 29 diagnoses match.")


def test_csv_with_loose_headers():
    targets, sales, _, _ = make_tables()
    daily = sales.groupby(["date", "line", "product"], as_index=False)["units"].sum()
    daily["date"] = daily["date"].map(lambda d: d.strftime("%d/%m/%Y"))
    files = [
        ("targets.csv", targets.rename(columns={"line": "Line", "product": "Category", "department": "Floor",
                                                "target": "Monthly target (units)"}).to_csv(index=False).encode()),
        ("Sales.CSV", daily.rename(columns={"date": "Date", "line": "Line", "product": "Product type",
                                            "units": "Qty"}).to_csv(index=False).encode()),
    ]
    s, _ = upload.read_upload(files)
    assert (s.month_name, s.today_day, s.departments) == ("June", 18, ["Ground", "First"])
    assert sum(p["units_sold_so_far"] for p in kpi.get_category_pace(store=s)) == int(daily["units"].sum())
    off = [name for name, on, _ in upload.feature_checklist(s) if not on]
    print(f"\nCSV files with 'Qty', 'Product type' and day-first dates: read as June, day 18.")
    print(f"  switched off for lack of data: {len(off)} features, e.g. {off[0]!r}")


def test_template_headers_are_all_recognised():
    for sheet, headers in upload.TEMPLATE.items():
        if sheet == "Settings":
            continue
        known = upload.COLUMNS[upload.SHEETS[sheet.lower()]]
        unknown = [h for h in headers if upload._plain(h) not in known]
        assert not unknown, f"{sheet}: {unknown}"


def test_filled_template():
    """The made-up June store, typed into the template the way a manager would fill it in."""
    targets, sales, stock_df, footfall = make_tables()
    t = upload.TEMPLATE
    frames = {
        "Targets": targets.rename(columns=dict(zip(["line", "product", "department", "target"],
                                                   [t["Targets"][i] for i in (0, 1, 2, 3)]))),
        "Sales": sales.rename(columns=dict(zip(
            ["date", "hour", "line", "product", "size", "units", "value", "transactions"],
            [t["Sales"][i] for i in range(8)]))),
        "Stock": stock_df.rename(columns=dict(zip(["date", "line", "product", "size", "units"],
                                                  [t["Stock"][i] for i in (0, 2, 3, 4, 5)]))),
        "Visitors": footfall.rename(columns=dict(zip(["date", "hour", "department", "visitors"],
                                                     t["Visitors"]))),
        "Settings": upload.pd.DataFrame({"Setting": upload.SETTINGS_ROWS,
                                         "Value": ["Test store", "Thursday", ""]}),
    }
    s, notes = upload.read_upload([("june.xlsx", upload._workbook(frames))])
    assert (s.name, s.month_name, s.today_day, s.delivery_weekday) == ("Test store", "June", 18, 3)
    causes = {d["category"]: d["cause"] for d in diagnosis.diagnose_store(store=s)}
    assert causes["Mens Trouser"] == "broken_size_run" and causes["Ladies Kurta"] == "stockout", causes
    off = [name for name, on, _ in upload.feature_checklist(s) if not on]
    assert off == ["Loyalty tier targeting at the till"], off
    print("\nThe June test store, filled into the template (no Loyalty sheet):")
    print(f"  found the trousers' broken size run and the kurtas' stockout; only feature off: {off[0]}")


def test_refusals():
    targets, sales, _, _ = make_tables()
    tcsv = targets.to_csv(index=False).encode()
    bad = sales.copy()
    bad.loc[0, "product"] = "Socks"
    cases = {
        "the empty template": [("template.xlsx", upload.template_bytes())],
        "a Word file": [("notes.docx", b"hello")],
        "sales for a category with no target": [("targets.csv", tcsv),
                                                ("sales.csv", bad.to_csv(index=False).encode())],
        "only a Targets sheet": [("targets.csv", tcsv)],
    }
    print("\nRefused, with these messages:")
    for label, files in cases.items():
        try:
            upload.read_upload(files)
        except StoreDataError as e:
            print(f"  {label}: {e}")
        else:
            raise AssertionError(f"{label} should have been refused")


if __name__ == "__main__":
    test_sample_round_trip()
    test_csv_with_loose_headers()
    test_template_headers_are_all_recognised()
    test_filled_template()
    test_refusals()
    print("\nAll checks passed.")
