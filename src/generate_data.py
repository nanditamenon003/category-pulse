"""
Simulated data generation for Category Pulse.

Simulates days 1..TODAY_DAY of a store month, hour by hour, and writes:
  - data/sales.csv     units sold, value and transactions, by category and size
  - data/stock.csv     units remaining on the shelf after each hour, by size
  - data/footfall.csv  visitors per floor zone per hour

Sales and stock are simulated together: shoppers arrive with demand for a
size, and a sale only happens if that size is on the shelf. That is why the
demo scenarios (a stockout, a broken size run) are not hard-coded here — they
emerge from the supply events configured in config.py (a missed delivery, a
short delivery), the same way they would in a real store.

Everything is simulated from a fixed random seed, so every run is identical.
"""

import math
import os

import numpy as np
import pandas as pd

from config import (
    AVG_PRICE,
    AVG_UPT,
    BASELINE_CONVERSION_RATE,
    CATEGORIES,
    CATEGORY_DEPARTMENT,
    CATEGORY_LINE,
    CATEGORY_PRODUCT,
    DAYS_IN_MONTH,
    DELIVERY_WEEKDAY,
    DEMAND_MULTIPLIER,
    DEPARTMENTS,
    LAST_YEAR_UNITS,
    MIN_PAR_PER_SIZE,
    MISSED_DELIVERIES,
    MONTHLY_TARGETS,
    PAR_WEEKS_OF_COVER,
    PAR_WEEKS_OVERRIDE,
    RANDOM_SEED,
    SALE_DAY,
    SALE_DAY_DISCOUNT,
    SHORT_DELIVERIES,
    SIZE_SWITCH_RATE,
    STORE_HOURS,
    TODAY_DAY,
    WEEKDAY_HOURLY_SHAPE,
    WEEKEND_HOURLY_SHAPE,
    day_weight,
    is_busy_day,
    month_calendar,
    size_system_for,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


# --- Demand model -------------------------------------------------------------

def hourly_shape(day_info):
    """Weekends and the sale day skew toward the afternoon and evening."""
    return WEEKEND_HOURLY_SHAPE if is_busy_day(day_info) else WEEKDAY_HOURLY_SHAPE


def expected_demand(category, day_info, hour, total_month_weight, include_scenarios=True):
    """
    Expected units shoppers want from one category in one hour. The monthly
    target is spread across the month in proportion to how busy each day is,
    then across the day by the hourly shape. The target is the store's plan,
    so a category performing to plan has demand that adds up to its target.
    """
    daily = MONTHLY_TARGETS[category] * day_weight(day_info) / total_month_weight
    demand = daily * hourly_shape(day_info)[hour]
    if include_scenarios:
        demand *= DEMAND_MULTIPLIER.get(category, 1.0)
    return demand


# --- Stock ------------------------------------------------------------------

def par_levels():
    """
    The stock level each size is topped up to on delivery day: enough for
    PAR_WEEKS_OF_COVER weeks of this category's actual expected sales (so
    replenishment keeps up with a fast seller), never below MIN_PAR_PER_SIZE.
    """
    levels = {}
    for category in CATEGORIES:
        weekly_units = (
            MONTHLY_TARGETS[category] * DEMAND_MULTIPLIER.get(category, 1.0) * 7 / DAYS_IN_MONTH
        )
        weeks = PAR_WEEKS_OVERRIDE.get(category, PAR_WEEKS_OF_COVER)
        system = size_system_for(category)
        levels[category] = {
            size: max(MIN_PAR_PER_SIZE, math.ceil(weekly_units * weeks * weight))
            for size, weight in zip(system["sizes"], system["weights"])
        }
    return levels


def deliver(stock, par, day):
    """
    Weekly delivery: top every size back up to par. Scenario gaps: a missed
    delivery ships nothing; a short delivery can't ship some sizes, and the
    warehouse substitutes the same number of units spread across the sizes it
    does have (in proportion to their normal sales mix).
    """
    for category in CATEGORIES:
        if day in MISSED_DELIVERIES.get(category, []):
            continue
        missing_sizes = SHORT_DELIVERIES.get(category, {}).get(day, [])
        top_up = {
            size: max(0, level - stock[category][size])
            for size, level in par[category].items()
        }

        substitute_units = sum(top_up[s] for s in missing_sizes)
        if substitute_units:
            system = size_system_for(category)
            available = {
                s: w for s, w in zip(system["sizes"], system["weights"]) if s not in missing_sizes
            }
            total_weight = sum(available.values())
            for size in missing_sizes:
                top_up[size] = 0
            for size, weight in available.items():
                top_up[size] += round(substitute_units * weight / total_weight)

        for size, units in top_up.items():
            stock[category][size] += units


# --- Selling ------------------------------------------------------------------

def sell_one_hour(rng, category, demand_units, stock):
    """
    Turns an hour's demand into actual sales, limited by what's on the shelf.
    Each shopper wants a particular size. If it's out of stock, most walk
    away; a small share (SIZE_SWITCH_RATE) take another size that is in stock.
    Returns units sold per size and updates `stock` in place.
    """
    system = size_system_for(category)
    sizes, weights = system["sizes"], system["weights"]
    wanted = rng.multinomial(demand_units, weights) if demand_units else [0] * len(sizes)

    sold = {size: 0 for size in sizes}
    unmet = 0
    for size, want in zip(sizes, wanted):
        units = min(int(want), stock[category][size])
        sold[size] += units
        stock[category][size] -= units
        unmet += int(want) - units

    switchers = int(rng.binomial(unmet, SIZE_SWITCH_RATE)) if unmet else 0
    for _ in range(switchers):
        available = [(s, w) for s, w in zip(sizes, weights) if stock[category][s] > 0]
        if not available:
            break
        names = [s for s, _ in available]
        probs = np.array([w for _, w in available])
        choice = rng.choice(names, p=probs / probs.sum())
        sold[choice] += 1
        stock[category][choice] -= 1

    return sold


def transactions_for(rng, units_sold):
    """A transaction count from units sold, using the average units per transaction."""
    if units_sold <= 0:
        return 0
    transactions = int(rng.poisson(units_sold / AVG_UPT))
    # Every transaction has at least one unit, and if anything sold, at least
    # one transaction happened.
    return max(1, min(transactions, units_sold))


def simulate_store():
    """
    Simulates days 1..TODAY_DAY hour by hour. Returns (sales_df, stock_df).
    Stock rows record what is left on the shelf at the end of each hour.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    calendar = month_calendar()
    total_month_weight = sum(day_weight(d) for d in calendar)

    par = par_levels()
    stock = {category: dict(levels) for category, levels in par.items()}

    sales_rows, stock_rows = [], []
    for day_info in calendar[:TODAY_DAY]:
        day = day_info["day"]
        if day_info["weekday"] == DELIVERY_WEEKDAY:
            deliver(stock, par, day)

        price_factor = (1 - SALE_DAY_DISCOUNT) if day == SALE_DAY else 1.0

        for hour in STORE_HOURS:
            for category in CATEGORIES:
                demand = int(rng.poisson(
                    expected_demand(category, day_info, hour, total_month_weight)
                ))
                sold = sell_one_hour(rng, category, demand, stock)
                units = sum(sold.values())
                transactions = transactions_for(rng, units)
                price = AVG_PRICE[category] * price_factor

                common = {
                    "date": day_info["date"],
                    "day": day,
                    "hour": hour,
                    "department": CATEGORY_DEPARTMENT[category],
                    "line": CATEGORY_LINE[category],
                    "category": category,
                }
                for size, size_units in sold.items():
                    sales_rows.append({
                        **common,
                        "product_type": CATEGORY_PRODUCT[category],
                        "size": size,
                        "units_sold": size_units,
                        "value": round(size_units * price, 2),
                        # Category-hour total, repeated on each size row so a
                        # size-level row still carries it (take it once per
                        # category-hour when aggregating).
                        "transactions": transactions,
                    })
                    stock_rows.append({
                        **common,
                        "size": size,
                        "units_remaining": stock[category][size],
                    })

    return pd.DataFrame(sales_rows), pd.DataFrame(stock_rows)


def generate_footfall():
    """
    Visitors per floor zone per hour, sized from each zone's *normal* demand
    (no scenario effects) and a baseline conversion rate. Uses its own random
    stream so it never shifts the sales simulation.
    """
    rng = np.random.default_rng(RANDOM_SEED + 1)
    calendar = month_calendar()
    total_month_weight = sum(day_weight(d) for d in calendar)

    rows = []
    for day_info in calendar[:TODAY_DAY]:
        for hour in STORE_HOURS:
            for zone in DEPARTMENTS:
                normal_units = sum(
                    expected_demand(c, day_info, hour, total_month_weight, include_scenarios=False)
                    for c in CATEGORIES
                    if CATEGORY_DEPARTMENT[c] == zone
                )
                expected_visitors = normal_units / AVG_UPT / BASELINE_CONVERSION_RATE
                rows.append({
                    "date": day_info["date"],
                    "day": day_info["day"],
                    "hour": hour,
                    "zone": zone,
                    "visitors": int(rng.poisson(expected_visitors)),
                })
    return pd.DataFrame(rows)


# --- Verification output ------------------------------------------------------

def _print_target_sheet(sales_df):
    """The store's familiar month-to-date sheet: LY / Target / Sold / Balance."""
    sold = sales_df.groupby("category")["units_sold"].sum()
    print(f"\nMonth-to-date at close of day {TODAY_DAY} (the store's usual target sheet):\n")
    print(f"{'Line':<7}{'Category':<18}{'LY':>6}{'Target':>8}{'Sold':>7}{'Balance':>9}")
    current_line = None
    for category in CATEGORIES:
        line = CATEGORY_LINE[category]
        if line != current_line:
            print("-" * 55)
            current_line = line
        target = MONTHLY_TARGETS[category]
        s = int(sold.get(category, 0))
        print(
            f"{line:<7}{CATEGORY_PRODUCT[category]:<18}{LAST_YEAR_UNITS[category]:>6}"
            f"{target:>8}{s:>7}{s - target:>9}"
        )
    print("-" * 55)
    print(f"{'Store total':<25}{sum(MONTHLY_TARGETS.values()):>14}{int(sold.sum()):>7}")


def _print_daily_rhythm(sales_df):
    calendar = {d["day"]: d for d in month_calendar()}
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    daily = sales_df.groupby("day")["units_sold"].sum()
    print("\nStore units per day (weekends and the sale day should stand out):\n")
    for day, units in daily.items():
        tag = "  <- sale day" if day == SALE_DAY else ""
        print(f"  day {day:>2} {names[calendar[day]['weekday']]}  {units:>4}{tag}")


def _print_scenario(sales_df, stock_df, footfall_df, category, label):
    core = size_system_for(category)["core"]
    s = sales_df[sales_df["category"] == category]
    st = stock_df[(stock_df["category"] == category) & (stock_df["hour"] == STORE_HOURS[-1])]
    daily_sold = s.groupby("day")["units_sold"].sum()
    total_left = st.groupby("day")["units_remaining"].sum()
    core_left = st[st["size"].isin(core)].groupby("day")["units_remaining"].sum()
    zone = footfall_df[footfall_df["zone"] == CATEGORY_DEPARTMENT[category]]
    zone_visitors = zone.groupby("day")["visitors"].sum()

    print(f"\n{label}: {category} (core sizes {', '.join(core)}), days 14-{TODAY_DAY}\n")
    print(f"  {'day':>4}{'sold':>6}{'stock left':>12}{'core left':>11}{'zone visitors':>15}")
    for day in range(14, TODAY_DAY + 1):
        print(
            f"  {day:>4}{int(daily_sold.get(day, 0)):>6}{int(total_left.get(day, 0)):>12}"
            f"{int(core_left.get(day, 0)):>11}{int(zone_visitors.get(day, 0)):>15}"
        )


def _print_unplanned_stockouts(stock_df):
    """Sizes that hit zero in categories with no deliberate supply scenario."""
    scenario = set(MISSED_DELIVERIES) | set(SHORT_DELIVERIES)
    zero = stock_df[(stock_df["units_remaining"] == 0) & ~stock_df["category"].isin(scenario)]
    print("\nUnplanned stockouts (non-scenario categories, size-hours at zero):")
    if zero.empty:
        print("  none")
        return
    summary = zero.groupby(["category", "size"])["day"].agg(["min", "count"])
    for (category, size), row in summary.iterrows():
        print(f"  {category} size {size}: first on day {row['min']}, {row['count']} hours at zero")


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)

    sales_df, stock_df = simulate_store()
    footfall_df = generate_footfall()

    for name, df in (("sales", sales_df), ("stock", stock_df), ("footfall", footfall_df)):
        path = os.path.join(DATA_DIR, f"{name}.csv")
        df.to_csv(path, index=False)
        print(f"Wrote {len(df):>6} rows to data/{name}.csv")

    _print_target_sheet(sales_df)
    _print_daily_rhythm(sales_df)
    _print_scenario(sales_df, stock_df, footfall_df, "Womens Knit Top", "Stockout scenario")
    _print_scenario(sales_df, stock_df, footfall_df, "THM Non Denim Bottom", "Broken size run scenario")
    _print_unplanned_stockouts(stock_df)
