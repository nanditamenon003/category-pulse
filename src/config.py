"""
All tunable business rules live here, in one place, so they are easy to
find and change without hunting through the rest of the codebase.
"""

# --- Store hours -----------------------------------------------------------
# The store is open 10:00 to 20:00 (a 10-hour selling day). STORE_HOURS is
# the list of hours we generate/report data for.
STORE_OPEN_HOUR = 10
STORE_CLOSE_HOUR = 20  # exclusive of the closing hour itself as a "sales hour"
STORE_HOURS = list(range(STORE_OPEN_HOUR, STORE_CLOSE_HOUR))  # 10..19
TOTAL_STORE_HOURS = len(STORE_HOURS)  # 10

# --- Categories and daily unit targets --------------------------------------
# Each category carries its own daily unit sales target. Chosen to be
# realistic for a single mid-size clothing store (20-60 units/day range).
CATEGORY_TARGETS = {
    "Jeans": 55,
    "Chinos": 40,
    "Formal Wear": 25,
    "Womenswear": 60,
    "Kidswear": 35,
}

CATEGORIES = list(CATEGORY_TARGETS.keys())

# --- Sizes -------------------------------------------------------------------
# Core sizes are the ones that sell fastest and matter most for the
# broken-size-run logic (Phase 6e): if M/L run out, the category can't sell
# even if total stock still looks fine.
SIZES = ["XS", "S", "M", "L", "XL", "XXL"]
CORE_SIZES = ["M", "L"]

# Relative weight of each size in a typical sales mix. M and L are weighted
# highest because they are the most commonly stocked/purchased sizes.
SIZE_WEIGHTS = {
    "XS": 0.08,
    "S": 0.17,
    "M": 0.30,
    "L": 0.27,
    "XL": 0.13,
    "XXL": 0.05,
}

# --- Reproducibility ---------------------------------------------------------
# Fixed random seed so the simulated data is identical every time it is
# regenerated. A reproducible demo is essential for this project.
RANDOM_SEED = 42

# --- Pace engine business rule (used in Phase 2) -----------------------------
# A category is flagged "behind" once it is more than 15% under its expected
# pace, and "ahead" once it is more than 15% over. This threshold is a
# business rule, not a technical constant: 15% was chosen because a category
# that is more than 15% behind by mid-afternoon is unlikely to recover
# without an intervention (restock, cross-sell push, staffing change) before
# close. It is tunable here if the business wants a stricter or looser bar.
PACE_THRESHOLD_PCT = 15

# --- Starting stock (used in Phase 3 / 6a / 6e) ------------------------------
# Total starting units per category per size, before any sales are
# subtracted. Chosen high enough that a normal day doesn't sell out, so any
# stockout in the data is a deliberate scenario, not an accident.
STARTING_STOCK_PER_SIZE = {
    "XS": 12,
    "S": 18,
    "M": 22,
    "L": 20,
    "XL": 12,
    "XXL": 8,
}

# --- Scenario store day ------------------------------------------------------
# The single simulated "today" that all Phase 1-5 data is generated for.
SIMULATED_DATE = "2026-09-22"

# --- Intra-day sales shape ----------------------------------------------------
# Relative share of a category's daily units expected in each store hour, on
# a normal day: slower morning, a midday lift, a small afternoon dip, and an
# evening peak. Must sum to 1.0 across the 10 store hours.
HOURLY_SHAPE = {
    10: 0.05,
    11: 0.07,
    12: 0.11,
    13: 0.12,
    14: 0.10,
    15: 0.08,
    16: 0.09,
    17: 0.12,
    18: 0.14,
    19: 0.12,
}

# --- Deliberate test scenarios (Section 4 of the spec) -----------------------
# These knobs control the four reproducible scenarios the whole demo relies
# on. Kept here, not buried in generate_data.py, so they are easy to find
# and retune without touching generation logic.

# Kidswear runs ahead of target pace all day.
KIDSWEAR_OVERPERFORM_MULTIPLIER = 1.4

# Womenswear stockout: sells normally, then a hard stockout hits around
# 13:30. Hour 13 (the 13:00-14:00 slot) is only half-normal because the
# stockout happens mid-hour; every hour from 14:00 onward is a near-total
# flatline (a trickle of stray sales, not a hard zero, since real stores
# occasionally sell a returned or misplaced item).
WOMENSWEAR_STOCKOUT_PARTIAL_HOUR = 13
WOMENSWEAR_STOCKOUT_PARTIAL_MULTIPLIER = 0.5
WOMENSWEAR_STOCKOUT_FLATLINE_HOUR = 14
WOMENSWEAR_STOCKOUT_FLATLINE_MULTIPLIER = 0.05

# Chinos broken size run: from mid-afternoon, the core sizes (M, L) are
# depleted. Most shoppers who wanted M/L will not buy a different size, so
# their demand is lost rather than shifted; CHINOS_SPILLOVER_RATE is the
# small fraction who do accept another size. This is what makes total units
# sold quietly fall behind pace even though non-core sizes keep selling
# normally and total remaining stock still looks adequate.
CHINOS_DEPLETION_HOUR = 15
CHINOS_SPILLOVER_RATE = 0.15

# --- Units per transaction (UPT) ---------------------------------------------
# Average units per transaction, used to simulate a transactions count from
# units sold. Real UPT for apparel stores is typically in this range.
AVG_UPT = 1.5
