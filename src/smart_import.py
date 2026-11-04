"""
Reading whatever files a store has into the tables build_store() needs.

Real exports rarely match a template: a report title above the table, one row
per bill line, "Qty" and "Net Amt" instead of Units and Value, the date and
time in one cell, no Line column, a Grand Total row, several months at once.
This module:
  - finds the table in each sheet or file (the first row of known headings)
  - guesses what each table is (sales, stock, targets, visitors, loyalty or
    settings) from its name or, failing that, from its columns
  - guesses what each column holds, from the words retailers use
  - turns the tables into build_store()'s columns: keeps the latest month,
    takes hours from bill times, drops total rows, and uses the same month
    last year as a yardstick when the file has it
Every guess can be changed on the upload page. Only the columns in use are
kept, so anything else in an export (customer names, phone numbers, prices)
is dropped before the data is saved.
"""

import csv
import hashlib
import io
import math
import re
from datetime import date, datetime

import numpy as np
import pandas as pd

import config
from store import (
    WEEKDAY_NAMES,
    StoreDataError,
    _clean_number_text,
    _normal_name,
    _parse_dates,
    month_comes_first,
    plan_weights,
    read_date_text,
)


class Important(str):
    """A note that changes what the numbers mean: shown as a warning, not in small print."""


# --- What each kind of table holds --------------------------------------------------------------

ROLES = {"sales": "Sales", "stock": "Stock on hand", "targets": "Targets", "visitors": "Visitors",
         "loyalty": "Loyalty tiers", "settings": "Settings", "skip": "Not used"}

# role -> [(field, label, required)], in the order the upload page lists them.
FIELDS = {
    # A sales file may have no date column if it's month-to-date totals: see summary_question.
    "sales": [("date", "Date", False), ("product", "Category", True), ("units", "Units sold", True),
              ("value", "Value (₹)", False), ("line", "Line or brand", False),
              ("department", "Floor", False), ("size", "Size", False), ("hour", "Hour or time", False),
              ("bill", "Bill number", False), ("transactions", "Number of bills", False)],
    "stock": [("product", "Category", True), ("units", "Units on hand", True),
              ("date", "Date counted", False), ("line", "Line or brand", False),
              ("size", "Size", False), ("hour", "Hour counted", False)],
    "targets": [("product", "Category", False), ("department", "Floor", False),
                ("line", "Line or brand", False), ("target", "Target (units)", False),
                ("target_value", "Target (₹)", False), ("last_year", "Last year (units)", False),
                ("last_year_value", "Last year (₹)", False), ("sold_so_far", "Sold so far (units)", False),
                ("sold_value", "Sold so far (₹)", False), ("month", "Month", False), ("sizes", "Sizes", False),
                ("core_sizes", "Core sizes", False)],
    "visitors": [("date", "Date", True), ("visitors", "Visitors", True), ("hour", "Hour or time", False),
                 ("department", "Floor", False)],
    "loyalty": [("tier", "Tier", True), ("product", "Category", True), ("line", "Line or brand", False),
                ("share_of_transactions", "Share of bills (%)", True),
                ("avg_basket_value", "Average bill value (₹)", True),
                ("avg_upt", "Units per bill", True),
                ("cross_sell_response_rate", "Takes up cross-sells (%)", True),
                ("preferred_offer_type", "Preferred offer", True)],
    "settings": [],
}
TARGET_NUMBERS = ("target", "target_value", "last_year", "last_year_value")

# Words that mark a rupee figure rather than a count.
MONEY = {"rs", "inr", "value", "amount", "amt", "revenue", "rupee", "rupees", "turnover", "nsv", "val",
         "sales"}

