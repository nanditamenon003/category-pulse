"""
Shared look and building blocks for the Category Pulse web app.

Everything visual that more than one page uses lives here: the palette and
styling, the chosen time of day, cached data lookups, and small HTML pieces
(status pills, cards, headings). Design rules from the spec: colour only for
status (green on track, amber drifting, red behind) plus one indigo accent
for interactive elements; one typeface (Inter); no shadows or gradients.
"""

import hashlib
import html
import pathlib
import re

import pandas as pd
import streamlit as st

import diagnosis
import digest
import kpi
import loyalty
import staffing
import stock
from store import demo_store

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
/* Streamlit Cloud adds "Fork" and GitHub buttons to the toolbar of public apps. */
[data-testid="stToolbarActions"] {{ display: none; }}
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
.cp-alert.amber {{ border-left-color: {AMBER}; }}

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

/* Guided tour card */
.cp-tour {{ background: {CARD}; border: 2px solid {ACCENT}; border-radius: 8px; padding: 12px 16px; }}
.cp-tour-step {{ font-size: 12px; font-weight: 700; color: {ACCENT}; text-transform: uppercase;
                letter-spacing: 0.04em; }}
.cp-tour-title {{ font-size: 16px; font-weight: 700; color: {TEXT}; margin: 2px 0 4px; }}
.cp-tour-text {{ font-size: 14px; color: {TEXT}; line-height: 1.55; }}
.st-key-tour_card {{ margin-bottom: 8px; }}

/* Welcome page */
.cp-hero {{ max-width: 760px; margin: 28px 0 20px; }}
.cp-hero-brand {{ font-size: 15px; font-weight: 700; color: {TEXT}; margin-bottom: 16px; }}
.cp-hero-title {{ font-size: 34px; font-weight: 700; color: {TEXT}; line-height: 1.2; margin-bottom: 12px; }}
.cp-hero-sub {{ font-size: 17px; color: {MUTED}; line-height: 1.55; }}
.cp-features {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px;
               margin-top: 32px; max-width: 900px; }}
.cp-feature {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 14px 16px; }}
.cp-feature-title {{ font-size: 15px; font-weight: 700; color: {TEXT}; margin-bottom: 4px; }}
.cp-feature-text {{ font-size: 14px; color: {TEXT}; line-height: 1.5; }}
.st-key-welcome_actions {{ margin-bottom: 4px; }}

/* Welcome guide, empty pages and numbered steps */
.cp-welcome-title {{ font-size: 20px; font-weight: 700; color: {TEXT}; margin: 6px 0 6px; }}
.cp-dots {{ display: flex; gap: 6px; margin: 14px 0 6px; }}
.cp-dot {{ width: 7px; height: 7px; border-radius: 4px; background: {BORDER}; }}
.cp-dot.on {{ width: 20px; background: {ACCENT}; }}
.cp-empty {{ background: {CARD}; border: 1px dashed {NEUTRAL}; border-radius: 8px; padding: 32px 22px;
            text-align: center; margin: 8px 0 14px; }}
.cp-empty-title {{ font-size: 18px; font-weight: 700; color: {TEXT}; margin-bottom: 6px; }}
.cp-empty-text {{ font-size: 14px; color: {MUTED}; max-width: 520px; margin: 0 auto; line-height: 1.55; }}
.cp-stepline {{ display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700;
               color: {TEXT}; margin: 26px 0 8px; }}
.cp-stepnum {{ width: 26px; height: 26px; border-radius: 13px; background: {TEXT}; color: #FFFFFF;
              font-size: 13px; display: inline-flex; align-items: center; justify-content: center; }}

