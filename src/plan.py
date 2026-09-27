"""
Tomorrow's plan: what the morning huddle needs, worked out at the close of a day.

A store manager's morning goes on turning yesterday's numbers into today's
jobs. This puts them on one screen, and in a message ready to share:
  - the goal: what tomorrow needs to sell to keep the month on course for its target,
    next to what a usual day like it brings in at the current rate
  - focus: the categories that are behind, why, the one thing to do about
    each, and what to say at the till
  - stock: sizes to request, most urgent first
  - people: tomorrow's busy hours and where to put the team (with visitor counts)
  - what's going well, to keep stocked
Every figure comes from the same functions as the rest of the app.
"""

import diagnosis
import forecast
import kpi
import loyalty
import staffing
import stock
from store import WEEKDAY_NAMES, resolve

MAX_FOCUS = 4
MAX_STOCK_LINES = 5


def _goal(store, day, judged):
    """
    What tomorrow needs to keep the month on course for its target (the gap still to
    sell, shared over the days left by how busy each is), and what a usual day like it
    brings at the current rate, for some categories.
    """
    weights = store.day_weights
    tomorrow = day + 1
    rest = sum(weights[d] for d in range(tomorrow, store.days_in_month + 1))
    sold = sum(p["units_sold_so_far"] for p in judged)
    target = sum(p["monthly_target"] for p in judged)
    done = sum(weights[d] for d in range(1, day + 1))
    needs = max(0.0, target - sold) * weights[tomorrow] / rest if rest else 0.0
    usual = sold / done * weights[tomorrow] if done else 0.0
    return {"needs": round(needs), "usual_at_current_rate": round(usual), "target": target, "sold": sold,
            "yardstick": forecast.yardstick_of(store, [p["category"] for p in judged])}


def get_plan(day=None, store=None):
    """
    The plan for the day after `day` (default: the store's latest day), from
    the data at that day's close. Returns a dict; "month_over" is True when
    there's no day left in the month to plan.
    """
    store, day, _ = resolve(store, day, None)
    close = store.hours[-1]
    tomorrow = day + 1
    if tomorrow > store.days_in_month:
        return {"month_over": True, "for_day": None}
    date = store.date(tomorrow)
    plan = {
        "month_over": False,
        "made_at": {"day": day, "hour": close},
        "for_day": tomorrow,
        "for_date": date.isoformat(),
        "weekday": WEEKDAY_NAMES[date.weekday()],
        "busy_day": store.is_busy(tomorrow),
        "measure": store.measure,
    }

    # The goal, for the store and each line (only categories with something to judge them by).
    pace = kpi.get_category_pace(day, close, store=store)
    judged = [p for p in pace if p["monthly_target"]]
    plan["goal"] = _goal(store, day, judged) if judged else None
    plan["goal_by_line"] = [
        {"line": line, **_goal(store, day, in_line)}
        for line in store.lines
        if (in_line := [p for p in judged if p["line"] == line])
    ]
    plan["month_end"] = forecast.get_month_end_range(day=day, hour=close, store=store)

    # Focus: behind first (worst first), then drifting.
    diagnoses = diagnosis.diagnose_store(day, close, store=store)
    ideas = {i["category"]: i for i in loyalty.get_cross_sell_ideas(day, close, store=store)}
    by_category = {p["category"]: p for p in pace}
    behind = sorted((d for d in diagnoses if d["status"] == "behind"), key=lambda d: d["pct_vs_pace"])
    drifting = sorted((d for d in diagnoses if d["status"] == "drifting"), key=lambda d: d["pct_vs_pace"])
    plan["focus"] = []
    for d in (behind + drifting)[:MAX_FOCUS]:
        p, idea = by_category[d["category"]], ideas.get(d["category"])
        plan["focus"].append({
            "category": d["category"],
            "status": d["status"],
            "pct_vs_pace": d["pct_vs_pace"],
            "cause": d["cause"],
            "headline": d["headline"],
            "action": d["action"] if d["status"] == "behind" else
            "Watch it: slipping, but it could still be normal ups and downs.",
            "needs_per_day": p["needed_units_per_day"],
            "target_source": p["target_source"],
            "at_the_till": _till_tip(idea),
        })
    plan["more_behind"] = max(0, len(behind) - MAX_FOCUS)

    # Stock to request, most urgent first.
    if store.has_stock:
        requests = stock.get_request_quantities(day, close, store=store)
        urgent = [c for c in requests["categories"] if c["core_sizes_out"] or c["urgent_sizes"]]
        plan["stock"] = {"basis": requests["basis"], "urgent": urgent[:MAX_STOCK_LINES],
                         "more_urgent": max(0, len(urgent) - MAX_STOCK_LINES),
                         "all": requests["categories"]}
    else:
        plan["stock"] = None

    # People: tomorrow's busy hours and floor split.
    plan["people"] = staffing.get_staffing_recommendation(tomorrow, store=store) \
        if store.has_visitor_hours else None

    # Going well: keep them stocked.
    plan["going_well"] = [p["category"] for p in sorted(
        (p for p in pace if p["status"] == "ahead"), key=lambda p: -p["pct_vs_pace"])][:3]
    return plan


