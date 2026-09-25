"""
End-of-day digest (Phase 5): what a manager reads in 30 seconds at close.

Plain prose, no tables, no jargon. It replaces the evening Excel pull: where
the month stands, what's genuinely behind and why, one action per problem
for tomorrow, what's working, and what tomorrow's floor needs. Every number
comes from the same functions the dashboard and agent use. Written from
sentence templates, so it needs no AI and no API credits. Paragraphs that
need data the store doesn't have (stock, visitor counts) are left out.
"""

import diagnosis
import kpi
import loyalty
import staffing
import stock
from store import WEEKDAY_NAMES, resolve

# The cause in a few words, to slot into "X is N% behind and ...".
CAUSE_CLAUSE = {
    "stockout": "sold out, though shoppers are still coming",
    "broken_size_run": "has a broken size run: plenty on the shelf, but not the sizes most people need",
    "traffic_drop": "getting fewer visitors than usual",
    "conversion_drop": "getting normal visitors but fewer buyers",
    "unclear": "slipping with no clear cause yet",
}


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _lower_first(text):
    return text[0].lower() + text[1:] if text else text


def _month_paragraph(store, day, hour, pace):
    sold = sum(p["units_sold_so_far"] for p in pace)
    expected = sum(p["expected_units_by_now"] for p in pace)
    target = sum(p["monthly_target"] for p in pace)
    projected = sum(p["projected_month_end_if_current_rate_continues"] for p in pace)
    pct = (sold - expected) / expected * 100
    standing = "right on pace" if abs(pct) < 3 else f"{abs(pct):.0f}% {'ahead' if pct > 0 else 'behind'}"
    weekday = WEEKDAY_NAMES[store.weekday(day)]
    moment = f"Close of day {day}" if hour == store.hours[-1] else f"Day {day} at {hour + 1}:00"
    return (f"{moment} ({weekday}), {store.days_in_month - day} days left. The store has sold "
            f"{sold:,} units against about {expected:,.0f} expected, {standing}; at the current rate it "
            f"would finish near {projected:,} of its {target:,} target.")


def _problems_paragraph(store, diagnoses, ideas):
    behind = sorted((d for d in diagnoses if d["status"] == "behind"), key=lambda d: d["pct_vs_pace"])
    if not behind:
        return "No category is clearly behind pace."
    explained = [d for d in behind if d["cause"] != "unclear"]
    unexplained = [d for d in behind if d["cause"] == "unclear"]
    sentences = []
    for d in explained:
        sentence = (f"{d['category']} is {abs(d['pct_vs_pace']):.0f}% behind and "
                    f"{CAUSE_CLAUSE[d['cause']]}. Tomorrow: {_lower_first(d['action'])}")
        idea = ideas.get(d["category"])
        if idea and idea.get("partner"):
            if idea["type"] == "substitute":
                sentence += f" Until then, steer shoppers to {idea['partner']}."
            elif idea["type"] == "complement_in_available_sizes":
                sentence += f" Meanwhile, sell the plentiful sizes as an outfit with {idea['partner']}."
        sentences.append(sentence)
    if unexplained:
        names = _join(f"{d['category']} ({d['pct_vs_pace']:+.0f}%)" for d in unexplained)
        verb = "is" if len(unexplained) == 1 else "are"
        also = " also" if explained else ""
        why = ("with no clear cause in the stock or visitor numbers"
               if store.has_stock or store.has_footfall
               else "and this data has no stock or visitor counts to show why")
        sentences.append(
            f"{names} {verb}{also} behind, {why}. "
            f"Tomorrow: check {'its' if len(unexplained) == 1 else 'their'} display and that every "
            f"size is out on the floor."
        )
    return " ".join(sentences)


def _wins_paragraph(store, day, hour, pace):
    ahead = sorted((p for p in pace if p["status"] == "ahead"), key=lambda p: -p["pct_vs_pace"])
    if not ahead:
        return None
    wins = []
    for p in ahead:
        win = f"{p['category']} ({p['pct_vs_pace']:+.0f}%)"
        if store.has_stock:
            cover = stock.get_days_of_cover(p["category"], day, hour, store=store)
            at_risk = [s["size"] for s in cover["sizes"] if s["likely_out_before_next_delivery"]]
            if at_risk:
                win += f", where size {_join(at_risk)} may run out before the next delivery"
        wins.append(win)
    return f"Going well: {'; '.join(wins)}."


def _tomorrow_paragraph(store, day):
    if day >= store.days_in_month or not store.has_visitor_hours:
        return None
    rec = staffing.get_staffing_recommendation(day + 1, store=store)
    split = rec["suggested_floor_split_at_busiest_hour_pct"]
    main_zone = max(split, key=split.get)
    main_peaks = next(z["peak_windows"] for z in rec["zones"] if z["zone"] == main_zone)
    if not main_peaks:
        return None
    first_peak = min(int(w.split(":")[0]) for z in rec["zones"] for w in z["peak_windows"])
    sentence = f"Tomorrow is {rec['weekday']}"
    if rec["notes"]:
        sentence += f", a delivery day: receive stock before the {first_peak}:00 peak"
    return (sentence + f". Put most floor cover in {main_zone} for {_join(main_peaks)}; at "
            f"{rec['store_busiest_hour']}:00 about {split[main_zone]}% of shoppers are there "
            f"(from the last {len(rec['based_on_days'])} {rec['day_type']}s).")


def _last_piece_paragraph(alerts):
    if not alerts:
        return None
    core = [f"{a['category']} in {a['size']}" for a in alerts if a["is_core_size"]]
    text = f"{len(alerts)} size{'s are' if len(alerts) > 1 else ' is'} down to the last piece"
    if core:
        text += f", including core size {_join(core)}"
    return text + ": check the backroom before opening."


def generate_digest(day=None, hour=None, store=None):
    """
    Returns the digest as a few short paragraphs of plain text. By default,
    as at the close of the store's latest day.
    """
    store, day, _ = resolve(store, day)
    if hour is None:
        hour = store.hours[-1]

    pace = kpi.get_category_pace(day, hour, store=store)
    diagnoses = diagnosis.diagnose_store(day, hour, store=store)
    ideas = {i["category"]: i for i in loyalty.get_cross_sell_ideas(day, hour, store=store)}
    drifting = [d["category"] for d in diagnoses if d["status"] == "drifting"]
    weak_lines = [t for t in kpi.get_today_pace(day=day, hour=hour, store=store)
                  if t["status"] == "behind"]

    paragraphs = [
        _month_paragraph(store, day, hour, pace),
        _problems_paragraph(store, diagnoses, ideas),
        f"Watch, but don't act yet: {_join(drifting)} {'is' if len(drifting) == 1 else 'are'} "
        f"slipping without a clear cause." if drifting else None,
        _wins_paragraph(store, day, hour, pace),
        "Today: " + _join(f"the {t['line']} line sold {t['units_sold_today']} of an expected "
                          f"{t['expected_by_now']:.0f}" for t in weak_lines) + "."
        if weak_lines else None,
        _tomorrow_paragraph(store, day),
        _last_piece_paragraph(stock.get_last_piece_alerts(day, hour, store=store))
        if store.has_stock else None,
    ]
    return "\n\n".join(p for p in paragraphs if p)


if __name__ == "__main__":
    text = generate_digest()
    print(text)
    print(f"\n({len(text.split())} words)")
