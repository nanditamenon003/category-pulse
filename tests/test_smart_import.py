"""
Checks reading files as stores really have them, not as the template asks.

  - A raw bill-level export from a till system: a report title on top, one
    row per bill line, the date and time in one cell, "Qty" and "Net Amt",
    a Men/Women column next to the real category, customer names and phone
    numbers, a Grand Total row, and last month plus the same month last year
    mixed in. It's read without any help, bills are counted, and personal
    columns are never kept.
  - Targets per floor in rupees, a store target typed on the upload page,
    and a changed column choice that is remembered for next time.
  - What gets saved reads back as the same store.

Run from the project folder:  python tests/test_smart_import.py
"""

import io
import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import kpi  # noqa: E402
import smart_import  # noqa: E402
import upload  # noqa: E402

CATEGORIES = {"Polo": ("Men", 1400), "Shirt": ("Men", 2200), "Chino": ("Men", 2600),
              "Kurta": ("Women", 1800), "Top": ("Women", 1200), "Dress": ("Women", 3000)}


def till_export(month=6, last_day=20, seed=3):
    """One row per bill line, like a till system's sales export."""
    rng = np.random.default_rng(seed)
    rows, bill = [], 1000
    for year, m, days in ((2026, month, range(1, last_day + 1)), (2026, month - 1, range(25, 31)),
                          (2025, month, range(1, 31))):
        for day in days:
            for _ in range(rng.integers(25, 40)):
                bill += 1
                hour, minute = int(rng.integers(11, 21)), int(rng.integers(0, 60))
                slow = year == 2026 and m == month and day > last_day - 6
                for _ in range(rng.integers(1, 3)):
                    name = rng.choice(list(CATEGORIES))
                    if slow and name == "Kurta" and rng.random() < 0.7:
                        name = "Top"  # kurtas slowed down in the last week
                    division, price = CATEGORIES[name]
                    qty = int(rng.integers(1, 3))
                    rows.append({"Bill No": f"B{bill}", "Bill Date": pd.Timestamp(year, m, day, hour, minute),
                                 "Customer Name": "A. Shopper", "Mobile": "98xxxxxx01",
                                 "Category": division, "Sub Category": name, "Size": rng.choice(["S", "M", "L"]),
                                 "Qty": qty, "MRP": price, "Disc": 0, "Net Amt": qty * price})
    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame([["Item-wise sales register"], ["Store 0421, 01-05-2025 to 20-06-2026"]]).to_excel(
            writer, sheet_name="Sheet1", index=False, header=False)
        df.to_excel(writer, sheet_name="Sheet1", index=False, startrow=3)
        total = pd.DataFrame([["Grand Total", None, None, None, None, None, None,
                               df["Qty"].sum(), None, None, df["Net Amt"].sum()]])
        total.to_excel(writer, sheet_name="Sheet1", index=False, header=False, startrow=len(df) + 4)
    return buffer.getvalue(), df


