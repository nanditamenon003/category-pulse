"""
A store's own data: reading uploads, the Excel template, and the sample file.

Only sales are required, in whatever shape the store's system exports them
(smart_import.py works out what each table and column is). Targets, stock,
visitors, loyalty tiers and settings are optional, and each unlocks more of
the app (see feature_checklist). The template is there for stores that
would rather fill something in; the sample file is the Sample Store's month
written into it.
"""

import functools
import hashlib
import io

import pandas as pd

import smart_import
from store import WEEKDAY_NAMES, StoreDataError, build_store, demo_store

# --- The template ---------------------------------------------------------------------------

# Sheet -> its column headers, as they appear in the template.
TEMPLATE = {
    "Targets": ["Line (optional)", "Category", "Floor (optional)", "Monthly target (units)",
                "Monthly target in ₹", "Last year (units, optional)", "Sizes (optional)",
                "Core sizes (optional)"],
    "Sales": ["Date", "Hour (optional)", "Line (optional)", "Category", "Size (optional)", "Units",
              "Value (₹, optional)", "Bills (optional)"],
    "Stock": ["Date", "Hour (optional)", "Line (optional)", "Category", "Size (optional)",
              "Units on hand"],
    "Visitors": ["Date", "Hour (optional)", "Floor (optional)", "Visitors"],
    "Loyalty": ["Tier", "Line (optional)", "Category", "Share of bills (%)", "Average bill value (₹)",
                "Units per bill", "Takes up cross-sells (%)", "Preferred offer"],
    "Settings": ["Setting", "Value"],
}
SETTINGS_ROWS = ["Store name", "Delivery day", "Sale days"]

READ_ME = [
    ("Category Pulse: store data template", ""),
    ("", ""),
    ("How to fill it in", "One month of data. Keep each sheet's header row as it is. Only Sales is "
                          "required; the other sheets are optional and each one unlocks more of the "
                          "app. Leave a sheet empty if you don't have that data. You don't have to use "
                          "this template: your system's own sales export works too."),
    ("", ""),
    ("Targets", "One row per category: the category (e.g. Polo), and its monthly target in units, in "
                "rupees, or both. Optional: its line and the floor it's on (e.g. Menswear); last "
                "year's units; its sizes in shelf order, separated by commas (S, M, L, XL); its core "
                "sizes, the ones most shoppers need (M, L). If core sizes are left blank they're "
                "worked out from sales. Only have targets per floor, or one for the whole store? "
                "Put those in instead, or type the store's target on the upload page. No targets at "
                "all? Each category is then compared with its own pace earlier in the month."),
    ("Sales", "One row per sale line: date, category and units. Optional: the line, the hour (10 "
              "means 10:00-11:00), the size, the value in rupees, and the number of bills. Rows can "
              "be per bill or already totalled; they're added up either way. If bills are only known "
              "per category and hour, put the count on one row and leave the others blank."),
    ("Stock", "Units on hand by category and size, counted at the close of a day (or at an hour, "
              "if you count hourly). One count on the last day is enough; a count every day also "
              "shows deliveries and what's usual."),
    ("Visitors", "Visitors counted per floor, by day or by hour. Needs Bills in the Sales sheet to "
                 "tell fewer visitors apart from fewer buyers. By hour, it also gives tomorrow's "
                 "busy hours."),
    ("Loyalty", "Loyalty tier figures per category (tier level only, never individual customers). "
                "Preferred offer is one of: outfit bundle, first-purchase discount, double points, "
                "early access, free alteration."),
    ("Settings", "Store name; the weekday deliveries arrive (e.g. Monday), used to spot missed "
                 "deliveries; and any sale days, as day numbers separated by commas (e.g. 13, 27)."),
    ("", ""),
    ("Dates", "Any normal date format works; day first (24/05/2026) is read as the day."),
    ("Privacy", "Your data stays private: only the columns the app uses are kept, they're encrypted "
                "before they're saved, visible only to your account, never shared or sold, and you "
                "can delete them any time. The AI chat only sees your figures after you say yes."),
]

# --- Reading uploads ------------------------------------------------------------------------

