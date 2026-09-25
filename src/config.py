"""
All tunable business rules live here, in one place, so they are easy to
find and change without hunting through the rest of the codebase.

The store model mirrors how a real Tommy Hilfiger store in India tracks its
business: monthly unit targets per line and category, reviewed month-to-date.
All numbers are simulated and illustrative — no real store figures are used.
"""

import math
from datetime import date, timedelta

# --- Store hours --------------------------------------------------------------
# The store is open 10:00 to 20:00 (a 10-hour selling day). STORE_HOURS is the
# list of hourly selling slots (10 = the 10:00-11:00 slot, ... 19 = 19:00-20:00).
STORE_OPEN_HOUR = 10
STORE_CLOSE_HOUR = 20
STORE_HOURS = list(range(STORE_OPEN_HOUR, STORE_CLOSE_HOUR))
TOTAL_STORE_HOURS = len(STORE_HOURS)

# --- The simulated month ----------------------------------------------------------
# Targets are monthly, and the store checks them month-to-date. "Today" is day
# 24: late enough that problems are real, early enough (7 days left) to act.
MONTH_START = "2026-05-01"
DAYS_IN_MONTH = 31
TODAY_DAY = 24

# Relative busyness of each weekday (Monday=0 ... Sunday=6). Weekends sell more.
WEEKDAY_WEIGHTS = {0: 0.85, 1: 0.85, 2: 0.85, 3: 0.90, 4: 1.05, 5: 1.35, 6: 1.40}

# One in-store sale event with clearly higher traffic, placed on a weekday so
# its effect isn't confused with the normal weekend lift.
SALE_DAY = 13
SALE_DAY_MULTIPLIER = 1.8
SALE_DAY_DISCOUNT = 0.20

# Share of a day's units sold in each hour: slow morning, midday lift, evening
# peak. Weekends and the sale day skew later (more afternoon/evening shoppers).
# Each must sum to 1.0.
WEEKDAY_HOURLY_SHAPE = {
    10: 0.05, 11: 0.07, 12: 0.11, 13: 0.12, 14: 0.10,
    15: 0.08, 16: 0.09, 17: 0.12, 18: 0.14, 19: 0.12,
}
WEEKEND_HOURLY_SHAPE = {
    10: 0.04, 11: 0.06, 12: 0.09, 13: 0.11, 14: 0.11,
    15: 0.10, 16: 0.11, 17: 0.13, 18: 0.14, 19: 0.11,
}


def month_calendar():
    """One entry per day of the month: day number, date string, weekday (Mon=0)."""
    start = date.fromisoformat(MONTH_START)
    days = []
    for i in range(DAYS_IN_MONTH):
        d = start + timedelta(days=i)
        days.append({"day": i + 1, "date": d.isoformat(), "weekday": d.weekday()})
    return days


def is_busy_day(day_info):
    """Weekends and the sale day trade differently (busier, later in the day)."""
    return day_info["weekday"] >= 5 or day_info["day"] == SALE_DAY


def day_weight(day_info):
    """How busy a day is relative to others (weekends and the sale day sell more)."""
    weight = WEEKDAY_WEIGHTS[day_info["weekday"]]
    if day_info["day"] == SALE_DAY:
        weight *= SALE_DAY_MULTIPLIER
    return weight

# --- Store structure: department -> line -> category --------------------------------
# Mirrors the store's own target sheet. Kidswear is split into Big Boys (BB),
# Big Girls (BG), Little Boys (LB) and Little Girls (LG).
LINES = {
    "THM": "Menswear",      # Tommy Hilfiger Menswear
    "THT": "Menswear",      # Tommy Hilfiger Tailored
    "TJM": "Menswear",      # Tommy Jeans Men
    "Womens": "Womenswear",
    "BB": "Kidswear",
    "BG": "Kidswear",
    "LB": "Kidswear",
    "LG": "Kidswear",
}
DEPARTMENTS = ["Menswear", "Womenswear", "Kidswear"]

