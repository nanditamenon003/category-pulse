"""
Shared look and building blocks for the Category Pulse web app.

Everything visual that more than one page uses lives here: the palette and
styling, the chosen time of day, cached data lookups, and small HTML pieces
(status pills, cards, headings). Design rules from the spec: colour only for
status (green on track, amber drifting, red behind) plus one indigo accent
for interactive elements; one typeface (Inter); no shadows or gradients.
"""

import html
import re

import pandas as pd
import streamlit as st

import diagnosis
import digest
import generate_data
import kpi
import loyalty
import staffing
import stock
from config import (
    CATEGORIES,
    CATEGORY_PRODUCT,
    DEFAULT_CURRENT_HOUR,
    DEPARTMENTS,
    STORE_HOURS,
    TODAY_DAY,
    month_calendar,
)

# Palette from the spec. Green is one shade darker than the spec's #1E8E3E so
# white label text on it meets accessibility contrast (4.2:1 -> 5.0:1).
RED, AMBER, GREEN, ACCENT = "#D93025", "#E0A100", "#188038", "#3F51B5"
TEXT, MUTED, BORDER, CARD, PAGE, NEUTRAL = "#1A1A1A", "#6B6B6B", "#E4E4E0", "#FFFFFF", "#F7F7F5", "#B8B8B2"

STATUS = {
    "behind": ("Behind", "red"),
    "drifting": ("Drifting", "amber"),
    "on_pace": ("On pace", "green"),
    "ahead": ("Ahead", "green"),
    "too_early": ("Too early", "neutral"),
}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
/* One typeface everywhere, except icon glyphs and code blocks. Pop-ups (dialogs,
   popovers) are drawn outside .stApp, so they're listed too. */
.stApp, .stApp *:not([data-testid="stIconMaterial"]),
[role="dialog"], [role="dialog"] *:not([data-testid="stIconMaterial"]),
[data-testid="stPopoverBody"], [data-testid="stPopoverBody"] *:not([data-testid="stIconMaterial"]) {{
    font-family: 'Inter', sans-serif;
}}
.stApp pre, .stApp pre *, .stApp code, .stApp code *,
[role="dialog"] pre, [role="dialog"] pre *, [role="dialog"] code, [role="dialog"] code * {{
    font-family: monospace !important;
}}

/* Flat pop-ups: a hairline border instead of a drop shadow. */
[role="dialog"], [data-testid="stPopoverBody"] {{ box-shadow: none !important; border: 1px solid {BORDER}; }}

/* Keep Streamlit's header and toolbar: the page menu lives inside them. Developer
   buttons are already off via toolbarMode = "minimal" in .streamlit/config.toml. */
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stAppDeployButton"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: {PAGE}; border-bottom: 1px solid {BORDER}; }}
.block-container {{ padding-top: 4.5rem; padding-bottom: 6rem; max-width: 1100px; }}

.cp-brand {{ font-size: 20px; font-weight: 700; color: {TEXT}; }}
.cp-meta {{ font-size: 13px; color: {MUTED}; }}
.cp-datalabel {{ display: inline-block; font-size: 12px; color: {MUTED}; border: 1px solid {BORDER};
                border-radius: 10px; padding: 1px 8px; margin-left: 6px; }}

.cp-title {{ font-size: 24px; font-weight: 700; color: {TEXT}; margin: 8px 0 2px; }}
.cp-intro {{ font-size: 14px; color: {MUTED}; margin-bottom: 12px; }}
.cp-h {{ font-size: 13px; font-weight: 700; color: {MUTED}; text-transform: uppercase;
        letter-spacing: 0.04em; margin: 22px 0 8px; }}
.cp-sub {{ font-size: 12px; color: {MUTED}; font-weight: 400; text-transform: none; letter-spacing: 0; }}

.cp-alert {{ background: {CARD}; border: 1px solid {BORDER}; border-left: 4px solid {RED};
            border-radius: 0; padding: 10px 14px; margin: 8px 0; }}
