"""
The pages and pop-ups of the Category Pulse web app.

Each page function draws one section of the site; app.py puts them in the
top menu. Pop-ups (st.dialog) sit on top of whatever page is open: the
category detail and the chat. Only one pop-up can be open at a time, so
"Ask the AI about this category" closes the detail and opens the chat.
"""

import hashlib
import json
import urllib.parse
import uuid

import altair as alt
import pandas as pd
import streamlit as st

import account
import agent
import smart_import
import storage
import tour
import upload
import usage
from config import QUESTIONS_PER_DAY
from forecast import CHANCE_PHRASE, GOAL_PHRASE, chance_words
from staffing import clock as staffing_clock
from staffing import friendly_window, people_per_floor
from store import WEEKDAY_NAMES, StoreDataError
from ui import (
    ACCENT,
    ACCENT_LINE,
    AMBER,
    BORDER,
    GREEN,
    MUTED,
    NEUTRAL,
    PAGE,
    RED,
    STATUS,
    TEXT,
    category_card,
    category_detail_at,
    cause_chip,
    clickable,
    contribution_at,
    inr,
    cross_sell_at,
    current_hour,
    current_store,
    diagnoses_at,
    digest_at,
    esc,
    grid,
    heading,
    html_block,
    kpi_card,
    last_pieces_at,
    month_end_at,
    month_path_at,
    plan_message_text,
    line_card,
    pace_at,
    pace_words,
    page_title,
    pill,
    playbook_for,
    requests_at,
    progress_bar,
    range_bar,
    request_forget,
    risky_sizes_at,
    slow_stock_at,
    slug,
    staffing_for,
    stock_health_at,
    time_label,
    today_at,
    tomorrow_plan,
    whatsapp_report_text,
    yardstick,
    zones_at,
)

# Short chip labels for the chat, mapped to the full question the AI receives.
SUGGESTED_QUESTIONS = {
    "Anything low on stock?": "Is anything low on stock?",
    "Staffing for tomorrow": "What should staffing look like tomorrow?",
    "Cross-sell ideas": "Any cross-sell ideas right now?",
    "Why is womenswear behind?": "Why is womenswear behind? Is it traffic or conversion?",
    "Trousers: who to target?": ("Men Casual Trousers are behind: which customers should we focus "
                                 "on, and with what offer?"),
}

STOCK_VERDICT = {
    "stockout": "Sold out: almost nothing left in any size.",
    "broken_size_run": "Broken size run: the shelf looks full, but the core sizes are gone.",
    "running_out": "Running out: core sizes gone and overall stock getting low.",
    "healthy": "Core sizes are in stock.",
}


def _not_in_data(what, add):
    """A plain note where a section needs data the store's upload didn't include."""
    html_block(f'<div class="cp-panel"><p><b>Not in your data:</b> {esc(what)}. To see this, '
               f'{esc(add)} on the Your data page.</p></div>')


# --- Welcome guide and empty pages ---------------------------------------------------------

WELCOME_STEPS = [
    ("Bring in your numbers",
     "Upload your sales as your till system exports them. Targets, stock and visitor counts are "
     "optional, and each one adds more."),
    ("See what needs action",
     "Every category gets a status against its monthly target, and the ones genuinely behind come "
     "first, so you know where to look."),
    ("Know why, and what to do",
     "Tap any category for the likely cause (sold out, missing sizes, fewer visitors or fewer "
     "buyers) and one clear next step."),
]


def _set_welcome_step(step):
    st.session_state["welcome_step"] = step


@st.dialog("Welcome to Category Pulse")
def welcome_guide():
    """A three-step introduction for someone signed in without data, once per visit."""
    step = st.session_state.setdefault("welcome_step", 0)
    title, text = WELCOME_STEPS[step]
    dots = "".join(f'<span class="cp-dot{" on" if i == step else ""}"></span>'
                   for i in range(len(WELCOME_STEPS)))
    html_block(f'<div class="cp-small">Step {step + 1} of {len(WELCOME_STEPS)}</div>'
               f'<div class="cp-welcome-title">{esc(title)}</div>'
               f'<div class="cp-tour-text">{esc(text)}</div><div class="cp-dots">{dots}</div>')
    last = step == len(WELCOME_STEPS) - 1
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        if step > 0:
            st.button("Back", key="welcome_back", on_click=_set_welcome_step, args=[step - 1])
        if last:
            if st.button("Take the 2-minute tour", key="welcome_tour", type="primary",
                         icon=":material/tour:"):
                tour.start()
        else:
            st.button("Next", key="welcome_next", type="primary", on_click=_set_welcome_step,
                      args=[step + 1])
        if st.button("Skip", key="welcome_skip", type="tertiary"):
            st.switch_page(tour.PAGES["Your data"])


def _no_data(page, what, detail):
    """
    For someone signed in who hasn't uploaded yet: the page says what will
    appear here, with the two ways forward. Returns True if it drew that.
    """
    if current_store() is not None:
        return False
    page_title(page)
    html_block(f'<div class="cp-empty"><div class="cp-empty-title">{esc(what)}</div>'
               f'<div class="cp-empty-text">{esc(detail)}</div></div>')
    with st.container(horizontal=True, gap="small", horizontal_alignment="center"):
        if st.button("Upload your data", key="empty_upload", type="primary",
                     icon=":material/upload_file:"):
            st.switch_page(tour.PAGES["Your data"])
        if st.button("Take the 2-minute tour", key="empty_tour", icon=":material/tour:"):
            tour.start()
    return True


# --- Today ---------------------------------------------------------------------------------

def privacy_promise():
    """What happens to a person's data, in plain words. Only what the app actually does."""
    if storage.is_configured():
        return ("Your data stays private. It's encrypted before it's saved, visible only to your "
                "account, never shared or sold, and you can delete it any time. The AI chat only sees "
                "your figures after you say yes.")
    return ("Your data stays private. It's used only while you're signed in on this visit, and it's "
            "never saved, shared or sold. The AI chat only sees your figures after you say yes.")


def _saved_line():
    """When the person's data was saved to their account, or a note if saving didn't work."""
    if st.session_state.get("save_note"):
        return st.session_state["save_note"]
    saved_at = st.session_state.get("saved_at")
    if saved_at:
        when = pd.Timestamp(saved_at).tz_convert("Asia/Kolkata").strftime("%d %b, %H:%M")
        return f"Saved privately to your account ({when}), so it's here next time you sign in."
    return ""


def _your_store_note(s):
    saved = _saved_line()
    html_block(
        '<div class="cp-panel"><div class="cp-panel-title">Your store</div>'
        f'<p>Showing your data for {esc(s.month_name)} {s.month_start.year}, as at the close of day '
        f'{s.today_day}. {esc(saved)} To replace or delete it, go to Your data.</p>'
        + "".join(f'<p class="cp-small">Note: {esc(w)}</p>' for w in s.warnings)
        + '</div>'
    )


def _start_here():
    # Hidden once dismissed, and while the tour runs (the tour card does its job).
    if st.session_state.get("hide_start_here") or st.session_state.get("tour_step") is not None:
        return
    s = current_store()
    html_block(
        '<div class="cp-panel"><div class="cp-panel-title">Start here</div>'
        f'<p>Category Pulse watches every category against its monthly target. It\'s day '
        f'{s.today_day} of {s.days_in_month}, and a few categories need attention.</p>'
        '<p><b>New here?</b> Take the 2-minute tour, or explore on your own: tap '
        '<b>Men Casual Trousers</b> below to see why it\'s behind, open <b>Ask Category Pulse</b> '
        '(bottom right) to question the data, or use the time button (top right) to rewind the '
        'day.</p></div>'
    )
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        if st.button("Take the tour", key="start_tour", icon=":material/tour:"):
            tour.start()
        if st.button("Got it, hide this", key="hide_start_btn", type="tertiary"):
            st.session_state["hide_start_here"] = True
            st.rerun()


def _problem_row(p, diag):
    s = current_store()
    tone = STATUS[p["status"]][1]
    evidence = diag["evidence"][1] if len(diag["evidence"]) > 1 and diag["cause"] in (
        "stockout", "broken_size_run") else diag["headline"]
    return (
        f'<div class="cp-row {tone}"><div style="flex:1;min-width:0">'
        f'{pill(p["status"])}{cause_chip(diag["cause"])}'
        f'<div class="cp-row-name">{esc(p["category"])}</div>'
        f'{progress_bar(p["units_sold_so_far"], p["monthly_target"], p["expected_units_by_now"])}'
        f'<div class="cp-small">{esc(s.amount(p["units_sold_so_far"]))} of {esc(s.amount(p["monthly_target"]))} '
        f'{esc(_yardstick_note(p))}· '
        f'{pace_words(p["pct_vs_pace"])} · '
        f'{esc(_needs_text(p))}</div>'
        f'<div class="cp-row-text">{esc(evidence)}</div>'
        f'</div><div class="cp-chev">&rsaquo;</div></div>'
    )


def _range_text(me, a):
    """'likely 3,150 to 3,230 · 20% chance of target' from a month-end range (or a plain note)."""
    if me["likely"] is None:
        return "too early for a range"
    text = f"likely {a(me['low'])} to {a(me['high'])}"
    if me["chance_of_target_pct"] is not None:
        text += f" · {me['chance_of_target_pct']}% {CHANCE_PHRASE[me['yardstick']]}"
    return text


def _share_block(message, key):
    """A message ready to copy (the copy button is top right), and a button to share it on WhatsApp."""
    st.code(message, language=None, wrap_lines=True)
    st.link_button("Share on WhatsApp", "https://wa.me/?text=" + urllib.parse.quote(message),
                   icon=":material/share:")


def _request_table(categories):
    return pd.DataFrame([
        {"Category": c["category"], "Size": r["size"], "On hand": r["units_on_hand"],
         "Sells per day": r["avg_per_day_when_in_stock"], "Request": r["request"],
         "Why": ("core size, sold out" if r["out_now"] and r["is_core_size"] else
                 "sold out" if r["out_now"] else
                 "won't last to the next delivery" if r["urgent"] else "top up")}
        for c in categories for r in c["sizes"]
    ])


def _yardstick_note(p):
    """'(last year) ' when a category isn't measured against its own target; '' when it is."""
    return "" if p.get("target_source") in (None, "target") else f"({yardstick(p)}) "


def _when_dropped(alert):
    """When a size hit its last piece: the hour if stock is counted hourly, else just 'today'."""
    if len(current_store().stock_hours_by_day.get(alert["day"], [])) > 1:
        return f"since {alert['hour']}:00-{alert['hour'] + 1}:00"
    return "by today's close"


def _needs_text(p):
    """What it takes from here, or that the month is over."""
    s = current_store()
    selling = s.amount(p["actual_units_per_day"], per_day=True)
    if p.get("month_finished"):
        return f"month finished, sold {selling}/day"
    if p["needed_units_per_day"] is None:
        return f"selling {selling}/day"
    return f"needs {s.amount(p['needed_units_per_day'], per_day=True)}/day, selling {selling}/day"


def _compact_row(tone, title, text):
    return (f'<div class="cp-row {tone}"><div style="flex:1;min-width:0">'
            f'<div class="cp-row-text"><b>{esc(title)}</b>: {esc(text)}</div></div>'
            f'<div class="cp-chev">&rsaquo;</div></div>')