# (line, product type, monthly unit target, last year's units, avg selling price in INR)
# Illustrative, disguised figures: realistic proportions, not real store data.
# Targets are set from last year plus growth; very small categories get a
# minimum "floor" target (e.g. Womens Non Denim), which is why a few targets
# sit far above last year.
CATEGORY_PLAN = [
    ("THM", "Polo", 810, 745, 4700),
    ("THM", "T-shirt", 95, 84, 3200),
    ("THM", "Non Denim Bottom", 135, 120, 5900),
    ("THM", "Woven Top", 425, 392, 5300),
    ("THT", "Non Denim Bottom", 65, 57, 6700),
    ("THT", "Blazer", 10, 6, 14500),
    ("THT", "Woven Top", 370, 344, 6000),
    ("TJM", "Denim Bottom", 50, 47, 8000),
    ("TJM", "T-shirt", 75, 71, 2900),
    ("TJM", "Woven Top", 50, 43, 4900),
    ("Womens", "Denim Bottom", 15, 6, 6500),
    ("Womens", "Dress", 50, 41, 6400),
    ("Womens", "Knit Top", 180, 158, 3000),
    ("Womens", "Non Denim Bottom", 25, 4, 5200),
    ("Womens", "Woven Top", 25, 3, 5000),
    ("BB", "Denim Bottom", 35, 30, 3400),
    ("BB", "Knit Top", 270, 250, 1900),
    ("BB", "Non Denim Bottom", 75, 67, 2800),
    ("BB", "Woven Top", 45, 42, 3200),
    ("BG", "Dress", 60, 53, 3500),
    ("BG", "Knit Top", 50, 44, 1900),
    ("BG", "Non Denim Bottom", 25, 13, 2800),
    ("LB", "Denim Bottom", 35, 11, 3200),
    ("LB", "Knit Top", 120, 104, 1800),
    ("LB", "Non Denim Bottom", 50, 39, 2600),
    ("LB", "Woven Top", 30, 22, 2900),
    ("LG", "Dress", 20, 14, 3300),
    ("LG", "Knit Top", 10, 7, 1800),
    ("LG", "Non Denim Bottom", 10, 4, 2600),
]

# Derived lookups. A category's unique id is "<line> <product type>",
# e.g. "Womens Knit Top", because product types repeat across lines.
CATEGORIES = [f"{line} {product}" for line, product, *_ in CATEGORY_PLAN]
CATEGORY_LINE = {f"{line} {product}": line for line, product, *_ in CATEGORY_PLAN}
CATEGORY_PRODUCT = {f"{line} {product}": product for line, product, *_ in CATEGORY_PLAN}
CATEGORY_DEPARTMENT = {cat: LINES[line] for cat, line in CATEGORY_LINE.items()}
MONTHLY_TARGETS = {f"{l} {p}": target for l, p, target, _, _ in CATEGORY_PLAN}
LAST_YEAR_UNITS = {f"{l} {p}": ly for l, p, _, ly, _ in CATEGORY_PLAN}
AVG_PRICE = {f"{l} {p}": price for l, p, _, _, price in CATEGORY_PLAN}

# --- Sizes ------------------------------------------------------------------------------
# Each kind of garment has its own size system. "core" sizes are the ones most
# customers need: when they run out, the category can't sell to most shoppers
# even if total stock still looks healthy (a "broken size run", Phase 6e).
SIZE_SYSTEMS = {
    "mens_tops": {
        "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
        "weights": [0.06, 0.16, 0.30, 0.28, 0.14, 0.06],
        "core": ["M", "L"],
    },
    "mens_bottoms": {
        "sizes": ["30", "32", "34", "36", "38"],
        "weights": [0.15, 0.30, 0.30, 0.17, 0.08],
        "core": ["32", "34"],
    },
    "womens_tops": {
        "sizes": ["XS", "S", "M", "L", "XL"],
        "weights": [0.15, 0.30, 0.30, 0.17, 0.08],
        "core": ["S", "M"],
    },
    "womens_bottoms": {
        "sizes": ["24", "26", "28", "30", "32"],
        "weights": [0.12, 0.28, 0.30, 0.20, 0.10],
        "core": ["26", "28"],
    },
    "big_kids": {
        "sizes": ["8Y", "10Y", "12Y", "14Y", "16Y"],
        "weights": [0.15, 0.28, 0.28, 0.19, 0.10],
        "core": ["10Y", "12Y"],
    },
    "little_kids": {
        "sizes": ["2Y", "3-4Y", "5-6Y", "7Y"],
        "weights": [0.20, 0.32, 0.30, 0.18],
        "core": ["3-4Y", "5-6Y"],
    },
}


def size_system_for(category):
    """Which size system a category uses, from its line and product type."""
    line = CATEGORY_LINE[category]
    is_bottom = "Bottom" in CATEGORY_PRODUCT[category]
    if line in ("BB", "BG"):
        return SIZE_SYSTEMS["big_kids"]
    if line in ("LB", "LG"):
        return SIZE_SYSTEMS["little_kids"]
    if line == "Womens":
        return SIZE_SYSTEMS["womens_bottoms" if is_bottom else "womens_tops"]
    return SIZE_SYSTEMS["mens_bottoms" if is_bottom else "mens_tops"]


