"""
A store: how it's organised, its calendar, and its data.

Every calculation in Category Pulse reads from a Store rather than from fixed
settings, so the same engine runs on the simulated demo store or on any
store's own data:
  - demo_store() builds the demo from config.py and the simulated data files
  - build_store() builds one from any store's tables: targets and sales are
    required; stock, visitor counts and loyalty tiers are optional

Missing pieces are normal (a store may have no footfall counter, or record
sales by day rather than by hour). Each has_* property says what the data
can support, and a calculation that needs something the data doesn't have
raises MissingData with a plain explanation instead of guessing.
"""

import calendar
import functools
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import pandas as pd

import config

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# The single "size" a category has when the data doesn't record sizes.
NO_SIZE = "All sizes"

# The floor zone every category sits in when the data doesn't name floors.
WHOLE_STORE = "Whole store"


class MissingData(ValueError):
    """A calculation needs something this store's data doesn't include."""


class StoreDataError(ValueError):
    """
    A store's tables can't be used as they are. `problems` lists everything
    to fix; the message joins them, one per line.
    """

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


@dataclass(eq=False)
class Store:
    id: str                    # cache key: "demo", or a fingerprint of the store's data
    name: str
    is_demo: bool
    month_start: date
    days_in_month: int
    today_day: int             # the latest day with sales
    hours: list                # hourly selling slots: 10 = the 10:00-11:00 hour
    hourly: bool               # sales recorded by hour (False: daily totals, read as at close)
    default_hour: int          # the moment shown when no time is chosen
    categories: list           # "<line> <product type>", in the store's own order
    category_line: dict
    category_product: dict
    category_department: dict
    lines: dict                # line -> department (floor zone), in order
    targets: dict              # category -> monthly unit target
    last_year: dict            # category -> last year's units, or None if not given
    sizes: dict                # category -> sizes in display order
    core_sizes: dict           # category -> the sizes most shoppers need
    day_weights: dict          # day -> how busy it is relative to other days
    sale_days: frozenset
    delivery_weekday: object   # Monday=0 ... Sunday=6, or None if deliveries have no fixed day
    sales: pd.DataFrame        # day, hour, category, size, units_sold, value (+ line, department)
    transactions: object       # DataFrame of day, hour, category, transactions; or None
    stock: object              # DataFrame of day, hour, category, size, units_remaining; or None
    footfall: object           # DataFrame of day, hour, zone, visitors; or None
    loyalty: object            # DataFrame of tier-level loyalty figures per category; or None
    warnings: list = field(default_factory=list)  # things worth checking about the data

    # --- Calendar ------------------------------------------------------------------

    @property
    def departments(self):
        return list(dict.fromkeys(self.lines.values()))

    @property
    def month_name(self):
        return calendar.month_name[self.month_start.month]

    def date(self, day):
        return self.month_start + timedelta(days=day - 1)

    def weekday(self, day):
        return self.date(day).weekday()

    def is_busy(self, day):
        """Weekends and sale days trade differently (busier, later in the day)."""
        return self.weekday(day) >= 5 or day in self.sale_days

    def day_weight(self, day):
        return self.day_weights[day]

    def categories_in(self, line=None, department=None):
        return [c for c in self.categories
                if (line is None or self.category_line[c] == line)
                and (department is None or self.category_department[c] == department)]

    # --- What the data can support -------------------------------------------------

    @functools.cached_property
    def has_value(self):
        return bool(self.sales["value"].sum() > 0)

    @functools.cached_property
    def has_sizes(self):
        return any(sizes != [NO_SIZE] for sizes in self.sizes.values())

    @property
    def has_transactions(self):
        return self.transactions is not None

    @property
    def has_stock(self):
        return self.stock is not None

    @functools.cached_property
    def has_stock_history(self):
        """Stock counted on at least two days, so deliveries and usual levels can be learned."""
        return self.has_stock and self.stock["day"].nunique() >= 2

    @property
    def has_footfall(self):
        return self.footfall is not None

    @functools.cached_property
    def has_visitor_hours(self):
        return self.has_footfall and self.footfall["hour"].nunique() > 1

    @property
    def has_loyalty(self):
        return self.loyalty is not None

    def require(self, what):
        """Raises MissingData unless the data includes `what` (a has_* name without 'has_')."""
        if not getattr(self, f"has_{what}"):
            raise MissingData(f"This store's data has no {MISSING_LABEL[what]}.")

    # --- Fast stock lookups ----------------------------------------------------------

    @functools.cached_property
    def stock_lookup(self):
        """(category, day, hour) -> {size: units left}, for quick repeated lookups."""
        lookup = {}
        for (category, day, hour), rows in self.stock.groupby(["category", "day", "hour"]):
            lookup[(category, int(day), int(hour))] = dict(
                zip(rows["size"], rows["units_remaining"].astype(int)))
        return lookup

    @functools.cached_property
    def stock_hours_by_day(self):
        """day -> the hours stock was recorded at that day, in order."""
        pairs = self.stock[["day", "hour"]].drop_duplicates().sort_values(["day", "hour"])
        by_day = {}
        for day, hour in pairs.itertuples(index=False):
            by_day.setdefault(int(day), []).append(int(hour))
        return by_day

    def latest_stock_moment(self, day, hour):
        """The latest (day, hour) with a stock record at or before the given moment, or None."""
        for d in range(day, 0, -1):
            hours = [h for h in self.stock_hours_by_day.get(d, []) if d < day or h <= hour]
            if hours:
                return d, hours[-1]
        return None


