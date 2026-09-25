"""
"Why is this category behind?" — one place that weighs the evidence.

Combines pace, stock health, delivery history, visitors and conversion into
a single likely cause, the numbers behind it, and one concrete action. The
end-of-day summary and the dashboard reuse it, and the AI agent can check
its own reasoning against it.

Order of evidence: stock problems are checked first, because an empty shelf
explains a slowdown directly and conversion figures would only echo it.
"""

import kpi
import stock
from config import CATEGORIES, CATEGORY_DEPARTMENT, DEFAULT_CURRENT_HOUR, TODAY_DAY


def load_all():
    """Loads every data file once, so a whole-store diagnosis doesn't re-read them per category."""
    return {
        "sales_df": kpi.load_sales_data(),
        "footfall_df": kpi.load_footfall_data(),
        "stock_df": stock.load_stock_data(),
    }


ACTIONS = {
    "traffic_drop": ("Fewer shoppers are reaching the {zone} floor: check its entrance and window "
                     "display, and move a best-selling piece to the front of the section."),
    "conversion_drop": ("Shoppers are coming but not buying: check every size is out on the floor "
                        "(not in the backroom), prices and offers are clearly marked, and put a "
                        "trained associate on the section at peak time."),
    "unclear": ("No single cause shows in the data yet, so this may be normal ups and downs. "
                "Keep watching; if it is still slipping in two days, check the display and sizes "
                "on the floor."),
}

HEADLINES = {
    "stockout": "Out of stock: shoppers are still coming, but there is nothing to sell.",
    "broken_size_run": ("Broken size run: the shelf looks full, but the sizes most shoppers need "
                        "are gone."),
    "traffic_drop": "Fewer shoppers than usual are coming to this floor.",
    "conversion_drop": "Shoppers are coming as usual, but fewer of them are buying.",
    "unclear": "Slipping, but the data shows no single clear cause yet.",
    "ahead": "Selling well ahead of plan.",
    "on_track": "On track.",
    "too_early": "Too early in the period to judge.",
}


def diagnose(category, day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, data=None):
    """
    The likely cause of a category's pace, with plain-language evidence
    (every number read from the data) and one concrete action.
    """
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}")
    if data is None:
        data = load_all()
    sales_df, footfall_df, stock_df = data["sales_df"], data["footfall_df"], data["stock_df"]

    pace = kpi.get_category_pace(day, hour, category=category, sales_df=sales_df)[0]
    status = pace["status"]
    evidence = [
        f"Sold {pace['units_sold_so_far']} against {pace['expected_units_by_now']:.0f} expected by "
        f"now ({pace['pct_vs_pace']:+.0f}% vs pace)."
    ]

    if status in ("on_pace", "ahead", "too_early"):
        cause = {"on_pace": "on_track", "ahead": "ahead", "too_early": "too_early"}[status]
        return {
            "category": category, "as_of": {"day": day, "hour": hour}, "status": status,
            "pct_vs_pace": pace["pct_vs_pace"], "cause": cause, "headline": HEADLINES[cause],
            "evidence": evidence, "action": None,
        }

    health = stock.check_size_runs(category, day, hour, stock_df=stock_df)
    conversion = kpi.get_conversion_metrics(category=category, day=day, hour=hour,
                                            sales_df=sales_df, footfall_df=footfall_df)
    zone = CATEGORY_DEPARTMENT[category]

    if health["verdict"] in ("stockout", "running_out"):
        cause = "stockout"
        if health["total_remaining"] == 0:
            evidence.append("Nothing left in any size.")
        else:
            evidence.append(f"Only {health['total_remaining']} units left across all sizes "
                            f"({health['total_as_pct_of_usual']}% of its usual stock).")
    elif health["verdict"] == "broken_size_run":
        cause = "broken_size_run"
        core = ", ".join(f"{s}: {u}" for s, u in health["core_remaining_by_size"].items())
        evidence.append(f"Core sizes are gone ({core}) while {health['total_remaining']} units "
                        f"remain on the shelf ({health['total_as_pct_of_usual']}% of usual).")
    elif conversion["reading"] in ("traffic_problem", "traffic_and_conversion_down"):
        cause = "traffic_drop"
    elif conversion["reading"] == "conversion_problem":
        cause = "conversion_drop"
    else:
        cause = "unclear"

    if conversion["reading"] == "too_few_sales_to_judge":
        evidence.append("Too few recent sales to tell a visitor drop from a buying drop.")
    else:
        recent, baseline = conversion["recent_last_3_days_and_today"], conversion["baseline_earlier_this_month"]
        evidence.append(
            f"The {zone} floor had {recent['zone_visitors']} visitors over days {recent['days']} "
            f"({conversion['visitors_change_vs_typical_pct']:+.0f}% vs a typical stretch); "
            f"{recent['conversion_rate_pct']}% of them bought from this category, vs "
            f"{baseline['conversion_rate_pct']}% earlier this month."
        )

    if cause in ("stockout", "broken_size_run"):
        history = stock.get_stock_history(category, day, hour, stock_df=stock_df, sales_df=sales_df)
        for missed_day in history["scheduled_deliveries_not_received"]:
            evidence.append(f"The scheduled delivery on day {missed_day} never arrived.")
        missing_core = [d for d in history["deliveries_received"] if d["core_sizes_missing"]]
        if cause == "broken_size_run" and missing_core:
            days = ", ".join(str(d["day"]) for d in missing_core)
            sizes = ", ".join(health["core_sizes"])
            evidence.append(f"Deliveries on day(s) {days} arrived without sizes {sizes}.")
        action = stock.suggest_supply_action(category, day, hour, health=health,
                                             stock_df=stock_df, sales_df=sales_df)
    else:
        action = ACTIONS[cause].format(zone=zone)

    return {
        "category": category, "as_of": {"day": day, "hour": hour}, "status": status,
        "pct_vs_pace": pace["pct_vs_pace"], "cause": cause, "headline": HEADLINES[cause],
        "evidence": evidence, "action": action,
    }


def diagnose_store(day=TODAY_DAY, hour=DEFAULT_CURRENT_HOUR, data=None):
    """Diagnosis for every category, loading the data only once."""
    if data is None:
        data = load_all()
    return [diagnose(c, day, hour, data=data) for c in CATEGORIES]


if __name__ == "__main__":
    data = load_all()
    for d in diagnose_store(data=data):
        if d["status"] not in ("behind", "drifting"):
            continue
        print(f"\n{d['category']}  [{d['status']}, {d['pct_vs_pace']:+.0f}%]  cause: {d['cause']}")
        print(f"  {d['headline']}")
        for e in d["evidence"]:
            print(f"   - {e}")
        print(f"  Action: {d['action']}")
