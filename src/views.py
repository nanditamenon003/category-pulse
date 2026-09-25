"""
The pages and pop-ups of the Category Pulse web app.

Each page function draws one section of the site; app.py puts them in the
top menu. Pop-ups (st.dialog) sit on top of whatever page is open.
"""

import json

import altair as alt
import pandas as pd
import streamlit as st

import agent
from config import DEMO_QUESTION_LIMIT, LINES, STORE_HOURS
from ui import (
    AMBER,
    GREEN,
    MUTED,
    NEUTRAL,
    PAGE,
    RED,
    STATUS,
    TEXT,
    BORDER,
    category_card,
    current_hour,
    digest_at,
    esc,
    grid,
    heading,
    html_block,
    last_pieces_at,
    line_card,
    pace_at,
    page_title,
    pill,
    stock_health_at,
    time_label,
    today_at,
)

# Short chip labels for the chat, mapped to the full question the AI receives.
SUGGESTED_QUESTIONS = {
    "Anything low on stock?": "Is anything low on stock?",
    "Staffing for tomorrow": "What should staffing look like tomorrow?",
    "Cross-sell ideas": "Any cross-sell ideas right now?",
    "Why is womenswear behind?": "Why is womenswear behind? Is it traffic or conversion?",
    "Chinos: who to target?": ("Chinos (THM Non Denim Bottom) are behind: which customers should we "
                               "focus on, and with what offer?"),
}


# --- Pieces shared by pages ------------------------------------------------------------

def alert_banner(hour):
    items = []
    for r in stock_health_at(hour):
        if r["verdict"] == "stockout":
            items.append(f"<b>{esc(r['category'])}</b>: sold out, {r['total_remaining']} units left.")
        elif r["verdict"] == "broken_size_run":
            core = " and ".join(r["core_sizes"])
            items.append(f"<b>{esc(r['category'])}</b>: broken size run. No {esc(core)}, "
                         f"yet {r['total_remaining']} units sit on the shelf.")
        else:
            items.append(f"<b>{esc(r['category'])}</b>: core sizes gone and stock running low.")

    alerts = last_pieces_at(hour)
    for a in (a for a in alerts if a["is_core_size"]):
        items.append(f"<b>{esc(a['category'])}</b>: last piece in {esc(a['size'])} (core size), "
                     f"since {a['hour']}:00-{a['hour'] + 1}:00.")
    others = [a for a in alerts if not a["is_core_size"]]
    if others:
        listed = ", ".join(f"{a['category']} {a['size']}" for a in others)
        items.append(f"{len(others)} other size{'s' if len(others) > 1 else ''} down to the last "
                     f"piece today: {esc(listed)}.")

    if not items:
        return ""
    return ('<div class="cp-alert"><div class="cp-alert-title">Needs action now</div><ul>'
            + "".join(f"<li>{i}</li>" for i in items) + "</ul></div>")