# field -> (exact headings, best first; groups of words that must all appear; words that rule it out).
# Headings are compared in plain form: lower case, no brackets, single spaces.
_WORDS = {
    "date": (["date", "bill date", "invoice date", "sale date", "sales date", "transaction date",
              "txn date", "doc date", "document date", "posting date", "day", "bill datetime",
              "invoice datetime", "created at", "timestamp", "date counted", "count date",
              "stock date", "as on date", "as of date", "bill dt", "invoice dt", "inv dt", "txn dt",
              "doc dt"], [("date",)], ["update", "birth", "dob"]),
    "hour": (["hour", "time", "bill time", "invoice time", "hour slot", "time slot", "hour counted"],
             [("time",), ("hour",)], ["date"]),
    "product": (["category", "product type", "product category", "item category", "article type",
                 "sub category", "subcategory", "sub-category", "product", "product group",
                 "item group", "merchandise category", "mc", "class", "sub class", "style category",
                 "category name"], [("category",), ("product",)], ["code", "id", "no"]),
    "line": (["line", "brand", "sub brand", "sub-brand", "label", "collection", "line name",
              "brand name"], [("brand",)], ["code", "id"]),
    "department": (["floor", "department", "dept", "zone", "section", "division", "gender",
                    "floor zone"], [("floor",), ("department",), ("dept",)], []),
    "size": (["size", "size code", "size name"], [("size",)], ["sizes"]),
    "bill": (["bill no", "bill number", "bill id", "invoice no", "invoice number", "invoice id",
              "receipt no", "receipt number", "transaction id", "txn id", "transaction no", "doc no",
              "document no", "voucher no", "order id", "order no", "bill"],
             [("bill", "no"), ("bill", "number"), ("invoice", "no"), ("invoice", "number"),
              ("receipt",)], ["date", "time", "amount", "value", "qty"]),
    "transactions": (["bills", "no of bills", "number of bills", "transactions", "invoices",
                      "bill count", "no of invoices", "tickets"], [], []),
    "visitors": (["visitors", "footfall", "walk-ins", "walk ins", "walkins", "footfalls", "traffic",
                  "people in", "entries", "visitor count"],
                 [("footfall",), ("visitor",), ("walk",)], []),
    "month": (["month", "period", "month name"], [], []),
    "sizes": (["sizes"], [], []),
    "core_sizes": (["core sizes", "core size"], [("core",)], []),
    "tier": (["tier", "loyalty tier", "member tier"], [("tier",)], []),
    "share_of_transactions": (["share of bills", "share of transactions"], [("share",)], []),
    "avg_basket_value": (["average bill value", "avg basket value", "avg bill value"],
                         [("average", "bill"), ("avg", "bill"), ("basket",)], []),
    "avg_upt": (["units per bill", "avg upt", "upt"], [("per", "bill"), ("upt",)], []),
    "cross_sell_response_rate": (["takes up cross-sells", "cross sell response rate"], [("cross",)], []),
    "preferred_offer_type": (["preferred offer", "preferred offer type"], [("offer",)], []),
}
_UNITS = {
    "sales": (["units", "qty", "quantity", "units sold", "sold qty", "sale qty", "sales qty", "net qty",
               "bill qty", "pcs", "pieces", "sold units", "qty sold", "quantity sold"],
              [("qty",), ("quantity",), ("units",), ("pcs",)],
              ["stock", "hand", "closing", "opening", "soh", "return", "free"]),
    "stock": (["units on hand", "on hand", "soh", "stock", "closing stock", "closing qty", "stock qty",
               "qty", "units", "quantity", "available", "available qty", "inventory", "balance qty",
               "closing balance"], [("stock",), ("hand",), ("qty",), ("closing",)],
              ["value", "amount", "opening", "date", "sold", "sale"]),
}
_VALUE = (["net sales value", "nsv", "net value", "net amount", "net sales", "net sale value",
           "sales value", "value", "amount", "net amt", "bill amount", "line total", "total",
           "total amount", "revenue", "sales amount", "gross amount", "taxable value"],
          [("amount",), ("value",), ("amt",), ("revenue",)],
          ["mrp", "discount", "disc", "tax", "gst", "cgst", "sgst", "igst", "rate", "price", "unit",
           "per"])

_NAME_ROLES = [("read me", "skip"), ("readme", "skip"), ("instruction", "skip"), ("setting", "settings"),
               ("target", "targets"), ("budget", "targets"), ("loyalty", "loyalty"), ("tier", "loyalty"),
               ("visitor", "visitors"), ("footfall", "visitors"), ("traffic", "visitors"),
               ("walk", "visitors"), ("stock", "stock"), ("inventory", "stock"), ("soh", "stock"),
               ("sale", "sales"), ("bill", "sales"), ("invoice", "sales"), ("transaction", "sales")]

_TOTAL_ROW = re.compile(r"^(grand |sub ?)?total\b")
_MONTH_TO_DATE = re.compile(r"\b(mtd|m\.t\.d|month[ -]to[ -]date|till[ -]date|to[ -]date|cumulative)\b", re.I)


def plain(text):
    """'Value (₹, optional)' -> 'value'; 'Units_Sold' -> 'units sold'."""
    text = re.sub(r"\(.*?\)", "", str(text)).replace("_", " ").replace(".", " ").lower()
    return re.sub(r"\s+", " ", text).strip()


def _words_for(role, field):
    if field == "units":
        return _UNITS["stock" if role == "stock" else "sales"]
    if field == "value":
        return _VALUE
    return _WORDS.get(field, ([], [], []))


def _match_rank(heading, words):
    """How well a plain heading fits a field: lower is better, None for no fit."""
    exact, groups, excluded = words
    if heading in exact:
        return exact.index(heading)
    tokens = set(re.findall(r"[a-z₹]+", heading))
    if any(x in tokens for x in excluded):
        return None
    for i, group in enumerate(groups):
        if all(g in tokens for g in group):
            return 100 + i
    return None


def _target_field(column):
    """
    Target and last-year columns, in units or rupees, from the words used:
    'Target (₹)', 'Sales target', 'Budget value' are rupees; 'Target qty' is units.
    """
    raw = str(column).lower()
    heading = plain(column)
    tokens = set(re.findall(r"[a-z]+", raw))
    money = "₹" in raw or bool(tokens & MONEY) and not tokens & {"qty", "units", "unit", "pcs", "quantity"}
    if tokens & {"target", "budget", "plan", "goal", "targets"}:
        return "target_value" if money else "target"
    if heading.split(" ")[0] == "ly" or "last year" in heading or "previous year" in heading \
            or "last yr" in heading:
        return "last_year_value" if money else "last_year"
    return None


def _sold_field(column):
    """A sold-so-far column in a targets sheet ('Sold', 'MTD Qty', 'Achieved'): units or rupees."""
    raw = str(column).lower()
    tokens = set(re.findall(r"[a-z]+", raw))
    if tokens & {"target", "budget", "plan", "goal", "balance", "last", "ly", "stock"} or "%" in raw:
        return None
    if tokens & {"sold", "mtd", "achieved", "achievement", "ach", "actual"}:
        money = "₹" in raw or bool(tokens & (MONEY - {"sales"}))
        return "sold_value" if money else "sold_so_far"
    return None


_LOOKS_LIKE_A_DATE = re.compile(r"^\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{2,4})")