def test_raw_till_export():
    data, df = till_export()
    tables, notes = smart_import.find_tables([("sales register.xlsx", data)])
    assert len(tables) == 1 and tables[0]["role"] == "sales", tables
    mapping = {f: c for c, f in tables[0]["mapping"].items() if f}
    assert mapping == {"bill": "Bill No", "date": "Bill Date", "department": "Category",
                       "product": "Sub Category", "size": "Size", "units": "Qty", "value": "Net Amt"}, mapping

    store, more, saved = upload.build(tables)
    june = df[(df["Bill Date"].dt.year == 2026) & (df["Bill Date"].dt.month == 6)]
    assert (store.month_name, store.today_day, store.hourly) == ("June", 20, True)
    assert int(store.sales["units_sold"].sum()) == int(june["Qty"].sum()), "totals and other months left out"
    assert int(store.transactions["transactions"].sum()) == int(
        june.groupby(["Bill No", "Sub Category"]).ngroups), "each bill counted once per category"
    assert store.departments == ["Men", "Women"] or sorted(store.departments) == ["Men", "Women"]
    assert all(src == "last year" for src in store.target_source["units"].values())
    assert store.last_year["Kurta"] == int(df[(df["Bill Date"].dt.year == 2025)
                                             & (df["Sub Category"] == "Kurta")]["Qty"].sum())
    everything = b"".join(d for _, d in saved)
    assert b"Shopper" not in everything and b"98xxxx" not in everything, "personal columns are never kept"

    pace = {p["category"]: p for p in kpi.get_category_pace(store=store)}
    print("A raw till export (title rows, bill lines, date+time, customer columns, Grand Total,")
    print("May and last June mixed in) was read with no help:")
    print("  columns:", ", ".join(f"{c} -> {f}" for f, c in mapping.items()))
    for n in notes + more:
        print("  note:", n)
    print(f"  June to day 20: {int(store.sales['units_sold'].sum())} units, "
          f"{int(store.transactions['transactions'].sum())} bill-category pairs; saved files hold only "
          f"{sorted(saved[0][1].decode().splitlines()[0].split(','))}")
    print("  against last June: " + ", ".join(f"{c} {p['pct_vs_pace']:+.0f}% {p['status']}"
                                               for c, p in pace.items()))


def test_floor_targets_in_rupees_and_typed_targets():
    data, _ = till_export()
    targets = pd.DataFrame({"Floor": ["Men", "Women"], "Target Value": [2_600_000, 2_300_000]})
    buffer = io.BytesIO()
    targets.to_csv(buffer, index=False)
    tables, _ = smart_import.find_tables([("sales register.xlsx", data), ("floor targets.csv", buffer.getvalue())])
    assert [t["role"] for t in tables] == ["sales", "targets"]
    store, notes, _ = upload.build(tables)
    assert store.default_measure == "value"
    assert set(store.target_source["value"].values()) == {"floor share"}
    men = [c for c in store.categories if store.category_department[c] == "Men"]
    assert abs(sum(store.value_targets[c] for c in men) - 2_600_000) < 5
    assert set(store.target_source["units"].values()) == {"from rupee target"}
    print("\nFloor targets in rupees: shared across each floor's categories; unit targets worked out")
    print("  from them at the average price. Notes: " + " | ".join(store.target_notes["value"]))

    # Only the sales file, with the store's month target typed on the upload page.
    tables, _ = smart_import.find_tables([("sales register.xlsx", data)])
    store, _, saved = upload.build(tables, {"store_target_value": 5_000_000, "name": "Brigade Road",
                                            "delivery_weekday": 0})
    assert (store.name, store.delivery_weekday) == ("Brigade Road", 0)
    assert abs(sum(store.value_targets.values()) - 5_000_000) < 10
    again, _ = upload.read_upload(saved)
    assert (again.name, again.categories, again.value_targets) == (store.name, store.categories,
                                                                   store.value_targets)
    print("A typed store target of Rs 50 L is shared across categories, and the saved files read back "
          "as the same store.")


def test_changed_choices_are_remembered():
    data, _ = till_export()
    tables, _ = smart_import.find_tables([("sales register.xlsx", data)])
    t = tables[0]
    t["mapping"]["Size"] = None  # the manager says: don't use sizes
    remembered = {smart_import.layout_key(t["columns"]): {"role": t["role"], "mapping": t["mapping"]}}
    again, _ = smart_import.find_tables([("next month.xlsx", data)], remembered)
    assert again[0]["mapping"]["Size"] is None and again[0]["mapping"]["Qty"] == "units"
    t["mapping"]["Qty"] = None
    assert smart_import.check_choices(tables) == [
        "sales register.xlsx › Sheet1 (Sales): choose which column holds units sold."]
    print("\nA changed column choice is remembered for the next file with the same headings, and a "
          "missing one is named plainly.")


if __name__ == "__main__":
    test_raw_till_export()
    test_floor_targets_in_rupees_and_typed_targets()
    test_changed_choices_are_remembered()
    print("\nAll checks passed.")
