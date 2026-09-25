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
from dataclasses import dataclass
from datetime import date, timedelta

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
    """A store's tables can't be used as they are; the message says what to fix."""


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


def _require_columns(df, table, columns):
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise StoreDataError(f"The {table} table is missing: {', '.join(missing)}.")


def _text(series):
    return series.astype(str).str.strip()


def _numbers(df, column, table, allow_blank=False):
    values = pd.to_numeric(df[column], errors="coerce")
    bad = values.isna() & (df[column].notna() if allow_blank else True)
    if bad.any():
        example = df.loc[bad, column].iloc[0]
        raise StoreDataError(f"The {table} table has a {column} that isn't a number: {example!r}.")
    if (values < 0).any():
        raise StoreDataError(f"The {table} table has a negative {column}.")
    return values


def _has(df, column):
    return column in df.columns and df[column].notna().any()


def _days(df, table, month_start, days_in_month):
    dates = pd.to_datetime(df["date"], errors="coerce", dayfirst=True)
    if dates.isna().any():
        example = df.loc[dates.isna(), "date"].iloc[0]
        raise StoreDataError(f"The {table} table has a date that can't be read: {example!r}.")
    outside = (dates.dt.year != month_start.year) | (dates.dt.month != month_start.month)
    if outside.any():
        raise StoreDataError(f"The {table} table has dates outside "
                             f"{calendar.month_name[month_start.month]} {month_start.year}. "
                             f"Use one month at a time.")
    return dates.dt.day.astype(int)


def _hours(df, table, fallback):
    """Whole-hour slots (10 = 10:00-11:00); rows without hours count as the close of the day."""
    if not _has(df, "hour"):
        return pd.Series(fallback, index=df.index), False
    if df["hour"].isna().any():
        raise StoreDataError(f"Some rows in the {table} table have an hour and some don't.")
    hours = _numbers(df, "hour", table)
    if ((hours % 1 != 0) | (hours > 23)).any():
        raise StoreDataError(f"The {table} table's hours must be whole hours from 0 to 23.")
    return hours.astype(int), True


def _category_ids(df, table, known):
    ids = _text(df["line"]) + " " + _text(df["product"])
    unknown = sorted(set(ids) - set(known))
    if unknown:
        raise StoreDataError(f"The {table} table has categories with no target: "
                             f"{', '.join(unknown[:5])}{' ...' if len(unknown) > 5 else ''}.")
    return ids


