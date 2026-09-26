"""
Checks the "Your data" upload path end to end.

  - The sample file (the Sample Store's month written into the template)
    reads back as the same store: identical sales, targets, contribution,
    stock problems and deliveries. Pace is within a few percent: an upload
    learns how busy each weekday is from its own sales, where the Sample
    Store uses the weekday pattern it was simulated with.
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

    ours, theirs = kpi.get_category_pace(store=s), kpi.get_category_pace(hour=close, store=d)
    for a, b in zip(ours, theirs):
        assert (a["category"], a["units_sold_so_far"], a["monthly_target"]) == \
               (b["category"], b["units_sold_so_far"], b["monthly_target"])
        assert abs(a["expected_units_by_now"] - b["expected_units_by_now"]) <= 0.03 * b["expected_units_by_now"] + 0.5
    assert kpi.get_contribution(store=s) == kpi.get_contribution(hour=close, store=d)
    health = [(r["category"], r["verdict"]) for r in stock.get_stock_health_report(store=s)]
    assert health == [(r["category"], r["verdict"])
                      for r in stock.get_stock_health_report(hour=close, store=d)]
    causes = {x["category"]: x["cause"] for x in diagnosis.diagnose_store(store=s)}
    for category in ("Men Casual Trousers", "Women Tops", "Little Boys Tops"):
        assert causes[category] == diagnosis.diagnose(category, hour=close, store=d)["cause"], category
    chinos = stock.get_stock_history("Men Casual Trousers", store=s)["deliveries_received"]
    assert [(x["day"], x["core_sizes_missing"]) for x in chinos] == [(4, ["32", "34"]), (11, ["32", "34"]),
                                                                       (18, ["32", "34"])]
    print(f"Sample file ({len(upload.sample_bytes()) // 1024} KB) reads back as the demo store at close:")
    print(f"  sales, contribution, stock problems {health} and deliveries match; pace within 3%;")
    print(f"  the planted problems get the same causes.")


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


def test_forgiving_uploads():
    """Ordinary spreadsheet habits are accepted; things worth knowing come back as warnings."""
    import io

    import pandas as pd
    from store import _parse_hour, build_store

    assert [_parse_hour(v) for v in ("10", 10, "10:00-11:00", "2 PM", "2:30 pm", "12 PM", "12 AM", "9am")] == \
        [10, 10, 10, 14, 14, 12, 0, 9]
    targets, sales, stock_df, _ = make_tables()

    messy = pd.concat([sales, sales.head(1).assign(product="Socks")])
    messy["product"] = messy["product"].str.lower()
    messy = messy.astype({"units": object})
    messy.loc[messy.index[1], "units"] = "1,200"
    late = stock_df.copy()
    late.loc[late.index[-1], "date"] = pd.Timestamp("2026-06-19")
    s = build_store(targets, messy, late)
    assert "Mens Shirt" in s.categories and s.sales["units_sold"].max() >= 1200
    assert any("socks" in w.lower() for w in s.warnings) and any("closing stock" in w for w in s.warnings)

    partial = build_store(targets, sales[pd.to_datetime(sales["date"]).dt.day >= 10])
    assert any("start on day 10" in w for w in partial.warnings)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([["Sales report"], ["01 Jun to 18 Jun 2026"]]).to_excel(
            writer, sheet_name="Sales report June", index=False, header=False)
        sales.rename(columns={"date": "Bill date", "line": "Line", "product": "Product type",
                              "units": "Qty", "hour": "Time"}).to_excel(
            writer, sheet_name="Sales report June", index=False, startrow=3)
        targets.rename(columns={"line": "Line", "product": "Category", "target": "Target"}).to_excel(
            writer, sheet_name="Targets", index=False)
    store, notes = upload.read_upload([("june.xlsx", buffer.getvalue())])
    assert int(store.sales["units_sold"].sum()) == int(sales["units"].sum())
    assert any("skipped 2 rows" in n for n in notes)

    bad = sales.astype({"units": object, "hour": object}).copy()
    bad.loc[bad.index[0], "units"] = "five"
    bad.loc[bad.index[1], "hour"] = "lunch"
    try:
        build_store(targets.assign(target=0), bad)
    except StoreDataError as e:
        assert len(e.problems) == 3, e.problems
    else:
        raise AssertionError("expected problems")
    print("\nForgiving uploads: '2 PM', '1,200', lower-case names, an unknown category, next-morning "
          "stock, a partial month\n  and report titles above the table all read correctly; three "
          "problems in one file are reported together.")


def test_refusals():
    targets, sales, _, _ = make_tables()
    tcsv = targets.to_csv(index=False).encode()
    bad = sales.astype({"units": object})
    bad.loc[bad.index[0], "units"] = "five"
    cases = {
        "the empty template": [("template.xlsx", upload.template_bytes())],
        "a Word file": [("notes.docx", b"hello")],
        "a word where a number should be": [("targets.csv", tcsv),
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
    test_forgiving_uploads()
    test_refusals()
    print("\nAll checks passed.")