.cp-alert-title {{ font-size: 14px; font-weight: 700; color: {TEXT}; margin-bottom: 4px; }}
.cp-alert ul {{ margin: 0; padding-left: 18px; }}
.cp-alert li {{ font-size: 13px; color: {TEXT}; margin: 2px 0; }}

.cp-counts {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 4px 0 0; }}
.cp-count {{ font-size: 13px; color: {TEXT}; }}

.cp-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(175px, 1fr)); gap: 8px; }}
.cp-card {{ background: {CARD}; border: 1px solid {BORDER}; border-left: 4px solid {NEUTRAL};
           border-radius: 0; padding: 10px 12px; }}
.cp-card.red {{ border-left-color: {RED}; }}
.cp-card.amber {{ border-left-color: {AMBER}; }}
.cp-card.green {{ border-left-color: {GREEN}; }}
.cp-name {{ font-size: 13px; color: {TEXT}; margin: 6px 0 2px; line-height: 1.3; }}
.cp-num {{ font-size: 26px; font-weight: 700; color: {TEXT}; line-height: 1.1; }}
.cp-of {{ font-size: 14px; font-weight: 400; color: {MUTED}; }}
.cp-small {{ font-size: 12px; color: {MUTED}; margin-top: 2px; }}

.cp-pill {{ display: inline-block; font-size: 11px; font-weight: 700; text-transform: uppercase;
           letter-spacing: 0.04em; padding: 2px 8px; border-radius: 10px; }}
.cp-pill.red {{ background: {RED}; color: #FFFFFF; }}
.cp-pill.amber {{ background: {AMBER}; color: {TEXT}; }}
.cp-pill.green {{ background: {GREEN}; color: #FFFFFF; }}
.cp-pill.neutral {{ background: {BORDER}; color: {TEXT}; }}

.cp-you {{ background: {PAGE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 12px;
          margin: 10px 0 6px auto; max-width: 80%; width: fit-content; font-size: 14px; }}
.cp-who {{ font-size: 12px; font-weight: 700; color: {MUTED}; margin: 4px 0 2px; }}

/* Headline numbers */
.cp-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }}
.cp-kpi {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 12px 14px; }}
.cp-kpi-label {{ font-size: 12px; color: {MUTED}; }}
.cp-kpi-num {{ font-size: 28px; font-weight: 700; color: {TEXT}; line-height: 1.2; }}
.cp-kpi-num.red {{ color: {RED}; }}
.cp-kpi-sub {{ font-size: 12px; color: {MUTED}; }}

/* Progress bar: sold vs monthly target, with a tick where it should be by now */
.cp-track {{ position: relative; height: 6px; background: {BORDER}; border-radius: 3px;
            margin: 8px 0 4px; max-width: 360px; }}
.cp-fill {{ height: 6px; background: {TEXT}; border-radius: 3px; }}
.cp-mark {{ position: absolute; top: -4px; width: 2px; height: 14px; background: {MUTED}; }}

/* Cause tag, e.g. "Broken size run" */
.cp-chip {{ display: inline-block; font-size: 11px; color: {TEXT}; border: 1px solid #C9C9C4;
           border-radius: 10px; padding: 1px 8px; margin-left: 4px; }}

/* A problem row in "Needs action now" */
.cp-row {{ background: {CARD}; border: 1px solid {BORDER}; border-left: 4px solid {NEUTRAL};
          border-radius: 0; padding: 12px 14px; display: flex; justify-content: space-between;
          align-items: center; gap: 12px; }}
.cp-row.red {{ border-left-color: {RED}; }}
.cp-row.amber {{ border-left-color: {AMBER}; }}
.cp-row.green {{ border-left-color: {GREEN}; }}
.cp-row-name {{ font-size: 15px; font-weight: 700; color: {TEXT}; margin-top: 4px; }}
.cp-row-text {{ font-size: 13px; color: {TEXT}; margin-top: 2px; }}
.cp-chev {{ font-size: 26px; color: {MUTED}; line-height: 1; }}

/* Clickable cards: an invisible button laid over the whole card. Streamlit gives text
   blocks a negative bottom margin, which made cards overlap the gap below them. */
[class*="st-key-click_"] {{ position: relative; }}
[class*="st-key-click_"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0 !important; }}
[class*="st-key-click_"] [class*="st-key-open_"] {{ position: absolute; inset: 0; margin: 0; z-index: 2;
                                                   width: 100% !important; height: 100% !important; }}
[class*="st-key-click_"] [class*="st-key-open_"] div,
[class*="st-key-click_"] [class*="st-key-open_"] button {{ width: 100% !important; height: 100% !important;
                                                          max-width: none !important; }}
[class*="st-key-click_"] [class*="st-key-open_"] button {{ opacity: 0; cursor: pointer; }}
[class*="st-key-click_"]:hover .cp-row, [class*="st-key-click_"]:hover .cp-card {{
    border-top-color: {ACCENT}; border-right-color: {ACCENT}; border-bottom-color: {ACCENT}; }}

/* "Start here" panel and small panels */
.cp-panel {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 14px 16px; }}
.cp-panel-title {{ font-size: 15px; font-weight: 700; color: {TEXT}; margin-bottom: 4px; }}
.cp-panel p, .cp-panel li {{ font-size: 14px; color: {TEXT}; margin: 2px 0; }}
.cp-do {{ background: {PAGE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 10px 12px;
         font-size: 14px; color: {TEXT}; margin-top: 8px; }}

/* Category card rows: cards wrap across the row and share it evenly. */
[class*="st-key-cards_"] {{ row-gap: 8px !important; }}
[class*="st-key-cards_"] > div:has(> [class*="st-key-click_cat_"]),
[class*="st-key-cards_"] > [class*="st-key-click_cat_"] {{ flex: 1 1 190px; min-width: 180px; max-width: 280px; }}
[class*="st-key-click_cat_"] .cp-card {{ height: 100%; }}
.cp-showing {{ font-size: 13px; color: {MUTED}; margin: 6px 0 4px; }}

/* The end-of-day summary, set for easy reading. */
.cp-digest {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 18px 22px;
             max-width: 760px; }}
