"""
Loyalty tier playbooks and pace-triggered cross-selling (Phases 6f + 6c).

Privacy by design: every recommendation here works at loyalty-tier level
(Silver, Gold, Platinum, Non-member), never at the level of an individual
customer. The data holds only tier-level aggregates, and nothing here looks
up, stores or reasons about any one shopper. This is a deliberate choice to
avoid individual profiling, not a missing feature.
"""

import os

import pandas as pd

import kpi
import stock
from config import (
    CATEGORIES,
    CATEGORY_DEPARTMENT,
    CATEGORY_LINE,
    CATEGORY_PRODUCT,
    COMPLEMENTS,
    DEFAULT_CURRENT_HOUR,
    SUBSTITUTES,
    TODAY_DAY,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LOYALTY_PATH = os.path.join(DATA_DIR, "loyalty_tiers.csv")

# How to say each offer at the till, in plain words.
OFFER_AT_THE_TILL = {
    "bundle": "an outfit bundle price when they buy both pieces together",
    "percentage_discount": "a first-purchase discount, and sign them up to the loyalty programme",
    "loyalty_points_multiplier": "double loyalty points on today's purchase",
    "early_access": "early access to the next collection drop",
    "complimentary_service": "a free alteration",
}

KIDS_LINES = {"BB", "BG", "LB", "LG"}


def load_loyalty_data():
    """Loads the tier-level loyalty data from disk."""
    return pd.read_csv(LOYALTY_PATH)


def get_tier_playbook(category, loyalty_df=None):
    """
    Each loyalty tier's response profile for a category, ranked by how often
    that tier takes up a cross-sell, with the offer each tier responds to
    best, phrased as something an associate can say at the till.
    """
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}")
    if loyalty_df is None:
        loyalty_df = load_loyalty_data()

    rows = loyalty_df[loyalty_df["category"] == category].sort_values(
        "cross_sell_response_rate", ascending=False
    )
    tiers = [
        {
            "tier": r["tier"],
            "cross_sell_response_rate_pct": float(r["cross_sell_response_rate"]),
            "share_of_transactions_pct": int(r["share_of_transactions"]),
            "avg_basket_value": int(r["avg_basket_value"]),
            "avg_upt": float(r["avg_upt"]),
            "preferred_offer_type": r["preferred_offer_type"],
            "offer_at_the_till": OFFER_AT_THE_TILL[r["preferred_offer_type"]],
        }
        for _, r in rows.iterrows()
    ]
    return {
        "category": category,
        "privacy_note": "Tier-level only; no individual customer data is used.",
        "best_responding_tier": tiers[0]["tier"],
        "tiers_ranked_by_response": tiers,
    }


def _eligible_partners(category, product_types, pace_by_category, health_by_category):
    """
    Categories of the given product types that are selling well (on pace or
    ahead) and have their core sizes in stock. Adults can pair across lines
    on the same floor; kids stay within their own line (different ages).
    Best first: same line, then ahead of on pace, then by % vs pace.
    """
    line = CATEGORY_LINE[category]
    candidates = []
    for other in CATEGORIES:
        if other == category or CATEGORY_PRODUCT[other] not in product_types:
            continue
        if line in KIDS_LINES and CATEGORY_LINE[other] != line:
            continue
        if CATEGORY_DEPARTMENT[other] != CATEGORY_DEPARTMENT[category]:
            continue
        pace = pace_by_category[other]
        if pace["status"] not in ("on_pace", "ahead") or health_by_category[other] != "healthy":
            continue
        candidates.append(pace)
    candidates.sort(key=lambda p: (
        CATEGORY_LINE[p["category"]] != line,
        p["status"] != "ahead",
        -p["pct_vs_pace"],
    ))
    return candidates


def _who(tier):
    return "non-members" if tier == "Non-member" else f"{tier} members"


def _tier_pitch(playbook):
    """The lead tier's pitch, plus how to handle non-members if they aren't the lead."""
    best = playbook["tiers_ranked_by_response"][0]
    pitch = (f"Lead with {_who(best['tier'])} ({best['cross_sell_response_rate_pct']:.0f}% take up "
             f"cross-sells here): offer {best['offer_at_the_till']}.")
    if best["tier"] != "Non-member":
        non_member = next(t for t in playbook["tiers_ranked_by_response"] if t["tier"] == "Non-member")
        pitch += f" For non-members, offer {non_member['offer_at_the_till']}."
    return best, pitch


def _supply_action(category, stock_verdict, health, day, hour, stock_df, sales_df):
    """
    What to do about supply, stated only as far as the delivery history
    actually shows it (never assumed).
    """
    history = stock.get_stock_history(category, day, hour, stock_df=stock_df, sales_df=sales_df)
    last = history["deliveries_received"][-1] if history["deliveries_received"] else None
    missed = history["scheduled_deliveries_not_received"]

    if stock_verdict == "broken_size_run":
        core = health["core_sizes"]
        sizes = ", ".join(core)
        if last and all(s in last["core_sizes_missing"] for s in core):
            why = f"the last delivery (day {last['day']}) came without them"
        elif last:
            why = f"they've sold through since the last delivery (day {last['day']})"
        else:
            why = "no delivery has arrived this month"
        return f"Request a transfer of sizes {sizes} from a nearby store: {why}."

    if missed:
        days = " and ".join(f"day {d}" for d in missed)
        return (f"The scheduled delivery on {days} never arrived: chase it with the warehouse, "
                f"or request an inter-store transfer.")
    return "Request a replenishment or an inter-store transfer."


