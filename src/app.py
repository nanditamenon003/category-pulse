"""
Category Pulse — Streamlit dashboard + chat (Phase 7).

Run from the project folder with:  streamlit run src/app.py

Designed for a store manager glancing at a phone on a busy floor: status
first (in words and colour), numbers second. Colour is used only for status
(green on track, amber drifting, red behind) plus one accent for buttons.
"""

import html
import json

import altair as alt
import pandas as pd
import streamlit as st

import agent
import diagnosis
import digest
import kpi
import stock
from config import (
    CATEGORY_PRODUCT,
    DEFAULT_CURRENT_HOUR,
    LINES,
    STORE_HOURS,
    STORE_NAME,
    TODAY_DAY,
    month_calendar,
)

st.set_page_config(page_title="Category Pulse", layout="wide", initial_sidebar_state="collapsed")

# Palette from the spec. Green is one shade darker than the spec's #1E8E3E so
# white label text on it meets accessibility contrast (4.2:1 -> 5.0:1).
RED, AMBER, GREEN, ACCENT = "#D93025", "#E0A100", "#188038", "#3F51B5"
NEUTRAL = "#B8B8B2"

STATUS = {
    "behind": ("Behind", "red"),
    "drifting": ("Drifting", "amber"),
    "on_pace": ("On pace", "green"),
    "ahead": ("Ahead", "green"),
    "too_early": ("Too early", "neutral"),
}

SUGGESTED_QUESTIONS = [
    "Is anything low on stock?",
    "What should staffing look like tomorrow?",
    "Any cross-sell ideas right now?",
    "Why is womenswear behind? Is it traffic or conversion?",
    "Chinos (THM Non Denim Bottom) are behind: which customers should we focus on, and with what offer?",
]

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
/* One typeface everywhere, except icon glyphs and code blocks. */
.stApp, .stApp *:not([data-testid="stIconMaterial"]) {{ font-family: 'Inter', sans-serif; }}
.stApp pre, .stApp pre *, .stApp code, .stApp code * {{ font-family: monospace !important; }}
#MainMenu, footer, header[data-testid="stHeader"], [data-testid="stToolbar"] {{ display: none; }}
.block-container {{ padding-top: 1rem; padding-bottom: 3rem; max-width: 1200px; }}