OFFERS = {"bundle": "bundle", "outfit bundle": "bundle",
          "first-purchase discount": "percentage_discount", "discount": "percentage_discount",
          "percentage discount": "percentage_discount",
          "double points": "loyalty_points_multiplier", "points": "loyalty_points_multiplier",
          "loyalty points multiplier": "loyalty_points_multiplier",
          "early access": "early_access", "free alteration": "complimentary_service",
          "alteration": "complimentary_service", "complimentary service": "complimentary_service"}
OFFER_WORDS = {"bundle": "outfit bundle", "percentage_discount": "first-purchase discount",
               "loyalty_points_multiplier": "double points", "early_access": "early access",
               "complimentary_service": "free alteration"}


def build(tables, extras=None):
    """
    A Store from the tables found in an upload, as they're currently set
    (smart_import.find_tables, perhaps changed on the upload page), plus any
    details typed in. Returns (store, notes, files to save: only the columns
    in use). Raises StoreDataError listing every problem.
    """
    problems = smart_import.check_choices(tables)
    if problems:
        raise StoreDataError(problems)
    canonical, settings, notes = smart_import.canonical_tables(tables, extras)

    loyalty = canonical["loyalty"]
    if loyalty is not None and "preferred_offer_type" in loyalty.columns:
        loyalty["preferred_offer_type"] = loyalty["preferred_offer_type"].map(
            lambda v: OFFERS.get(smart_import.plain(v), smart_import.plain(v).replace(" ", "_")))

    files = smart_import.to_files(canonical, settings)
    fingerprint = hashlib.sha1(b"".join(data for _, data in files)).hexdigest()[:12]
    store = build_store(
        canonical["targets"], canonical["sales"], canonical["stock"], canonical["visitors"], loyalty,
        name=settings.get("name") or "Your store", store_id=f"upload-{fingerprint}",
        sale_days=settings.get("sale_days", ()), delivery_weekday=settings.get("delivery_weekday"),
    )
    return store, notes, files


def read_upload(files, extras=None, remembered=None):
    """
    Reads uploaded files ([(name, bytes)]: any Excel or CSV files, the
    template or the store's own exports) into a Store, with every guess as
    made. Returns (store, notes): notes say how the files were read, while
    store.warnings are things to check about the data. Raises
    StoreDataError listing every problem if the data can't be used.
    """
    tables, notes = smart_import.find_tables(files, remembered)
    if not tables:
        raise StoreDataError("No tables were found in the file. It needs at least your sales: a row of "
                             "headings (like Date, Category and Units), then one row per sale.")
    store, more, _ = build(tables, extras)
    return store, notes + more


# --- What the data unlocks ----------------------------------------------------------------------

def feature_checklist(store):
    """
    Each feature, whether this store's data supports it, and (when it's off)
    what would switch it on.
    """
    has_bills_and_visitors = store.has_footfall and store.has_transactions
    features = [
        ("Month-to-date pace and status for every category", True, ""),
        ("Contribution report and end-of-day summary", True, ""),
        ("Rupee values, and pace in rupees", store.has_value, "include sales values (₹)"),
        ("Step through the day hour by hour", store.hourly, "include sale times"),
        ("Broken size runs and core sizes", store.has_sizes and store.has_stock,
         "include sizes in sales and stock" if store.has_stock else "upload a stock report with sizes"),
        ("Stock problems and last-piece alerts", store.has_stock, "upload a stock report"),
        ("Deliveries and usual stock levels", store.has_stock_history,
         "count stock on more than one day"),
        ("Missed deliveries and run-out warnings", store.has_stock_history and store.delivery_weekday is not None,
         "choose a delivery day" if store.has_stock_history else "daily stock counts and a delivery day"),
        ("Fewer visitors or fewer buyers?", has_bills_and_visitors,
         "upload visitor counts" + ("" if store.has_transactions else ", with bill numbers in your sales")),
        ("Tomorrow's busy hours and floor split", store.has_visitor_hours, "upload visitors by hour"),
        ("Loyalty tier targeting at the till", store.has_loyalty, "upload loyalty tier figures"),
    ]
    return [(name, on, "" if on else hint) for name, on, hint in features]


# --- Writing the template and the sample ------------------------------------------------------------