def _column_of_dates(columns, mapping, df):
    """A column not yet used whose values are nearly all dates, whatever its heading says."""
    for c in columns:
        values = df[c].dropna().astype(str)
        if mapping[c] is None and len(values) and values.str.match(_LOOKS_LIKE_A_DATE).mean() >= 0.8:
            return c
    return None


def guess_columns(role, columns, df=None):
    """{column: field or None} for a table in `role`: each field goes to its best-fitting column."""
    mapping = {c: None for c in columns}
    if role not in FIELDS or role == "settings":
        return mapping
    headings = {c: plain(c) for c in columns}
    if role == "targets":
        for c in columns:
            field = _target_field(c) or _sold_field(c)
            if field and field not in mapping.values():
                mapping[c] = field
    for field, _, _ in FIELDS[role]:
        if field in mapping.values():
            continue
        words = _words_for(role, field)
        ranked = sorted((rank, i, c) for i, c in enumerate(columns)
                        if mapping[c] is None and (rank := _match_rank(headings[c], words)) is not None)
        if not ranked:
            continue
        if field == "product" and df is not None and len(ranked) > 1:
            # Several category-like columns (Category, Sub category...): pick the first with a
            # sensible number of different values. Two values (Men / Women) is a floor, not a category.
            sensible = [r for r in ranked if 3 <= df[r[2]].nunique() <= 300]
            ranked = sensible or ranked
        mapping[ranked[0][2]] = field
    if df is not None and "department" not in mapping.values() and "department" in dict(
            (f, 1) for f, _, _ in FIELDS[role]):
        # A second category-like column with a handful of values (Men / Women / Kids) is the floor.
        words = _words_for(role, "product")
        for c in columns:
            if mapping[c] is None and _match_rank(headings[c], words) is not None \
                    and 2 <= df[c].nunique() <= 6:
                mapping[c] = "department"
                break
    if df is not None and role in ("sales", "stock", "visitors") and "date" not in mapping.values():
        # An unusual heading shouldn't turn a file of dated rows into "totals with no date".
        dates = _column_of_dates(columns, mapping, df)
        if dates is not None:
            mapping[dates] = "date"
    return mapping


def _role_from_name(name):
    heading = plain(name)
    return next((role for word, role in _NAME_ROLES if word in heading), None)


def _role_from_columns(columns, df):
    """What a table holds, judged by its headings."""
    headings = [plain(c) for c in columns]
    if headings[:2] == ["setting", "value"]:
        return "settings"
    guesses = {role: {f for f in guess_columns(role, columns, df).values() if f}
               for role in ("sales", "stock", "targets", "visitors", "loyalty")}
    if "visitors" in guesses["visitors"] and "product" not in guesses["sales"]:
        return "visitors"
    if guesses["targets"] & set(TARGET_NUMBERS):
        return "targets"
    if {"tier", "share_of_transactions"} <= guesses["loyalty"]:
        return "loyalty"
    stock_words = ("hand", "soh", "closing", "stock", "inventory", "available", "balance")
    if any(w in h for h in headings for w in stock_words) and "value" not in guesses["sales"] \
            and "bill" not in guesses["sales"]:
        return "stock" if {"product", "units"} <= guesses["stock"] else "skip"
    if {"date", "units"} <= guesses["sales"] or {"product", "units"} <= guesses["sales"]:
        return "sales"
    return "skip"


# --- Finding the tables ---------------------------------------------------------------------------

def _all_headings():
    known = set()
    for words in list(_WORDS.values()) + list(_UNITS.values()) + [_VALUE]:
        known |= set(words[0])
    return known


_KNOWN = _all_headings()


def _looks_like_heading(cell):
    if cell is None or (isinstance(cell, float) and pd.isna(cell)) or isinstance(cell, (int, float)):
        return False
    heading = plain(cell)
    return bool(heading) and (heading in _KNOWN or _target_field(cell) is not None
                              or _sold_field(cell) is not None or heading in ("setting", "tier"))


def _find_header(rows, role_known=False):
    """
    The first of the top 20 rows with at least two known headings, or None. When the
    file's name already says what it holds ("targets.csv"), one known heading is
    enough, so a small file like Store | Target isn't lost.
    """
    for wanted in (2, 1) if role_known else (2,):
        for i, row in enumerate(rows[:20]):
            if sum(_looks_like_heading(c) for c in row) >= wanted:
                return i
    return None


def _separator(text):
    """Comma, unless the typical line has more tabs, semicolons or pipes (some systems export those)."""
    lines = [line for line in text.splitlines()[:30] if line.strip()]
    typical = {s: sorted(line.count(s) for line in lines)[len(lines) // 2] if lines else 0 for s in ",\t;|"}
    return max(typical, key=lambda s: (typical[s], s == ","))


def _read_csv(name, data):
    """A CSV file as a table of cells, whatever its encoding."""
    encodings = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise StoreDataError(f"{name} couldn't be read as text.")
    try:
        rows = [row for row in csv.reader(io.StringIO(text, newline=""), delimiter=_separator(text))
                if any(cell.strip() for cell in row)]
    except csv.Error:
        raise StoreDataError(f"{name} couldn't be read as a CSV file. Open it in Excel and save it as "
                             f"Excel (.xlsx) or CSV, then upload that.")
    width = max((len(r) for r in rows), default=0)
    return pd.DataFrame([r + [None] * (width - len(r)) for r in rows]).replace({"": None})


def _sheets(files):
    """[(file name, bytes)] -> [(file name, sheet name or None, raw cells)]."""
    out = []
    for name, data in files:
        lower = name.lower()
        if lower.endswith((".xlsx", ".xlsm")):
            try:
                sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, engine="openpyxl", header=None)
            except Exception as e:
                raise StoreDataError(f"{name} couldn't be opened as an Excel file ({e}).")
            out += [(name, sheet, raw) for sheet, raw in sheets.items()]
        elif lower.endswith(".csv"):
            out.append((name, None, _read_csv(name, data)))
        else:
            raise StoreDataError(f"{name} isn't an Excel (.xlsx) or CSV file. If it's an older .xls file, "
                                 f"open it in Excel and save it as .xlsx.")
    return out


def _unique(headings):
    seen, out = {}, []
    for i, h in enumerate(headings):
        h = str(h).strip() if h is not None and str(h) != "nan" and str(h).strip() else f"Column {i + 1}"
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    return out


def _example(value):
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%d %b %Y %H:%M") if (value.hour or value.minute) else value.strftime("%d %b %Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)[:30]


