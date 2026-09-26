"""
Loyalty tier playbooks and pace-triggered cross-selling (Phases 6f + 6c).

Privacy by design: every recommendation here works at loyalty-tier level
(Silver, Gold, Platinum, Non-member), never at the level of an individual
customer. The data holds only tier-level aggregates, and nothing here looks
up, stores or reasons about any one shopper. This is a deliberate choice to
avoid individual profiling, not a missing feature.

Cross-sell ideas work without loyalty data too; they just can't name a tier.
"""

import kpi
import stock
from config import COMPLEMENTS, SUBSTITUTES
from store import resolve

# How to say each offer at the till, in plain words.
OFFER_AT_THE_TILL = {
    "bundle": "an outfit bundle price when they buy both pieces together",
    "percentage_discount": "a first-purchase discount, and sign them up to the loyalty programme",
    "loyalty_points_multiplier": "double loyalty points on today's purchase",
    "early_access": "early access to the next collection drop",
    "complimentary_service": "a free alteration",
}

# Kids' lines are different ages (e.g. big boys vs little boys), so a kids'
# category is only paired with categories in its own line.
KIDS_DEPARTMENT = "Kidswear"


def _offer_words(offer_type):
    return OFFER_AT_THE_TILL.get(offer_type, str(offer_type).replace("_", " "))


def get_tier_playbook(category, store=None):
    """
    Each loyalty tier's response profile for a category, ranked by how often
    that tier takes up a cross-sell, with the offer each tier responds to
    best, phrased as something an associate can say at the till.
    """
    store, _, _ = resolve(store)
    if category not in store.categories:
        raise ValueError(f"Unknown category {category!r}")
    store.require("loyalty")

    rows = store.loyalty[store.loyalty["category"] == category].sort_values(
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
            "offer_at_the_till": _offer_words(r["preferred_offer_type"]),
        }
        for _, r in rows.iterrows()
    ]
    return {
        "category": category,
        "privacy_note": "Tier-level only; no individual customer data is used.",
        "best_responding_tier": tiers[0]["tier"] if tiers else None,
        "tiers_ranked_by_response": tiers,
    }


def _eligible_partners(store, category, product_types, pace_by_category, health_by_category):
    """
    Categories of the given product types that are selling well (on pace or
    ahead) and have their core sizes in stock (or no stock data to say
    otherwise). Adults can pair across lines on the same floor; kids stay
    within their own line (different ages). Best first: same line, then
    ahead of on pace, then by % vs pace.
    """
    line = store.category_line[category]
    department = store.category_department[category]
    candidates = []
    for other in store.categories:
        if other == category or store.category_product[other] not in product_types:
            continue
        if department == KIDS_DEPARTMENT and store.category_line[other] != line:
            continue
        if store.category_department[other] != department:
            continue
        pace = pace_by_category[other]
        if pace["status"] not in ("on_pace", "ahead") or health_by_category[other] not in ("healthy", "unknown"):
            continue
        candidates.append(pace)
    candidates.sort(key=lambda p: (
        store.category_line[p["category"]] != line,
        p["status"] != "ahead",
        -p["pct_vs_pace"],
    ))
    return candidates


def _who(tier):
    return "non-members" if tier == "Non-member" else f"{tier} members"


def _tier_pitch(playbook):
    """
    The lead tier's pitch, plus how to handle non-members if they aren't the
    lead. (None, "") when there's no loyalty data for the category.
    """
    if playbook is None or not playbook["tiers_ranked_by_response"]:
        return None, ""
    best = playbook["tiers_ranked_by_response"][0]
    pitch = (f"Lead with {_who(best['tier'])} ({best['cross_sell_response_rate_pct']:.0f}% take up "
             f"cross-sells here): offer {best['offer_at_the_till']}.")
    non_member = next((t for t in playbook["tiers_ranked_by_response"] if t["tier"] == "Non-member"), None)
    if best["tier"] != "Non-member" and non_member:
        pitch += f" For non-members, offer {non_member['offer_at_the_till']}."
    return best, pitch