.cp-digest p {{ font-size: 16px; line-height: 1.65; color: {TEXT}; margin: 0 0 12px; }}
.cp-digest p:last-child {{ margin-bottom: 0; }}
.cp-check {{ font-size: 13px; color: {MUTED}; margin: 4px 0 8px; }}

/* Text links (tertiary buttons) use the accent colour; pop-up bullets a touch smaller. */
.stButton button[kind="tertiary"], .stButton button[kind="tertiary"] p {{ color: {ACCENT}; }}
[role="dialog"] li, [role="dialog"] li p {{ font-size: 14px; }}

/* Floating chat button, raised clear of Streamlit Cloud's corner badge. */
.st-key-chat_fab {{ position: fixed; right: 24px; bottom: 84px; z-index: 1000; width: auto; }}
.st-key-chat_fab button {{ border-radius: 22px; padding: 10px 18px; }}

@media (max-width: 640px) {{
  .cp-grid {{ grid-template-columns: 1fr; }}
  /* Filter buttons wrap onto a new line instead of scrolling sideways out of view. */
  [data-testid="stButtonGroup"] > div:has(> button) {{ flex-wrap: wrap; overflow-x: visible; row-gap: 6px; }}
  [class*="st-key-cards_"] > div:has(> [class*="st-key-click_cat_"]),
  [class*="st-key-cards_"] > [class*="st-key-click_cat_"] {{ max-width: none; flex-basis: 100%; }}
  .cp-num {{ font-size: 24px; }}
  .block-container {{ padding-top: 4rem; }}
  /* On phones the chat button shrinks to a round icon so it covers less content. */
  .st-key-chat_fab {{ right: 12px; bottom: 76px; }}
  .st-key-chat_fab button {{ width: 52px; height: 52px; padding: 0; border-radius: 26px;
                              justify-content: center; }}
  .st-key-chat_fab button [data-testid="stMarkdownContainer"] {{ display: none; }}
}}
</style>
"""


# --- The chosen time of day, shared by every page -----------------------------------

def current_hour():
    """The hour slot the whole app is looking at (10 = 10:00-11:00 ... 19 = 19:00-20:00)."""
    return st.session_state.setdefault("hour", DEFAULT_CURRENT_HOUR)


def time_label(hour):
    """Clock time at the end of an hour slot, e.g. slot 16 -> '17:00'."""
    return "20:00 (close)" if hour == STORE_HOURS[-1] else f"{hour + 1}:00"


def today_label():
    day = month_calendar()[TODAY_DAY - 1]
    return pd.Timestamp(day["date"]).strftime("%a %d %b")


# --- Data (loaded once, computed once per hour) --------------------------------------

@st.cache_resource(show_spinner="Setting up the simulated store data (first start only)...")
def load_data():
    # The data files aren't stored in git: they regenerate identically from
    # the fixed seed, so a fresh deployment builds them on first start.
    if not generate_data.data_is_present():
        generate_data.generate_all()
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


@st.cache_data(show_spinner=False)
def diagnoses_at(hour):
    """Likely cause, evidence and action for every category, keyed by category."""
    return {d["category"]: d for d in diagnosis.diagnose_store(TODAY_DAY, hour, data=load_data())}


@st.cache_data(show_spinner=False)
def contribution_at(hour):
    return kpi.get_contribution(TODAY_DAY, hour, sales_df=load_data()["sales_df"])


def inr(value):
    """Rupees with Indian digit grouping, e.g. 5432440 -> '₹54,32,440'."""
    digits = str(int(round(value)))
    if len(digits) <= 3:
        return f"₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups) + "," + tail


@st.cache_data(show_spinner=False)
def playbook_for(category):
    return loyalty.get_tier_playbook(category)


@st.cache_data(show_spinner=False)
def cross_sell_at(hour):
    data = load_data()
    ideas = loyalty.get_cross_sell_ideas(TODAY_DAY, hour, sales_df=data["sales_df"],
                                         stock_df=data["stock_df"])
    return {i["category"]: i for i in ideas}


@st.cache_data(show_spinner="Checking every size...")
def risky_sizes_at(hour):
    """Sizes likely to run out before the next scheduled delivery (a projection), all categories."""
    data = load_data()
    rows, next_delivery = [], None
    for category in CATEGORIES:
        cover = stock.get_days_of_cover(category, TODAY_DAY, hour, stock_df=data["stock_df"],
                                        sales_df=data["sales_df"])
        next_delivery = cover["next_scheduled_delivery_day"]
        for s in cover["sizes"]:
            if s["likely_out_before_next_delivery"]:
                rows.append({"category": category, **s})
    return sorted(rows, key=lambda r: r["projected_days_of_cover"]), next_delivery


@st.cache_data(show_spinner=False)
def zones_at(hour):
    """Visitors today and visitors-vs-buyers for each floor zone."""
    data = load_data()
    return [
        {
            "footfall": kpi.get_footfall(zone=zone, day=TODAY_DAY, hour=hour,
                                         footfall_df=data["footfall_df"]),
            "conversion": kpi.get_conversion_metrics(zone=zone, day=TODAY_DAY, hour=hour,
                                                     sales_df=data["sales_df"],
                                                     footfall_df=data["footfall_df"]),
        }
        for zone in DEPARTMENTS
    ]


@st.cache_data(show_spinner=False)
def staffing_for(day):
    """Staffing advice for `day`, plus each zone's hourly visitor pattern for the chart."""
    footfall_df = load_data()["footfall_df"]
    rec = staffing.get_staffing_recommendation(day, footfall_df=footfall_df)
    busy = rec["day_type"] != "weekday"
    patterns = {z: staffing.get_peak_hours(z, busy, before_day=day, footfall_df=footfall_df)
                for z in DEPARTMENTS}
    return rec, patterns


