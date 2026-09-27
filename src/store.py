"""
A store: how it's organised, its calendar, and its data.

Every calculation in Category Pulse reads from a Store rather than from fixed
settings, so the same engine runs on the simulated demo store or on any
store's own data:
  - demo_store() builds the demo from config.py and the simulated data files
  - build_store() builds one from any store's tables: only sales are
    required; targets, stock, visitor counts and loyalty tiers are optional

Missing pieces are normal (a store may have no footfall counter, or record
sales by day rather than by hour). Each has_* property says what the data
can support, and a calculation that needs something the data doesn't have
raises MissingData with a plain explanation instead of guessing.
"""

import calendar
import dataclasses
import functools
import hashlib
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
    targets: dict              # category -> monthly target in units (None: no target to judge by)
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
    value_targets: dict = field(default_factory=dict)  # category -> monthly target in rupees
    target_notes: dict = field(default_factory=dict)   # measure -> notes on where targets came from
    target_source: dict = field(default_factory=dict)  # measure -> {category: where its target came from}
    measure: str = "units"     # what pace is judged in: "units" or "value" (rupees)

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

    # --- Units or rupees -----------------------------------------------------------

    @property
    def measures(self):
        """What pace can be judged in: units always, rupees when the sales have values."""
        return ["units", "value"] if self.has_value else ["units"]

    @property
    def default_measure(self):
        """Rupees if only rupee targets were given; otherwise units."""
        def given(measure, targets):
            if measure not in self.target_source:
                return any(targets.values())
            return any(s in GIVEN_SOURCES for s in self.target_source[measure].values())
        has_unit, has_value = given("units", self.targets), given("value", self.value_targets)
        return "value" if has_value and not has_unit and self.has_value else "units"

    def for_measure(self, measure):
        """This store, judged in `measure`. The same data; a different yardstick."""
        if measure == self.measure or measure not in self.measures:
            return self
        views = self.__dict__.setdefault("_measure_views", {})
        if measure not in views:
            views[measure] = dataclasses.replace(self, measure=measure, id=f"{self.id}:{measure}")
        return views[measure]

    @property
    def sold_column(self):
        return "value" if self.measure == "value" else "units_sold"

    @property
    def active_targets(self):
        return self.value_targets if self.measure == "value" else self.targets

    @functools.cached_property
    def avg_price(self):
        """category -> average rupees per unit this month (for judging rupee gaps fairly)."""
        totals = self.sales.groupby("category")[["units_sold", "value"]].sum()
        overall = (totals["value"].sum() / totals["units_sold"].sum()) if totals["units_sold"].sum() else 1.0
        return {c: (row.value / row.units_sold if row.units_sold > 0 else overall)
                for c, row in totals.iterrows()} | {"_overall": overall or 1.0}

    @property
    def measure_label(self):
        return "rupees" if self.measure == "value" else "units"

    def amount(self, x, per_day=False):
        """A figure in this store's measure, as people say it: '1,234' (units) or '₹2.45 L'."""
        if x is None:
            return "-"
        if self.measure == "value":
            return short_inr(x)
        return f"{x:,.1f}" if per_day else f"{x:,.0f}"

    def amount_of(self, x):
        """Like amount(), naming the unit: '1,234 units' or '₹2.45 L'."""
        return self.amount(x) if self.measure == "value" else f"{self.amount(x)} units"

    def scale(self, categories):
        """
        How many 'units' one step of the measure is worth, for the noise check:
        1 for units; the average price for rupees (a rupee total varies by
        about the square root of expected rupees x price).
        """
        if self.measure != "value":
            return 1.0
        prices = [self.avg_price.get(c, self.avg_price["_overall"]) for c in categories]
        return sum(prices) / len(prices) if prices else self.avg_price["_overall"]

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
        rows = self.stock[["category", "day", "hour", "size", "units_remaining"]]
        for category, day, hour, size, units in rows.itertuples(index=False):
            lookup.setdefault((category, int(day), int(hour)), {})[size] = int(units)
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