def layout_key(columns):
    """A fingerprint of a table's headings, to remember how that kind of file was read."""
    return hashlib.sha1("|".join(sorted(plain(c) for c in columns)).encode()).hexdigest()[:16]


def find_tables(files, remembered=None):
    """
    Every table in the uploaded files, with a guess at what it holds and what
    each column is. `remembered` is {layout_key: {"role", "mapping"}} from
    earlier uploads by the same person. Returns (tables, notes); each table
    is a dict: key, label, role, columns, mapping, examples, df, rows.
    """
    tables, notes = [], []
    for file, sheet, raw in _sheets(files):
        label = f"{file} › {sheet}" if sheet is not None else file
        name = sheet if sheet is not None else re.sub(r"\.csv$", "", file, flags=re.I)
        raw = raw.dropna(how="all").dropna(axis=1, how="all")
        name_role = _role_from_name(name)
        if raw.empty or name_role == "skip":
            continue
        rows = raw.values.tolist()
        at = _find_header(rows, role_known=name_role is not None)
        title = ""
        if at is None and name_role == "settings" and raw.shape[1] >= 2:
            df = pd.DataFrame([r[:2] for r in rows], columns=["Setting", "Value"])
        elif at is None:
            notes.append(Important(f"{label} was left out: it has no row of headings like Date, Category and "
                                   f"Qty. If it's a summary with dates or categories across the top (a pivot), "
                                   f"export the detailed report instead, with one row per sale or per day."))
            continue
        else:
            if at > 0:
                notes.append(f"{label}: skipped {at} row{'s' if at > 1 else ''} above the headings.")
                title = " ".join(str(c) for row in rows[:at] for c in row if c is not None and str(c) != "nan")
            df = pd.DataFrame(rows[at + 1:], columns=_unique(rows[at])).dropna(how="all")
        df = df.loc[:, [not (c.startswith("Column ") and df[c].isna().all()) for c in df.columns]]
        if df.empty:
            continue
        columns = list(df.columns)
        saved = (remembered or {}).get(layout_key(columns))
        if saved and set(saved.get("mapping", {})) <= set(columns):
            role, mapping = saved["role"], {c: saved["mapping"].get(c) for c in columns}
        else:
            role = name_role or _role_from_columns(columns, df)
            mapping = guess_columns(role, columns, df)
        examples = {c: [_example(v) for v in df[c].dropna().drop_duplicates().head(3)] for c in columns}
        tables.append({"key": label, "label": label, "role": role, "columns": columns,
                       "mapping": mapping, "examples": examples, "df": df, "rows": len(df),
                       "title": title, "file": file})
    return tables, notes


SAVED_COPY = "Saved by Category Pulse"


def _date_in_title(text):
    """The latest date written in a report's title lines ('01-06-2026 to 20-06-2026'), or None."""
    found = re.findall(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[ -][A-Za-z]{3,9}[ -]\d{2,4}",
                       text or "")
    if not found:
        return None
    dates = _parse_dates(pd.Series(found)).dropna()
    return dates.max() if len(dates) else None


def _numeric(values):
    return pd.to_numeric(_clean_number_text(values), errors="coerce")


def _sold_in_targets(tables):
    """Targets tables that also hold what's sold so far (the classic target-vs-achieved sheet)."""
    return [t for t in tables if t["role"] == "targets" and "sold_so_far" in t["mapping"].values()]