def today_page():
    if _no_data("Today", "Your store at a glance will show here",
                "How the month is going against target, which categories need action and why, and "
                "how today is going. Upload your sales to start."):
        return
    hour = current_hour()
    s = current_store()
    page_title("Today", f"The store at {time_label(hour)} on day {s.today_day} of the month.")
    if s.is_demo:
        _start_here()
    else:
        _your_store_note(s)

    pace = pace_at(hour)
    by_category = {p["category"]: p for p in pace}
    diags = diagnoses_at(hour)
    lines_today = today_at(hour)

    # Totals compare like with like: only categories with something to judge them by.
    judged = [p for p in pace if p["monthly_target"]]
    sold_all = sum(p["units_sold_so_far"] for p in pace)
    sold = sum(p["units_sold_so_far"] for p in judged)
    expected = sum(p["expected_units_by_now"] for p in judged)
    target = sum(p["monthly_target"] for p in judged)
    projected = sum(p["projected_month_end_if_current_rate_continues"] for p in judged)
    behind = sorted((p for p in pace if p["status"] == "behind"), key=lambda p: p["pct_vs_pace"])
    drifting = sorted((p for p in pace if p["status"] == "drifting"), key=lambda p: p["pct_vs_pace"])
    lines_judged = [t for t in lines_today if t["expected_by_now"] is not None]
    today_sold = sum(t["units_sold_today"] for t in lines_judged)
    today_expected = sum(t["expected_by_now"] for t in lines_judged)
    a = s.amount

    if not judged:
        month_cards = (kpi_card("Month so far", a(sold_all), "no targets to judge by yet")
                       + kpi_card("Month-end at this rate", "-", "needs targets or 12 days of sales"))
    elif pace[0]["month_finished"]:
        month_cards = (kpi_card("Month so far", a(sold), f"of about {a(expected)} expected by now")
                       + kpi_card("Month result", a(sold), f"of the {a(target)} target"))
    else:
        path = month_path_at(hour)
        me = path["range"]
        if me["likely"] is None:
            end_card = kpi_card("Month-end at this rate", a(projected), f"target {a(target)} (a projection)")
        else:
            chance = me["chance_of_target_pct"]
            end_card = kpi_card(
                "Month-end at this rate", a(me["likely"]),
                f"likely {a(me['low'])} to {a(me['high'])}"
                + (f" · {chance}% {CHANCE_PHRASE[me['yardstick']]}" if chance is not None else ""),
                extra=range_bar(me, target))
        month_cards = kpi_card("Month so far", a(sold), f"of about {a(expected)} expected by now") + end_card
    html_block('<div class="cp-kpis">'
               + month_cards
               + kpi_card("Need action", f"{len(behind)} behind", f"{len(drifting)} to watch",
                          tone="red" if behind else "")
               + (kpi_card("Today so far", a(today_sold), f"of about {a(today_expected)} by now")
                  if lines_judged else
                  kpi_card("Today so far", a(sum(t["units_sold_today"] for t in lines_today)), ""))
               + "</div>")

    if s.today_day < s.days_in_month:
        with st.container(key="plan_link"):
            st.page_link(tour.PAGES["Plan"], label=f"Plan for {WEEKDAY_NAMES[s.weekday(s.today_day + 1)]} is "
                         f"ready: what to focus on, stock to request, busy hours", icon=":material/checklist:")

    if judged and not pace[0]["month_finished"]:
        path = month_path_at(hour)
        html_block(heading("The month so far", f"{s.measure_label} sold vs {PATH_LABEL[path['yardstick']].lower()}"))
        html_block('<div class="cp-legend">'
                   f'<span><i style="background:{ACCENT}"></i>Sold so far</span>'
                   f'<span><i style="background:repeating-linear-gradient(90deg,#8A8A84 0 5px,transparent 5px 9px)">'
                   f'</i>{PATH_LABEL[path["yardstick"]]}</span>'
                   f'<span><i style="background:{ACCENT_LINE};height:9px"></i>Likely finish (a projection)</span>'
                   '</div>')
        st.altair_chart(month_chart(path, s), width="stretch")

    html_block(heading("Needs action now", "tap a category for the full picture"))
    if not behind:
        html_block('<div class="cp-panel"><p>Nothing is clearly behind right now.</p></div>')
    for p in behind:
        if clickable(f"today_{slug(p['category'])}", _problem_row(p, diags[p["category"]]),
                     f"Open {p['category']}"):
            category_dialog(p["category"])

    behind_names = {p["category"] for p in behind}
    stock_rows = [(r["category"], r["verdict"]) for r in stock_health_at(hour)
                  if r["category"] not in behind_names] if s.has_stock else []
    core_pieces = [a for a in last_pieces_at(hour) if a["is_core_size"]] if s.has_stock else []
    if stock_rows or core_pieces:
        html_block(heading("Stock alerts"))
        for category, verdict in stock_rows:
            if clickable(f"stock_{slug(category)}",
                         _compact_row(STATUS[by_category[category]["status"]][1], category,
                                      STOCK_VERDICT[verdict]), f"Open {category}"):
                category_dialog(category)
        for a in core_pieces:
            text = f"last piece in {a['size']} (a core size), {_when_dropped(a)}"
            if clickable(f"piece_{slug(a['category'])}_{slug(a['size'])}",
                         _compact_row(STATUS[by_category[a["category"]]["status"]][1],
                                      a["category"], text), f"Open {a['category']}"):
                category_dialog(a["category"])

    if drifting:
        html_block(heading("Keep an eye on", "slipping, but could still be normal ups and downs"))
        for p in drifting:
            text = f"{pace_words(p['pct_vs_pace'])}, {_needs_text(p).split(',')[0]}"
            if clickable(f"drift_{slug(p['category'])}", _compact_row("amber", p["category"], text),
                         f"Open {p['category']}"):
                category_dialog(p["category"])

    html_block(heading("Today so far, by line", f"{s.measure_label} sold today / expected by now")
               + grid(line_card(t) for t in lines_today))


# --- Category detail pop-up -------------------------------------------------------------------

@st.dialog("Category details", width="large")
def category_dialog(category):
    hour = current_hour()
    detail = category_detail_at(category, hour)
    diag = diagnoses_at(hour)[category]
    p = detail["pace"]

    html_block(f'<div class="cp-title" style="margin-top:0">{esc(category)}</div>'
               f'<div>{pill(p["status"])}{cause_chip(diag["cause"])}</div>'
               f'<div class="cp-intro" style="margin-top:6px">{esc(diag["headline"])}</div>')

    why, sizes, shoppers, sell = st.tabs(["Why", "Stock by size", "Shoppers", "Sell"])
    with why:
        _why_tab(p, diag)
    with sizes:
        _stock_tab(category, detail)
    with shoppers:
        _shoppers_tab(detail)
    with sell:
        _sell_tab(category, detail, hour)

    if st.button("Ask the AI about this category", icon=":material/forum:",
                                             key="ask_about"):
        status_word = {"behind": "behind", "drifting": "slipping", "on_pace": "on track",
                       "ahead": "ahead"}.get(p["status"], "being tracked")
        st.session_state["chat_pending"] = (f"{category} is {status_word} this month. Why, and what "
                                            f"should the floor team do about it?")
        st.session_state["open_chat"] = True
        st.rerun()


def _why_tab(p, diag):
    s = current_store()
    a = s.amount
    if not p["monthly_target"]:
        html_block('<div class="cp-kpis">'
                   + kpi_card("Sold this month", a(p["units_sold_so_far"]), "no target to judge by yet")
                   + kpi_card("Selling per day", a(p["actual_units_per_day"], per_day=True), "")
                   + "</div>")
    else:
        html_block('<div class="cp-kpis">'
                   + kpi_card("Sold this month", a(p["units_sold_so_far"]), f"of {a(p['monthly_target'])} {yardstick(p)}")
                   + kpi_card("Expected by now", a(p["expected_units_by_now"]),
                              pace_words(p["pct_vs_pace"]))
                   + kpi_card("Selling per day", a(p["actual_units_per_day"], per_day=True),
                              "the month is over" if p["month_finished"] else
                              f"needs {a(p['needed_units_per_day'], per_day=True)}/day "
                              + {"last year": "to match last year",
                                 "own pace": "to keep its earlier pace"}.get(p["target_source"],
                                                                            "to hit target"))
                   + (kpi_card("Month result", a(p["units_sold_so_far"]), f"of {a(p['monthly_target'])} target")
                      if p["month_finished"] else
                      kpi_card("Month-end at this rate",
                               a(p["projected_month_end_if_current_rate_continues"]),
                               _range_text(me, a) if (me := month_end_at(p["as_of"]["hour"], p["category"]))
                               else "a projection, not a result"))
                   + "</div>"
                   + progress_bar(p["units_sold_so_far"], p["monthly_target"], p["expected_units_by_now"]))
        if not p["month_finished"] and me.get("caution"):
            html_block(f'<div class="cp-small">{esc(me["caution"])}</div>')

    if p["status"] in ("behind", "drifting"):
        html_block(heading("The evidence"))
        for line in diag["evidence"]:
            st.markdown(f"- {line}")
    action = diag["action"] or "No action needed: this category is on track."
    html_block(f'<div class="cp-do"><b>Do next:</b> {esc(action)}</div>')


def _size_chart(stock_status):
    core = set(stock_status["core_sizes"])
    rows = []
    for size, units in stock_status["remaining_by_size"].items():
        out_core = size in core and units == 0
        rows.append({
            "Size": f"{size} (core)" if size in core else size,
            "Units left": units,
            "Label": "out" if out_core else str(units),
            "Kind": "Core size, out" if out_core else ("Core size" if size in core else "Other size"),
        })
    df = pd.DataFrame(rows)
    color = alt.Color("Kind:N", legend=None, scale=alt.Scale(
        domain=["Core size, out", "Core size", "Other size"], range=[RED, TEXT, "#9A9A95"]))
    bars = alt.Chart(df).mark_bar(size=34).encode(
        x=alt.X("Size:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Units left:Q", title=None,
                scale=alt.Scale(domain=[0, max(1, int(df["Units left"].max()))]),
                axis=alt.Axis(grid=False, labels=False, ticks=False, domain=False)),
        color=color, tooltip=["Size", "Units left"],
    )
    # An empty core size has no bar to see, so its label says "out" in red.
    labels = bars.mark_text(dy=-8, fontSize=12, fontWeight="bold").encode(
        text="Label:N",
        color=alt.condition(alt.datum.Kind == "Core size, out", alt.value(RED), alt.value(TEXT)))
    return (bars + labels).properties(height=170).configure_view(stroke=None).configure_axis(
        labelFont="Inter", labelFontSize=12, labelColor=TEXT, domainColor=BORDER, tickColor=BORDER,
    ).configure(background="#FFFFFF")


def _stock_tab(category, detail):
    if detail["stock"] is None:
        _not_in_data("stock counts", "upload a stock report")
        return
    s = current_store()
    health, status, history, cover = detail["health"], detail["stock"], detail["history"], detail["cover"]
    usual = (f", {health['total_as_pct_of_usual']}% of its usual level"
             if health["total_as_pct_of_usual"] is not None else "")
    counted = status["stock_counted_at"]
    when = (f" (counted on day {counted['day']})"
            if counted and counted["day"] != status["as_of"]["day"] else "")
    html_block(f'<div class="cp-row-text"><b>{esc(STOCK_VERDICT[health["verdict"]])}</b> '
               f'{status["total_remaining"]} units on the shelf{usual}{when}.</div>')
    if s.has_sizes:
        st.altair_chart(_size_chart(status), width="stretch", theme=None)

    risky = [s for s in cover["sizes"] if s["likely_out_before_next_delivery"]]
    if risky:
        names = ", ".join(s["size"] for s in risky)
        html_block(f'<div class="cp-small">Projection: size {esc(names)} will likely run out before '
                   f'the next delivery (day {cover["next_scheduled_delivery_day"]}).</div>')

    html_block(heading("Deliveries this month"))
    if not s.has_stock_history:
        html_block('<div class="cp-small">Stock was counted on only one day, so deliveries can\'t be '
                   'worked out. A count at the close of every day shows them.</div>')
        return
    events = [(d, f"**Day {d}: scheduled delivery never arrived**")
              for d in history["scheduled_deliveries_not_received"]]
    for d in history["deliveries_received"]:
        sizes = ", ".join(f"{s}: {u}" for s, u in d["by_size"].items() if u > 0)
        missing = (f" · no {' or '.join(d['core_sizes_missing'])}" if d["core_sizes_missing"] else "")
        events.append((d["day"], f"Day {d['day']}: {d['units_received']} units ({sizes}){missing}"))
    for _, text in sorted(events):
        st.markdown(f"- {text}")
    if not events:
        st.markdown("- No deliveries recorded this month.")


