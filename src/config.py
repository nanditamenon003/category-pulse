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

# Business rule: don't judge a category until enough units were expected to
# make the percentage meaningful. With only a handful of units expected,
# ordinary hour-to-hour randomness produces huge % swings (e.g. 0 sold vs 1.8
# expected reads as -100%), which would flash false "behind" alarms every
# morning. Below this many expected units, status is "too_early" instead.
MIN_EXPECTED_UNITS_FOR_STATUS = 5

# --- Starting stock (used in Phase 3 / 6a / 6e) ------------------------------
# Morning starting units per category per size, before any sales are
# subtracted. Jeans, Formal Wear and Kidswear get generous stock, comfortably
# above what they sell in a day, so nothing there runs low by accident.
#
# Womenswear and Chinos are deliberately tuned tight, against the actual
# per-size cumulative sales the fixed seed produces, so that remaining stock
# lands on the exact scenario from section 4 of the spec:
#   - Womenswear: M and L (its best-selling sizes) hit zero right around the
#     13:30 stockout, matching the sales data flattening from 14:00.
#   - Chinos: M and L run out mid-afternoon (around CHINOS_DEPLETION_HOUR)
#     while the other sizes keep a healthy buffer — so total remaining
#     stock still looks fine even though the category can't sell to most
#     shoppers. This is what makes it a "broken size run" and not a plain
#     stockout.
# If RANDOM_SEED or the sales generation logic changes, these numbers would
# need to be recalibrated against the new sales data to keep the scenarios
# landing on schedule.
DEFAULT_STARTING_STOCK = {"XS": 15, "S": 20, "M": 25, "L": 22, "XL": 15, "XXL": 10}

STARTING_STOCK = {
    "Jeans": dict(DEFAULT_STARTING_STOCK),
    "Formal Wear": {"XS": 10, "S": 12, "M": 18, "L": 15, "XL": 10, "XXL": 8},
    "Kidswear": dict(DEFAULT_STARTING_STOCK),
    "Womenswear": {"XS": 3, "S": 1, "M": 5, "L": 7, "XL": 1, "XXL": 2},
    "Chinos": {"XS": 15, "S": 15, "M": 5, "L": 6, "XL": 20, "XXL": 10},
}

# A size is a "last piece" alert the moment it drops to exactly this many
# units remaining (Phase 6a).
LAST_PIECE_THRESHOLD = 1

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

# --- Footfall / conversion (Phase 3, 6d) -------------------------------------
# Baseline conversion rate (transactions / visitors) used to size simulated
# footfall from a category's normal, non-scenario demand (target x hourly
# shape). Footfall deliberately ignores the scenario multipliers above (the
# Womenswear stockout, Chinos depletion) because footfall measures customer
# *interest*, not whether they could buy. That is exactly what makes a
# stockout diagnosable: footfall stays normal while conversion collapses.
BASELINE_CONVERSION_RATE = 0.30

# --- AI agent (Phase 4) -------------------------------------------------------
# Model name kept in one place so it can be swapped without touching agent
# code. Use a current Claude Sonnet model.
CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_MAX_TOKENS = 1024

# Safety cap on how many rounds of tool calls the agent can make while
# answering a single question, so a confused loop can't run forever.
MAX_TOOL_ITERATIONS = 6

# The "current simulated hour" the agent and app treat as "right now" when a
# tool doesn't get an explicit hour. 16:00 is mid-afternoon: late enough
# that the Womenswear stockout and Chinos broken size run have both fully
# played out, but early enough that there's still time in the day to act on
# a recommendation. The Phase 7 Streamlit slider overrides this per session.
DEFAULT_CURRENT_HOUR = 16