def build_store(targets, sales, stock=None, footfall=None, loyalty=None, *, name="Your store",
                store_id=None, sale_days=(), delivery_weekday=None):
    """
    Builds a Store from any store's tables (columns listed above). Raises
    StoreDataError with a plain explanation if something can't be used.

    The month and "today" come from the sales dates: today is the latest day
    with sales, and the store is read as at the close of that day.
    """
    # Structure and targets
    _require_columns(targets, "targets", ["line", "product", "target"])
    targets = targets.dropna(subset=["line", "product"]).copy()
    targets["line"], targets["product"] = _text(targets["line"]), _text(targets["product"])
    targets["category"] = targets["line"] + " " + targets["product"]
    duplicated = targets.loc[targets["category"].duplicated(), "category"].tolist()
    if duplicated:
        raise StoreDataError(f"The targets table lists a category twice: {duplicated[0]}.")
    targets["target"] = _numbers(targets, "target", "targets")
    if (targets["target"] <= 0).any():
        raise StoreDataError("Every target must be more than zero.")
    department = (_text(targets["department"]) if _has(targets, "department")
                  else pd.Series(WHOLE_STORE, index=targets.index))
    categories = targets["category"].tolist()
    category_line = dict(zip(categories, targets["line"]))
    category_department = dict(zip(categories, department))
    lines = {}
    for category in categories:
        line, dept = category_line[category], category_department[category]
        if lines.setdefault(line, dept) != dept:
            raise StoreDataError(f"Line {line} is on more than one floor ({lines[line]} and {dept}).")

    # Sales: fix the month, "today" and the hours
    _require_columns(sales, "sales", ["date", "line", "product", "units"])
    sales = sales.dropna(subset=["date"]).copy()
    if sales.empty:
        raise StoreDataError("The sales table has no rows.")
    first = pd.to_datetime(sales["date"], errors="coerce", dayfirst=True).min()
    if pd.isna(first):
        raise StoreDataError("The sales table's dates can't be read.")
    month_start = date(first.year, first.month, 1)
    days_in_month = calendar.monthrange(first.year, first.month)[1]

    sales["day"] = _days(sales, "sales", month_start, days_in_month)
    sales["hour"], hourly = _hours(sales, "sales", config.STORE_HOURS[-1])
    hours = (list(range(int(sales["hour"].min()), int(sales["hour"].max()) + 1)) if hourly
             else list(config.STORE_HOURS))
    sales["category"] = _category_ids(sales, "sales", categories)
    sales["units_sold"] = _numbers(sales, "units", "sales")
    sales["value"] = _numbers(sales, "value", "sales").fillna(0) if _has(sales, "value") else 0.0
    sales["size"] = _text(sales["size"]) if _has(sales, "size") else NO_SIZE
    today_day = int(sales["day"].max())

    transactions = None
    if _has(sales, "transactions"):
        sales["transactions"] = _numbers(sales, "transactions", "sales", allow_blank=True).fillna(0)
        transactions = sales.groupby(["day", "hour", "category"], as_index=False)["transactions"].sum()

    keys = ["day", "hour", "category", "size"]
    sales = sales.groupby(keys, as_index=False)[["units_sold", "value"]].sum()

    # Stock (optional)
    if stock is not None:
        _require_columns(stock, "stock", ["date", "line", "product", "units"])
        stock = stock.dropna(subset=["date"]).copy()
        stock["day"] = _days(stock, "stock", month_start, days_in_month)
        if (stock["day"] > today_day).any():
            raise StoreDataError(f"The stock table has counts after the last day of sales (day "
                                 f"{today_day}). Date the latest count on the last day of sales.")
        stock["hour"], _ = _hours(stock, "stock", hours[-1])
        stock["category"] = _category_ids(stock, "stock", categories)
        stock["units_remaining"] = _numbers(stock, "units", "stock")
        stock["size"] = _text(stock["size"]) if _has(stock, "size") else NO_SIZE
        if (stock["size"] == NO_SIZE).all() != (sales["size"] == NO_SIZE).all():
            raise StoreDataError("Sales and stock must both have sizes, or both leave them out.")
        stock = stock.groupby(keys, as_index=False)["units_remaining"].sum()

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

    # Visitor counts (optional)
    if footfall is not None:
        _require_columns(footfall, "footfall", ["date", "visitors"])
        footfall = footfall.dropna(subset=["date"]).copy()
        footfall["day"] = _days(footfall, "footfall", month_start, days_in_month)
        footfall["hour"], _ = _hours(footfall, "footfall", hours[-1])
        footfall["zone"] = (_text(footfall["department"]) if _has(footfall, "department")
                            else WHOLE_STORE)
        unknown = sorted(set(footfall["zone"]) - set(lines.values()))
        if unknown:
            raise StoreDataError(f"The footfall table has floors that no line is on: {', '.join(unknown)}.")
        footfall["visitors"] = _numbers(footfall, "visitors", "footfall")
        footfall = footfall.groupby(["day", "hour", "zone"], as_index=False)["visitors"].sum()

    # Loyalty tiers (optional)
    if loyalty is not None:
        _require_columns(loyalty, "loyalty", ["tier", "line", "product", "share_of_transactions",
                                              "avg_basket_value", "avg_upt",
                                              "cross_sell_response_rate", "preferred_offer_type"])
        loyalty = loyalty.dropna(subset=["tier"]).copy()
        loyalty["category"] = _category_ids(loyalty, "loyalty", categories)
        for column in ("share_of_transactions", "avg_basket_value", "avg_upt", "cross_sell_response_rate"):
            loyalty[column] = _numbers(loyalty, column, "loyalty")

    # Add each row's line and floor, as the demo data has them.
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
        last_year=(dict(zip(categories, _numbers(targets, "last_year", "targets", allow_blank=True)))
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