def _workbook(frames):
    """{sheet: DataFrame} -> .xlsx bytes, with a Read me sheet, bold headers and sensible widths."""
    from openpyxl.styles import Alignment, Font

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(READ_ME).to_excel(writer, sheet_name="Read me", index=False, header=False)
        sheet = writer.sheets["Read me"]
        sheet.column_dimensions["A"].width = 24
        sheet.column_dimensions["B"].width = 110
        for row in sheet.iter_rows():
            row[0].font = Font(bold=True)
            row[1].alignment = Alignment(wrap_text=True, vertical="top")
        sheet["A1"].font = Font(bold=True, size=14)

        for name, df in frames.items():
            df.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for i, column in enumerate(df.columns):
                cell = sheet.cell(row=1, column=i + 1)
                cell.font = Font(bold=True)
                letter = cell.column_letter
                sheet.column_dimensions[letter].width = max(12, len(str(column)) + 3)
                if pd.api.types.is_datetime64_any_dtype(df[column]) or column == "Date":
                    for row in range(2, len(df) + 2):
                        sheet.cell(row=row, column=i + 1).number_format = "DD/MM/YYYY"
    return buffer.getvalue()


@functools.lru_cache(maxsize=1)
def template_bytes():
    """The empty template: every sheet with its header row, plus the Read me."""
    frames = {name: pd.DataFrame(columns=columns) for name, columns in TEMPLATE.items()}
    frames["Settings"] = pd.DataFrame({"Setting": SETTINGS_ROWS, "Value": [None] * len(SETTINGS_ROWS)})
    return _workbook(frames)


@functools.lru_cache(maxsize=1)
def sample_bytes():
    """
    The simulated demo month, filled into the template: hourly sales by
    size, stock counted at each day's close, visitors by hour, loyalty
    tiers and settings. Uploading it shows the demo store as at the close
    of its latest day.
    """
    s = demo_store()
    t = TEMPLATE

    targets = pd.DataFrame([dict(zip(t["Targets"], [
        s.category_line[c], s.category_product[c], s.category_department[c], s.targets[c],
        s.value_targets[c], s.last_year.get(c), ", ".join(s.sizes[c]), ", ".join(s.core_sizes[c]),
    ])) for c in s.categories])

    # One row per category, hour and size that sold; each category-hour's
    # bills sit on its first row, as the Read me describes.
    sales = s.sales[s.sales["units_sold"] > 0].drop(columns="transactions", errors="ignore")
    sales = sales.merge(s.transactions, on=["day", "hour", "category"])
    sales = sales.sort_values(["day", "hour", "category"], kind="stable")
    first_row = ~sales.duplicated(["day", "hour", "category"])
    sales = pd.DataFrame({
        "Date": sales["day"].map(s.date), "Hour (optional)": sales["hour"],
        "Line (optional)": sales["line"], "Category": sales["category"].map(s.category_product),
        "Size (optional)": sales["size"], "Units": sales["units_sold"],
        "Value (₹, optional)": sales["value"].round(2),
        "Bills (optional)": sales["transactions"].where(first_row),
    })

    closing = s.stock[s.stock["hour"] == s.hours[-1]]
    stock = pd.DataFrame({
        "Date": closing["day"].map(s.date), "Line (optional)": closing["line"],
        "Category": closing["category"].map(s.category_product), "Size (optional)": closing["size"],
        "Units on hand": closing["units_remaining"],
    })

    visitors = pd.DataFrame({
        "Date": s.footfall["day"].map(s.date), "Hour (optional)": s.footfall["hour"],
        "Floor (optional)": s.footfall["zone"], "Visitors": s.footfall["visitors"],
    })

    loyalty = pd.DataFrame({
        "Tier": s.loyalty["tier"], "Line (optional)": s.loyalty["category"].map(s.category_line),
        "Category": s.loyalty["category"].map(s.category_product),
        "Share of bills (%)": s.loyalty["share_of_transactions"],
        "Average bill value (₹)": s.loyalty["avg_basket_value"],
        "Units per bill": s.loyalty["avg_upt"],
        "Takes up cross-sells (%)": s.loyalty["cross_sell_response_rate"],
        "Preferred offer": s.loyalty["preferred_offer_type"].map(OFFER_WORDS),
    })

    settings = pd.DataFrame({"Setting": SETTINGS_ROWS, "Value": [
        "Sample Store (from the sample file)", WEEKDAY_NAMES[s.delivery_weekday],
        ", ".join(str(d) for d in sorted(s.sale_days))]})

    return _workbook({"Targets": targets, "Sales": sales, "Stock": stock, "Visitors": visitors,
                      "Loyalty": loyalty, "Settings": settings})