@st.cache_data(show_spinner="Looking up this category...")
def category_detail_at(category, hour):
    """Everything the category pop-up shows, fetched once per category and hour."""
    data = load_data()
    sales_df, stock_df, footfall_df = data["sales_df"], data["stock_df"], data["footfall_df"]
    return {
        "pace": kpi.get_category_pace(TODAY_DAY, hour, category=category, sales_df=sales_df)[0],
        "stock": stock.get_stock_status(category, TODAY_DAY, hour, stock_df=stock_df),
        "health": stock.check_size_runs(category, TODAY_DAY, hour, stock_df=stock_df),
        "history": stock.get_stock_history(category, TODAY_DAY, hour, stock_df=stock_df,
                                           sales_df=sales_df),
        "cover": stock.get_days_of_cover(category, TODAY_DAY, hour, stock_df=stock_df,
                                         sales_df=sales_df),
        "conversion": kpi.get_conversion_metrics(category=category, day=TODAY_DAY, hour=hour,
                                                 sales_df=sales_df, footfall_df=footfall_df),
        "playbook": loyalty.get_tier_playbook(category),
    }


# --- Small HTML builders -------------------------------------------------------------

def esc(text):
    return html.escape(str(text))


def slug(text):
    """A widget-key-safe version of a name, e.g. 'THM Non Denim Bottom' -> 'THM_Non_Denim_Bottom'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_")


def html_block(markup):
    st.markdown(markup, unsafe_allow_html=True)


def pill(status):
    label, tone = STATUS[status]
    return f'<span class="cp-pill {tone}">{label}</span>'


def page_title(title, intro=""):
    intro_html = f'<div class="cp-intro">{esc(intro)}</div>' if intro else ""
    html_block(f'<div class="cp-title">{esc(title)}</div>{intro_html}')


def heading(text, sub=""):
    sub_html = f' <span class="cp-sub">{esc(sub)}</span>' if sub else ""
    return f'<div class="cp-h">{esc(text)}{sub_html}</div>'


def category_card(p, show_line=True, cause=None):
    name = p["category"] if show_line else CATEGORY_PRODUCT[p["category"]]
    tone = STATUS[p["status"]][1]
    detail = f"{p['pct_vs_pace']:+.0f}% vs pace"
    if p["status"] in ("behind", "drifting") and p["needed_units_per_day"] > 0:
        detail += f" · needs {p['needed_units_per_day']:.1f}/day"
    chip = cause_chip(cause) if p["status"] in ("behind", "drifting") else ""
    return (
        f'<div class="cp-card {tone}">{pill(p["status"])}{chip}'
        f'<div class="cp-name">{esc(name)}</div>'
        f'<div class="cp-num">{p["units_sold_so_far"]}<span class="cp-of"> / {p["monthly_target"]}</span></div>'
        f'{progress_bar(p["units_sold_so_far"], p["monthly_target"], p["expected_units_by_now"])}'
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


CAUSE_TAG = {
    "stockout": "Sold out",
    "broken_size_run": "Broken size run",
    "traffic_drop": "Fewer visitors",
    "conversion_drop": "Fewer buyers",
    "unclear": "No clear cause yet",
}


def cause_chip(cause):
    tag = CAUSE_TAG.get(cause)
    return f'<span class="cp-chip">{esc(tag)}</span>' if tag else ""


def progress_bar(sold, target, expected):
    """Sold vs target as a bar, with a tick marking where it should be by now."""
    fill = min(sold / target, 1.0) * 100 if target else 0
    mark = min(expected / target, 1.0) * 100 if target else 0
    return (f'<div class="cp-track"><div class="cp-fill" style="width:{fill:.0f}%"></div>'
            f'<div class="cp-mark" style="left:{mark:.0f}%"></div></div>')


def kpi_card(label, value, sub="", tone=""):
    return (f'<div class="cp-kpi"><div class="cp-kpi-label">{esc(label)}</div>'
            f'<div class="cp-kpi-num {tone}">{esc(value)}</div>'
            f'<div class="cp-kpi-sub">{esc(sub)}</div></div>')


def clickable(key, markup, label):
    """
    Shows `markup` with an invisible button laid over it, so the whole card is
    clickable. Returns True when clicked. `label` is read out by screen readers.
    """
    with st.container(key=f"click_{key}"):
        html_block(markup)
        return st.button(label, key=f"open_{key}")