def _till_tip(idea):
    """One short line for the till team, from a cross-sell idea (or None)."""
    if not idea or not idea.get("partner"):
        return None
    offer = loyalty._offer_words(idea["offer_type"]) if idea.get("offer_type") else None
    move = {"substitute": f"walk shoppers to {idea['partner']}",
            "complement_in_available_sizes": f"sell the sizes in stock as an outfit with {idea['partner']}"
            }.get(idea["type"], f"pair it with {idea['partner']}")
    who = f"; {idea['lead_tier']} members respond best: offer {offer}" if idea.get("lead_tier") and offer else ""
    return move[0].upper() + move[1:] + who + "."


def plan_message(plan, store):
    """The plan as a short message for the team's WhatsApp group (WhatsApp shows *text* in bold)."""
    if plan["month_over"]:
        return f"*{store.name}*: the month is over. Well done, team."
    a = store.amount
    date = store.date(plan["for_day"])
    lines = [f"*Plan for {plan['weekday']} {date:%d %b}* ({store.name})"]
    goal = plan["goal"]
    if goal:
        busy = " (a busy day)" if plan["busy_day"] else ""
        if goal["needs"] > 0:
            lines.append(f"Goal: {store.amount_of(goal['needs'])} {forecast.GOAL_PHRASE[goal['yardstick']]}{busy}. "
                         f"A usual {plan['weekday']} at our current rate brings about {a(goal['usual_at_current_rate'])}.")
        else:
            lines.append(f"The month's target is already reached{busy}. Keep going.")
    if plan["focus"]:
        lines.append("")
        lines.append("*Focus:*")
        for i, f in enumerate(plan["focus"], 1):
            lines.append(f"{i}. {f['category']} ({f['pct_vs_pace']:+.0f}%): {f['action']}")
            if f["at_the_till"]:
                lines.append(f"   At the till: {f['at_the_till']}")
    stock_plan = plan["stock"]
    if stock_plan and stock_plan["urgent"]:
        lines.append("")
        lines.append("*Request today:*")
        for c in stock_plan["urgent"]:
            lines.append(f"- {c['category']}: {c['text']}")
    people = plan["people"]
    if people:
        peaks = "; ".join(f"{z['zone']} {', '.join(z['peak_windows'])}" for z in people["zones"] if z["peak_windows"])
        split = ", ".join(f"{zone} {pct}%" for zone, pct in people["suggested_floor_split_at_busiest_hour_pct"].items())
        lines.append("")
        lines.append(f"*Busiest:* {peaks}.")
        lines.append(f"At {people['store_busiest_hour']}:00 put the team: {split}.")
    if plan["going_well"]:
        lines.append("")
        lines.append(f"*Going well, keep it stocked:* {', '.join(plan['going_well'])}.")
    return "\n".join(lines)


if __name__ == "__main__":
    demo, _, _ = resolve()
    p = get_plan(store=demo)
    print(plan_message(p, demo))
    print(f"\n({len(plan_message(p, demo))} characters)")