def short_inr(value):
    """Rupees as shops say them: ₹850, ₹45,000, ₹2.45 L (lakh), ₹1.20 Cr (crore)."""
    sign, v = ("-" if value < 0 else ""), abs(float(value))
    if v >= 1e7:
        return f"{sign}₹{v / 1e7:.2f} Cr"
    if v >= 1e5:
        return f"{sign}₹{v / 1e5:.2f} L"
    return f"{sign}₹{v:,.0f}"


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
        value_targets={c: config.MONTHLY_TARGETS[c] * config.AVG_PRICE[c] for c in config.CATEGORIES},
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
#   sales:    date, product, units, [line], [department], [hour], [size], [value],
#             [transactions] or [bill] (a bill number per row: bills are counted)
#   targets:  (optional) one of: product (per category), department (per floor),
#             line (per line), or none (one store total); then [target] (units),
#             [target_value] (rupees), [last_year], [last_year_value], [line],
#             [department], [sizes], [core_sizes]
#   stock:    product, units, [date], [line], [hour], [size]
#   footfall: date, visitors, [hour], [department]
#   loyalty:  tier, product, [line], share_of_transactions, avg_basket_value, avg_upt,
#             cross_sell_response_rate, preferred_offer_type
# A category is "<line> <product>" when the sales name lines, else just the product.
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
    counts = stock[stock["category"] == category] if stock is not None else None
    if counts is not None and not counts.empty:
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


# --- Targets from whatever the store has ------------------------------------------------
#
# A store may set targets per category, per floor or line, or only for the
# whole store; in units, rupees or both; or not at all. Each category's target
# comes from the first of these that exists:
#   1. its own target
#   2. a share of its floor's or line's target, or of the store's target
#   3. its target in the other measure, at this month's average price
#   4. last year's sales for the month
#   5. its own pace earlier in the month (so "behind" means it has slowed down)
# target_source records which, so the screens can say "estimated".

GIVEN_SOURCES = {"target", "floor share", "line share", "store share"}

LEVEL_NAMES = {"department": "floor", "line": "line"}


def _plural(n, one, many):
    return one if n == 1 else many


def _who(n, total):
    return "any category" if n == total else f"{n} {_plural(n, 'category', 'categories')}"


def _early_share(sales, column, categories, today_day):
    """
    Each category's sales before the last week (or the whole month so far, if
    that's too short). Used to share a floor or store target across
    categories: sharing by the whole month so far would make every category
    exactly as far behind as its floor, hiding which one slipped.
    """
    baseline = sales[sales["day"] <= today_day - 7]
    use_baseline = baseline["day"].nunique() >= 5 and baseline[column].sum() > 0
    by_category = (baseline if use_baseline else sales).groupby("category")[column].sum()
    return {c: max(float(by_category.get(c, 0.0)), 0.0) for c in categories}, use_baseline


def _share_out(targets, source, members, total, shares, how):
    weight = sum(shares[c] for c in members)
    for c in members:
        share = shares[c] / weight if weight else 1 / len(members)
        if share > 0:
            targets[c], source[c] = total * share, how


def _stated_targets(given, by_group, store_total, level, category_group, shares):
    """Targets as the store set them: per category, or shared out from a floor, line or store total."""
    targets = {c: v for c, v in given.items() if v}
    source = {c: "target" for c in targets}
    for group, total in by_group.items():
        members = [c for c in shares if category_group[c] == group and c not in targets]
        _share_out(targets, source, members, total, shares, f"{LEVEL_NAMES[level]} share")
    if store_total:
        members = [c for c in shares if c not in targets]
        _share_out(targets, source, members, store_total, shares, "store share")
    return targets, source