def _shoppers_tab(detail):
    c = detail["conversion"]
    if c is None:
        _not_in_data("visitor and bill counts", "upload visitor counts, with bill numbers in your sales")
        return
    recent, baseline = c["recent_last_3_days_and_today"], c["baseline_earlier_this_month"]
    change = c["visitors_change_vs_typical_pct"]
    html_block('<div class="cp-kpis">'
               + kpi_card(f"Visitors to {c['zone']}", f"{recent['zone_visitors']:,}",
                          f"last 3 days and today: {_versus_usual(change) or 'nothing to compare yet'}")
               + kpi_card("Bought from this category", _per_visitors(recent["conversion_rate_pct"]),
                          f"of the floor's visitors (earlier: {_per_visitors(baseline['conversion_rate_pct'])})")
               + kpi_card("Pieces per bill", f"{recent['units_per_transaction'] or 0}",
                          f"earlier this month: {baseline['units_per_transaction'] or 0}")
               + "</div>")
    html_block(_reading_row(c["subject"], c, numbers=False))


def _sell_tab(category, detail, hour):
    idea = cross_sell_at(hour).get(category)
    if idea:
        if idea.get("supply_action"):
            html_block(f'<div class="cp-do"><b>First:</b> {esc(idea["supply_action"])}</div>')
        html_block(f'<div class="cp-do"><b>At the till:</b> {esc(idea["at_the_till"])}</div>')
    else:
        html_block('<div class="cp-small">This category isn\'t behind, so there\'s no cross-sell '
                   'push for it. Here is how each loyalty tier responds, if you want one.</div>')

    html_block(heading("Loyalty tiers", "best responders first"))
    if detail["playbook"] is None:
        _not_in_data("loyalty tier figures", "upload loyalty tier figures")
    else:
        _tier_table(detail["playbook"])


def _tier_table(playbook):
    table = pd.DataFrame([
        {
            "Customer": t["tier"],
            "Say yes to an extra item": f"{t['cross_sell_response_rate_pct']:.0f} in 100",
            "Share of bills": f"{t['share_of_transactions_pct']}%",
            "Offer at the till": t["offer_at_the_till"],
        }
        for t in playbook["tiers_ranked_by_response"]
    ])
    st.dataframe(table, hide_index=True, width="stretch")
    html_block('<div class="cp-small">By loyalty tier only (Platinum, Gold, Silver, not a member): no '
               'individual customer\'s data is used, by design.</div>')


# --- Other pages ------------------------------------------------------------------------------

PATH_LABEL = {"target": "Path to target", "last year": "Last year's month",
              "own pace": "The month's early pace"}


def month_chart(path, s):
    """Running sales so far, the path to target, and a band for where the month is likely to land."""
    sold, plan, me = path["sold"], path["plan"], path["range"]
    today, end = len(sold), len(plan)
    x = alt.X("day:Q", title=None, scale=alt.Scale(domain=[1, end], nice=False),
              axis=alt.Axis(values=[1, 8, 15, 22, 29], labelExpr="'day ' + datum.value", grid=False))
    if s.measure == "value":
        y_axis = alt.Axis(title=None, labelExpr="datum.value >= 1e7 ? '₹' + format(datum.value / 1e7, '.1f') + ' Cr' : "
                                                "datum.value >= 1e5 ? '₹' + format(datum.value / 1e5, '.0f') + ' L' : "
                                                "'₹' + format(datum.value, ',')", tickCount=4)
    else:
        y_axis = alt.Axis(title=None, labelExpr="format(datum.value, ',')", tickCount=4)
    y = alt.Y("value:Q", axis=y_axis)
    tooltip = [alt.Tooltip("day:Q", title="Day"), alt.Tooltip("shown:N", title="")]
    layers = []
    if me["likely"] is not None:
        band = pd.DataFrame({"day": [today, end], "low": [sold[-1], me["low"]], "high": [sold[-1], me["high"]]})
        layers.append(alt.Chart(band).mark_area(color=ACCENT_LINE, opacity=0.9).encode(
            x=x, y=alt.Y("low:Q", axis=y_axis), y2="high:Q"))
    plan_df = pd.DataFrame({"day": range(1, end + 1), "value": plan,
                            "shown": [f"{PATH_LABEL[path['yardstick']]}: {s.amount(v)}" for v in plan]})
    sold_df = pd.DataFrame({"day": range(1, today + 1), "value": sold,
                            "shown": [f"Sold so far: {s.amount(v)}" for v in sold]})
    layers.append(alt.Chart(plan_df).mark_line(color="#8A8A84", strokeDash=[6, 5], strokeWidth=2).encode(
        x=x, y=y, tooltip=tooltip))
    layers.append(alt.Chart(sold_df).mark_line(color=ACCENT, strokeWidth=3.5).encode(x=x, y=y, tooltip=tooltip))
    now = sold_df.tail(1).assign(label=f"Today: {s.amount(sold[-1])}")
    layers.append(alt.Chart(now).mark_point(filled=True, size=90, color=ACCENT, stroke="#FFFFFF", strokeWidth=2,
                                            opacity=1).encode(x=x, y=y))
    layers.append(alt.Chart(now).mark_text(align="right", dx=-10, dy=-12, fontSize=13, fontWeight="bold",
                                           color=TEXT).encode(x=x, y=y, text="label:N"))
    goal = pd.DataFrame({"day": [end], "value": [path["target"]],
                         "label": [f"{'Target' if path['yardstick'] == 'target' else 'Goal'} {s.amount(path['target'])}"]})
    layers.append(alt.Chart(goal).mark_point(filled=True, size=40, color=TEXT, opacity=1).encode(x=x, y=y))
    layers.append(alt.Chart(goal).mark_text(align="right", dx=-4, dy=-12, fontSize=12, color=TEXT).encode(
        x=x, y=y, text="label:N"))
    return alt.layer(*layers).properties(
        height=230, padding={"left": 8, "right": 12, "top": 16, "bottom": 4}
    ).configure_view(stroke=None, strokeWidth=0).configure_axis(
        labelFont="Inter", labelFontSize=11, labelColor=MUTED, domainColor=BORDER, tickColor=BORDER,
        gridColor="#EEEDE9",
    ).configure(background=PAGE)


def pace_chart(pace):
    rows = []
    for p in pace:
        if not p["monthly_target"]:
            continue
        expected = p["expected_units_by_now"]
        rows.append({
            "Category": p["category"],
            "Percent of expected": round(p["units_sold_so_far"] / expected * 100) if expected else 0,
            "Status": STATUS[p["status"]][0],
            "Sold": p["units_sold_so_far"],
            "Expected by now": round(expected),
        })
    df = pd.DataFrame(rows).sort_values("Percent of expected")
    order = list(df["Category"])

    color = alt.Color("Status:N", legend=None, scale=alt.Scale(
        domain=["Behind", "Watch", "On track", "Ahead", "Too early", "No target"],
        range=[RED, AMBER, GREEN, GREEN, NEUTRAL, NEUTRAL]))
    y = alt.Y("Category:N", sort=order, title=None,
              axis=alt.Axis(labelLimit=180, labelOverlap=False, grid=False, ticks=False))
    bars = alt.Chart(df).mark_bar(height=14).encode(
        x=alt.X("Percent of expected:Q", title="Sold as % of where it should be by now (100 = on track)",
                axis=alt.Axis(grid=False)),
        y=y, color=color,
        tooltip=["Category", "Status", "Sold", "Expected by now", "Percent of expected"],
    )
    labels = bars.mark_text(align="left", dx=4, fontSize=11, color=TEXT).encode(
        text=alt.Text("Percent of expected:Q", format=".0f"), color=alt.value(TEXT))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(color=TEXT).encode(x="x:Q")

    return (bars + labels + rule).properties(
        height=22 * len(df), padding={"left": 40, "right": 12, "top": 4, "bottom": 4}
    ).configure_view(stroke=None, strokeWidth=0).configure_axis(
        grid=False, labelFont="Inter", titleFont="Inter", labelFontSize=11, titleFontSize=11,
        labelColor=TEXT, titleColor=MUTED, domainColor=BORDER, tickColor=BORDER,
    ).configure(background=PAGE)


STATUS_FILTER = {"Behind": "behind", "Watch": "drifting", "On track": "on_pace",
                 "Ahead": "ahead", "Too early": "too_early", "No target": "no_target"}
SORT_ORDERS = {
    "By line": None,
    "Worst first": lambda p: p["pct_vs_pace"],
    "Best first": lambda p: -p["pct_vs_pace"],
    "Biggest target": lambda p: -(p["monthly_target"] or 0),
}


def _card_row(cards, key, diags):
    """A wrapping row of clickable category cards; clicking one opens its detail."""
    with st.container(horizontal=True, wrap=True, gap="small", key=f"cards_{key}"):
        for p, show_line in cards:
            markup = category_card(p, show_line=show_line, cause=diags[p["category"]]["cause"])
            if clickable(f"cat_{slug(p['category'])}", markup, f"Open {p['category']}"):
                category_dialog(p["category"])