def summary_question(tables, earlier=None):
    """
    Whether the sales might be month-to-date totals rather than sales by day. A file
    with no dates, or with only one date, could be either, and only the person knows.
    Returns None when the sales are clearly by day, or a dict: "as_of" (the date the
    figures run up to, or None), "guess" ("mtd" or "day") and "can_be_day" (False when
    there's no date, so it can only be totals).
    """
    sales = [t for t in tables if t["role"] == "sales"]
    if any(t["role"] == "settings" and SAVED_COPY in t["df"].iloc[:, 0].astype(str).values for t in tables):
        return None  # our own saved copy: already by day, so nothing to ask
    if not sales:
        return ({"as_of": next((d for t in tables if (d := _date_in_title(t["title"])) is not None), None),
                 "guess": "mtd", "can_be_day": False} if _sold_in_targets(tables) else None)
    dates, undated = set(), False
    for t in sales:
        fields = {f: c for c, f in t["mapping"].items() if f}
        if "date" not in fields:
            undated = True
            continue
        if "bill" in fields or "hour" in fields:
            return None  # bill lines or hours: this is real day-by-day trading
        dates |= set(_parse_dates(t["df"][fields["date"]]).dropna().dt.normalize())
    if undated:
        as_of = next((d for t in sales if (d := _date_in_title(t["title"])) is not None), None)
        return {"as_of": as_of, "guess": "mtd", "can_be_day": False}
    if len(dates) != 1:
        return None
    as_of = next(iter(dates))

    def units_of(t):
        column = next((c for c, f in t["mapping"].items() if f == "units"), None)
        return float(_numeric(t["df"][column]).fillna(0).sum()) if column else 0.0

    sold = sum(units_of(t) for t in sales)
    says_mtd = any(_MONTH_TO_DATE.search(" ".join([t["title"], t["file"], t["label"]] + [str(c) for c in t["columns"]]))
                   for t in sales)
    guess = None
    if says_mtd:
        guess = "mtd"
    if guess is None and earlier:
        saved = _read_saved(earlier).get("sales")
        if saved is not None and "date" in saved.columns:
            when = _parse_dates(saved["date"])
            same_month = (when.dt.year == as_of.year) & (when.dt.month == as_of.month) & (when < as_of)
            before = float(_numeric(saved.loc[same_month, "units"]).fillna(0).sum())
            if before > 0:
                guess = "mtd" if sold >= 0.7 * before else "day"
    if guess is None and sold > 0:
        for t in tables:
            column = next((c for c, f in t["mapping"].items() if f == "target"), None) if t["role"] == "targets" else None
            target = float(_numeric(t["df"][column]).fillna(0).sum()) if column else 0.0
            if target > 0:
                month_days = as_of.days_in_month
                as_totals = abs(math.log(sold / (target * as_of.day / month_days)))
                as_a_day = abs(math.log(sold / (target / month_days)))
                guess = "mtd" if as_totals < as_a_day else "day"
                break
    if guess is None:
        guess = "mtd" if as_of.day > 3 else "day"
    return {"as_of": as_of, "guess": guess, "can_be_day": True}


def check_choices(tables, extras=None):
    """Problems with what each table and column is set to, in plain words ([] if none)."""
    problems = []
    question = summary_question(tables)
    if question and not question["can_be_day"] and question["as_of"] is None and not (extras or {}).get("as_of"):
        problems.append("Your figures have no date. Choose the date they run up to.")
    if not any(t["role"] == "sales" for t in tables) and not _sold_in_targets(tables):
        problems.append("No sales found. Upload your sales (at least a date, a category and units "
                        "sold for each row), or choose which of your tables holds them.")
    for t in tables:
        if t["role"] in ("skip", "settings"):
            continue
        chosen = [f for f in t["mapping"].values() if f]
        labels = {f: label for f, label, _ in FIELDS[t["role"]]}
        twice = sorted({labels.get(f, f) for f in chosen if chosen.count(f) > 1})
        if twice:
            problems.append(f"{t['label']}: {', '.join(twice)} is chosen for more than one column.")
        missing = [label for f, label, required in FIELDS[t["role"]] if required and f not in chosen]
        if missing:
            problems.append(f"{t['label']} ({ROLES[t['role']]}): choose which column holds "
                            f"{' and '.join(missing).lower()}.")
        if t["role"] == "targets" and not set(chosen) & set(TARGET_NUMBERS):
            problems.append(f"{t['label']} (Targets): choose which column holds the target "
                            f"(in units or ₹) or last year's figures.")
    return problems


# --- Turning them into build_store()'s tables --------------------------------------------------------

def _datetimes(values):
    """Dates with their times kept (store._parse_dates reads the date only)."""
    if pd.api.types.is_datetime64_any_dtype(values):
        return values
    text = values.astype(str).str.strip()
    year_first = text.str.match(r"^\d{4}-\d{1,2}-\d{1,2}")
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    out[year_first] = pd.to_datetime(text[year_first], format="mixed", errors="coerce")
    out[~year_first] = read_date_text(text[~year_first])
    return out


def _spread_totals(sales, as_of, sale_days, uplift):
    """
    Month-to-date totals -> a row for each day up to `as_of`, shared by how busy each
    day usually is, in whole units that add back up exactly. So pace, the month-end
    range and stock rates work, but no single day's figure is real, and the store is
    marked summary_only so the pages don't present them as if they were.
    """
    month_start = date(as_of.year, as_of.month, 1)
    weights = plan_weights(config.WEEKDAY_WEIGHTS, month_start, as_of.days_in_month, sale_days, uplift)
    shares = np.array([weights[d] for d in range(1, as_of.day + 1)])
    running = np.cumsum(shares) / shares.sum()
    units = _numeric(sales["units"])
    if units.isna().any():
        raise StoreDataError(f"Some figures for units sold aren't numbers (for example "
                             f"{sales.loc[units.isna(), 'units'].iloc[0]!r}).")
    value = _numeric(sales["value"]).fillna(0) if "value" in sales.columns else None
    keep = [c for c in sales.columns if c in ("product", "line", "department", "size")]
    rows = []
    for i, total in units.items():
        daily = np.diff(np.round(total * running), prepend=0)
        for d, count in zip(range(1, as_of.day + 1), daily):
            if count == 0 and d != as_of.day:
                continue  # a category with no sales still keeps one row, so it stays on the list
            row = {c: sales.at[i, c] for c in keep}
            row["date"] = f"{as_of.year:04d}-{as_of.month:02d}-{d:02d}"
            row["units"] = int(count)
            if value is not None:
                row["value"] = round(float(value[i]) * (count / total if total else (d == as_of.day)), 2)
            rows.append(row)
    return pd.DataFrame(rows)


def _read_saved(files):
    """Saved tables (see to_files) as {role: DataFrame}, plus their "settings"."""
    out = {}
    for name, data in files or []:
        role = re.sub(r"\.csv$", "", name, flags=re.I)
        if role in ("sales", "targets", "stock", "visitors", "loyalty", "settings"):
            out[role] = pd.read_csv(io.BytesIO(data), dtype=str)
    return out