.cp-bar {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between;
          gap: 4px 16px; border-bottom: 1px solid #E4E4E0; padding-bottom: 8px; margin-bottom: 8px; }}
.cp-brand {{ font-size: 20px; font-weight: 700; color: #1A1A1A; }}
.cp-meta {{ font-size: 13px; color: #6B6B6B; }}
.cp-time {{ font-size: 13px; color: #1A1A1A; font-weight: 700; }}

.cp-h {{ font-size: 13px; font-weight: 700; color: #6B6B6B; text-transform: uppercase;
        letter-spacing: 0.04em; margin: 20px 0 8px; }}
.cp-sub {{ font-size: 12px; color: #6B6B6B; font-weight: 400; text-transform: none; letter-spacing: 0; }}

.cp-alert {{ background: #FFFFFF; border: 1px solid #E4E4E0; border-left: 4px solid {RED};
            border-radius: 6px; padding: 10px 14px; margin: 8px 0; }}
.cp-alert-title {{ font-size: 14px; font-weight: 700; color: #1A1A1A; margin-bottom: 4px; }}
.cp-alert ul {{ margin: 0; padding-left: 18px; }}
.cp-alert li {{ font-size: 13px; color: #1A1A1A; margin: 2px 0; }}

.cp-counts {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 0; }}
.cp-count {{ font-size: 13px; color: #1A1A1A; }}

.cp-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 8px; }}
.cp-card {{ background: #FFFFFF; border: 1px solid #E4E4E0; border-left: 4px solid {NEUTRAL};
           border-radius: 6px; padding: 10px 12px; }}
.cp-card.red {{ border-left-color: {RED}; }}
.cp-card.amber {{ border-left-color: {AMBER}; }}
.cp-card.green {{ border-left-color: {GREEN}; }}
.cp-name {{ font-size: 13px; color: #1A1A1A; margin: 6px 0 2px; line-height: 1.3; }}
.cp-num {{ font-size: 26px; font-weight: 700; color: #1A1A1A; line-height: 1.1; }}
.cp-of {{ font-size: 14px; font-weight: 400; color: #6B6B6B; }}
.cp-small {{ font-size: 12px; color: #6B6B6B; margin-top: 2px; }}

.cp-pill {{ display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.04em; padding: 2px 8px; border-radius: 10px; }}
.cp-pill.red {{ background: {RED}; color: #FFFFFF; }}
.cp-pill.amber {{ background: {AMBER}; color: #1A1A1A; }}
.cp-pill.green {{ background: {GREEN}; color: #FFFFFF; }}
.cp-pill.neutral {{ background: #E4E4E0; color: #1A1A1A; }}

.cp-msg {{ background: #FFFFFF; border: 1px solid #E4E4E0; border-radius: 6px; padding: 10px 14px;
          margin: 8px 0 4px; }}
.cp-who {{ font-size: 12px; font-weight: 700; color: #6B6B6B; margin-bottom: 2px; }}

.stButton > button {{ color: {ACCENT}; border-color: #E4E4E0; background: #FFFFFF;
                      text-align: left; justify-content: flex-start; }}
.stButton > button:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}

@media (max-width: 640px) {{
  .cp-grid {{ grid-template-columns: 1fr; }}
  .cp-num {{ font-size: 24px; }}
}}
</style>
"""


# --- Data (loaded once, computed once per hour) ------------------------------------

@st.cache_resource(show_spinner=False)
def load_data():
    return diagnosis.load_all()


@st.cache_data(show_spinner=False)
def pace_at(hour):
    return kpi.get_category_pace(TODAY_DAY, hour, sales_df=load_data()["sales_df"])


@st.cache_data(show_spinner=False)
def today_at(hour):
    return kpi.get_today_pace(day=TODAY_DAY, hour=hour, sales_df=load_data()["sales_df"])


@st.cache_data(show_spinner=False)
def stock_health_at(hour):
    return stock.get_stock_health_report(TODAY_DAY, hour, stock_df=load_data()["stock_df"])


@st.cache_data(show_spinner=False)
def last_pieces_at(hour):
    data = load_data()
    return stock.get_last_piece_alerts(TODAY_DAY, hour, stock_df=data["stock_df"],
                                       sales_df=data["sales_df"])


@st.cache_data(show_spinner=False)
def digest_at(hour):
    return digest.generate_digest(TODAY_DAY, hour, data=load_data())


# --- Small HTML builders -------------------------------------------------------------

def esc(text):
    return html.escape(str(text))


def pill(status):
    label, tone = STATUS[status]
    return f'<span class="cp-pill {tone}">{label}</span>'


def category_card(p, show_line=True):
    name = p["category"] if show_line else CATEGORY_PRODUCT[p["category"]]
    tone = STATUS[p["status"]][1]
    detail = f"{p['pct_vs_pace']:+.0f}% vs pace"
    if p["status"] in ("behind", "drifting") and p["needed_units_per_day"] > 0:
        detail += f" · needs {p['needed_units_per_day']:.1f}/day"
    return (
        f'<div class="cp-card {tone}">{pill(p["status"])}'
        f'<div class="cp-name">{esc(name)}</div>'
        f'<div class="cp-num">{p["units_sold_so_far"]}<span class="cp-of"> / {p["monthly_target"]}</span></div>'
        f'<div class="cp-small">{esc(detail)}</div></div>'
    )


def line_card(t):
    tone = STATUS[t["status"]][1]
    return (
        f'<div class="cp-card {tone}">{pill(t["status"])}'
        f'<div class="cp-name">{esc(t["line"])} <span class="cp-small">{esc(t["department"])}</span></div>'
        f'<div class="cp-num">{t["units_sold_today"]}<span class="cp-of"> / {t["expected_by_now"]:.0f}</span></div>'
        f'<div class="cp-small">sold today / expected by now</div></div>'
    )


def grid(cards):
    return '<div class="cp-grid">' + "".join(cards) + "</div>"


def heading(text, sub=""):
    sub_html = f' <span class="cp-sub">{esc(sub)}</span>' if sub else ""
    return f'<div class="cp-h">{esc(text)}{sub_html}</div>'


# --- Page sections ---------------------------------------------------------------------

def top_bar(hour):
    day = month_calendar()[TODAY_DAY - 1]
    date = pd.Timestamp(day["date"]).strftime("%A %d %B %Y")
    time_label = "20:00 (close)" if hour == STORE_HOURS[-1] else f"{hour + 1}:00"
    return (
        f'<div class="cp-bar"><div><span class="cp-brand">Category Pulse</span> '
        f'<span class="cp-meta">&nbsp;{esc(STORE_NAME)}</span></div>'
        f'<div class="cp-meta">{esc(date)} · day {TODAY_DAY} of the month · '
        f'<span class="cp-time">now {esc(time_label)}</span></div></div>'
    )


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
    core_alerts = [a for a in alerts if a["is_core_size"]]
    for a in core_alerts:
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
    labels = bars.mark_text(align="left", dx=4, fontSize=11, color="#1A1A1A").encode(
        text=alt.Text("Percent of expected:Q", format=".0f"), color=alt.value("#1A1A1A"))
    rule = alt.Chart(pd.DataFrame({"x": [100]})).mark_rule(color="#1A1A1A").encode(x="x:Q")

    return (bars + labels + rule).properties(
        height=22 * len(df), padding={"left": 40, "right": 12, "top": 4, "bottom": 4}
    ).configure_view(
        stroke=None, strokeWidth=0
    ).configure_axis(
        grid=False, labelFont="Inter", titleFont="Inter", labelFontSize=11, titleFontSize=11,
        labelColor="#1A1A1A", titleColor="#6B6B6B", domainColor="#E4E4E0", tickColor="#E4E4E0",
    ).configure(background="#F7F7F5")


def chat_section(hour):
    st.markdown(heading("Ask Category Pulse",
                        "answers are built from the store's own numbers"), unsafe_allow_html=True)
    st.session_state.setdefault("chat", [])

    question = None
    for i, q in enumerate(SUGGESTED_QUESTIONS):
        if st.button(q, key=f"suggested_{i}", use_container_width=True):
            question = q

    with st.form("ask", clear_on_submit=True, border=False):
        typed = st.text_input("Your question", placeholder="Type a question about today's numbers",
                              label_visibility="collapsed")
        if st.form_submit_button("Ask", type="primary") and typed.strip():
            question = typed.strip()

    if question:
        with st.spinner("Checking the numbers..."):
            answer, calls = agent.ask(question, current_hour=hour)
        st.session_state["chat"].append({"question": question, "answer": answer, "calls": calls})

    for turn in reversed(st.session_state["chat"]):
        st.markdown(f'<div class="cp-msg"><div class="cp-who">You asked</div>{esc(turn["question"])}</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="cp-who">Category Pulse</div>', unsafe_allow_html=True)
        st.markdown(turn["answer"])
        with st.expander("How I got this"):
            if not turn["calls"]:
                st.write("No data tools were called for this answer.")
            for call in turn["calls"]:
                st.markdown(f"**{call['tool']}** with `{json.dumps(call['input'])}`")
                st.code(json.dumps(call["result"], indent=2, default=str)[:4000], language="json")


# --- Page ----------------------------------------------------------------------------------

def main():
    st.markdown(CSS, unsafe_allow_html=True)
    bar = st.empty()
    hour = st.select_slider(
        "Step through the day", options=STORE_HOURS, value=DEFAULT_CURRENT_HOUR,
        format_func=lambda h: "20:00 (close)" if h == STORE_HOURS[-1] else f"{h + 1}:00",
    )
    bar.markdown(top_bar(hour), unsafe_allow_html=True)

    banner = alert_banner(hour)
    if banner:
        st.markdown(banner, unsafe_allow_html=True)

    pace = pace_at(hour)
    counts = {s: sum(1 for p in pace if p["status"] == s) for s in STATUS}
    on_track = counts["on_pace"] + counts["ahead"]
    st.markdown(
        heading("This month so far", "units sold / monthly target")
        + '<div class="cp-counts">'
        + f'<span class="cp-count">{pill("behind")} {counts["behind"]}</span>'
        + f'<span class="cp-count">{pill("drifting")} {counts["drifting"]}</span>'
        + f'<span class="cp-count">{pill("on_pace")} {on_track}</span>'
        + (f'<span class="cp-count">{pill("too_early")} {counts["too_early"]}</span>'
           if counts["too_early"] else "")
        + "</div>",
        unsafe_allow_html=True,
    )

    attention = sorted((p for p in pace if p["status"] in ("behind", "drifting")),
                       key=lambda p: (p["status"] != "behind", p["pct_vs_pace"]))
    if attention:
        st.markdown(heading("Needs attention") + grid(category_card(p) for p in attention),
                    unsafe_allow_html=True)

    with st.expander("All categories, by line", expanded=False):
        for line, department in LINES.items():
            cards = [category_card(p, show_line=False) for p in pace if p["line"] == line]
            st.markdown(heading(line, department) + grid(cards), unsafe_allow_html=True)

    st.markdown(heading("Today so far, by line", "units sold today / expected by now")
                + grid(line_card(t) for t in today_at(hour)), unsafe_allow_html=True)

    st.markdown(heading("Pace by category", "worst first"), unsafe_allow_html=True)
    # theme=None so Streamlit's default chart styling (gridlines etc.) doesn't override ours.
    st.altair_chart(pace_chart(pace), use_container_width=True, theme=None)

    chat_section(hour)

    title = "End-of-day summary" if hour == STORE_HOURS[-1] else "Summary so far today"
    with st.expander(title, expanded=False):
        for paragraph in digest_at(hour).split("\n\n"):
            st.markdown(paragraph)


main()
