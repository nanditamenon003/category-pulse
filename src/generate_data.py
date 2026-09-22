"""
Simulated data generation for Category Pulse.

Phase 1 builds `generate_sales()`, which writes data/sales.csv: one row per
category, per hour, per size, for a single simulated store day. Later phases
(3 and 6b) add footfall, stock, and historical data to this same file.

Everything here is simulated. There is no live POS integration. A fixed
random seed (config.RANDOM_SEED) makes every run produce identical numbers,
which matters because the four demo scenarios must be reliably reproducible.
"""

import os

import numpy as np
import pandas as pd

from config import (
    AVG_UPT,
    BASELINE_CONVERSION_RATE,
    CATEGORIES,
    CATEGORY_TARGETS,
    CHINOS_DEPLETION_HOUR,
    CHINOS_SPILLOVER_RATE,
    CORE_SIZES,
    HOURLY_SHAPE,
    KIDSWEAR_OVERPERFORM_MULTIPLIER,
    RANDOM_SEED,
    SIMULATED_DATE,
    SIZES,
    SIZE_WEIGHTS,
    STARTING_STOCK,
    STORE_HOURS,
    WOMENSWEAR_STOCKOUT_FLATLINE_HOUR,
    WOMENSWEAR_STOCKOUT_FLATLINE_MULTIPLIER,
    WOMENSWEAR_STOCKOUT_PARTIAL_HOUR,
    WOMENSWEAR_STOCKOUT_PARTIAL_MULTIPLIER,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _hour_total_units(rng, category, hour):
    """
    Expected total units sold for one category in one hour, before splitting
    across sizes. Starts from the category's daily target times the normal
    intra-day shape, then applies a scenario-specific multiplier so the four
    deliberate test scenarios (section 4 of the spec) show up in the data.
    Poisson noise keeps the numbers integer and realistically "bumpy"
    instead of perfectly smooth.
    """
    base_expected = CATEGORY_TARGETS[category] * HOURLY_SHAPE[hour]

    multiplier = 1.0
    if category == "Kidswear":
        # Overperformance scenario: ahead of pace all day.
        multiplier = KIDSWEAR_OVERPERFORM_MULTIPLIER
    elif category == "Womenswear":
        # Stockout scenario: normal until ~13:30, then flatlines.
        if hour == WOMENSWEAR_STOCKOUT_PARTIAL_HOUR:
            multiplier = WOMENSWEAR_STOCKOUT_PARTIAL_MULTIPLIER
        elif hour >= WOMENSWEAR_STOCKOUT_FLATLINE_HOUR:
            multiplier = WOMENSWEAR_STOCKOUT_FLATLINE_MULTIPLIER

    expected = base_expected * multiplier
    return int(rng.poisson(max(expected, 0.01)))


def _split_units_across_sizes(rng, category, hour, total_units):
    """
    Splits one hour's total units for a category across the six sizes.

    Normally this just follows SIZE_WEIGHTS (M and L sell fastest). The
    Chinos broken-size-run scenario is the exception: from
    CHINOS_DEPLETION_HOUR onward, M and L are out of stock. Most shoppers
    who wanted those sizes leave without buying rather than switching sizes
    (CHINOS_SPILLOVER_RATE controls the small fraction who do switch), so
    the realized total for the hour drops even though non-core sizes keep
    selling at their normal rate. This is what makes the category quietly
    fall behind pace while total remaining stock still looks fine.
    """
    if category == "Chinos" and hour >= CHINOS_DEPLETION_HOUR:
        core_weight = sum(SIZE_WEIGHTS[s] for s in CORE_SIZES)
        non_core_sizes = [s for s in SIZES if s not in CORE_SIZES]
        non_core_weight = sum(SIZE_WEIGHTS[s] for s in non_core_sizes)

        # Demand that was headed for M/L: most is lost, a small slice
        # spills over to other sizes.
        core_demand = total_units * core_weight
        spillover_units = core_demand * CHINOS_SPILLOVER_RATE

        realized_total = int(round(total_units * non_core_weight + spillover_units))
        realized_total = min(realized_total, total_units)

        probs = np.array([SIZE_WEIGHTS[s] / non_core_weight for s in non_core_sizes])
        counts = rng.multinomial(realized_total, probs) if realized_total > 0 else np.zeros(
            len(non_core_sizes), dtype=int
        )

        units_by_size = {s: 0 for s in SIZES}
        for s, c in zip(non_core_sizes, counts):
            units_by_size[s] = int(c)
        return units_by_size

    probs = np.array([SIZE_WEIGHTS[s] for s in SIZES])
    counts = rng.multinomial(total_units, probs) if total_units > 0 else np.zeros(
        len(SIZES), dtype=int
    )
    return {s: int(c) for s, c in zip(SIZES, counts)}


def _transactions_for_hour(rng, units_sold):
    """
    Simulates a transaction count from units sold using an average units-
    per-transaction (UPT). Transactions is a category-hour level figure
    (not per size), needed later for UPT and conversion-rate calculations.
    """
    if units_sold <= 0:
        return 0
    expected_transactions = units_sold / AVG_UPT
    transactions = int(rng.poisson(max(expected_transactions, 0.01)))
    # A transaction always contains at least 1 unit, so transactions can
    # never exceed units sold; and if anything sold, at least one
    # transaction happened.
    transactions = max(1, min(transactions, units_sold))
    return transactions


def generate_sales():
    """
    Generates one simulated store day of hourly, size-level unit sales for
    every category, and returns it as a DataFrame with columns:
    date, hour, category, size, units_sold, transactions.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    rows = []

    for category in CATEGORIES:
        for hour in STORE_HOURS:
            total_units = _hour_total_units(rng, category, hour)
            units_by_size = _split_units_across_sizes(rng, category, hour, total_units)
            actual_total = sum(units_by_size.values())
            transactions = _transactions_for_hour(rng, actual_total)

            for size in SIZES:
                rows.append(
                    {
                        "date": SIMULATED_DATE,
                        "hour": hour,
                        "category": category,
                        "size": size,
                        "units_sold": units_by_size[size],
                        # Same transactions figure repeated on every size
                        # row for this category-hour (denormalized on
                        # purpose, so a size-level row can still be summed
                        # or grouped without a separate lookup).
                        "transactions": transactions,
                    }
                )

    return pd.DataFrame(rows)


def generate_footfall():
    """
    Generates hourly visitor counts per category (data/footfall.csv).

    Footfall is sized from each category's *normal* demand (daily target x
    hourly shape) and a baseline conversion rate — deliberately ignoring the
    scenario multipliers applied to sales. Real customer interest in a
    section doesn't drop just because the shelf is empty, so this is what
    lets the agent later tell a traffic problem (low footfall) apart from a
    conversion problem (normal footfall, collapsed sales) — see Phase 6d.
    """
    rng = np.random.default_rng(RANDOM_SEED)
    rows = []

    for category in CATEGORIES:
        for hour in STORE_HOURS:
            normal_units = CATEGORY_TARGETS[category] * HOURLY_SHAPE[hour]
            normal_transactions = normal_units / AVG_UPT
            expected_visitors = normal_transactions / BASELINE_CONVERSION_RATE
            visitors = int(rng.poisson(max(expected_visitors, 0.01)))

            rows.append(
                {
                    "date": SIMULATED_DATE,
                    "hour": hour,
                    "category": category,
                    "visitors": visitors,
                }
            )

    return pd.DataFrame(rows)


def generate_stock(sales_df):
    """
    Generates hourly remaining-stock-by-size (data/stock.csv) by subtracting
    cumulative units sold from each category/size's starting stock
    (config.STARTING_STOCK). Remaining stock is floored at zero.

    Starting stock is deliberately tuned per category (see the comment on
    STARTING_STOCK in config.py) so remaining stock lands on the Womenswear
    stockout and Chinos broken-size-run scenarios at the right time of day.
    """
    cumulative = (
        sales_df.sort_values("hour")
        .groupby(["category", "size"])["units_sold"]
        .cumsum()
    )
    sales_with_cumulative = sales_df.assign(cumulative_sold=cumulative)

    rows = []
    for _, row in sales_with_cumulative.iterrows():
        starting = STARTING_STOCK[row["category"]][row["size"]]
        remaining = max(0, starting - row["cumulative_sold"])
        rows.append(
            {
                "date": row["date"],
                "hour": row["hour"],
                "category": row["category"],
                "size": row["size"],
                "units_remaining": int(remaining),
            }
        )

    return pd.DataFrame(rows)


def _print_sales_verification(df):
    """Prints an hours x categories pivot of total units sold, per spec."""
    pivot = df.pivot_table(
        index="hour", columns="category", values="units_sold", aggfunc="sum"
    )[CATEGORIES]
    print("\nUnits sold by hour x category:\n")
    print(pivot.to_string())

    print("\nDaily totals vs targets:\n")
    totals = pivot.sum()
    for category in CATEGORIES:
        target = CATEGORY_TARGETS[category]
        actual = totals[category]
        pct = (actual - target) / target * 100
        print(f"  {category:<14} actual={actual:>4}  target={target:>4}  ({pct:+.0f}%)")

    print(
        "\nCheck: Womenswear should visibly flatten after 14:00; "
        "Kidswear should be clearly high (ahead of target)."
    )


def _print_footfall_verification(footfall_df):
    """Prints Womenswear footfall before vs after its 13:30 stockout."""
    womenswear = footfall_df[footfall_df["category"] == "Womenswear"].set_index("hour")[
        "visitors"
    ]
    before = womenswear.loc[10:13].mean()
    after = womenswear.loc[14:19].mean()
    print("\nWomenswear footfall, before vs after the 13:30 stockout:\n")
    print(womenswear.to_string())
    print(f"\n  avg visitors 10:00-13:00 = {before:.1f}")
    print(f"  avg visitors 14:00-19:00 = {after:.1f}")
    print(
        "\nCheck: footfall should stay roughly similar before and after "
        "14:00, even though sales collapsed — traffic is fine, conversion "
        "is what broke."
    )


def _print_stock_verification(stock_df):
    """Prints remaining stock for the sizes that should hit zero on schedule."""
    print("\nWomenswear M/L remaining stock by hour:\n")
    ww = stock_df[
        (stock_df["category"] == "Womenswear") & (stock_df["size"].isin(["M", "L"]))
    ].pivot_table(index="hour", columns="size", values="units_remaining")
    print(ww.to_string())

    print("\nChinos M/L remaining stock by hour:\n")
    chinos = stock_df[
        (stock_df["category"] == "Chinos") & (stock_df["size"].isin(["M", "L"]))
    ].pivot_table(index="hour", columns="size", values="units_remaining")
    print(chinos.to_string())

    print(
        "\nCheck: Womenswear M/L should hit 0 around hour 13; "
        "Chinos M/L should hit 0 around the mid-afternoon depletion hour."
    )


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)

    sales_df = generate_sales()
    sales_path = os.path.join(DATA_DIR, "sales.csv")
    sales_df.to_csv(sales_path, index=False)
    print(f"Wrote {len(sales_df)} rows to {sales_path}")
    _print_sales_verification(sales_df)

    footfall_df = generate_footfall()
    footfall_path = os.path.join(DATA_DIR, "footfall.csv")
    footfall_df.to_csv(footfall_path, index=False)
    print(f"\nWrote {len(footfall_df)} rows to {footfall_path}")
    _print_footfall_verification(footfall_df)

    stock_df = generate_stock(sales_df)
    stock_path = os.path.join(DATA_DIR, "stock.csv")
    stock_df.to_csv(stock_path, index=False)
    print(f"\nWrote {len(stock_df)} rows to {stock_path}")
    _print_stock_verification(stock_df)