MISSING_LABEL = {
    "value": "sales value (rupees)",
    "sizes": "sizes",
    "transactions": "bill (transaction) counts",
    "stock": "stock counts",
    "stock_history": "stock counts on more than one day",
    "footfall": "visitor counts",
    "visitor_hours": "visitor counts by hour",
    "loyalty": "loyalty tier figures",
}


def resolve(store=None, day=None, hour=None):
    """Fills in defaults: the demo store, its latest day, and its default hour."""
    store = store if store is not None else demo_store()
    return (store,
            store.today_day if day is None else day,
            store.default_hour if hour is None else hour)


# --- The demo store ---------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def demo_store():
    """The simulated demo store, built from config.py and the generated data files."""
    import generate_data  # imported here: only needed when the data has to be built

    if not generate_data.data_is_present(DATA_DIR):
        generate_data.generate_all(DATA_DIR)

    def read(name):
        return pd.read_csv(os.path.join(DATA_DIR, f"{name}.csv"))

    sales, stock = read("sales"), read("stock")
    for df in (sales, stock):
        df["size"] = df["size"].astype(str)  # waist sizes like 32 are labels, not numbers

    return Store(
        id="demo",
        name=config.STORE_NAME,
        is_demo=True,
        month_start=date.fromisoformat(config.MONTH_START),
        days_in_month=config.DAYS_IN_MONTH,
        today_day=config.TODAY_DAY,
        hours=list(config.STORE_HOURS),
        hourly=True,
        default_hour=config.DEFAULT_CURRENT_HOUR,
        categories=list(config.CATEGORIES),
        category_line=dict(config.CATEGORY_LINE),
        category_product=dict(config.CATEGORY_PRODUCT),
        category_department=dict(config.CATEGORY_DEPARTMENT),
        lines=dict(config.LINES),
        targets=dict(config.MONTHLY_TARGETS),
        last_year=dict(config.LAST_YEAR_UNITS),
        sizes={c: list(config.size_system_for(c)["sizes"]) for c in config.CATEGORIES},
        core_sizes={c: list(config.size_system_for(c)["core"]) for c in config.CATEGORIES},
        day_weights={d["day"]: config.day_weight(d) for d in config.month_calendar()},
        sale_days=frozenset({config.SALE_DAY}),
        delivery_weekday=config.DELIVERY_WEEKDAY,
        sales=sales,
        # The simulated sales repeat each category-hour's transaction count on
        # every size row, so it's taken once per category-hour.
        transactions=sales.groupby(["day", "hour", "category"], as_index=False)["transactions"].first(),
        stock=stock,
        footfall=read("footfall"),
        loyalty=read("loyalty_tiers"),
    )