def _shared_columns(new, old):
    """Optional columns only stay if both tables have them: half a month with sizes can't be judged by size."""
    optional = ("hour", "size", "line", "department", "value", "bill", "transactions")
    dropped = [c for c in optional if (c in new.columns) != (c in old.columns)]
    return new.drop(columns=dropped, errors="ignore"), old.drop(columns=dropped, errors="ignore")


def _add_earlier_days(new, old):
    """`new`, plus the rows of `old` on days `new` doesn't cover. Returns (table, how many days were kept)."""
    new, old = _shared_columns(new, old)
    covered = set(_parse_dates(new["date"]).dropna().dt.normalize())
    when = _parse_dates(old["date"]).dt.normalize()
    keep = old[when.notna() & ~when.isin(covered)]
    return pd.concat([keep, new], ignore_index=True), int(when[keep.index].nunique())


def _merge_with_saved(out, settings, earlier, summary, latest, notes):
    """
    A file that adds to the person's saved month: a day's report joins the days already
    saved, and anything the new file doesn't include (targets, stock...) carries over.
    A later month starts afresh; an older month is flagged, since it replaces newer data.
    """
    saved = _read_saved(earlier)
    old_settings = _settings(saved["settings"].where(saved["settings"].notna(), None)) if "settings" in saved else {}
    for key in ("name", "delivery_weekday"):
        if key in old_settings:
            settings.setdefault(key, old_settings[key])
    if "sales" not in saved:
        return
    last_saved = _parse_dates(saved["sales"]["date"]).max()
    if pd.isna(last_saved):
        return
    if (last_saved.year, last_saved.month) > (latest.year, latest.month):
        notes.append(Important(f"This file is for {latest:%B %Y}, older than your saved {last_saved:%B %Y}. "
                               f"Showing it replaces the saved month."))
        return
    if (last_saved.year, last_saved.month) < (latest.year, latest.month):
        return
    for key in ("sale_days", "sale_uplift"):
        if key in old_settings:
            settings.setdefault(key, old_settings[key])
    if not summary:
        out["sales"], kept = _add_earlier_days(out["sales"], saved["sales"])
        if kept:
            notes.append(Important(f"Added to your saved {latest:%B}: {kept} earlier "
                                   f"day{'s' if kept != 1 else ''} kept, and this file's days taken from the "
                                   f"new file."))
            if old_settings.get("summary_only"):
                settings["summary_only"] = True  # the earlier days are still estimates
    for role in ("targets", "loyalty"):
        if out.get(role) is None and role in saved:
            out[role] = saved[role]
    for role in ("stock", "visitors"):
        new, old = out.get(role), saved.get(role)
        if old is None or "date" not in old.columns:
            out[role] = new if new is not None else old
        elif new is None:
            out[role] = old
        else:
            new = new.copy()
            if "date" not in new.columns:
                new["date"] = _parse_dates(out["sales"]["date"]).max()
            out[role] = _add_earlier_days(new, old)[0]


def _settings(df):
    """A Settings table -> {"name", "delivery_weekday", "sale_days"} (only what it gives)."""
    values, out = {}, {}
    if df is not None and df.shape[1] >= 2:
        for _, row in df.iterrows():
            values[plain(row.iloc[0])] = row.iloc[1]
    name = values.get("store name")
    if name is not None and pd.notna(name) and str(name).strip():
        out["name"] = str(name).strip()
    delivery = values.get("delivery day")
    if delivery is not None and pd.notna(delivery) and str(delivery).strip():
        out["delivery_weekday"] = weekday_number(delivery)
    raw = values.get("sale days")
    if raw is not None and (isinstance(raw, (datetime, date)) or (pd.notna(raw) and str(raw).strip())):
        out["sale_days"] = sale_day_numbers(raw)
    uplift = values.get("sale day uplift")
    if uplift is not None and pd.notna(uplift) and str(uplift).strip():
        out["sale_uplift"] = float(uplift)
    if str(values.get("month-to-date file", "")).strip().lower() == "yes":
        out["summary_only"] = True
    return out


def weekday_number(text):
    """'Mon', 'monday' -> 0. Raises StoreDataError for anything else."""
    wanted = str(text).strip().lower()[:3]
    matches = [i for i, day in enumerate(WEEKDAY_NAMES) if day.lower().startswith(wanted)]
    if not wanted or not matches:
        raise StoreDataError(f"The delivery day {text!r} isn't a weekday name.")
    return matches[0]


def sale_day_numbers(raw):
    """'13, 27', a range like '21-31', or dates -> day numbers. Raises StoreDataError for anything else."""
    if isinstance(raw, (datetime, date)):
        return [raw.day]
    days = []
    for part in (p.strip() for p in re.split(r"[,;]", str(raw)) if p.strip()):
        if re.fullmatch(r"\d{1,2}(\.0)?", part):  # a day number, e.g. 13
            days.append(int(float(part)))
            continue
        span = re.fullmatch(r"(\d{1,2})\s*(?:-|to)\s*(\d{1,2})", part)
        if span:  # a stretch of days, e.g. a festival: 21-31
            days.extend(range(int(span.group(1)), int(span.group(2)) + 1))
            continue
        when = pd.to_datetime(part, errors="coerce", dayfirst=True)  # a date, e.g. 13/05/2026
        if pd.isna(when):
            raise StoreDataError(f"The sale day {part!r} isn't a day number or a date.")
        days.append(when.day)
    return days