def get_cross_sell_ideas(day=None, hour=None, statuses=("behind",), store=None):
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
    Each idea names a tier and an offer (when there's loyalty data), phrased
    for use at the till.
    """
    store, day, hour = resolve(store, day, hour)

    def playbook(category):
        return get_tier_playbook(category, store=store) if store.has_loyalty else None

    pace_by_category = {p["category"]: p for p in kpi.get_category_pace(day, hour, store=store)}
    if store.has_stock:
        health = {c: stock.check_size_runs(c, day, hour, store=store) for c in store.categories}
        verdict = {c: h["verdict"] for c, h in health.items()}
    else:
        health, verdict = {}, {c: "unknown" for c in store.categories}

    ideas = []
    for category, pace in pace_by_category.items():
        if pace["status"] not in statuses:
            continue
        product = store.category_product[category]
        stock_verdict = verdict[category]
        idea = {
            "category": category,
            "status": pace["status"],
            "pct_vs_pace": pace["pct_vs_pace"],
            "stock_verdict": stock_verdict,
            "supply_action": None,
        }

        if stock_verdict in ("stockout", "running_out"):
            partners = _eligible_partners(store, category, SUBSTITUTES.get(product, []),
                                          pace_by_category, verdict)
            idea["type"] = "substitute"
            idea["supply_action"] = stock.suggest_supply_action(
                category, day, hour, health=health[category], store=store)
            if partners:
                partner = partners[0]["category"]
                best, pitch = _tier_pitch(playbook(partner))
                idea.update(partner=partner, lead_tier=best and best["tier"],
                            offer_type=best and best["preferred_offer_type"])
                idea["at_the_till"] = (
                    f"{category} is sold out in most sizes, so don't lose the shopper: walk them "
                    f"to {partner}, which is selling well and in stock. {pitch}"
                ).strip()
            else:
                idea.update(partner=None, lead_tier=None, offer_type=None,
                            at_the_till=f"{category} is sold out and no in-stock substitute is "
                                        f"doing well; focus on getting stock back.")

        else:
            partners = _eligible_partners(store, category, COMPLEMENTS.get(product, []),
                                          pace_by_category, verdict)
            best, pitch = _tier_pitch(playbook(category))
            partner = partners[0]["category"] if partners else None
            idea.update(type="complement", partner=partner, lead_tier=best and best["tier"],
                        offer_type=best and best["preferred_offer_type"])
            if partner:
                pairing = f"Pair it with {partner} as an outfit."
            elif store.has_stock:
                pairing = "No complementary category is both selling well and in stock right now."
            else:
                pairing = "No complementary category is selling well right now."

            if stock_verdict == "broken_size_run":
                h = health[category]
                missing = ", ".join(h["core_sizes"])
                plentiful = ", ".join(s for s, u in h["other_sizes_remaining"].items() if u >= 5)
                idea["type"] = "complement_in_available_sizes"
                idea["supply_action"] = stock.suggest_supply_action(
                    category, day, hour, health=h, store=store)
                idea["at_the_till"] = (
                    f"{category} is {abs(pace['pct_vs_pace']):.0f}% behind pace because sizes {missing} "
                    f"are gone. Until they're back, focus on shoppers who fit {plentiful}, where "
                    f"there's plenty of stock. {pairing} {pitch}"
                ).strip()
            else:
                idea["at_the_till"] = (
                    f"{category} is {abs(pace['pct_vs_pace']):.0f}% behind pace. {pairing} {pitch}"
                ).strip()
        ideas.append(idea)

    return ideas


# --- Verification output -------------------------------------------------------

def _print_playbooks(store):
    print("\nTier playbooks: best-responding tier and its offer vary by category\n")
    for category in ("Men Casual Trousers", "Men Formal Shirts", "Women Tops", "Men Denim T-shirts",
                     "Boys Tops"):
        p = get_tier_playbook(category, store=store)
        ranked = ", ".join(f"{t['tier']} {t['cross_sell_response_rate_pct']:.0f}%"
                           for t in p["tiers_ranked_by_response"])
        best = p["tiers_ranked_by_response"][0]
        print(f"  {category:<22} {ranked:<52} -> {best['tier']}: {best['preferred_offer_type']}")


def _print_ideas(store, title, **kwargs):
    ideas = get_cross_sell_ideas(store=store, **kwargs)
    print(f"\n{title}: {len(ideas)}\n")
    for i in ideas:
        print(f"  [{i['category']} | {i['status']} {i['pct_vs_pace']}% | stock: {i['stock_verdict']} "
              f"| idea: {i['type']}]")
        if i["supply_action"]:
            print(f"    First: {i['supply_action']}")
        print(f"    At the till: {i['at_the_till']}\n")


if __name__ == "__main__":
    demo, _, _ = resolve()
    _print_playbooks(demo)
    _print_ideas(demo, "Cross-sell ideas for categories behind pace")
    _print_ideas(demo, "For illustration, 'drifting' categories (not triggered by default)",
                 statuses=("drifting",))