# --- Any store's data ---------------------------------------------------------------------
#
# build_store() takes plain tables with these columns ([...] = optional):
#   targets:  line, product, target, [department], [last_year], [sizes], [core_sizes]
#   sales:    date, line, product, units, [hour], [size], [value], [transactions]
#   stock:    date, line, product, units, [hour], [size]
#   footfall: date, visitors, [hour], [department]
#   loyalty:  tier, line, product, share_of_transactions, avg_basket_value, avg_upt,
#             cross_sell_response_rate, preferred_offer_type
# Sizes and core sizes in the targets table are comma-separated, e.g. "S, M, L".

_LETTER_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "XXXL", "3XL"]


def _size_sort_key(size):
    upper = size.upper()
    if upper in _LETTER_SIZES:
        return (0, _LETTER_SIZES.index(upper), size)
    number = re.match(r"\d+(\.\d+)?", size)  # 30, 32 ... and kids' 8Y, 3-4Y
    if number:
        return (1, float(number.group()), size)
    return (2, 0, size)


def _core_by_sales(units_by_size):
    """
    Core sizes learned from sales: the fewest best-selling sizes that together
    make up at least CORE_SIZE_SALES_SHARE of the category's units.
    """
    total = sum(units_by_size.values())
    if total <= 0:
        return []
    core, running = [], 0
    for size, units in sorted(units_by_size.items(), key=lambda kv: -kv[1]):
        core.append(size)
        running += units
        if running >= config.CORE_SIZE_SALES_SHARE * total:
            break
    return core


def _sales_with_full_size_run(sales, stock, category, sizes):
    """
    Units sold by size, counting only days that opened with every size on the
    shelf (from the previous day's last stock count). A size that has been
    sold out for a week looks unpopular in raw sales, which would hide the
    very broken size run the core sizes are meant to catch. Without stock
    counts, or with fewer than 3 such days, all days count.
    """
    rows = sales[sales["category"] == category]
    if stock is not None:
        counts = stock[stock["category"] == category]
        closing = counts[counts["hour"] == counts.groupby("day")["hour"].transform("max")]
        by_day = closing.groupby("day").apply(
            lambda day: all(day.loc[day["size"] == s, "units_remaining"].sum() > 0 for s in sizes))
        full_days = {int(d) + 1 for d, full in by_day.items() if full}
        if len(full_days & set(rows["day"])) >= 3:
            rows = rows[rows["day"].isin(full_days)]
    return rows.groupby("size")["units_sold"].sum().to_dict()


class _Check:
    """
    Collects everything wrong with a store's tables, so the person sees every
    problem at once instead of fixing one, re-uploading and meeting the next.
    Problems stop the upload; warnings are things it could work around.
    """

    def __init__(self):
        self.problems, self.warnings = [], []

    def problem(self, message):
        self.problems.append(message)

    def warn(self, message):
        self.warnings.append(message)

    def stop_if_problems(self):
        if self.problems:
            raise StoreDataError(self.problems)


def _examples(values, limit=3):
    values = list(dict.fromkeys(str(v) for v in values))
    shown = ", ".join(repr(v) for v in values[:limit])
    return shown + (f" and {len(values) - limit} more" if len(values) > limit else "")


def _require_columns(df, table, columns, check):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        check.problem(f"The {table} table is missing: {', '.join(missing)}.")
    return not missing


def _text(series):
    return series.astype(str).str.strip()


def _normal_name(text):
    """For matching names regardless of capitals and spacing: ' Men  casual ' -> 'men casual'."""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _clean_number_text(values):
    """'1,200', '₹ 3,400', 'Rs. 500' -> numbers; blanks stay blank."""
    if values.dtype != object:
        return values
    text = values.astype(str).str.replace(r"(?i)rs\.?|₹|,|\s", "", regex=True)
    return text.where(values.notna() & (text != ""))