def _drop_total_rows(df, notes, label):
    """Report exports often end with Total / Grand Total rows: they'd count everything twice."""
    text = df.astype(str).apply(lambda col: col.str.strip().str.lower())
    totals = text.apply(lambda col: col.str.match(_TOTAL_ROW)).any(axis=1)
    if totals.any():
        notes.append(f"{label}: left out {int(totals.sum())} total row{'s' if totals.sum() > 1 else ''}.")
    return df[~totals]


def _in_month(df, label, month, notes):
    """Rows dated in `month` (a Timestamp); rows without a readable date stay, for build_store to report."""
    if "date" not in df.columns:
        return df
    when = _parse_dates(df["date"])
    other = when.notna() & ((when.dt.year != month.year) | (when.dt.month != month.month))
    if other.any():
        notes.append(f"{label}: used {month:%B %Y} only, and left out {int(other.sum())} rows from other "
                     f"months.")
    return df[~other]


def _for_this_month(targets, latest, notes):
    """A targets table with a Month column: only the row or rows for the month being judged."""
    def is_this_month(value):
        text = str(value).strip()
        when = pd.to_datetime(text, errors="coerce", dayfirst=True, format="mixed")
        if pd.notna(when) and re.search(r"\d", text):
            return (when.year, when.month) == (latest.year, latest.month)
        return text[:3].lower() == latest.strftime("%b").lower()

    matched = targets["month"].map(is_this_month)
    if not matched.any():
        notes.append(Important(f"Your targets have no row for {latest:%B %Y}, so they were left out."))
        return None
    return targets[matched].drop(columns="month")


def canonical_tables(tables, extras=None, earlier=None):
    """
    The chosen tables as build_store() takes them: {"sales", "targets", "stock", "visitors",
    "loyalty"} (missing ones None), the store's settings, and notes on what was done.
    `extras` are details typed on the upload page: name, delivery_weekday, sale_days,
    sale_uplift, store_target_units, store_target_value (these override the file's Settings),
    one_date_means ("mtd" or "day") and as_of. `earlier` is the person's saved month, which
    a file for part of that month adds to.
    """
    extras = extras or {}
    notes, by_role, settings = [], {}, {}
    for t in tables:
        if t["role"] == "skip":
            continue
        if t["role"] == "settings":
            settings.update(_settings(t["df"]))
            continue
        chosen = {c: f for c, f in t["mapping"].items() if f}
        df = t["df"][list(chosen)].rename(columns=chosen).copy()
        if t["role"] in ("sales", "stock"):
            df = _drop_total_rows(df, notes, t["label"])
        by_role.setdefault(t["role"], []).append((t["label"], df))
    out = {role: (pd.concat([df for _, df in parts], ignore_index=True) if parts else None)
           for role, parts in by_role.items()}

    for key in ("name", "delivery_weekday", "sale_days", "sale_uplift"):
        if extras.get(key) not in (None, "", []):
            settings[key] = extras[key]

    # Targets that also hold what's sold so far: the classic target-vs-achieved sheet.
    if out.get("targets") is not None and "sold_so_far" in out["targets"].columns:
        sheet = out["targets"]
        if out.get("sales") is None:
            if "product" not in sheet.columns:
                raise StoreDataError("Your targets sheet has sold figures but no category column. Choose "
                                     "which column holds the category.")
            names = {"sold_so_far": "units", "sold_value": "value"}
            out["sales"] = sheet[[c for c in ("product", "line", "department", "sold_so_far", "sold_value")
                                  if c in sheet.columns]].rename(columns=names)
        out["targets"] = sheet.drop(columns=["sold_so_far", "sold_value"], errors="ignore")
    if out.get("targets") is not None and "sold_value" in out["targets"].columns:
        out["targets"] = out["targets"].drop(columns="sold_value")

    # Month-to-date totals: share them across the days, and say the daily figures are estimates.
    summary = False
    question = summary_question(tables, earlier)
    if question and (not question["can_be_day"] or extras.get("one_date_means", question["guess"]) == "mtd"):
        as_of = extras.get("as_of") or question["as_of"]
        if as_of is None:
            raise StoreDataError("Your figures have no date. Choose the date they run up to.")
        as_of = pd.Timestamp(as_of)
        out["sales"] = _spread_totals(out["sales"], as_of, settings.get("sale_days", ()), settings.get("sale_uplift"))
        settings["summary_only"] = True
        summary = True

    sales = out["sales"]
    # The month: the latest one in the sales. The same month a year earlier is a yardstick.
    dates = _datetimes(sales["date"])
    text_dates = sales["date"].astype(str).str.strip()
    if not pd.api.types.is_datetime64_any_dtype(sales["date"]) and month_comes_first(text_dates):
        notes.append(Important("Your dates look month first (06/13/2026), so they were read that way. If "
                               "that's wrong, check the dates in your file."))
    latest = dates.max()
    if pd.isna(latest):
        raise StoreDataError(f"None of the sales dates can be read (for example "
                             f"{', '.join(map(str, sales['date'].dropna().head(2)))}).")
    if latest.normalize() > pd.Timestamp(date.today()) + pd.Timedelta(days=1):
        notes.append(Important(f"The latest sales date is {latest:%d %b %Y}, which is in the future. Please "
                               f"check the dates in your file."))
    same = (dates.dt.year == latest.year) & (dates.dt.month == latest.month)
    year_ago = (dates.dt.year == latest.year - 1) & (dates.dt.month == latest.month)
    last_year = sales[year_ago].copy()
    other = dates.notna() & ~same & ~year_ago
    if same.sum() < 0.2 * other.sum():
        notes.append(Important(f"Only {int(same.sum())} of your {int(dates.notna().sum())} sales rows are from "
                               f"{latest:%B %Y}, the latest month in the file, and only those are used. If that "
                               f"looks wrong, check the dates."))
    if other.any() or year_ago.any():
        notes.append(f"Sales: used {latest:%B %Y}, the latest month in your file"
                     + (f"; {int(other.sum())} rows from other months were left out" if other.any() else "")
                     + ".")
    sales, dates = sales[same | dates.isna()].copy(), dates[same | dates.isna()]
    if "hour" not in sales.columns:
        timed = dates.notna() & ((dates.dt.hour > 0) | (dates.dt.minute > 0))
        if timed.mean() > 0.5:
            sales["hour"] = dates.dt.hour.where(dates.notna())
            notes.append("Sales: took the hour of each sale from its time.")
    out["sales"] = sales

    targets = out.get("targets")
    if targets is not None and "month" in targets.columns:
        targets = _for_this_month(targets, latest, notes)
    if targets is None and (extras.get("store_target_units") or extras.get("store_target_value")):
        targets = pd.DataFrame({"target": [extras.get("store_target_units")],
                                "target_value": [extras.get("store_target_value")]})
    if last_year["date"].pipe(_parse_dates).dt.day.nunique() >= 25:
        targets = _with_last_year(targets, last_year, latest, notes)
    out["targets"] = targets

    stock = out.get("stock")
    if stock is not None and "date" in stock.columns:
        counted = _parse_dates(stock["date"])
        last_sale = _parse_dates(sales["date"]).max()
        if counted.nunique() == 1 and counted.iloc[0] > last_sale:
            # One stock report, run after the last sales day: it's the stock at that close.
            stock["date"] = last_sale
            notes.append(f"Stock: the count dated {counted.iloc[0]:%d %b} was used as the stock at the "
                         f"close of {last_sale:%d %b}, the last day of sales.")
        else:
            stock = _in_month(stock, "Stock", latest, notes)
    out["stock"] = stock
    if out.get("visitors") is not None:
        out["visitors"] = _in_month(out["visitors"], "Visitors", latest, notes)
    if earlier:
        _merge_with_saved(out, settings, earlier, summary, latest, notes)

    return {role: out.get(role) for role in ("sales", "targets", "stock", "visitors", "loyalty")}, \
        settings, notes