# --- Stock and deliveries -----------------------------------------------------------------------
# The warehouse delivers once a week and tops each size back up to its "par"
# level (the stock the store aims to hold). Par = enough for this many weeks
# of normal sales, but never fewer than MIN_PAR_PER_SIZE units per size so
# every size is always presentable on the shelf.
DELIVERY_WEEKDAY = 0  # Monday
PAR_WEEKS_OF_COVER = 2.0
MIN_PAR_PER_SIZE = 4

# When a shopper's size is out, most walk away; only this share will accept
# a neighbouring size instead.
SIZE_SWITCH_RATE = 0.15

# --- Deliberate demo scenarios ---------------------------------------------------------------------
# The spec's four scenarios, mapped onto real store categories. Stockouts and
# broken size runs are NOT hard-coded: they emerge from these supply events,
# because the simulation can only sell what is on the shelf.

# Womenswear stockout: Womens Knit Top was allocated tight stock and its
# Monday delivery on day 18 never arrived. Shoppers keep coming (footfall stays
# normal) but there is nothing to sell.
PAR_WEEKS_OVERRIDE = {"Womens Knit Top": 1.3}
MISSED_DELIVERIES = {"Womens Knit Top": [18]}

# "Chinos" broken size run: all month the warehouse has been out of the core
# waists, so every THM Non Denim Bottom delivery arrives without sizes 32 and
# 34. As often happens, the warehouse ships other waists in their place, so
# the shelf fills up with 30s and 36s and total stock looks healthy — yet once
# the opening stock of 32/34 sells through, most shoppers can't find their size.
SHORT_DELIVERIES = {
    "THM Non Denim Bottom": {4: ["32", "34"], 11: ["32", "34"], 18: ["32", "34"]}
}

# Kidswear overperformance: LB Knit Top sells well above plan all month.
# (Replenishment keeps up, because par is based on its real sell-through.)
DEMAND_MULTIPLIER = {"LB Knit Top": 1.4}

# "Jeans" (TJM Denim Bottom) and "Formal Wear" (THT Woven Top) have no
# scenario: they track roughly on pace with normal variation.

# --- Cross-selling (Phase 6c) ------------------------------------------------------
# Which product types go together as an outfit (to pair a lagging category
# with), and which can stand in for each other when one is sold out.
# Adults can pair across lines on the same floor (e.g. THM chinos with a THT
# shirt); kids pair only within the same line, since BB/BG/LB/LG are
# different ages.
COMPLEMENTS = {
    "Non Denim Bottom": ["Woven Top", "Polo", "Knit Top", "Blazer"],
    "Denim Bottom": ["T-shirt", "Knit Top", "Woven Top", "Polo"],
    "Polo": ["Non Denim Bottom", "Denim Bottom"],
    "T-shirt": ["Denim Bottom", "Non Denim Bottom"],
    "Woven Top": ["Non Denim Bottom", "Blazer", "Denim Bottom"],
    "Blazer": ["Woven Top", "Non Denim Bottom"],
    "Knit Top": ["Denim Bottom", "Non Denim Bottom"],
    "Dress": ["Woven Top", "Knit Top"],
}
SUBSTITUTES = {
    "Knit Top": ["Woven Top", "T-shirt", "Polo"],
    "Woven Top": ["Knit Top", "Polo"],
    "Polo": ["T-shirt", "Knit Top", "Woven Top"],
    "T-shirt": ["Polo", "Knit Top"],
    "Non Denim Bottom": ["Denim Bottom"],
    "Denim Bottom": ["Non Denim Bottom"],
    "Dress": ["Woven Top", "Knit Top"],
    "Blazer": ["Woven Top"],
}

# --- Reproducibility ---------------------------------------------------------------
# Fixed random seed so the simulated data is identical every time it is
# regenerated. A reproducible demo is essential for this project.
RANDOM_SEED = 42

# --- Transactions and footfall ---------------------------------------------------------
# Average units per transaction, used to simulate a transaction count.
AVG_UPT = 1.5

# Footfall is counted per floor zone (Menswear / Womenswear / Kidswear), the
# way a zone counter would, not per category. It is sized from normal demand
# and a baseline conversion rate, and deliberately ignores stockouts: an empty
# shelf doesn't stop people walking in. That is what makes a stockout
# diagnosable later (traffic normal, conversion collapsed).
BASELINE_CONVERSION_RATE = 0.30