/* Guide page */
.cp-term {{ font-size: 14px; color: {TEXT}; margin: 6px 0; line-height: 1.55; }}
.cp-term b {{ font-weight: 700; }}

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
  .cp-hero-title {{ font-size: 26px; }}
  .cp-hero-sub {{ font-size: 16px; }}
  .block-container {{ padding-top: 4rem; }}
  /* On phones the chat button shrinks to a round icon so it covers less content. */
  .st-key-chat_fab {{ right: 12px; bottom: 76px; }}
  .st-key-chat_fab button {{ width: 52px; height: 52px; padding: 0; border-radius: 26px;
                              justify-content: center; }}
  .st-key-chat_fab button [data-testid="stMarkdownContainer"] {{ display: none; }}
}}
</style>
"""


# --- The store being looked at, and the chosen time of day ---------------------------

@st.cache_resource(show_spinner="Setting up the Sample Store (first start only)...")
def load_data():
    # The demo's data files aren't stored in git: they regenerate identically
    # from the fixed seed, so a fresh deployment builds them on first start.
    return demo_store()


def current_store():
    """
    The store the pages show, or None:
      - during the guided tour, the Sample Store (the tour is written for it)
      - for someone signed in, their own uploaded data, or None until they upload
      - for guests, the Sample Store
    """
    if st.session_state.get("tour_step") is not None:
        return load_data()
    if st.session_state.get("member"):
        return st.session_state.get("my_store")
    return load_data()


# Saved choices that only make sense for one store, cleared when the store in use changes.
_STORE_SPECIFIC_KEYS = ("hour", "cat_department", "sell_category", "chat_chip")
_UPLOAD_KEYS = ("my_store", "upload_result", "upload_key", "upload_files")


def request_forget():
    """Remove the visitor's uploaded data on the next run."""
    st.session_state["forget_mine"] = True


def prepare_session():
    """
    Runs at the very top of app.py, before any widget is drawn (Streamlit only
    lets a widget's saved value be cleared before the widget exists): removes
    uploaded data if asked, and clears choices that belonged to a different
    store than the one about to be shown. Returns that store (or None).
    """
    if st.session_state.pop("forget_mine", False):
        for key in _UPLOAD_KEYS:
            st.session_state.pop(key, None)
        # Worked-out results for the uploaded data go too (the Sample Store's rebuild on demand).
        _lookup.clear()
        _slow_lookup.clear()
    store = current_store()
    store_id = store.id if store is not None else None
    if st.session_state.get("store_in_use", store_id) != store_id:
        for key in list(st.session_state):
            if key in _STORE_SPECIFIC_KEYS or str(key).startswith(("category_table_", "last_pick_")):
                del st.session_state[key]
    st.session_state["store_in_use"] = store_id
    return store

def current_hour():
    """The hour slot the whole app is looking at (10 = 10:00-11:00 ... 19 = 19:00-20:00)."""
    s = current_store()
    if st.session_state.get("hour") not in s.hours:
        st.session_state["hour"] = s.default_hour
    return st.session_state["hour"]


def time_label(hour):
    """Clock time at the end of an hour slot, e.g. slot 16 -> '17:00'."""
    return f"{hour + 1}:00 (close)" if hour == current_store().hours[-1] else f"{hour + 1}:00"


def today_label():
    """The day being shown: the date for a store's own data, the weekday for the Sample Store."""
    s = current_store()
    return s.date(s.today_day).strftime("%A" if s.is_demo else "%a %d %b")


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


# --- Data lookups, each worked out once per store and time ------------------------------
# Results are cached by the store's id plus the lookup's arguments, so one
# store's numbers can never be shown for another.

def _risky_sizes(s, hour):
    """Sizes likely to run out before the next scheduled delivery (a projection), all categories."""
    rows, next_delivery = [], None
    for category in s.categories:
        cover = stock.get_days_of_cover(category, hour=hour, store=s)
        next_delivery = cover["next_scheduled_delivery_day"]
        for size in cover["sizes"]:
            if size["likely_out_before_next_delivery"]:
                rows.append({"category": category, **size})
    return sorted(rows, key=lambda r: r["projected_days_of_cover"]), next_delivery


def _zones(s, hour):
    """Visitors today and visitors-vs-buyers for each floor zone."""
    return [
        {
            "footfall": kpi.get_footfall(zone=zone, hour=hour, store=s),
            "conversion": (kpi.get_conversion_metrics(zone=zone, hour=hour, store=s)
                           if s.has_transactions else None),
        }
        for zone in s.departments
    ]


def _staffing(s, day):
    """Staffing advice for `day`, plus each zone's hourly visitor pattern for the chart."""
    rec = staffing.get_staffing_recommendation(day, store=s)
    busy = rec["day_type"] != "weekday"
    patterns = {z: staffing.get_peak_hours(z, busy, before_day=day, store=s) for z in s.departments}
    return rec, patterns