def _category_table(shown, filters_key):
    rows = pd.DataFrame([
        {
            "Status": STATUS[p["status"]][0],
            "Category": p["category"],
            "Sold": p["units_sold_so_far"],
            "Target": p["monthly_target"],
            "Progress": (round(p["units_sold_so_far"] / p["monthly_target"] * 100)
                         if p["monthly_target"] else None),
            "Ahead / behind": p["pct_vs_pace"],
            "Selling per day": p["actual_units_per_day"],
            "Needs per day": p["needed_units_per_day"],
            "Month-end (likely)": p["projected_month_end_if_current_rate_continues"],
        }
        for p in shown
    ])
    status_colour = {"Behind": RED, "Watch": "#8A6300", "On track": GREEN, "Ahead": GREEN}
    styled = rows.style.map(
        lambda v: f"color: {status_colour.get(v, TEXT)}; font-weight: 700", subset=["Status"])
    # A new key per filter combination, so a ticked row doesn't carry over to a
    # different category once the filters change.
    table_key = f"category_table_{filters_key}"
    event = st.dataframe(
        styled, hide_index=True, width="stretch", key=table_key,
        on_select="rerun", selection_mode="single-row",
        column_config={
            # Neutral, like the card progress bars: indigo is kept for things you can click.
            "Progress": st.column_config.ProgressColumn(
                "Sold vs target", min_value=0, max_value=100, format="%d%%", color=TEXT),
            "Ahead / behind": st.column_config.NumberColumn(
                "Ahead / behind", format="%+.0f%%",
                help="How far ahead of (+) or behind (-) where it should be by now."),
            "Selling per day": st.column_config.NumberColumn(format="%.1f"),
            "Needs per day": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    # Open the detail when a new row is picked. A picked row stays selected, so
    # remember it; otherwise the pop-up would reopen on every later click.
    picked = event.selection.rows[0] if event.selection.rows else None
    last_pick_key = f"last_pick_{table_key}"
    if picked is not None and picked != st.session_state.get(last_pick_key):
        st.session_state[last_pick_key] = picked
        category_dialog(rows.iloc[picked]["Category"])
    elif picked is None:
        st.session_state[last_pick_key] = None


def categories_page():
    if _no_data("Categories", "Every category will show here",
                "How every category is doing this month against its target, as cards, a table or a chart."):
        return
    hour = current_hour()
    page_title("Categories", "How every category is doing this month against its target. Tap a card "
                             "or a row for the full picture.")
    pace = pace_at(hour)
    diags = diagnoses_at(hour)

    # Filters live in a compact "Filters" button so they don't push the cards down on a
    # phone. Its label counts the filters that are on, read from the previous run.
    active = sum([
        (st.session_state.get("cat_department") or "All") != "All",
        bool(st.session_state.get("cat_status")),
        st.session_state.get("cat_sort", "By line") != "By line",
    ])
    with st.container(horizontal=True, wrap=True, vertical_alignment="center", gap="small"):
        with st.popover(f"Filters · {active} on" if active else "Filters", icon=":material/tune:"):
            department = st.segmented_control("Floor", ["All"] + current_store().departments,
                                              default="All",
                                              key="cat_department") or "All"
            statuses = st.pills("Status", list(STATUS_FILTER), selection_mode="multi",
                                key="cat_status")
            sort = st.selectbox("Sort", list(SORT_ORDERS), key="cat_sort")
        view = st.segmented_control("View", ["Cards", "Table", "Chart"], default="Cards",
                                    key="cat_view", label_visibility="collapsed") or "Cards"

    wanted = {STATUS_FILTER[s] for s in statuses} if statuses else set(STATUS_FILTER.values())
    shown = [p for p in pace
             if p["status"] in wanted and (department == "All" or p["department"] == department)]
    if SORT_ORDERS[sort]:
        shown = sorted(shown, key=SORT_ORDERS[sort])
    html_block(f'<div class="cp-showing">Showing {len(shown)} of {len(pace)} categories</div>')

    if not shown:
        html_block('<div class="cp-panel"><p>No categories match these filters.</p></div>')
        return

    if view == "Table":
        _category_table(shown, slug(f"{department}-{'-'.join(sorted(statuses or []))}-{sort}"))
    elif view == "Chart":
        # theme=None so Streamlit's default chart styling (gridlines etc.) doesn't override ours.
        st.altair_chart(pace_chart(shown), width="stretch", theme=None)
    elif sort == "By line":
        for line, dept in current_store().lines.items():
            in_line = [(p, False) for p in shown if p["line"] == line]
            if in_line:
                html_block(heading(line, dept if dept != line else ""))
                _card_row(in_line, line, diags)
    else:
        _card_row([(p, True) for p in shown], "sorted", diags)


# --- Stock page ---------------------------------------------------------------------------------

def _stock_problem_text(r):
    core = " or ".join(r["core_sizes"])
    if r["verdict"] == "broken_size_run":
        usual = f" ({r['total_as_pct_of_usual']}% of usual)" if r["total_as_pct_of_usual"] is not None else ""
        return f"broken size run. No {core}, yet {r['total_remaining']} units sit on the shelf{usual}."
    if r["verdict"] == "stockout":
        return ("sold out, nothing left in any size." if r["total_remaining"] == 0
                else f"almost sold out, {r['total_remaining']} units left.")
    return f"running out: no {core}, and only {r['total_remaining']} units left overall."


def stock_page():
    if _no_data("Stock", "Stock problems will show here",
                "Sold-out sizes, broken size runs, last pieces, sizes at risk of running out, and "
                "slow stock. Include "
                "stock counts when you upload your sales."):
        return
    hour = current_hour()
    page_title("Stock", "What's on the shelf, what's missing, and what's about to run out.")
    s = current_store()
    if not s.has_stock:
        _not_in_data("stock counts", "upload a stock report with units on hand by category and size")
        return
    pace = {p["category"]: p for p in pace_at(hour)}
    problems = stock_health_at(hour)
    pieces = last_pieces_at(hour)
    risky, next_delivery = risky_sizes_at(hour)
    delivery_weekday = WEEKDAY_NAMES[s.weekday(next_delivery)] if next_delivery else ""
    no_schedule = s.delivery_weekday is None

    html_block('<div class="cp-kpis">'
               + kpi_card("Stock problems", f"{len(problems)}", "stockouts and broken size runs",
                          tone="red" if problems else "")
               + kpi_card("Last pieces today", f"{len(pieces)}",
                          f"{sum(a['is_core_size'] for a in pieces)} in core sizes")
               + kpi_card("At risk of running out", f"{len(risky)}",
                          f"{'size' if len(risky) == 1 else 'sizes'}, before the next delivery")
               + kpi_card("Next delivery", "Not set" if no_schedule else
                          (f"Day {next_delivery}" if next_delivery else "None this month"),
                          "choose one on Your data" if no_schedule else delivery_weekday)
               + "</div>")

    html_block(heading("Stock problems", "tap for sizes and deliveries"))
    if not problems:
        html_block('<div class="cp-panel"><p>No stockouts or broken size runs right now.</p></div>')
    for r in problems:
        tone = STATUS[pace[r["category"]]["status"]][1]
        if clickable(f"stk_{slug(r['category'])}", _compact_row(tone, r["category"], _stock_problem_text(r)),
                     f"Open {r['category']}"):
            category_dialog(r["category"])

    requests = requests_at(hour)
    html_block(heading("What to request", requests["basis"]))
    urgent = [c for c in requests["categories"] if c["core_sizes_out"] or c["urgent_sizes"]]
    if not requests["categories"]:
        html_block('<div class="cp-panel"><p>Every size has enough stock for now.</p></div>')
    else:
        if urgent:
            html_block('<div class="cp-small">Most urgent first: core sizes already sold out, then sizes '
                       'that won\'t last until the next delivery (those need a transfer from a nearby '
                       'store, not just an order).</div>')
            st.dataframe(_request_table(urgent), hide_index=True, width="stretch")
        with st.expander(f"Everything to top up ({len(requests['categories'])} categories)"):
            everything = _request_table(requests["categories"])
            st.dataframe(everything, hide_index=True, width="stretch")
            st.download_button("Download the request (CSV)", data=everything.to_csv(index=False),
                               file_name=f"category-pulse-request-day{s.today_day}.csv", mime="text/csv",
                               icon=":material/download:")
        html_block(f'<div class="cp-small">{esc(requests["note"])}</div>')

    html_block(heading("Last pieces today", "in the order they happened"))
    if not pieces:
        html_block('<div class="cp-panel"><p>No size has dropped to its last piece today.</p></div>')
    for a in pieces:
        kind = "a core size" if a["is_core_size"] else "not a core size"
        text = f"last one left in {a['size']} ({kind}), {_when_dropped(a)}"
        tone = STATUS[pace[a["category"]]["status"]][1]
        if clickable(f"lp_{slug(a['category'])}_{slug(a['size'])}", _compact_row(tone, a["category"], text),
                     f"Open {a['category']}"):
            category_dialog(a["category"])

    _slow_stock_section(s, hour)

    html_block(heading("At risk of running out before the next delivery",
                       "a watch list, from each size's selling rate over the last 7 days"))
    if no_schedule:
        _not_in_data("the weekday deliveries arrive", "choose a delivery day")
    elif not risky:
        html_block('<div class="cp-panel"><p>No size looks at risk of running out before the next '
                   'delivery.</p></div>')
    else:
        html_block('<div class="cp-small" style="margin-bottom:6px">Not a certainty: a size sells a few '
                   'pieces a week, so most of these will last. But they\'re far likelier to sell out than '
                   'other sizes, so keep them out on the floor and on the request.</div>')
        st.dataframe(pd.DataFrame([
            {
                "Category": r["category"],
                "Size": r["size"],
                "Left": r["units_remaining"],
                "Sells per day": r["avg_units_sold_per_day_last_7_days"],
                "Days it will last": r["projected_days_of_cover"],
            }
            for r in risky
        ]), hide_index=True, width="stretch")


def _slow_stock_row(item, s):
    """One slow-stock item: what's not selling, the money in it, and what to do."""
    value = f" · {inr(item['value_tied_up'])} tied up" if item["value_tied_up"] else ""
    what = ("the whole category" if item["whole_category"]
            else f"size{'s' if len(item['sizes_not_selling']) > 1 else ''} {', '.join(item['sizes_not_selling'])}")
    return (f'<div class="cp-row" style="margin-bottom:8px"><div style="flex:1;min-width:0">'
            f'<span class="cp-chip" style="margin-left:0">Slow</span>'
            f'<div class="cp-row-name">{esc(item["category"])} '
            f'<span class="cp-small">{esc(what)}, {item["units"]} pieces{esc(value)}</span></div>'
            f'<div class="cp-row-text">{esc(item["suggestion"])}</div></div>'
            f'<div class="cp-chev">&rsaquo;</div></div>')


def _slow_stock_section(s, hour):
    report = slow_stock_at(hour)
    html_block(heading("Slow stock", "not selling: money tied up, most first"))
    if not report["enough_history"]:
        html_block('<div class="cp-panel"><p>Slow stock shows once there are two weeks of sales to '
                   'judge by.</p></div>')
        return
    if not report["slow"]:
        html_block('<div class="cp-panel"><p>Nothing is sitting unsold: every category is selling '
                   'through at a normal rate.</p></div>')
        return
    total = sum(i["value_tied_up"] or 0 for i in report["slow"])
    if total:
        html_block(f'<div class="cp-small" style="margin-bottom:6px">{inr(total)} of stock isn\'t '
                   f'selling. {esc(report["note"])}</div>')
    for item in report["slow"]:
        if clickable(f"slow_{slug(item['category'])}", _slow_stock_row(item, s), f"Open {item['category']}"):
            category_dialog(item["category"])


# --- Floor and staff page ---------------------------------------------------------------------

# Visitors vs buyers, as it would be said on the floor: a short label (with its
# colour), what's happening, and what to check.
READING = {
    "normal": ("Normal", "green", "People are coming in and buying as usual.", None),
    "possible_dip": ("Watch", "amber", "A little lower than usual, but it could still be normal ups "
                     "and downs.", "Keep an eye on it; nothing to change yet."),
    "conversion_problem": ("Fewer buying", "red", "People are coming in as usual, but fewer of them "
                           "are buying.",
                           "Check the shelves: are the popular sizes out? Is someone free to help "
                           "and at the fitting room? Are prices and offers clear?"),
    "traffic_problem": ("Fewer visitors", "red", "Fewer people than usual are coming to this floor, "
                        "but those who come still buy as usual.",
                        "Make the entrance and displays more inviting, and tell the manager: it "
                        "may need marketing or a window change."),
    "traffic_and_conversion_down": ("Fewer of both", "red", "Fewer people are coming in, and fewer "
                                    "of those are buying.",
                                    "Check the shelves and sizes first, then the entrance and "
                                    "displays."),
    "too_few_sales_to_judge": ("Too early", "neutral", "Too few sales lately to tell.", None),
}


def _per_visitors(pct):
    """A share of visitors as people say it: '11 in every 100' (or 'in every 1,000' when small)."""
    if pct is None:
        return "none"
    if pct >= 5:
        return f"{pct:.0f} in every 100"
    return f"{pct * 10:.0f} in every 1,000"


def _versus_usual(pct):
    """'+18' -> '18% busier than usual'; small changes are 'about usual'."""
    if pct is None:
        return None
    if abs(pct) < 8:
        return "about usual"
    return f"{abs(pct):.0f}% {'busier' if pct > 0 else 'quieter'} than usual"


def _hour_chart(pattern):
    peaks = set()
    for window in pattern["peak_windows"]:
        start, end = (int(t.split(":")[0]) for t in window.split("-"))
        peaks.update(range(start, end))
    df = pd.DataFrame([
        {"Hour": f"{h % 12 or 12}{'a' if h < 12 else 'p'}", "Time": staffing_clock(h), "Visitors": v,
         "Peak": "Busy" if h in peaks else "Other"}
        for h, v in pattern["avg_visitors_by_hour"].items()
    ])
    return alt.Chart(df).mark_bar(size=14, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("Hour:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Visitors:Q", title=None, axis=alt.Axis(grid=False, labels=False, ticks=False,
                                                        domain=False)),
        color=alt.Color("Peak:N", legend=None,
                        scale=alt.Scale(domain=["Busy", "Other"], range=[ACCENT, "#D5D4CF"])),
        tooltip=["Time", alt.Tooltip("Visitors:Q", format=".0f", title="Usual visitors")],
    ).properties(height=120).configure_view(stroke=None).configure_axis(
        labelFont="Inter", labelFontSize=10, labelColor=MUTED, domainColor=BORDER, tickColor=BORDER,
    ).configure(background="#FFFFFF")


def floor_page():
    if _no_data("Floor and staff", "Visitors, buyers and tomorrow's rota will show here",
                "Whether fewer people are coming in or fewer are buying, and where to put the team "
                "tomorrow. Include visitor counts when you upload."):
        return
    hour = current_hour()
    page_title("Floor and staff", "Who's coming in, whether they're buying, and where to put the team "
                                  "tomorrow.")
    s = current_store()
    if not s.has_footfall:
        _not_in_data("visitor counts", "upload visitor counts per floor, by day or hour")
        return
    zones = zones_at(hour)
    kind = "a weekend or sale day" if s.is_busy(s.today_day) else "a normal weekday"
    moment = f"by {staffing_clock(hour + 1)}" if s.hourly and hour < s.hours[-1] else "today"

    def usual(f):
        if f["typical_visitors_by_this_hour"] is None:
            return "no earlier days like today to compare with yet"
        verdict = _versus_usual(f["pct_vs_typical"])
        return f"{verdict} (usually about {f['typical_visitors_by_this_hour']:.0f} {moment})"

    html_block(heading("Visitors today", f"{moment}, compared with {kind} earlier this month")
               + '<div class="cp-kpis">'
               + "".join(kpi_card(z["footfall"]["zone"], f"{z['footfall']['visitors_today_so_far']:,}",
                                  usual(z["footfall"]))
                         for z in zones)
               + "</div>")

    if not s.has_transactions:
        _not_in_data("bill counts, needed to tell fewer visitors from fewer buyers",
                     "include bill numbers or bill counts in your sales")
    else:
        _visitors_vs_buyers(zones)

    if not s.has_visitor_hours:
        _not_in_data("visitors by hour, needed for tomorrow's busy hours",
                     "upload visitor counts by hour")
        return
    if s.today_day >= s.days_in_month:
        return
    _tomorrow(s)


def _reading_row(zone_or_name, c, numbers=True):
    """One floor's (or category's) visitors-vs-buyers reading, in plain words."""
    label, tone, what, check = READING[c["reading"]]
    recent, before = c["recent_last_3_days_and_today"], c["baseline_earlier_this_month"]
    numbers = (f"{_per_visitors(recent['conversion_rate_pct'])} visitors bought "
               f"(earlier this month: {_per_visitors(before['conversion_rate_pct'])})." if numbers else "")
    do = f'<div class="cp-row-text"><b>What to do:</b> {esc(check)}</div>' if check else ""
    return (f'<div class="cp-row {tone}" style="margin-bottom:8px"><div style="flex:1;min-width:0">'
            f'<span class="cp-pill {tone}">{esc(label)}</span>'
            f'<div class="cp-row-name">{esc(zone_or_name)}</div>'
            f'<div class="cp-row-text">{esc(what)} {esc(numbers)}</div>{do}</div></div>')


def _visitors_vs_buyers(zones):
    html_block(heading("Are they buying?", "the last 3 days and today, compared with earlier this month")
               + "".join(_reading_row(z["conversion"]["zone"], z["conversion"]) for z in zones))


def _tomorrow(s):
    rec, patterns = staffing_for(s.today_day + 1)
    busy = "a weekend or sale day" if rec["day_type"] != "weekday" else "a weekday"
    html_block(heading(f"Tomorrow, {rec['weekday']}: when it gets busy",
                       f"from the last {len(rec['based_on_days'])} days like it ({busy})"))
    for note in rec["notes"]:
        html_block(f'<div class="cp-do">{esc(note)}</div>')

    columns = st.columns(len(patterns))
    for column, (zone, pattern) in zip(columns, patterns.items()):
        with column:
            windows = ", ".join(friendly_window(w) for w in pattern["peak_windows"]) or "no clear busy time"
            rough = "" if pattern["reliability"] == "good" else " (few visitors, so a rough guide)"
            html_block(f'<div class="cp-row-name">{esc(zone)}</div>'
                       f'<div class="cp-small">Busiest {esc(windows)}{esc(rough)}</div>')
            st.altair_chart(_hour_chart(pattern), width="stretch", theme=None)

    busiest = staffing_clock(rec["store_busiest_hour"])
    html_block(heading(f"Where to put people at {busiest}", "the busiest hour of the day"))
    people = st.number_input("How many people will be on the floor then?", min_value=1, max_value=60,
                             value=st.session_state.get("floor_people", 6), step=1, key="floor_people")
    split = rec["suggested_floor_split_at_busiest_hour_pct"]
    counts = people_per_floor(split, int(people))
    bars = "".join(
        f'<div class="cp-row-text" style="margin-top:8px"><b>{esc(zone)}: {counts[zone]} '
        f'{"person" if counts[zone] == 1 else "people"}</b> '
        f'<span class="cp-small">({pct}% of shoppers are here)</span></div>'
        f'<div class="cp-track"><div class="cp-fill" style="width:{pct}%"></div></div>'
        for zone, pct in split.items())
    html_block(f'<div class="cp-panel">{bars}</div>')
    with st.expander("Which past days is this based on?"):
        st.write(", ".join(rec["based_on_days"]))


# --- Sell page --------------------------------------------------------------------------------------

def sell_page():
    if _no_data("Sell", "What to say at the till will show here",
                "For each category that's behind: what to suggest with it, and the offer each kind "
                "of customer is likeliest to say yes to."):
        return
    hour = current_hour()
    page_title("Sell", "What to say at the till to help the categories that are behind, and which "
                       "offer each kind of customer likes best.")
    ideas = cross_sell_at(hour)

    html_block(heading("Help these categories today", "only the ones really behind this month"))
    if not ideas:
        html_block('<div class="cp-panel"><p>No category is behind this month, so there\'s nothing '
                   'extra to push at the till right now.</p></div>')
    for category, idea in ideas.items():
        cause = idea["stock_verdict"] if idea["stock_verdict"] in ("stockout", "broken_size_run") else None
        first = (f'<div class="cp-do"><b>Before the till (manager):</b> {esc(idea["supply_action"])}</div>'
                 if idea.get("supply_action") else "")
        steps = idea.get("steps") or [idea["at_the_till"]]
        html_block(
            f'<div class="cp-panel">{pill(idea["status"])}{cause_chip(cause)}'
            f'<div class="cp-row-name">{esc(category)} '
            f'<span class="cp-small">{abs(idea["pct_vs_pace"]):.0f}% behind this month</span></div>{first}'
            f'<div class="cp-do"><b>At the till:</b><ol style="margin:4px 0 0;padding-left:20px">'
            + "".join(f'<li style="font-size:14px;margin:2px 0">{esc(step)}</li>' for step in steps)
            + '</ol></div></div>'
        )
        if st.button(f"Open {category} details", key=f"sell_open_{slug(category)}", type="tertiary"):
            category_dialog(category)

    html_block(heading("Which offer works for whom", "pick any category"))
    if not current_store().has_loyalty:
        _not_in_data("loyalty tier figures", "upload loyalty tier figures")
        return
    options = list(ideas) + [c for c in current_store().categories if c not in ideas]
    chosen = st.selectbox("Category", options, key="sell_category", label_visibility="collapsed")
    _tier_table(playbook_for(chosen))

    with st.expander("The likeliest customers to say yes, for every category"):
        best = []
        for category in current_store().categories:
            tiers = playbook_for(category)["tiers_ranked_by_response"]
            if not tiers:
                continue
            top = tiers[0]
            best.append({"Category": category, "Most likely to say yes": top["tier"],
                         "Say yes to an extra item": f"{top['cross_sell_response_rate_pct']:.0f} in 100",
                         "Offer at the till": top["offer_at_the_till"]})
        st.dataframe(pd.DataFrame(best), hide_index=True, width="stretch")


def plan_page():
    if _no_data("Plan", "Tomorrow's plan will show here",
                "What tomorrow needs to sell, which categories to focus on and what to do about each, "
                "stock to request, and where to put the team, ready to share with the team."):
        return
    s = current_store()
    p = tomorrow_plan()
    if p["month_over"]:
        page_title("Plan")
        html_block('<div class="cp-panel"><p>The month is over. Upload next month\'s sales to plan the '
                   'days ahead.</p></div>')
        return
    a = s.amount
    date = s.date(p["for_day"])
    page_title(f"Plan for {p['weekday']} {date:%d %b}",
               f"For the morning huddle, from the figures at the close of day {p['made_at']['day']}. "
               f"Share it with the team at the bottom of the page.")

    goal, me = p["goal"], p["month_end"]
    cards = []
    if goal:
        cards.append(kpi_card("Tomorrow needs", a(goal["needs"]) if goal["needs"] > 0 else "Target reached",
                              GOAL_PHRASE[goal["yardstick"]].replace("keeps", "to keep", 1)
                              if goal["needs"] > 0 else "keep going",
                              tone="red" if goal["needs"] > 1.15 * goal["usual_at_current_rate"] else ""))
        cards.append(kpi_card(f"A usual {p['weekday']}", a(goal["usual_at_current_rate"]),
                              "at this month's current rate" + (" (a busy day)" if p["busy_day"] else "")))
    else:
        cards.append(kpi_card("Tomorrow needs", "-", "no targets to judge by yet"))
    if me["likely"] is not None:
        chance = me["chance_of_target_pct"]
        cards.append(kpi_card("Month-end", f"{a(me['low'])} to {a(me['high'])}",
                              f"{chance}% {CHANCE_PHRASE[me['yardstick']]} ({chance_words(chance)})" if chance is not None
                              else "likely range (a projection)", extra=range_bar(me, me["target"])))
    cards.append(kpi_card("To focus on", f"{len(p['focus'])}",
                          f"{'category' if len(p['focus']) == 1 else 'categories'} behind or slipping",
                          tone="red" if any(f["status"] == "behind" for f in p["focus"]) else ""))
    html_block('<div class="cp-kpis">' + "".join(cards) + "</div>")
    if len(p["goal_by_line"]) > 1:
        with st.expander("Tomorrow's goal by line"):
            st.dataframe(pd.DataFrame([
                {"Line": g["line"], "Tomorrow needs": a(g["needs"]),
                 f"A usual {p['weekday']}": a(g["usual_at_current_rate"])}
                for g in p["goal_by_line"]]), hide_index=True, width="stretch")

    html_block(heading("Focus", "tap a category for the full picture"))
    if not p["focus"]:
        html_block('<div class="cp-panel"><p>Nothing is behind or slipping. Keep the floor stocked and '
                   'the best sellers visible.</p></div>')
    for f in p["focus"]:
        tone = STATUS[f["status"]][1]
        till = f'<div class="cp-row-text"><b>At the till:</b> {esc(f["at_the_till"])}</div>' \
            if f["at_the_till"] else ""
        markup = (f'<div class="cp-row {tone}"><div style="flex:1;min-width:0">{pill(f["status"])}'
                  + (cause_chip(f["cause"]) if f["status"] == "behind" or f["cause"] != "unclear" else "")
                  + f'<div class="cp-row-name">{esc(f["category"])} '
                  f'<span class="cp-small">{pace_words(f["pct_vs_pace"])}</span></div>'
                  f'<div class="cp-row-text"><b>Do:</b> {esc(f["action"])}</div>{till}'
                  f'</div><div class="cp-chev">&rsaquo;</div></div>')
        if clickable(f"plan_{slug(f['category'])}", markup, f"Open {f['category']}"):
            category_dialog(f["category"])
    if p["more_behind"]:
        html_block(f'<div class="cp-small">{p["more_behind"]} more behind: see Categories.</div>')

    html_block(heading("Stock to request today"))
    if p["stock"] is None:
        _not_in_data("stock counts", "upload a stock report with units on hand by category and size")
    elif not p["stock"]["urgent"]:
        html_block('<div class="cp-panel"><p>Nothing urgent: no core size is sold out and every size should '
                   'last until the next delivery. The Stock page has the usual top-up.</p></div>')
    else:
        html_block(f'<div class="cp-small">{esc(p["stock"]["basis"][0].upper() + p["stock"]["basis"][1:])}. '
                   f'Most urgent first.</div>')
        st.dataframe(_request_table(p["stock"]["urgent"]), hide_index=True, width="stretch")

    html_block(heading("The floor team", "tomorrow's busy hours"))
    people = p["people"]
    if people is None:
        _not_in_data("visitors by hour, needed for tomorrow's busy hours", "upload visitor counts by hour")
    else:
        split = ", ".join(f"{zone} {pct}%" for zone, pct in people["suggested_floor_split_at_busiest_hour_pct"].items())
        html_block('<div class="cp-panel">'
                   + "".join(f'<p><b>{esc(z["zone"])}:</b> busiest '
                             f'{esc(", ".join(friendly_window(w) for w in z["peak_windows"]) or "no clear peak")}'
                             f'{" · " + esc(z["reliability"]) if z["reliability"] != "good" else ""}</p>'
                             for z in people["zones"])
                   + f'<p>At {staffing_clock(people["store_busiest_hour"])}, the store\'s busiest hour, split the team: '
                     f'{esc(split)}.</p>'
                   + "".join(f'<p class="cp-small">{esc(n)}</p>' for n in people["notes"])
                   + "</div>")

    if p["going_well"]:
        html_block(heading("Going well") + f'<div class="cp-panel"><p>{esc(", ".join(p["going_well"]))}: '
                   f'keep them stocked and on display.</p></div>')

    if p.get("slow"):
        html_block(heading("Worth a look: slow stock", "not selling, most money first; more on the Stock page"))
        for item in p["slow"]:
            if clickable(f"planslow_{slug(item['category'])}", _slow_stock_row(item, s),
                         f"Open {item['category']}"):
                category_dialog(item["category"])

    html_block(heading("Share with the team", "for the huddle or the team's WhatsApp group"))
    _share_block(plan_message_text(), "plan")


def summary_page():
    if _no_data("Summary", "Your end-of-day summary will show here",
                "A short plain-English summary of the day, and the month's contribution report, "
                "both downloadable."):
        return
    hour = current_hour()
    s = current_store()
    is_close = hour == s.hours[-1]
    moment = "close" if is_close else time_label(hour).replace(":", "")
    page_title("End-of-day summary" if is_close else "Summary so far today",
               "The plain-English summary that replaces the evening spreadsheet, and the month's "
               "contribution report.")

    html_block(heading("Evening report for WhatsApp", f"at the close of day {s.today_day}")
               + '<div class="cp-small" style="margin:0 0 6px">Short enough to read on a phone. Copy it '
                 '(top right of the box) or share it straight to your area manager or the team.</div>')
    _share_block(whatsapp_report_text(), "evening")

    html_block(heading("The full summary"))
    text = digest_at(hour)
    html_block('<div class="cp-digest">'
               + "".join(f"<p>{esc(p)}</p>" for p in text.split("\n\n")) + "</div>")
    st.download_button("Download summary", data=text, icon=":material/download:",
                       file_name=f"category-pulse-summary-day{s.today_day}-{moment}.txt",
                       mime="text/plain")

    report = contribution_at(hour)
    html_block(heading("Contribution, month to date",
                       f"as of day {s.today_day}, {time_label(hour)}")
               + '<div class="cp-check">Totals checked automatically: '
               + esc("; ".join(report["checks_passed"])) + ".</div>")

    lines = [
        {
            "Line": l["line"],
            "Floor": l["department"],
            "Units": l["units"],
            "Units share": f"{l['units_pct']}%",
            "Value": inr(l["value"]),
            "Value share": f"{l['value_pct']}%",
        }
        for l in report["lines"]
    ]
    lines.append({"Line": "Store", "Floor": "", "Units": report["store_units"],
                  "Units share": "100%", "Value": inr(report["store_value"]), "Value share": "100%"})
    value_columns = [] if s.has_value else ["Value", "Value share", "Value (INR)"]
    st.dataframe(pd.DataFrame(lines).drop(columns=value_columns, errors="ignore"),
                 hide_index=True, width="stretch")

    categories = [
        {
            "Line": l["line"],
            "Category": c["product_type"],
            "Units": c["units"],
            "Units share": c["units_pct"],
            "Value (INR)": c["value"],
            "Value share": c["value_pct"],
        }
        for l in report["lines"] for c in l["categories"]
    ]
    with st.expander("Every category"):
        st.dataframe(pd.DataFrame(categories).drop(columns=value_columns, errors="ignore"),
                     hide_index=True, width="stretch",
                     column_config={
                         "Units share": st.column_config.NumberColumn(format="%.1f%%"),
                         "Value (INR)": st.column_config.NumberColumn(format="localized"),
                         "Value share": st.column_config.NumberColumn(format="%.1f%%"),
                     })
    st.download_button("Download contribution report (CSV)", icon=":material/download:",
                       data=pd.DataFrame(categories).to_csv(index=False),
                       file_name=f"category-pulse-contribution-day{s.today_day}-{moment}.csv",
                       mime="text/csv")


# --- Your data page ----------------------------------------------------------------------------

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


NOT_USED = "Not used"
ROLE_CHOICES = ["sales", "stock", "targets", "visitors", "loyalty", "settings", "skip"]


def _reading(files, layouts=None):
    """Finds the tables in uploaded files, guessing what each is. The upload page can change the guesses."""
    try:
        tables, notes = smart_import.find_tables(files, layouts)
    except StoreDataError as e:
        return {"files": files, "tables": [], "notes": [], "ok": False, "problems": e.problems}
    if not tables:
        return {"files": files, "tables": [], "notes": notes, "ok": False, "problems": [
            "No tables were found in the file. It needs at least your sales: a row of headings (like "
            "Date, Category and Units), then one row per sale."]}
    return {"files": files, "tables": tables, "notes": notes}


def _file_settings(tables):
    """Store name, delivery day and sale days from a Settings sheet, if the upload has one."""
    settings = {}
    for t in tables:
        if t["role"] == "settings":
            try:
                settings.update(smart_import._settings(t["df"]))
            except StoreDataError:
                pass
    return settings


def _read_upload(files):
    """Reads newly uploaded files, and fills the page's details in from the file's Settings."""
    result = _reading(files, st.session_state.get("layouts"))
    _fill_details(result)
    st.session_state["upload_result"] = result


def _fill_details(result):
    """The page's details (store name, delivery day, sale days) start from the file's Settings."""
    settings = _file_settings(result["tables"])
    st.session_state["up_name"] = settings.get("name", "")
    delivery = settings.get("delivery_weekday")
    st.session_state["up_delivery"] = WEEKDAY_NAMES[delivery] if delivery is not None else NOT_SET
    st.session_state["up_sale_days"] = ", ".join(str(d) for d in settings.get("sale_days", []))
    st.session_state["up_target_units"] = None
    st.session_state["up_target_value"] = None


NOT_SET = "No fixed day"


def _upload_extras():
    """Details typed on the upload page -> (extras for smart_import, problems)."""
    extras, problems = {}, []
    name = (st.session_state.get("up_name") or "").strip()
    if name:
        extras["name"] = name
    delivery = st.session_state.get("up_delivery")
    if delivery and delivery != NOT_SET:
        extras["delivery_weekday"] = WEEKDAY_NAMES.index(delivery)
    sale_days = (st.session_state.get("up_sale_days") or "").strip()
    if sale_days:
        try:
            extras["sale_days"] = smart_import.sale_day_numbers(sale_days)
        except StoreDataError as e:
            problems += e.problems
    for key, extra in (("up_target_units", "store_target_units"), ("up_target_value", "store_target_value")):
        if st.session_state.get(key):
            extras[extra] = st.session_state[key]
    return extras, problems


def _build_upload(result):
    """(Re)builds the store when the choices or details have changed since the last build."""
    if not result["tables"]:
        return
    extras, problems = _upload_extras()
    signature = repr([(t["role"], sorted(t["mapping"].items())) for t in result["tables"]]) + repr(extras)
    if result.get("signature") == signature:
        return
    result["signature"] = signature
    if problems:
        result.update(ok=False, store=None, problems=problems)
        return
    try:
        with st.spinner("Working out your store..."):
            store, more, keep = upload.build(result["tables"], extras)
        result.update(ok=True, store=store, build_notes=more, keep=keep, problems=[])
    except StoreDataError as e:
        result.update(ok=False, store=None, problems=e.problems)


def _table_editor(i, t, upload_key):
    """What one table is, and what each of its columns holds, as the person can change them."""
    role = st.selectbox(f"What's in {t['label']}?", ROLE_CHOICES, index=ROLE_CHOICES.index(t["role"]),
                        format_func=lambda r: smart_import.ROLES[r], key=f"role_{upload_key}_{i}")
    if role != t["role"]:
        t["role"] = role
        t["mapping"] = smart_import.guess_columns(role, t["columns"], t["df"])
        t.pop("shown", None)
    if role in ("skip", "settings"):
        return
    # The editor always starts from the same rows (what was first shown for this role); the
    # person's changes are kept by the editor itself.
    shown = t.setdefault("shown", dict(t["mapping"]))
    labels = {field: label for field, label, _ in smart_import.FIELDS[role]}
    by_label = {label: field for field, label in labels.items()}
    rows = pd.DataFrame({
        "Column in your file": t["columns"],
        "Examples": [", ".join(t["examples"][c]) for c in t["columns"]],
        "Used as": [labels.get(shown.get(c), NOT_USED) for c in t["columns"]],
    })
    edited = st.data_editor(
        rows, hide_index=True, width="stretch", key=f"cols_{upload_key}_{i}_{role}",
        disabled=["Column in your file", "Examples"],
        column_config={"Used as": st.column_config.SelectboxColumn(
            "Used as", options=[NOT_USED] + list(labels.values()), required=True, width="medium")},
    )
    t["mapping"] = {c: by_label.get(used) for c, used in zip(t["columns"], edited["Used as"])}


def _how_we_read_it(result, upload_key):
    """Step 2: what each table and column was read as, with dropdowns to put it right."""
    tables = result["tables"]
    summary = " · ".join(f"{smart_import.ROLES[t['role']]}: {t['label']} ({t['rows']:,} rows)"
                         for t in tables if t["role"] not in ("skip",))
    html_block(_step_heading(2, "Check how we read it", "change anything that's wrong")
               + f'<div class="cp-small" style="margin:0 0 6px">{esc(summary)}</div>')
    with st.expander("See and change what each column is used for",
                     expanded=bool(result.get("problems"))):
        html_block('<div class="cp-small">Only the columns marked as used are kept. Anything else '
                   'in your file (customer names, phone numbers, prices) is left out and never '
                   'saved. Your choices are remembered for next time.</div>')
        for i, t in enumerate(tables):
            _table_editor(i, t, upload_key)
    has_targets = any(t["role"] == "targets" for t in tables)
    with st.expander("A few details (optional)", expanded=not has_targets):
        if not has_targets:
            html_block('<div class="cp-small"><b>No targets in your file.</b> If you know the store\'s '
                       'target for the month, type it in and it\'s shared across categories by how '
                       'they usually sell. If not, each category is compared with last year\'s sales '
                       'when your file has them, or else with its own pace earlier in the month.</div>')
            cols = st.columns(2)
            cols[0].number_input("Store target for the month (₹)", min_value=0, step=100000,
                                 value=None, key="up_target_value", placeholder="e.g. 5000000")
            cols[1].number_input("Store target for the month (units)", min_value=0, step=100,
                                 value=None, key="up_target_units", placeholder="e.g. 4000")
        st.text_input("Store name", key="up_name", placeholder="Your store")
        cols = st.columns(2)
        cols[0].selectbox("Delivery day", [NOT_SET] + WEEKDAY_NAMES, key="up_delivery",
                          help="The weekday new stock usually arrives: used to spot missed deliveries "
                               "and sizes that will run out before the next one.")
        cols[1].text_input("Sale days this month", key="up_sale_days", placeholder="e.g. 13, 27",
                           help="Day numbers, separated by commas. Sale days are busier, so they're "
                                "judged separately from ordinary days.")


def _judged_by(store):
    """How each measure's targets were worked out, in plain words."""
    lines = []
    for measure in store.measures:
        notes = store.target_notes.get(measure, [])
        label = "In rupees" if measure == "value" else "In units"
        if notes:
            lines.append(f"<b>{label}:</b> " + " ".join(esc(n) for n in notes))
        else:
            lines.append(f"<b>{label}:</b> every category has its own target.")
    return "".join(f'<p class="cp-small">{line}</p>' for line in lines)


def _upload_preview(store, notes):
    html_block(_step_heading(3, "Check it", "before every page switches to it"))
    lines, floors = len(store.lines), len(store.departments)
    units = int(store.sales["units_sold"].sum())
    value_line = f"worth {store.for_measure('value').amount(store.sales['value'].sum())}" \
        if store.has_value else "no rupee values in the file"
    html_block('<div class="cp-kpis">'
               + kpi_card("Month", f"{store.month_name} {store.month_start.year}",
                          f"data up to day {store.today_day} ({store.date(store.today_day):%a %d %b})")
               + kpi_card("Categories", f"{len(store.categories)}",
                          (f"in {lines} line{'s' if lines != 1 else ''} on " if set(store.lines) != set(store.departments)
                           else "on ") + f"{floors} floor{'s' if floors != 1 else ''}")
               + kpi_card("Sold so far", f"{units:,} units", value_line)
               + "</div>")

    if store.warnings:
        html_block('<div class="cp-alert amber"><div class="cp-alert-title">Check these before you continue'
                   '</div><ul>' + "".join(f"<li>{esc(w)}</li>" for w in store.warnings) + "</ul></div>")

    html_block(heading("How each category is judged") + f'<div class="cp-panel">{_judged_by(store)}</div>')
    html_block(heading("What your data switches on")
               + '<div class="cp-panel">'
               + "".join(_feature_row(*f) for f in upload.feature_checklist(store))
               + "</div>")
    for note in notes:
        html_block(f'<div class="cp-small">{esc(note)}</div>')

    if store.has_sizes:
        with st.expander("Check the core sizes"):
            html_block('<div class="cp-small">Core sizes are the ones most shoppers need: when they run '
                       'out, a category can\'t sell even while the shelf looks full. Unless your targets '
                       'list them, they\'re worked out from sales on days every size was in stock. With '
                       'only a few sales that can pick the wrong ones, so check them.</div>')
            st.dataframe(pd.DataFrame([
                {"Category": c, "Sizes": ", ".join(store.sizes[c]),
                 "Core sizes": ", ".join(store.core_sizes[c]) or "none yet (no sales)"}
                for c in store.categories
            ]), hide_index=True, width="stretch")

    if st.session_state.get("my_store") is store:
        html_block('<div class="cp-small" style="margin-top:8px">This is the store loaded above.</div>')
        return
    if storage.is_configured():
        html_block('<div class="cp-small" style="margin:8px 0">Showing it also saves it to your account, '
                   'encrypted, so it\'s here next time you sign in. Only the columns in use are '
                   'saved.</div>')
    if st.button("Show my store", key="show_upload", type="primary", icon=":material/arrow_forward:"):
        result = st.session_state["upload_result"]
        layouts = dict(st.session_state.get("layouts") or {})
        for t in result["tables"]:
            layouts[smart_import.layout_key(t["columns"])] = {"role": t["role"], "mapping": t["mapping"]}
        st.session_state["layouts"] = dict(list(layouts.items())[-20:])
        st.session_state["my_store"] = store
        st.session_state["tour_step"] = None
        _save_to_account(store, result.get("keep", []), st.session_state["layouts"])
        st.switch_page(tour.PAGES["Today"])


def load_saved_store():
    """At the start of a visit: opens the person's saved data, if they have any."""
    person = account.person_id()
    if not (storage.is_configured() and person):
        return
    try:
        with st.spinner("Opening your saved data..."):
            saved = storage.load(person)
            if saved is None:
                return
            result = _reading(saved["files"], saved["layouts"])
            _fill_details(result)
            _build_upload(result)
    except storage.StorageError as e:
        st.session_state["save_note"] = str(e)
        return
    if not result.get("ok"):
        st.session_state["save_note"] = ("Your saved data couldn't be read with this version of the app. "
                                         "Please upload your file again.")
        return
    st.session_state["layouts"] = saved["layouts"]
    st.session_state["my_store"] = result["store"]
    st.session_state["upload_result"] = result
    st.session_state["saved_at"] = saved["saved_at"]


def _save_to_account(store, files, layouts=None):
    """Saves the columns in use, encrypted, to the signed-in person's account (if saving is on)."""
    st.session_state.pop("save_note", None)
    st.session_state.pop("saved_at", None)
    person = account.person_id()
    if not (storage.is_configured() and person and files):
        return
    try:
        storage.save(person, files, store.name, layouts)
        st.session_state["saved_at"] = pd.Timestamp.now(tz="UTC").isoformat()
    except storage.StorageError as e:
        st.session_state["save_note"] = str(e)


def _feature_row(name, on, hint):
    badge = '<span class="cp-pill green">On</span>' if on else '<span class="cp-pill neutral">Off</span>'
    extra = f' <span class="cp-small">({esc(hint)})</span>' if hint else ""
    return f'<div class="cp-term">{badge} {esc(name)}{extra}</div>'


def _delete_my_data():
    """Removes the person's data from this visit and, if saved, from their account."""
    person = account.person_id()
    if storage.is_configured() and person:
        try:
            storage.delete(person)
        except storage.StorageError as e:
            st.session_state["delete_note"] = str(e)
            return
    request_forget()


def _step_heading(number, text, sub=""):
    sub_html = f' <span class="cp-sub">{esc(sub)}</span>' if sub else ""
    return (f'<div class="cp-stepline"><span class="cp-stepnum">{number}</span>'
            f'{esc(text)}{sub_html}</div>')


def your_data_page():
    guest = account.is_guest()
    mine = st.session_state.get("my_store")
    page_title("Your data", "Upload your sales as your till system exports them. Stock, targets and "
                            "visitor counts are optional: each one switches on more of the app.")

    if guest:
        html_block('<div class="cp-panel"><p>You\'re looking around the <b>Sample Store</b>: a made-up '
                   'store with realistic numbers and a few problems hidden in them, so you can see '
                   'what Category Pulse does. Create a free account to bring in your own store\'s '
                   'data.</p></div>')
        if account.is_configured():
            st.button("Log in or create account", key="guest_signup", type="primary",
                      on_click=account.sign_in)
    elif mine is not None:
        saved = _saved_line()
        html_block(heading("Your store")
                   + f'<div class="cp-panel"><p><b>{esc(mine.name)}</b>: {esc(mine.month_name)} '
                     f'{mine.month_start.year}, up to day {mine.today_day}. Every page is showing it. '
                     f'{esc(saved)} To replace it, upload new files below.</p>'
                     f'<p class="cp-small">{esc(privacy_promise())}</p></div>')
        if st.session_state.get("delete_note"):
            html_block(f'<div class="cp-small">{esc(st.session_state.pop("delete_note"))}</div>')
        if st.button("Delete my data", key="mine_forget", type="tertiary", icon=":material/delete:"):
            _delete_my_data()
            st.rerun()
    else:
        name = account.first_name()
        html_block(f'<div class="cp-panel"><p><b>Welcome{", " + esc(name) if name else ""}.</b> Upload '
                   'your sales and every page fills in with your store\'s numbers.</p>'
                   f'<p class="cp-small">{esc(privacy_promise())}</p></div>')
        if st.button("Take the 2-minute tour first", key="data_tour", icon=":material/tour:",
                     type="tertiary"):
            tour.start()

    html_block(_step_heading(1, "Upload your sales", "Excel or CSV, as your system exports it")
               + '<div class="cp-small" style="margin:0 0 8px">One row per bill line or per day, it '
                 'doesn\'t matter: all it needs is a <b>date</b>, a <b>category</b> and <b>units</b> '
                 'sold. Rupee values, sizes, times and bill numbers add more. You can add other files '
                 'too: a stock report, your targets (per category, per floor or for the store; in units '
                 'or ₹), visitor counts. Don\'t have targets? The app compares with last year or with '
                 'each category\'s own recent pace.</div>')
    if guest:
        st.download_button("See the file layout (Excel template)", data=upload.template_bytes(), mime=XLSX,
                           file_name="category-pulse-template.xlsx", icon=":material/download:",
                           type="tertiary")
        return

    files = st.file_uploader("Upload your data", type=["xlsx", "xlsm", "csv"], accept_multiple_files=True,
                             key="upload_files", label_visibility="collapsed")
    st.download_button("No export handy? Use the Excel template", data=upload.template_bytes(), mime=XLSX,
                       file_name="category-pulse-template.xlsx", icon=":material/download:",
                       type="tertiary")
    upload_key = None
    if files:
        data = [(f.name, f.getvalue()) for f in files]
        upload_key = hashlib.sha1(b"".join(d for _, d in data)).hexdigest()[:10]
        if st.session_state.get("upload_key") != upload_key:  # read each new upload once
            st.session_state["upload_key"] = upload_key
            with st.spinner("Reading your file..."):
                _read_upload(data)

    result = st.session_state.get("upload_result")
    if result is None:
        return
    if result["tables"]:
        _how_we_read_it(result, upload_key or "saved")
        _build_upload(result)
    if not result.get("ok"):
        problems = result["problems"]
        title = ("That file can\'t be used yet" if len(problems) == 1
                 else f"{len(problems)} things to fix before this file can be used")
        fix = ("Change what a column is used for in step 2, or fix the file and upload it again."
               if result["tables"] else "Fix the file and upload it again.")
        html_block(f'<div class="cp-alert"><div class="cp-alert-title">{title}</div><ul>'
                   + "".join(f"<li>{esc(p)}</li>" for p in problems)
                   + "</ul></div>"
                   + f'<div class="cp-small">{fix}</div>')
        return
    _upload_preview(result["store"], result["notes"] + result.get("build_notes", []))

# --- Guide page ------------------------------------------------------------------------------

GUIDE_STATUSES = [
    ("behind", "More than 15% under where it should be by now, by more than normal day-to-day "
               "ups and downs. Needs action."),
    ("drifting", "More than 15% under, but small enough that it could still be chance. Worth "
                 "watching, not yet proven."),
    ("on_pace", "Within 15% of where it should be."),
    ("ahead", "More than 15% over, by more than chance."),
    ("too_early", "Too few units expected so far for a percentage to mean anything."),
    ("no_target", "No target, no last year's figure, and too few days of sales to compare the "
                  "category with its own earlier pace yet."),
]

GUIDE_TERMS = [
    ("Pace", "Sales so far (in units or rupees: use the switch at the top) compared with what the "
             "monthly target says should have sold by now. Month-to-date, because targets are "
             "monthly. Without a target, a category is measured against last year's sales or its own "
             "pace earlier in the month, and the page says so."),
    ("Needs per day", "How many units a day the category must sell from now on to hit its "
                      "target, next to what it's actually selling."),
    ("Month-end at this rate", "A projection, not a result: where the month lands if the rest of it "
                               "keeps the same pace against plan. It can't know about stockouts or "
                               "a month-end push."),
    ("Month-end range", "Where the month is likely to finish, as a range roughly 8 months in 10 like "
                        "this would land inside, and the chance of reaching the target. Worked out from "
                        "the pace so far and how much this store's daily sales usually vary. A "
                        "projection: it can't know about a stockout next week."),
    ("Tomorrow's plan", "What tomorrow needs to sell to keep the month on course for its target (the "
                        "gap left, shared over the remaining days by how busy each is), the categories "
                        "to focus on and what to do, stock to request, and busy hours."),
    ("What to request", "For each size: enough to last until the delivery after next at the rate it "
                        "sells when it's on the shelf, minus what's on hand. Core sizes get a margin "
                        "for a busy week."),
    ("Core sizes", "The sizes most shoppers need, such as M and L in men's tops or waists 32 and "
                   "34 in men's bottoms."),
    ("Stockout", "Almost nothing left to sell in any size."),
    ("Broken size run", "The shelf still looks full, but the core sizes are gone, so most "
                        "shoppers can't find their size. The category total hides it."),
    ("Last piece", "A size that has just dropped to its final unit. Flagged the moment it "
                   "happens."),
    ("Visitors vs buyers", "Fewer visitors means a traffic problem (displays, marketing). Normal "
                           "visitors but fewer buyers means a conversion problem (stock, sizes, "
                           "price or service)."),
    ("Loyalty tiers", "Platinum, Gold, Silver and non-members. Cross-sell ideas target a tier and "
                      "the offer it responds to, never an individual customer."),
    ("Contribution", "Each line's and category's share of the store's units and value "
                     "month-to-date. The report checks its own totals."),
]


def guide_page():
    page_title("Guide", "How to read Category Pulse, in plain English.")
    if st.button("Start the guided tour", key="guide_tour", icon=":material/tour:"):
        tour.start()

    html_block(heading("What this is")
               + '<div class="cp-panel"><p>An assistant for a clothing store\'s floor team. It '
                 'watches every category against its monthly target, finds what\'s genuinely '
                 'behind, works out why, and suggests what to do while there\'s still time.</p></div>')

    html_block(heading("What the statuses mean")
               + '<div class="cp-panel">'
               + "".join(f'<div class="cp-term">{pill(s)} {esc(text)}</div>' for s, text in GUIDE_STATUSES)
               + "</div>")

    html_block(heading("Key terms")
               + '<div class="cp-panel">'
               + "".join(f'<div class="cp-term"><b>{esc(term)}:</b> {esc(text)}</div>'
                         for term, text in GUIDE_TERMS)
               + "</div>")

    html_block(heading("Using your own store's data")
               + '<div class="cp-panel"><p>On the Your data page, upload your sales as your till '
                 'system exports them, plus anything else you have: a stock report, targets (per '
                 'category, per floor or for the whole store, in units or rupees), visitor counts. '
                 'The app works out what each column is and shows you, so you can put anything right. '
                 'Every page then switches to your store. The more you add, the more the app can tell '
                 'you; it says plainly what\'s missing rather than guessing. Without targets, each '
                 'category is compared with last year or with its own recent pace. To keep your data '
                 'private, only the columns in use are kept, and the AI chat asks before it sends any '
                 'of your figures to its AI service.</p></div>')

    html_block(heading("How the AI chat works")
               + '<div class="cp-panel"><p>Ask Category Pulse answers by looking up the store\'s '
                 'numbers with the same calculations the pages use, and never guesses a figure. '
                 'Under each answer, "How I got this" lists every lookup and the numbers it '
                 f'returned. Once you\'re signed in, you can ask up to {QUESTIONS_PER_DAY} questions a day.'
                 '</p></div>')


# --- Chat pop-up -----------------------------------------------------------------------------

def _suggestions(store):
    """Suggested questions: the Sample Store's own, or ones any store's data can answer."""
    if store.is_demo:
        return SUGGESTED_QUESTIONS
    questions = {"What needs action?": "Which categories need action right now, and why?"}
    if store.has_stock:
        questions["Anything low on stock?"] = "Is anything low on stock?"
    questions["Cross-sell ideas"] = "Any cross-sell ideas right now?"
    if store.has_visitor_hours and store.today_day < store.days_in_month:
        questions["Staffing for tomorrow"] = "What should staffing look like tomorrow?"
    questions["How's the month going?"] = "How is the month going overall, and where will we finish?"
    return questions


def _queue_suggestion():
    """Chip clicked: queue its full question and clear the chip so it can be clicked again."""
    label = st.session_state.get("chat_chip")
    if label:
        st.session_state["chat_pending"] = _suggestions(current_store())[label]
    st.session_state["chat_chip"] = None


def _chat_needs_sign_in():
    """Guests are asked to sign in before using the chat, so daily limits can hold."""
    if not account.is_configured() or account.is_signed_in():
        return False
    html_block('<div class="cp-panel"><div class="cp-panel-title">Sign in to ask the AI</div>'
               f'<p>The AI chat comes with a free account: up to {QUESTIONS_PER_DAY} questions a day, '
               'each answered from the store\'s numbers with a note of how it was worked out. '
               'Everything else here works without one.</p></div>')
    st.button("Log in or create account", key="chat_signup", type="primary", on_click=account.sign_in)
    return True


def _agree_to_chat(store_id):
    st.session_state["chat_consent"] = store_id


def _chat_notice(store):
    """
    Before the chat answers about a store's own data: say plainly that the
    figures it looks up go to the AI service, and ask for a clear yes. Asked
    once per visit. Returns True once agreed.
    """
    if store.is_demo or st.session_state.get("chat_consent") == store.id:
        return True
    html_block('<div class="cp-panel"><div class="cp-panel-title">Before you ask about your data</div>'
               f'<p>To answer, the chat sends the figures it looks up from your data (for example '
               f'sales, targets and stock by category) to {esc(agent.PROVIDER["name"])}, the AI service '
               'behind it. Category Pulse doesn\'t keep them after your visit.</p>'
               '<p><b>Only continue with dummy data, or with real figures your manager has approved '
               'for use with an outside AI service.</b></p></div>')
    with st.container(horizontal=True, gap="small"):
        st.button("I understand, continue", key="chat_agree", type="primary",
                  on_click=_agree_to_chat, args=[store.id])
        if st.button("Cancel", key="chat_cancel", type="tertiary"):
            st.session_state.pop("chat_pending", None)
            st.rerun()
    return False


@st.dialog("Ask Category Pulse", width="large")
def chat_dialog():
    store = current_store()
    if _chat_needs_sign_in() or not _chat_notice(store):
        return
    # Daily questions are counted per account (or per visit, where sign-in isn't set up).
    person = account.person_id() or st.session_state.setdefault("visitor_id", uuid.uuid4().hex)
    hour = current_hour()
    history_key = f"chat_{store.id}"  # each store keeps its own conversation
    st.session_state.setdefault(history_key, [])

    # Filled in at the end, so the "questions left" count includes this answer.
    status_line = st.empty()
    st.pills("Suggested questions", list(_suggestions(store)), key="chat_chip",
             on_change=_queue_suggestion, label_visibility="collapsed")

    conversation = st.container(height=420, border=False, autoscroll=True)
    typed = st.chat_input("Ask about today's numbers", key="chat_typed")
    question = st.session_state.pop("chat_pending", None) or typed

    with conversation:
        for turn in st.session_state[history_key]:
            _render_turn(turn)
        if not st.session_state[history_key] and not question:
            st.markdown('<div class="cp-small">Pick a suggested question or type your own. '
                        'Each answer shows how it was worked out.</div>', unsafe_allow_html=True)

        if question:
            left, everyone_done = usage.questions_left(person)
            if everyone_done:
                turn = {"question": question, "calls": [],
                        "answer": ("The AI chat has answered as many questions as it can today. Try "
                                   "again tomorrow; everything else on the site still works.")}
            elif left == 0:
                turn = {"question": question, "calls": [],
                        "answer": (f"You've asked your {QUESTIONS_PER_DAY} questions for today. You can "
                                   f"ask more tomorrow; everything else on the site still works.")}
            else:
                # Show the question with a spinner while the AI works, then swap in the answer.
                waiting = st.empty()
                with waiting.container():
                    st.markdown(f'<div class="cp-you">{esc(question)}</div>', unsafe_allow_html=True)
                    with st.spinner("Checking the numbers..."):
                        answer, calls = agent.ask(question, current_hour=hour, store=store)
                waiting.empty()
                usage.record_question(person)
                turn = {"question": question, "answer": answer, "calls": calls}
            st.session_state[history_key].append(turn)
            _render_turn(turn)

    left, _ = usage.questions_left(person)
    status_line.caption(f"Answers are built from the store's own numbers as of {time_label(hour)}. "
                        f"AI: {agent.PROVIDER['name']} · {left} of {QUESTIONS_PER_DAY} questions left today.")


def _render_turn(turn):
    st.markdown(f'<div class="cp-you">{esc(turn["question"])}</div>', unsafe_allow_html=True)
    st.markdown('<div class="cp-who">Category Pulse</div>', unsafe_allow_html=True)
    st.markdown(turn["answer"])
    lookups = len(turn["calls"])
    with st.expander(f"How I got this ({lookups} lookup{'s' if lookups != 1 else ''})"):
        if not turn["calls"]:
            st.write("No data lookups were made for this answer.")
        for call in turn["calls"]:
            st.markdown(f"**{call['tool']}** with `{json.dumps(call['input'])}`")
            st.code(json.dumps(call["result"], indent=2, default=str)[:4000], language="json")