def _numbers(df, column, table, check, allow_blank=False, allow_negative=False):
    raw = df[column]
    values = pd.to_numeric(_clean_number_text(raw), errors="coerce")
    bad = values.isna() & (raw.notna() if allow_blank else True)
    if bad.any():
        check.problem(f"In the {table} table, the {column} column has things that aren't numbers: "
                      f"{_examples(raw[bad])}.")
    if not allow_negative and (values < 0).any():
        check.problem(f"In the {table} table, the {column} column has negative numbers.")
    return values.fillna(0) if bad.any() else values


def _has(df, column):
    return column in df.columns and df[column].notna().any()


def _parse_dates(values):
    """
    Dates as the store's files write them: real dates (from Excel), year-first
    text (2026-05-24), or day-first text as written in India (24/05/2026).
    Unreadable ones come back blank.
    """
    if pd.api.types.is_datetime64_any_dtype(values):
        return values
    text = values.astype(str).str.strip()
    year_first = text.str.match(r"^\d{4}-\d{1,2}-\d{1,2}")
    dates = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    dates[year_first] = pd.to_datetime(text[year_first].str[:10], format="%Y-%m-%d", errors="coerce")
    dates[~year_first] = pd.to_datetime(text[~year_first], dayfirst=True, format="mixed", errors="coerce")
    return dates


def _days(dates, raw, table, month_start, check):
    """Day of the month for each row; problems for unreadable dates or other months."""
    if dates.isna().any():
        check.problem(f"The {table} table has dates that can't be read: {_examples(raw[dates.isna()])}.")
    known = dates.dropna()
    outside = (known.dt.year != month_start.year) | (known.dt.month != month_start.month)
    if outside.any():
        months = sorted({f"{calendar.month_name[d.month]} {d.year}" for d in known[outside]})
        check.problem(f"The {table} table has dates outside {calendar.month_name[month_start.month]} "
                      f"{month_start.year} (also {', '.join(months)}). Use one month at a time.")
    return dates.dt.day.fillna(1).astype(int)


def _parse_hour(value):
    """
    An hour slot from how shops write times: 10, 10.0, '10', '10:00',
    '10:00-11:00', '2 PM', '2:30 pm', or a time. 10 means 10:00-11:00.
    None if it can't be read.
    """
    if isinstance(value, (datetime, time)):
        return value.hour
    if isinstance(value, (int, float)) and not pd.isna(value):
        return int(value) if float(value).is_integer() and 0 <= value <= 23 else None
    match = re.match(r"\s*(\d{1,2})(?::\d{2})?(?::\d{2})?\s*([ap]\.?m\.?)?", str(value), re.I)
    if not match:
        return None
    hour, half = int(match.group(1)), (match.group(2) or "").lower().replace(".", "")
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    return hour if 0 <= hour <= 23 else None


def _hours(df, table, fallback, check):
    """Whole-hour slots; rows without hours count as the close of the day."""
    if not _has(df, "hour"):
        return pd.Series(fallback, index=df.index), False
    if df["hour"].isna().any():
        check.problem(f"Some rows in the {table} table have an hour and some don't. Fill in every "
                      f"hour, or remove the Hour column.")
    hours = df["hour"].map(lambda v: None if pd.isna(v) else _parse_hour(v))
    unreadable = hours.isna() & df["hour"].notna()
    if unreadable.any():
        check.problem(f"The {table} table has hours that can't be read: "
                      f"{_examples(df.loc[unreadable, 'hour'])}. Use 10, 10:00 or 10 AM.")
    return hours.fillna(fallback).astype(int), True


def _category_ids(df, table, lookup, check):
    """
    Each row's category id, matched to the Targets sheet regardless of
    capitals and spacing. Rows for categories with no target are left out
    (with a warning), since there's nothing to measure them against.
    """
    typed = _text(df["line"]) + " " + _text(df["product"])
    ids = typed.map(lambda name: lookup.get(_normal_name(name)))
    unknown = typed[ids.isna()]
    if len(unknown) and ids.notna().any():
        check.warn(f"The {table} table has categories that aren't in Targets, so they were left out: "
                   f"{_examples(unknown, 5)}. Add them to Targets to include them.")
    elif len(unknown):
        check.problem(f"None of the categories in the {table} table match the Targets sheet (for "
                      f"example {_examples(unknown, 2)}). Check the line and category names match.")
    return ids