def _own_pace_target(sales, column, category, weights, today_day):
    """
    A benchmark when there's no target: the category's pace before the last
    week, carried over the whole month (busy days weigh more). None with
    fewer than 5 days to learn from.
    """
    base_days = list(range(1, today_day - 6))
    if len(base_days) < 5:
        return None
    rows = sales[(sales["category"] == category) & sales["day"].isin(base_days)]
    sold, weight = float(rows[column].sum()), sum(weights[d] for d in base_days)
    if sold <= 0 or weight <= 0:
        return None
    return sold / weight * sum(weights.values())


def _match_ids(full, product, lookup, product_lookup):
    """Category ids for rows of another table, by 'line product' or, failing that, by a unique product name."""
    return pd.Series([lookup.get(_normal_name(f)) or product_lookup.get(_normal_name(p))
                      for f, p in zip(full, product)], index=full.index, dtype=object)


def build_store(targets, sales, stock=None, footfall=None, loyalty=None, *, name="Your store",
                store_id=None, sale_days=(), delivery_weekday=None, target_level=None):
    """
    Builds a Store from any store's tables (columns listed above). Raises
    StoreDataError listing every problem if the data can't be used; things it
    can work around are listed in the store's `warnings` instead.

    Only sales are required: a date, units and a category ("product"). Lines,
    floors (department), sizes, hours, value, bills, stock, visitor counts,
    loyalty figures and targets are all optional. Targets (None for none) can
    be per category, per floor, per line or for the whole store:
    `target_level` is "category", "department", "line" or "store", worked out
    from the targets' columns if not given.

    The month and "today" come from the sales dates: today is the latest day
    with sales, and the store is read as at the close of that day.
    """
    check = _Check()

    # --- Sales: the month and the categories they name ---------------------------------------
    if not _require_columns(sales, "sales", ["date", "product", "units"], check):
        check.stop_if_problems()
    sales = sales.dropna(subset=["date"]).copy()
    sales = sales[sales["product"].notna() & (_text(sales["product"]) != "")]
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

    has_line = _has(sales, "line")
    sales["product"] = _text(sales["product"])
    sales["line"] = _text(sales["line"]) if has_line else ""
    sales["typed"] = (sales["line"] + " " + sales["product"]) if has_line else sales["product"]
    sales["floor"] = _text(sales["department"]) if _has(sales, "department") else ""

    # --- Targets: which level they're set at ---------------------------------------------------
    if targets is not None:
        targets = targets.dropna(how="all").copy()
        if targets.empty:
            targets = None
    if targets is not None and target_level is None:
        target_level = ("category" if _has(targets, "product") else
                        "department" if _has(targets, "department") else
                        "line" if _has(targets, "line") else "store")
    measure_columns = {"units": ("target", "last_year"), "value": ("target_value", "last_year_value")}
    if targets is not None and not any(_has(targets, c) for pair in measure_columns.values() for c in pair):
        check.problem("The targets table has no target or last-year numbers.")
        check.stop_if_problems()

    # --- Categories: every one with sales, plus any with a target but no sales yet -------------
    lookup, info = {}, {}   # normalised id -> id; id -> [product, line, floor]
    for typed, product, line, floor in (sales[["typed", "product", "line", "floor"]]
                                        .drop_duplicates("typed").itertuples(index=False)):
        key = _normal_name(typed)
        if key not in lookup:
            lookup[key] = typed
            info[typed] = [product, line, floor]
        elif not info[lookup[key]][2]:
            info[lookup[key]][2] = floor
    by_product = {}
    for cid, (product, _, _) in info.items():
        by_product.setdefault(_normal_name(product), []).append(cid)
    product_lookup = {p: ids[0] for p, ids in by_product.items() if len(ids) == 1}

    order, target_row = [], {}
    if targets is not None and target_level == "category":
        targets["product"] = _text(targets["product"])
        t_line = _text(targets["line"]) if _has(targets, "line") else pd.Series("", index=targets.index)
        t_floor = (_text(targets["department"]) if _has(targets, "department")
                   else pd.Series("", index=targets.index))
        full = (t_line + " " + targets["product"]) if has_line and _has(targets, "line") else targets["product"]
        ids = _match_ids(full, targets["product"], lookup, product_lookup)
        for i, cid in ids.items():
            if cid is None:  # a target for a category with no sales yet
                cid = full[i]
                lookup[_normal_name(cid)] = cid
                info[cid] = [targets.at[i, "product"], t_line[i], t_floor[i]]
            elif cid != full[i] and _normal_name(cid) == _normal_name(full[i]) and cid not in target_row:
                # Same category, spelt differently: use the targets' spelling.
                info[full[i]] = info.pop(cid)
                info[full[i]][0] = targets.at[i, "product"]
                for names in (lookup, product_lookup):
                    names.update({k: full[i] for k, v in names.items() if v == cid})
                cid = full[i]
            if cid in target_row:
                check.problem(f"The targets table lists a category twice: {cid!r}.")
                continue
            target_row[cid] = targets.loc[i]
            order.append(cid)
            info[cid][1] = info[cid][1] or t_line[i]
            info[cid][2] = info[cid][2] or t_floor[i]
        unmatched = [c for c in info if c not in target_row]
        if unmatched:
            check.warn(f"{len(unmatched)} {_plural(len(unmatched), 'category has', 'categories have')} "
                       f"sales but no row in your targets: {_examples(unmatched, 5)}. "
                       f"{_plural(len(unmatched), 'It is', 'They are')} still included. If one is another "
                       f"spelling of a target category, make the names match.")
    categories = order + [c for c in info if c not in target_row]
    sales["category"] = sales["typed"].map(lambda t: lookup[_normal_name(t)])

    # Floors: a category without one takes its line's floor, if another category names it.
    line_floor = {}
    for c in categories:
        if info[c][1] and info[c][2]:
            line_floor.setdefault(info[c][1], info[c][2])
    category_department = {c: info[c][2] or line_floor.get(info[c][1]) or WHOLE_STORE for c in categories}
    category_line = {c: info[c][1] or (category_department[c] if category_department[c] != WHOLE_STORE
                                       else "All categories") for c in categories}
    category_product = {c: info[c][0] for c in categories}
    lines = {}
    for c in categories:
        line, dept = category_line[c], category_department[c]
        if lines.setdefault(line, dept) != dept:
            check.problem(f"Line {line} is on more than one floor ({lines[line]} and {dept}).")

    # --- Sales numbers ----------------------------------------------------------------------------
    sales["day"] = _days(dates, sales["date"], "sales", month_start, check)
    sales["hour"], hourly = _hours(sales, "sales", config.STORE_HOURS[-1], check)
    sales["units_sold"] = _numbers(sales, "units", "sales", check, allow_negative=True)
    sales["value"] = (_numbers(sales, "value", "sales", check, allow_blank=True, allow_negative=True)
                      .fillna(0) if _has(sales, "value") else 0.0)
    sales["size"] = _text(sales["size"]) if _has(sales, "size") else NO_SIZE
    returns = int((sales["units_sold"] < 0).sum())
    if returns:
        check.warn(f"{returns} sales row{'s' if returns > 1 else ''} with negative units (returns) "
                   f"were netted off against sales.")
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
    if _has(sales, "bill"):
        # Bill-level exports: each bill counts once per category and hour.
        transactions = (sales.dropna(subset=["bill"])
                        .groupby(["day", "hour", "category"])["bill"].nunique()
                        .reset_index(name="transactions"))
    elif _has(sales, "transactions"):
        sales["transactions"] = _numbers(sales, "transactions", "sales", check, allow_blank=True).fillna(0)
        transactions = sales.groupby(["day", "hour", "category"], as_index=False)["transactions"].sum()

    keys = ["day", "hour", "category", "size"]
    sales = sales.groupby(keys, as_index=False)[["units_sold", "value"]].sum()

    def ids_for(df, table):
        """Category ids for another table's rows; rows for unknown categories are left out."""
        product = _text(df["product"])
        full = (_text(df["line"]) + " " + product) if has_line and _has(df, "line") else product
        ids = _match_ids(full, product, lookup, product_lookup)
        if ids.isna().any():
            check.warn(f"The {table} table has categories with no sales or target, so they were left "
                       f"out: {_examples(full[ids.isna()], 5)}.")
        return ids

    # --- Stock (optional) --------------------------------------------------------------------------
    if stock is not None and _require_columns(stock, "stock", ["product", "units"], check):
        stock = stock.dropna(how="all").copy()
        last_sales_date = pd.Timestamp(month_start + timedelta(days=today_day - 1))
        if _has(stock, "date"):
            stock_dates = _parse_dates(stock["date"])
            raw_dates = stock["date"]
        else:
            # A stock report without dates is a count taken now: the close of the last sales day.
            stock_dates = raw_dates = pd.Series(last_sales_date, index=stock.index)
            check.warn(f"The stock report has no dates, so it was read as the stock at the close of "
                       f"{last_sales_date:%d %b}, the last day of sales.")
        next_morning = stock_dates.dt.normalize() == last_sales_date + pd.Timedelta(days=1)
        if next_morning.any() and not _has(stock, "hour"):
            # A count taken before opening the next day is the previous day's closing stock.
            stock_dates = stock_dates.where(~next_morning, last_sales_date)
            check.warn(f"Stock counted on {(last_sales_date + pd.Timedelta(days=1)):%d %b} was used as "
                       f"the closing stock of {last_sales_date:%d %b}, the last day of sales.")
        stock["day"] = _days(stock_dates, raw_dates, "stock", month_start, check)
        if (stock["day"] > today_day).any():
            check.problem(f"The stock table has counts after the last day of sales (day {today_day}). "
                          f"Date the latest count on the last day of sales.")
        stock["hour"], _ = _hours(stock, "stock", hours[-1], check)
        stock["category"] = ids_for(stock, "stock")
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

    # --- Visitor counts (optional) -----------------------------------------------------------------
    if footfall is not None and _require_columns(footfall, "footfall", ["date", "visitors"], check):
        footfall = footfall.dropna(subset=["date"]).copy()
        footfall["day"] = _days(_parse_dates(footfall["date"]), footfall["date"], "footfall",
                                month_start, check)
        footfall["hour"], _ = _hours(footfall, "footfall", hours[-1], check)
        floors = list(dict.fromkeys(lines.values()))
        if len(floors) == 1 or not _has(footfall, "department"):
            # One floor (or counts for the whole store): every visitor counts for it.
            if len(floors) > 1:
                check.problem(f"The visitors table has no floor column, but your categories are on "
                              f"{len(floors)} floors ({', '.join(floors)}). Add a floor to each row.")
            footfall["zone"] = floors[0]
        else:
            by_name = {_normal_name(f): f for f in floors}
            typed = _text(footfall["department"])
            footfall["zone"] = typed.map(lambda f: by_name.get(_normal_name(f)))
            if footfall["zone"].isna().any():
                check.problem(f"The visitors table has floors that no category is on: "
                              f"{_examples(typed[footfall['zone'].isna()])}. Use the same floor names "
                              f"as your sales or targets ({', '.join(floors)}).")
        footfall["visitors"] = _numbers(footfall, "visitors", "footfall", check)
        footfall = footfall.groupby(["day", "hour", "zone"], as_index=False)["visitors"].sum()
    elif footfall is not None:
        footfall = None

    # --- Loyalty tiers (optional) ------------------------------------------------------------------
    loyalty_columns = ["tier", "product", "share_of_transactions", "avg_basket_value", "avg_upt",
                       "cross_sell_response_rate", "preferred_offer_type"]
    if loyalty is not None and _require_columns(loyalty, "loyalty", loyalty_columns, check):
        loyalty = loyalty.dropna(subset=["tier"]).copy()
        loyalty["category"] = ids_for(loyalty, "loyalty")
        for column in ("share_of_transactions", "avg_basket_value", "avg_upt", "cross_sell_response_rate"):
            loyalty[column] = _numbers(loyalty, column, "loyalty", check)
        loyalty = loyalty[loyalty["category"].notna()]
    elif loyalty is not None:
        loyalty = None

    check.stop_if_problems()

    # --- Sizes, in display order, and the core sizes most shoppers need -----------------------------
    sizes, core_sizes = {}, {}
    for category in categories:
        row = target_row.get(category)
        listed_sizes = row.get("sizes") if row is not None else None
        listed_core = row.get("core_sizes") if row is not None else None
        seen = set(sales.loc[sales["category"] == category, "size"])
        if stock is not None:
            seen |= set(stock.loc[stock["category"] == category, "size"])
        if listed_sizes is not None and pd.notna(listed_sizes):
            listed = [s.strip() for s in str(listed_sizes).split(",") if s.strip()]
            sizes[category] = listed + sorted(seen - set(listed) - {NO_SIZE}, key=_size_sort_key)
        else:
            sizes[category] = sorted(seen - {NO_SIZE}, key=_size_sort_key) or [NO_SIZE]
        if sizes[category] == [NO_SIZE]:
            core_sizes[category] = []
        elif listed_core is not None and pd.notna(listed_core):
            core_sizes[category] = [s.strip() for s in str(listed_core).split(",") if s.strip()]
        else:
            units = _sales_with_full_size_run(sales, stock, category, sizes[category])
            core = _core_by_sales({s: units.get(s, 0) for s in sizes[category]})
            core_sizes[category] = [s for s in sizes[category] if s in core]

    # --- Targets in units and rupees, from whatever was given ---------------------------------------
    weights = _learn_day_weights(sales, month_start, days_in_month, today_day, set(sale_days))
    group_of = category_line if target_level == "line" else category_department

    def numbers_of(column):
        """(per category, per floor or line, store total) from one targets column."""
        if targets is None or not _has(targets, column):
            return {}, {}, None
        values = _numbers(targets, column, "targets", check, allow_blank=True)
        if target_level == "category":
            return {cid: float(values[row.name]) for cid, row in target_row.items()
                    if pd.notna(values[row.name]) and values[row.name] > 0}, {}, None
        if target_level in LEVEL_NAMES:
            groups = {_normal_name(g): g for g in group_of.values()}
            by_group = {}
            for i, typed in _text(targets[target_level]).items():
                if pd.notna(values[i]) and values[i] > 0:
                    group = groups.get(_normal_name(typed))
                    if group is None:
                        check.warn(f"The target for {typed!r} was left out: no category is on that "
                                   f"{LEVEL_NAMES[target_level]}.")
                    else:
                        by_group[group] = by_group.get(group, 0.0) + float(values[i])
            return {}, by_group, None
        total = float(values.fillna(0).sum())
        return {}, {}, (total if total > 0 else None)

    has_value = bool(sales["value"].sum() > 0)
    prices = sales.groupby("category")[["units_sold", "value"]].sum()
    overall_price = (prices["value"].sum() / prices["units_sold"].sum()) if prices["units_sold"].sum() > 0 else 0
    price = {c: (prices.at[c, "value"] / prices.at[c, "units_sold"]
                 if c in prices.index and prices.at[c, "units_sold"] > 0 else overall_price)
             for c in categories}

    measures = ["units", "value"] if has_value else ["units"]
    resolved, source, notes, last_years = {}, {}, {}, {}
    for measure in measures:
        target_col, ly_col = measure_columns[measure]
        column = "units_sold" if measure == "units" else "value"
        given, by_group, store_total = numbers_of(target_col)
        shares, from_baseline = _early_share(sales, column, categories, today_day)
        resolved[measure], source[measure] = _stated_targets(
            given, by_group, store_total, target_level, group_of, shares)
        last_years[measure] = numbers_of(ly_col)[0] if target_level == "category" else {}
        label = "unit" if measure == "units" else "rupee"
        how = ("each category's share of sales before the last week" if from_baseline
               else "each category's share of sales so far this month")
        notes[measure] = []
        if by_group:
            notes[measure].append(f"Your {LEVEL_NAMES[target_level]} {label} targets were shared out "
                                  f"across their categories by {how}, so each category's target is "
                                  f"an estimate.")
        if store_total:
            notes[measure].append(f"Your store {label} target was shared out across categories by "
                                  f"{how}, so each category's target is an estimate.")
    check.stop_if_problems()

    # Only one measure given for a category: work out the other at this month's average price.
    if has_value:
        for measure, other in (("units", "value"), ("value", "units")):
            filled = [c for c in categories if c not in resolved[measure] and c in resolved[other]
                      and source[other][c] in GIVEN_SOURCES and price[c] > 0]
            for c in filled:
                amount = resolved[other][c]
                resolved[measure][c] = amount / price[c] if measure == "units" else amount * price[c]
                source[measure][c] = f"from {'rupee' if other == 'value' else 'unit'} target"
            if filled:
                label, other_label = ("unit", "rupee") if measure == "units" else ("rupee", "unit")
                notes[measure].append(f"No {label} target for {_who(len(filled), len(categories))}, so "
                                      f"{_plural(len(filled), 'it was', 'they were')} worked out from the "
                                      f"{other_label} target at this month's average price.")

    # Still nothing: last year, then the category's own earlier pace.
    n = len(categories)
    for measure in measures:
        column = "units_sold" if measure == "units" else "value"
        label = "unit" if measure == "units" else "rupee"
        counts = {"last year": [], "own pace": [], None: []}
        for c in categories:
            if c in resolved[measure]:
                continue
            if last_years[measure].get(c):
                resolved[measure][c], how = last_years[measure][c], "last year"
            else:
                resolved[measure][c] = _own_pace_target(sales, column, c, weights, today_day)
                how = "own pace" if resolved[measure][c] else None
            source[measure][c] = how
            counts[how].append(c)
        k = len(counts["last year"])
        if k:
            notes[measure].append(f"No {label} target for {_who(k, n)}, so "
                                  f"{'each is' if k == n else _plural(k, 'it is', 'they are')} judged "
                                  f"against last year's sales for the month.")
        k = len(counts["own pace"])
        if k:
            notes[measure].append(f"No {label} target for {_who(k, n)}, so "
                                  f"{'each is' if k == n else _plural(k, 'it is', 'each is')} compared "
                                  f"with its own pace earlier in the month: behind means its sales "
                                  f"have slowed down lately.")
        k = len(counts[None])
        if k:
            notes[measure].append(f"No {label} target for {_who(k, n)}, and fewer than 12 days of "
                                  f"sales to compare with, so {_plural(k, 'it', 'they')} can't be "
                                  f"judged in {'units' if measure == 'units' else 'rupees'} yet.")

    # Add each row's line and floor, as the Sample Store's data has them.
    for df in (sales, stock):
        if df is not None:
            df["line"] = df["category"].map(category_line)
            df["department"] = df["category"].map(category_department)

    fingerprint = hashlib.sha256(
        (str(pd.util.hash_pandas_object(sales, index=False).sum())
         + repr(sorted((m, c, round(v or 0, 2)) for m in resolved for c, v in resolved[m].items()))
         ).encode()).hexdigest()[:16]
    return Store(
        id=store_id or f"store-{fingerprint}",
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
        category_product=category_product,
        category_department=category_department,
        lines=lines,
        targets={c: round(resolved["units"].get(c) or 0) or None for c in categories},
        value_targets=({c: round(resolved["value"].get(c) or 0) or None for c in categories}
                       if has_value else {}),
        target_notes=notes,
        target_source=source,
        last_year=last_years.get("units", {}),
        sizes=sizes,
        core_sizes=core_sizes,
        day_weights=weights,
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