# --- Pace engine business rules ------------------------------------------------------
# A category is flagged "behind" once it is more than 15% under its expected
# pace, and "ahead" once it is more than 15% over. This threshold is a
# business rule, not a technical constant: a category more than 15% behind
# this late is unlikely to recover without an intervention (restock,
# cross-sell push, staffing change). Tunable here.
PACE_THRESHOLD_PCT = 15

# Don't judge a category until enough units were expected to make the
# percentage meaningful. With only a handful of units expected, ordinary
# randomness produces huge % swings (0 sold vs 1.8 expected reads as -100%),
# which would flash false alarms. Below this, status is "too_early".
MIN_EXPECTED_UNITS_FOR_STATUS = 5

# Is a gap real, or just normal randomness? At this store's volumes many
# categories sell only a few units a day, so a 15% gap can easily be chance.
# Sales counts vary by roughly the square root of the expected number (e.g.
# expected 100 -> about +/-10 is one normal swing). Past the 15% line:
#   - "behind" / "ahead" needs a gap bigger than NORMAL_VARIATION_Z swings
#     (1.65 = would happen by chance only about 1 time in 20)
#   - "drifting" (amber: worth watching, not yet proven) needs a gap bigger
#     than DRIFTING_Z swings, so a 1-unit miss early in the day stays quiet
NORMAL_VARIATION_Z = 1.65
DRIFTING_Z = 1.0

# Traffic vs conversion (Phase 6d). Comparing the last 7 full days with
# earlier this month:
#   - a traffic problem: visitors per day down more than TRAFFIC_DROP_PCT
#   - a conversion problem: visitors normal, but the share of visitors who
#     buy is down more than CONVERSION_DROP_PCT — usually stock, sizes, price
#     or service, not marketing
# Categories expected to make fewer than MIN_TRANSACTIONS_FOR_READING sales
# in the window are too small to judge either way.
TRAFFIC_DROP_PCT = 15
CONVERSION_DROP_PCT = 20
MIN_TRANSACTIONS_FOR_READING = 10

# Staffing (Phase 6b): an hour counts as a peak if its average visitors are
# at least this share of the zone's busiest hour.
PEAK_SHARE_OF_BUSIEST_HOUR = 0.80

# If a category needs more than this multiple of its current daily sales rate
# to reach target by month end, flag it as unlikely without action: at 1.5x,
# "keep doing what we're doing" clearly won't get there.
REQUIRED_RATE_STRETCH = 1.5

# A size is a "last piece" alert the moment it drops to exactly this many
# units remaining (Phase 6a).
LAST_PIECE_THRESHOLD = 1

# Stock health rules (Phase 6e). "Usual stock" is learned from the data: the
# category's typical end-of-day stock earlier this month.
#   - stockout: total stock has fallen below STOCKOUT_MAX_SHARE of usual,
#     so there is almost nothing left to sell in any size
#   - broken size run: every core size is down to CORE_DEPLETED_MAX_UNITS or
#     fewer, while total stock is still at least BROKEN_RUN_MIN_SHARE of usual.
#     The category total looks fine, which is exactly why nobody notices,
#     but most shoppers can't find their size.
#   - running out: core sizes depleted and total stock in between
STOCKOUT_MAX_SHARE = 0.10
BROKEN_RUN_MIN_SHARE = 0.30
CORE_DEPLETED_MAX_UNITS = 1

# --- AI agent (Phase 4) -----------------------------------------------------------------
# Model name kept in one place so it can be swapped without touching agent
# code. Use a current Claude Sonnet model.
CLAUDE_MODEL = "claude-sonnet-5"
CLAUDE_MAX_TOKENS = 1024

# Safety cap on how many rounds of tool calls the agent can make while
# answering a single question, so a confused loop can't run forever.
MAX_TOOL_ITERATIONS = 6

# The simulated "right now" when a tool isn't given an explicit hour:
# 16:00 on day 24 — mid-afternoon, with time left today to act.
DEFAULT_CURRENT_HOUR = 16


def _check_shapes():
    for name, shape in (("WEEKDAY", WEEKDAY_HOURLY_SHAPE), ("WEEKEND", WEEKEND_HOURLY_SHAPE)):
        assert math.isclose(sum(shape.values()), 1.0), f"{name}_HOURLY_SHAPE must sum to 1"
    for name, system in SIZE_SYSTEMS.items():
        assert math.isclose(sum(system["weights"]), 1.0), f"{name} size weights must sum to 1"


_check_shapes()