def _category_detail(s, category, hour):
    """Everything the category pop-up shows; None for parts the store's data can't support."""
    has_stock = s.has_stock
    return {
        "pace": kpi.get_category_pace(hour=hour, category=category, store=s)[0],
        "stock": stock.get_stock_status(category, hour=hour, store=s) if has_stock else None,
        "health": stock.check_size_runs(category, hour=hour, store=s) if has_stock else None,
        "history": stock.get_stock_history(category, hour=hour, store=s) if has_stock else None,
        "cover": stock.get_days_of_cover(category, hour=hour, store=s) if has_stock else None,
        "conversion": (kpi.get_conversion_metrics(category=category, hour=hour, store=s)
                       if s.has_footfall and s.has_transactions else None),
        "playbook": loyalty.get_tier_playbook(category, store=s) if s.has_loyalty else None,
    }


LOOKUPS = {
    "pace": lambda s, hour: kpi.get_category_pace(hour=hour, store=s),
    "today": lambda s, hour: kpi.get_today_pace(hour=hour, store=s),
    "stock_health": lambda s, hour: stock.get_stock_health_report(hour=hour, store=s),
    "last_pieces": lambda s, hour: stock.get_last_piece_alerts(hour=hour, store=s),
    "digest": lambda s, hour: digest.generate_digest(hour=hour, store=s),
    "diagnoses": lambda s, hour: {d["category"]: d for d in diagnosis.diagnose_store(hour=hour, store=s)},
    "contribution": lambda s, hour: kpi.get_contribution(hour=hour, store=s),
    "playbook": lambda s, category: loyalty.get_tier_playbook(category, store=s),
    "cross_sell": lambda s, hour: {i["category"]: i for i in loyalty.get_cross_sell_ideas(hour=hour, store=s)},
    "risky_sizes": _risky_sizes,
    "zones": _zones,
    "staffing": _staffing,
    "category_detail": _category_detail,
}


# A fingerprint of the app's code, part of every cache key: after an update,
# results worked out by the previous version are never shown again.
CODE_VERSION = hashlib.sha1(b"".join(
    p.read_bytes() for p in sorted(pathlib.Path(__file__).parent.glob("*.py")))).hexdigest()[:12]


# Results are kept for an hour at most, so an uploaded store's figures don't
# linger in the app's memory.
@st.cache_data(show_spinner=False, ttl="1h")
def _lookup(store_id, code_version, name, args, _store):
    return LOOKUPS[name](_store, *args)


@st.cache_data(show_spinner="Working it out...", ttl="1h")
def _slow_lookup(store_id, code_version, name, args, _store):
    return LOOKUPS[name](_store, *args)


def _get(name, *args, slow=False):
    s = current_store()
    return (_slow_lookup if slow else _lookup)(s.id, CODE_VERSION, name, args, s)


def pace_at(hour):
    return _get("pace", hour)


def today_at(hour):
    return _get("today", hour)


def stock_health_at(hour):
    return _get("stock_health", hour)


def last_pieces_at(hour):
    return _get("last_pieces", hour)


def digest_at(hour):
    return _get("digest", hour)


def diagnoses_at(hour):
    """Likely cause, evidence and action for every category, keyed by category."""
    return _get("diagnoses", hour)


def contribution_at(hour):
    return _get("contribution", hour)


def playbook_for(category):
    return _get("playbook", category)


def cross_sell_at(hour):
    return _get("cross_sell", hour)


def risky_sizes_at(hour):
    return _get("risky_sizes", hour, slow=True)


def zones_at(hour):
    return _get("zones", hour)


def staffing_for(day):
    return _get("staffing", day)


def category_detail_at(category, hour):
    return _get("category_detail", category, hour, slow=True)

# --- Small HTML builders -------------------------------------------------------------

def esc(text):
    return html.escape(str(text))


def slug(text):
    """A widget-key-safe version of a name, e.g. 'Men Casual Trousers' -> 'Men_Casual_Trousers'."""
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
    name = p["category"] if show_line else current_store().category_product[p["category"]]
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