def _with_last_year(targets, last_year, latest, notes):
    """Last year's totals for the same month, per category, added where no last-year figure was given."""
    keys = ["line", "product"] if "line" in last_year.columns else ["product"]
    last_year = last_year.dropna(subset=["product"]).copy()
    for k in keys:
        last_year[k] = last_year[k].astype(str).str.strip()
    numbers = {"units": "last_year"} | ({"value": "last_year_value"} if "value" in last_year.columns else {})
    for column in numbers:
        last_year[column] = pd.to_numeric(last_year[column].astype(str).str.replace(r"[₹,\s]|rs\.?", "",
                                                                                    regex=True, case=False),
                                          errors="coerce")
    totals = last_year.groupby(keys, as_index=False)[list(numbers)].sum().rename(columns=numbers)
    if targets is None:
        notes.append(f"Your file has {latest:%B} {latest.year - 1} too, so each category is judged "
                     f"against last year's sales for the month.")
        return totals
    if "product" not in targets.columns:
        return targets  # floor, line or store targets: one targets table can't also hold categories
    match = [k for k in keys if k in targets.columns]
    merged = targets.copy()
    lookup = {tuple(_normal_name(v) for v in row[match]): row for _, row in totals.iterrows()}
    added = 0
    for column in numbers.values():
        if column not in merged.columns:
            merged[column] = None
        for i, row in merged.iterrows():
            if pd.isna(row[column]) or row[column] in ("", 0):
                found = lookup.get(tuple(_normal_name(row[k]) for k in match))
                if found is not None:
                    merged.at[i, column] = found[column]
                    added += 1
    if added:
        notes.append(f"Your file has {latest:%B} {latest.year - 1} too, so last year's sales were filled "
                     f"in where your targets didn't give them.")
    return merged


# --- Saving only what's used ----------------------------------------------------------------------------

def to_files(canonical, settings):
    """
    The tables in use, as CSV files with plain column names, for saving to an
    account: only the columns the app reads, so nothing else in the export
    (customer names, phone numbers, prices) is ever stored. Reading them back
    with find_tables() gives the same store.
    """
    files = []
    for role, df in canonical.items():
        if df is not None and not df.empty:
            out = df.copy()
            if "date" in out.columns:
                out["date"] = _parse_dates(out["date"]).dt.strftime("%Y-%m-%d").where(
                    _parse_dates(out["date"]).notna(), out["date"])
            files.append((f"{role}.csv", out.to_csv(index=False).encode("utf-8")))
    rows = [("Store name", settings.get("name", "")),
            ("Delivery day", WEEKDAY_NAMES[settings["delivery_weekday"]]
             if settings.get("delivery_weekday") is not None else ""),
            ("Sale days", ", ".join(str(d) for d in settings.get("sale_days", []))),
            ("Sale day uplift", settings.get("sale_uplift") or ""),
            ("Month-to-date file", "yes" if settings.get("summary_only") else ""),
            (SAVED_COPY, "yes")]
    files.append(("settings.csv", pd.DataFrame(rows, columns=["Setting", "Value"])
                  .to_csv(index=False).encode("utf-8")))
    return files