def pace_chart(pace):
    rows = []
    for p in pace:
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
        domain=["Behind", "Drifting", "On pace", "Ahead", "Too early"],
        range=[RED, AMBER, GREEN, GREEN, NEUTRAL]))
    y = alt.Y("Category:N", sort=order, title=None,
              axis=alt.Axis(labelLimit=180, labelOverlap=False, grid=False, ticks=False))
    bars = alt.Chart(df).mark_bar(height=14).encode(
        x=alt.X("Percent of expected:Q", title="Sold as % of expected by now (100 = on pace)",
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


# --- Pages -------------------------------------------------------------------------------

def today_page():
    hour = current_hour()
    page_title("Today", f"Where the month stands at {time_label(hour)}, and what needs action.")

    banner = alert_banner(hour)
    if banner:
        html_block(banner)

    pace = pace_at(hour)
    counts = {s: sum(1 for p in pace if p["status"] == s) for s in STATUS}
    html_block(
        heading("This month so far", "units sold / monthly target")
        + '<div class="cp-counts">'
        + f'<span class="cp-count">{pill("behind")} {counts["behind"]}</span>'
        + f'<span class="cp-count">{pill("drifting")} {counts["drifting"]}</span>'
        + f'<span class="cp-count">{pill("on_pace")} {counts["on_pace"] + counts["ahead"]}</span>'
        + (f'<span class="cp-count">{pill("too_early")} {counts["too_early"]}</span>'
           if counts["too_early"] else "")
        + "</div>"
    )

    attention = sorted((p for p in pace if p["status"] in ("behind", "drifting")),
                       key=lambda p: (p["status"] != "behind", p["pct_vs_pace"]))
    if attention:
        html_block(heading("Needs attention") + grid(category_card(p) for p in attention))

    html_block(heading("Today so far, by line", "units sold today / expected by now")
               + grid(line_card(t) for t in today_at(hour)))


def categories_page():
    hour = current_hour()
    page_title("Categories", "Every category's month-to-date pace. Being redesigned in step 3.")
    pace = pace_at(hour)
    for line, department in LINES.items():
        cards = [category_card(p, show_line=False) for p in pace if p["line"] == line]
        html_block(heading(line, department) + grid(cards))
    html_block(heading("Pace by category", "worst first"))
    # theme=None so Streamlit's default chart styling (gridlines etc.) doesn't override ours.
    st.altair_chart(pace_chart(pace), use_container_width=True, theme=None)


def coming_soon_page(title, what):
    def page():
        page_title(title, what)
        st.info("This page is being built in the next steps of the redesign.")
    return page


def summary_page():
    hour = current_hour()
    is_close = hour == STORE_HOURS[-1]
    page_title("End-of-day summary" if is_close else "Summary so far today",
               "A short plain-English summary that replaces the evening spreadsheet.")
    for paragraph in digest_at(hour).split("\n\n"):
        st.markdown(paragraph)


# --- Chat pop-up -----------------------------------------------------------------------------

def _queue_suggestion():
    """Chip clicked: queue its full question and clear the chip so it can be clicked again."""
    label = st.session_state.get("chat_chip")
    if label:
        st.session_state["chat_pending"] = SUGGESTED_QUESTIONS[label]
    st.session_state["chat_chip"] = None


@st.dialog("Ask Category Pulse", width="large")
def chat_dialog():
    hour = current_hour()
    st.session_state.setdefault("chat", [])
    st.session_state.setdefault("questions_asked", 0)
    left = DEMO_QUESTION_LIMIT - st.session_state["questions_asked"]

    st.caption(f"Answers are built from the store's own numbers as of {time_label(hour)}. "
               f"AI: {agent.PROVIDER['name']} · {max(left, 0)} of {DEMO_QUESTION_LIMIT} questions left.")
    st.pills("Suggested questions", list(SUGGESTED_QUESTIONS), key="chat_chip",
             on_change=_queue_suggestion, label_visibility="collapsed")

    conversation = st.container(height=420, border=False, autoscroll=True)
    typed = st.chat_input("Ask about today's numbers", key="chat_typed")
    question = st.session_state.pop("chat_pending", None) or typed

    with conversation:
        for turn in st.session_state["chat"]:
            _render_turn(turn)
        if not st.session_state["chat"] and not question:
            st.markdown('<div class="cp-small">Pick a suggested question or type your own. '
                        'Each answer shows how it was worked out.</div>', unsafe_allow_html=True)

        if question:
            if st.session_state["questions_asked"] >= DEMO_QUESTION_LIMIT:
                turn = {"question": question, "calls": [],
                        "answer": (f"This demo allows {DEMO_QUESTION_LIMIT} questions per visit, and "
                                   f"they've been used. Everything else on the site still works.")}
            else:
                st.markdown(f'<div class="cp-you">{esc(question)}</div>', unsafe_allow_html=True)
                with st.spinner("Checking the numbers..."):
                    answer, calls = agent.ask(question, current_hour=hour)
                st.session_state["questions_asked"] += 1
                turn = {"question": question, "answer": answer, "calls": calls}
            st.session_state["chat"].append(turn)
            st.rerun(scope="fragment")


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