def get_cross_sell_ideas(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, statuses=("behind",),
                         sales_df=None, stock_df=None, loyalty_df=None):
    """
    Cross-sell ideas for every category whose month-to-date pace status is
    in `statuses` (by default only categories actually behind pace, per the
    15% rule). The idea depends on why the category is behind:
      - stockout: nothing to sell, so redirect shoppers to an in-stock
        substitute rather than pushing the empty category
      - broken size run: fix supply first (request the missing sizes), and
        meanwhile pair the sizes that are plentiful with a complementary piece
      - otherwise (a demand problem): pair it with a complementary category
        that is selling well, aimed at the tier most likely to respond
    Each idea names a tier and an offer, phrased for use at the till.
    """
    if sales_df is None:
        sales_df = kpi.load_sales_data()
    if stock_df is None:
        stock_df = stock.load_stock_data()
    if loyalty_df is None:
        loyalty_df = load_loyalty_data()

    pace_by_category = {p["category"]: p for p in kpi.get_category_pace(day, hour, sales_df=sales_df)}
    health = {c: stock.check_size_runs(c, day, hour, stock_df=stock_df) for c in CATEGORIES}
    verdict = {c: h["verdict"] for c, h in health.items()}

    ideas = []
    for category, pace in pace_by_category.items():
        if pace["status"] not in statuses:
            continue
        product = CATEGORY_PRODUCT[category]
        stock_verdict = verdict[category]
        idea = {
            "category": category,
            "status": pace["status"],
            "pct_vs_pace": pace["pct_vs_pace"],
            "stock_verdict": stock_verdict,
            "supply_action": None,
        }

        if stock_verdict in ("stockout", "running_out"):
            partners = _eligible_partners(category, SUBSTITUTES.get(product, []),
                                          pace_by_category, verdict)
            idea["type"] = "substitute"
            idea["supply_action"] = _supply_action(category, stock_verdict, health[category],
                                                   day, hour, stock_df, sales_df)
            if partners:
                partner = partners[0]["category"]
                best, pitch = _tier_pitch(get_tier_playbook(partner, loyalty_df))
                idea.update(partner=partner, lead_tier=best["tier"],
                            offer_type=best["preferred_offer_type"])
                idea["at_the_till"] = (
                    f"{category} is sold out in most sizes, so don't lose the shopper: walk them "
                    f"to {partner}, which is selling well and in stock. {pitch}"
                )
            else:
                idea.update(partner=None, lead_tier=None, offer_type=None,
                            at_the_till=f"{category} is sold out and no in-stock substitute is "
                                        f"doing well; focus on getting stock back.")

        else:
            partners = _eligible_partners(category, COMPLEMENTS.get(product, []),
                                          pace_by_category, verdict)
            best, pitch = _tier_pitch(get_tier_playbook(category, loyalty_df))
            partner = partners[0]["category"] if partners else None
            idea.update(type="complement", partner=partner, lead_tier=best["tier"],
                        offer_type=best["preferred_offer_type"])
            pairing = f"Pair it with {partner} as an outfit." if partner else \
                "No complementary category is both selling well and in stock right now."

            if stock_verdict == "broken_size_run":
                h = health[category]
                missing = ", ".join(h["core_sizes"])
                plentiful = ", ".join(s for s, u in h["other_sizes_remaining"].items() if u >= 5)
                idea["type"] = "complement_in_available_sizes"
                idea["supply_action"] = _supply_action(category, stock_verdict, h,
                                                       day, hour, stock_df, sales_df)
                idea["at_the_till"] = (
                    f"{category} is {abs(pace['pct_vs_pace']):.0f}% behind pace because sizes {missing} "
                    f"are gone. Until they're back, focus on shoppers who fit {plentiful}, where "
                    f"there's plenty of stock. {pairing} {pitch}"
                )
            else:
                idea["at_the_till"] = (
                    f"{category} is {abs(pace['pct_vs_pace']):.0f}% behind pace. {pairing} {pitch}"
                )
        ideas.append(idea)

    return ideas


# --- Verification output -------------------------------------------------------

def _print_playbooks():
    print("\nTier playbooks: best-responding tier and its offer vary by category\n")
    for category in ("THM Non Denim Bottom", "THT Woven Top", "Womens Knit Top", "TJM T-shirt",
                     "BB Knit Top"):
        p = get_tier_playbook(category)
        ranked = ", ".join(f"{t['tier']} {t['cross_sell_response_rate_pct']:.0f}%"
                           for t in p["tiers_ranked_by_response"])
        best = p["tiers_ranked_by_response"][0]
        print(f"  {category:<22} {ranked:<52} -> {best['tier']}: {best['preferred_offer_type']}")


def _print_ideas(title, **kwargs):
    ideas = get_cross_sell_ideas(**kwargs)
    print(f"\n{title}: {len(ideas)}\n")
    for i in ideas:
        print(f"  [{i['category']} | {i['status']} {i['pct_vs_pace']}% | stock: {i['stock_verdict']} "
              f"| idea: {i['type']}]")
        if i["supply_action"]:
            print(f"    First: {i['supply_action']}")
        print(f"    At the till: {i['at_the_till']}\n")


if __name__ == "__main__":
    _print_playbooks()
    _print_ideas("Cross-sell ideas for categories behind pace")
    _print_ideas("For illustration, 'drifting' categories (not triggered by default)",
                 statuses=("drifting",))
