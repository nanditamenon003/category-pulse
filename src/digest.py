"""
End-of-day digest (Phase 5): what a manager reads in 30 seconds at close.

Plain prose, no tables, no jargon. It replaces the evening Excel pull: where
the month stands, what's genuinely behind and why, one action per problem
for tomorrow, what's working, and what tomorrow's floor needs. Every number
comes from the same functions the dashboard and agent use. Written from
sentence templates, so it needs no AI and no API credits.
"""

import diagnosis
import kpi
import loyalty
import staffing
import stock
from config import DAYS_IN_MONTH, STORE_HOURS, TODAY_DAY, month_calendar

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

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


def _month_paragraph(day, hour, pace):
    sold = sum(p["units_sold_so_far"] for p in pace)
    expected = sum(p["expected_units_by_now"] for p in pace)
    target = sum(p["monthly_target"] for p in pace)
    projected = sum(p["projected_month_end_if_current_rate_continues"] for p in pace)
    pct = (sold - expected) / expected * 100
    standing = "right on pace" if abs(pct) < 3 else f"{abs(pct):.0f}% {'ahead' if pct > 0 else 'behind'}"
    weekday = WEEKDAYS[month_calendar()[day - 1]["weekday"]]
    moment = f"Close of day {day}" if hour == STORE_HOURS[-1] else f"Day {day} at {hour + 1}:00"
    return (f"{moment} ({weekday}), {DAYS_IN_MONTH - day} days left. The store has sold "
            f"{sold:,} units against about {expected:,.0f} expected, {standing}; at the current rate it "
            f"would finish near {projected:,} of its {target:,} target.")


def _problems_paragraph(diagnoses, ideas):
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
        sentences.append(
            f"{names} {verb} also behind, with no clear cause in the stock or visitor numbers. "
            f"Tomorrow: check {'its' if len(unexplained) == 1 else 'their'} display and that every "
            f"size is out on the floor."
        )
    return " ".join(sentences)


def _wins_paragraph(day, hour, pace, stock_df, sales_df):
    ahead = sorted((p for p in pace if p["status"] == "ahead"), key=lambda p: -p["pct_vs_pace"])
    if not ahead:
        return None
    wins = []
    for p in ahead:
        win = f"{p['category']} ({p['pct_vs_pace']:+.0f}%)"
        cover = stock.get_days_of_cover(p["category"], day, hour, stock_df=stock_df, sales_df=sales_df)
        at_risk = [s["size"] for s in cover["sizes"] if s["likely_out_before_next_delivery"]]
        if at_risk:
            win += f", where size {_join(at_risk)} may run out before the next delivery"
        wins.append(win)
    return f"Going well: {'; '.join(wins)}."


def _tomorrow_paragraph(day, footfall_df):
    if day >= DAYS_IN_MONTH:
        return None
    rec = staffing.get_staffing_recommendation(day + 1, footfall_df=footfall_df)
    split = rec["suggested_floor_split_at_busiest_hour_pct"]
    main_zone = max(split, key=split.get)
    main_peaks = next(z["peak_windows"] for z in rec["zones"] if z["zone"] == main_zone)
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


def generate_digest(day=TODAY_DAY, hour=STORE_HOURS[-1], data=None):
    """Returns the digest as a few short paragraphs of plain text."""
    if data is None:
        data = diagnosis.load_all()
    sales_df, stock_df, footfall_df = data["sales_df"], data["stock_df"], data["footfall_df"]

    pace = kpi.get_category_pace(day, hour, sales_df=sales_df)
    diagnoses = diagnosis.diagnose_store(day, hour, data=data)
    ideas = {i["category"]: i for i in loyalty.get_cross_sell_ideas(
        day, hour, sales_df=sales_df, stock_df=stock_df)}
    drifting = [d["category"] for d in diagnoses if d["status"] == "drifting"]
    weak_lines = [t for t in kpi.get_today_pace(day=day, hour=hour, sales_df=sales_df)
                  if t["status"] == "behind"]

    paragraphs = [
        _month_paragraph(day, hour, pace),
        _problems_paragraph(diagnoses, ideas),
        f"Watch, but don't act yet: {_join(drifting)} are slipping without a clear cause."
        if drifting else None,
        _wins_paragraph(day, hour, pace, stock_df, sales_df),
        "Today: " + _join(f"the {t['line']} line sold {t['units_sold_today']} of an expected "
                          f"{t['expected_by_now']:.0f}" for t in weak_lines) + "."
        if weak_lines else None,
        _tomorrow_paragraph(day, footfall_df),
        _last_piece_paragraph(stock.get_last_piece_alerts(day, hour, stock_df=stock_df,
                                                          sales_df=sales_df)),
    ]
    return "\n\n".join(p for p in paragraphs if p)


if __name__ == "__main__":
    text = generate_digest()
    print(text)
    print(f"\n({len(text.split())} words)")