def build_store(targets, sales, stock=None, footfall=None, loyalty=None, *, name="Your store",
                store_id=None, sale_days=(), delivery_weekday=None):
    """
    Builds a Store from any store's tables (columns listed above). Raises
    StoreDataError listing every problem if the data can't be used; things it
    can work around are listed in the store's `warnings` instead.

    The month and "today" come from the sales dates: today is the latest day
    with sales, and the store is read as at the close of that day.
    """
    check = _Check()

    # Structure and targets
    if not _require_columns(targets, "targets", ["line", "product", "target"], check):
        check.stop_if_problems()
    targets = targets.dropna(subset=["line", "product"]).copy()
    targets["line"], targets["product"] = _text(targets["line"]), _text(targets["product"])
    targets["category"] = targets["line"] + " " + targets["product"]
    targets["key"] = targets["category"].map(_normal_name)
    duplicated = targets.loc[targets["key"].duplicated(), "category"].tolist()
    if duplicated:
        check.problem(f"The targets table lists a category twice: {_examples(duplicated)}.")
    targets["target"] = _numbers(targets, "target", "targets", check)
    if (targets["target"] <= 0).any():
        check.problem(f"Every target must be more than zero (check "
                      f"{_examples(targets.loc[targets['target'] <= 0, 'category'])}).")
    department = (_text(targets["department"]) if _has(targets, "department")
                  else pd.Series(WHOLE_STORE, index=targets.index))
    categories = targets["category"].tolist()
    lookup = dict(zip(targets["key"], categories))
    category_line = dict(zip(categories, targets["line"]))
    category_department = dict(zip(categories, department))
    lines = {}
    for category in categories:
        line, dept = category_line[category], category_department[category]
        if lines.setdefault(line, dept) != dept:
            check.problem(f"Line {line} is on more than one floor ({lines[line]} and {dept}).")

    # Sales: fix the month, "today" and the hours
    if not _require_columns(sales, "sales", ["date", "line", "product", "units"], check):
        check.stop_if_problems()
    sales = sales.dropna(subset=["date"]).copy()
    if sales.empty:
        check.problem("The sales table has no rows.")
        check.stop_if_problems()
    dates = _parse_dates(sales["date"])
    if dates.isna().all():
        check.problem(f"The sales table's dates can't be read (for example {_examples(sales['date'], 2)}).")
        check.stop_if_problems()
    first = dates.min()
    month_start = date(first.year, first.month, 1)
    days_in_month = calendar.monthrange(first.year, first.month)[1]

    sales["day"] = _days(dates, sales["date"], "sales", month_start, check)
    sales["hour"], hourly = _hours(sales, "sales", config.STORE_HOURS[-1], check)
    sales["category"] = _category_ids(sales, "sales", lookup, check)
    sales["units_sold"] = _numbers(sales, "units", "sales", check, allow_negative=True)
    sales["value"] = (_numbers(sales, "value", "sales", check, allow_blank=True, allow_negative=True)
                      .fillna(0) if _has(sales, "value") else 0.0)
    sales["size"] = _text(sales["size"]) if _has(sales, "size") else NO_SIZE
    returns = int((sales["units_sold"] < 0).sum())
    if returns:
        check.warn(f"{returns} sales row{'s' if returns > 1 else ''} with negative units (returns) "
                   f"were netted off against sales.")
    sales = sales[sales["category"].notna()]
    check.stop_if_problems()

    today_day = int(sales["day"].max())
    first_day = int(sales["day"].min())
    if first_day > 1:
        check.warn(f"Your sales start on day {first_day}, so days 1 to {first_day - 1} count as no "
                   f"sales and every category will look further behind than it is. If the store "
                   f"was open then, include sales from the 1st of the month.")
    hours = (list(range(int(sales["hour"].min()), int(sales["hour"].max()) + 1)) if hourly
             else list(config.STORE_HOURS))

    transactions = None
    if _has(sales, "transactions"):
        sales["transactions"] = _numbers(sales, "transactions", "sales", check, allow_blank=True).fillna(0)
        transactions = sales.groupby(["day", "hour", "category"], as_index=False)["transactions"].sum()

    keys = ["day", "hour", "category", "size"]
    sales = sales.groupby(keys, as_index=False)[["units_sold", "value"]].sum()

    # Stock (optional)
    if stock is not None and _require_columns(stock, "stock", ["date", "line", "product", "units"], check):
        stock = stock.dropna(subset=["date"]).copy()
        stock_dates = _parse_dates(stock["date"])
        last_sales_date = pd.Timestamp(month_start + timedelta(days=today_day - 1))
        next_morning = stock_dates.dt.normalize() == last_sales_date + pd.Timedelta(days=1)
        if next_morning.any() and not _has(stock, "hour"):
            # A count taken before opening the next day is the previous day's closing stock.
            stock_dates = stock_dates.where(~next_morning, last_sales_date)
            check.warn(f"Stock counted on {(last_sales_date + pd.Timedelta(days=1)):%d %b} was used as "
                       f"the closing stock of {last_sales_date:%d %b}, the last day of sales.")
        stock["day"] = _days(stock_dates, stock["date"], "stock", month_start, check)
        if (stock["day"] > today_day).any():
            check.problem(f"The stock table has counts after the last day of sales (day {today_day}). "
                          f"Date the latest count on the last day of sales.")
        stock["hour"], _ = _hours(stock, "stock", hours[-1], check)
        stock["category"] = _category_ids(stock, "stock", lookup, check)
        stock["units_remaining"] = _numbers(stock, "units", "stock", check, allow_negative=True)
        if (stock["units_remaining"] < 0).any():
            check.warn("Some stock counts were negative (usually unrecorded deliveries), so they were "
                       "treated as zero.")
            stock["units_remaining"] = stock["units_remaining"].clip(lower=0)
        stock["size"] = _text(stock["size"]) if _has(stock, "size") else NO_SIZE
        if (stock["size"] == NO_SIZE).all() != (sales["size"] == NO_SIZE).all():
            check.problem("Sales and stock must both have sizes, or both leave them out.")
        stock = stock[stock["category"].notna()]
        stock = stock.groupby(keys, as_index=False)["units_remaining"].sum()
    elif stock is not None:
        stock = None

    # Visitor counts (optional)
    if footfall is not None and _require_columns(footfall, "footfall", ["date", "visitors"], check):
        footfall = footfall.dropna(subset=["date"]).copy()
        footfall["day"] = _days(_parse_dates(footfall["date"]), footfall["date"], "footfall",
                                month_start, check)
        footfall["hour"], _ = _hours(footfall, "footfall", hours[-1], check)
        floors = {_normal_name(f): f for f in lines.values()}
        typed = (_text(footfall["department"]) if _has(footfall, "department")
                 else pd.Series(WHOLE_STORE, index=footfall.index))
        footfall["zone"] = typed.map(lambda f: floors.get(_normal_name(f)))
        if footfall["zone"].isna().any():
            check.problem(f"The visitors table has floors that no line is on: "
                          f"{_examples(typed[footfall['zone'].isna()])}. Use the floor names from "
                          f"Targets ({', '.join(dict.fromkeys(lines.values()))}).")
        footfall["visitors"] = _numbers(footfall, "visitors", "footfall", check)
        footfall = footfall.groupby(["day", "hour", "zone"], as_index=False)["visitors"].sum()
    elif footfall is not None:
        footfall = None

    # Loyalty tiers (optional)
    loyalty_columns = ["tier", "line", "product", "share_of_transactions", "avg_basket_value", "avg_upt",
                       "cross_sell_response_rate", "preferred_offer_type"]
    if loyalty is not None and _require_columns(loyalty, "loyalty", loyalty_columns, check):
        loyalty = loyalty.dropna(subset=["tier"]).copy()
        loyalty["category"] = _category_ids(loyalty, "loyalty", lookup, check)
        for column in ("share_of_transactions", "avg_basket_value", "avg_upt", "cross_sell_response_rate"):
            loyalty[column] = _numbers(loyalty, column, "loyalty", check)
        loyalty = loyalty[loyalty["category"].notna()]
    elif loyalty is not None:
        loyalty = None

    check.stop_if_problems()

    # Sizes, in display order, and the core sizes most shoppers need
    sizes, core_sizes = {}, {}
    for _, row in targets.iterrows():
        category = row["category"]
        seen = set(sales.loc[sales["category"] == category, "size"])
        if stock is not None:
            seen |= set(stock.loc[stock["category"] == category, "size"])
        if _has(targets, "sizes") and pd.notna(row.get("sizes")):
            listed = [s.strip() for s in str(row["sizes"]).split(",") if s.strip()]
            sizes[category] = listed + sorted(seen - set(listed) - {NO_SIZE}, key=_size_sort_key)
        else:
            sizes[category] = sorted(seen - {NO_SIZE}, key=_size_sort_key) or [NO_SIZE]
        if sizes[category] == [NO_SIZE]:
            core_sizes[category] = []
        elif _has(targets, "core_sizes") and pd.notna(row.get("core_sizes")):
            core_sizes[category] = [s.strip() for s in str(row["core_sizes"]).split(",") if s.strip()]
        else:
            units = _sales_with_full_size_run(sales, stock, category, sizes[category])
            core = _core_by_sales({s: units.get(s, 0) for s in sizes[category]})
            core_sizes[category] = [s for s in sizes[category] if s in core]

    # Add each row's line and floor, as the Sample Store's data has them.
    for df in (sales, stock):
        if df is not None:
            df["line"] = df["category"].map(category_line)
            df["department"] = df["category"].map(category_department)

    return Store(
        id=store_id or f"store-{pd.util.hash_pandas_object(sales, index=False).sum()}",
        name=name,
        is_demo=False,
        month_start=month_start,
        days_in_month=days_in_month,
        today_day=today_day,
        hours=hours,
        hourly=hourly,
        default_hour=hours[-1],  # a store's own data is read as at the close of its latest day
        categories=categories,
        category_line=category_line,
        category_product=dict(zip(categories, targets["product"])),
        category_department=category_department,
        lines=lines,
        targets=dict(zip(categories, targets["target"].astype(int))),
        last_year=(dict(zip(categories, _numbers(targets, "last_year", "targets", check, allow_blank=True)))
                   if _has(targets, "last_year") else {}),
        sizes=sizes,
        core_sizes=core_sizes,
        day_weights=_learn_day_weights(sales, month_start, days_in_month, today_day, set(sale_days)),
        sale_days=frozenset(sale_days),
        delivery_weekday=delivery_weekday,
        sales=sales,
        transactions=transactions,
        stock=stock,
        footfall=footfall,
        loyalty=loyalty,
        warnings=check.warnings,
    )


def _learn_day_weights(sales, month_start, days_in_month, today_day, sale_days):
    """
    How busy each day of the month is relative to the others, used to phase a
    monthly target into a target for today. Learned from this month's own
    sales by weekday once there are two full weeks to learn from; before that,
    the usual weekday-vs-weekend pattern in config.py.
    """
    weekday = {d: (month_start + timedelta(days=d - 1)).weekday() for d in range(1, days_in_month + 1)}
    daily = sales[sales["day"] < today_day].groupby("day")["units_sold"].sum()
    daily = daily[[d not in sale_days for d in daily.index]]
    by_weekday = daily.groupby([weekday[d] for d in daily.index]).mean()
    if len(daily) >= 14 and len(by_weekday) == 7 and daily.mean() > 0:
        weights = (by_weekday / daily.mean()).to_dict()
    else:
        weights = config.WEEKDAY_WEIGHTS
    return {d: float(weights[weekday[d]]) for d in range(1, days_in_month + 1)}
