"""
The pages and pop-ups of the Category Pulse web app.

Each page function draws one section of the site; app.py puts them in the
top menu. Pop-ups (st.dialog) sit on top of whatever page is open: the
category detail and the chat. Only one pop-up can be open at a time, so
"Ask the AI about this category" closes the detail and opens the chat.
"""

import hashlib
import json

import altair as alt
import pandas as pd
import streamlit as st

import agent
import tour
import upload
from config import DEMO_QUESTION_LIMIT
from store import WEEKDAY_NAMES, StoreDataError
from ui import (
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
    line_card,
    pace_at,
    page_title,
    pill,
    playbook_for,
    progress_bar,
    request_forget,
    request_store,
    risky_sizes_at,
    slug,
    staffing_for,
    stock_health_at,
    time_label,
    today_at,
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
               f'{esc(add)} (the Your data page has the template).</p></div>')


# --- Today ---------------------------------------------------------------------------------

def _your_store_note(s):
    html_block(
        '<div class="cp-panel"><div class="cp-panel-title">Your store</div>'
        f'<p>Showing your uploaded data for {esc(s.month_name)} {s.month_start.year}, as at the '
        f'close of day {s.today_day}. It\'s read for this session only and isn\'t saved. To keep it '
        'private, the AI chat and the tour aren\'t used with your own data. Switch stores at the top '
        'of the page.</p>'
        '</div>'
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
    tone = STATUS[p["status"]][1]
    evidence = diag["evidence"][1] if len(diag["evidence"]) > 1 and diag["cause"] in (
        "stockout", "broken_size_run") else diag["headline"]
    return (
        f'<div class="cp-row {tone}"><div style="flex:1;min-width:0">'
        f'{pill(p["status"])}{cause_chip(diag["cause"])}'
        f'<div class="cp-row-name">{esc(p["category"])}</div>'
        f'{progress_bar(p["units_sold_so_far"], p["monthly_target"], p["expected_units_by_now"])}'
        f'<div class="cp-small">{p["units_sold_so_far"]} of {p["monthly_target"]} · '
        f'{abs(p["pct_vs_pace"]):.0f}% {"behind" if p["pct_vs_pace"] < 0 else "ahead of"} pace · '
        f'needs {p["needed_units_per_day"]:.1f}/day, selling {p["actual_units_per_day"]:.1f}/day</div>'
        f'<div class="cp-row-text">{esc(evidence)}</div>'
        f'</div><div class="cp-chev">&rsaquo;</div></div>'
    )


def _when_dropped(alert):
    """When a size hit its last piece: the hour if stock is counted hourly, else just 'today'."""
    if len(current_store().stock_hours_by_day.get(alert["day"], [])) > 1:
        return f"since {alert['hour']}:00-{alert['hour'] + 1}:00"
    return "by today's close"


def _compact_row(tone, title, text):
    return (f'<div class="cp-row {tone}"><div style="flex:1;min-width:0">'
            f'<div class="cp-row-text"><b>{esc(title)}</b>: {esc(text)}</div></div>'
            f'<div class="cp-chev">&rsaquo;</div></div>')


def today_page():
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

    sold = sum(p["units_sold_so_far"] for p in pace)
    expected = sum(p["expected_units_by_now"] for p in pace)
    target = sum(p["monthly_target"] for p in pace)
    projected = sum(p["projected_month_end_if_current_rate_continues"] for p in pace)
    behind = sorted((p for p in pace if p["status"] == "behind"), key=lambda p: p["pct_vs_pace"])
    drifting = sorted((p for p in pace if p["status"] == "drifting"), key=lambda p: p["pct_vs_pace"])
    today_sold = sum(t["units_sold_today"] for t in lines_today)
    today_expected = sum(t["expected_by_now"] for t in lines_today)

    html_block('<div class="cp-kpis">'
               + kpi_card("Month so far", f"{sold:,}", f"of about {expected:,.0f} expected by now")
               + kpi_card("Month-end at this rate", f"{projected:,}", f"target {target:,} (a projection)")
               + kpi_card("Need action", f"{len(behind)} behind", f"{len(drifting)} drifting",
                          tone="red" if behind else "")
               + kpi_card("Today so far", f"{today_sold}", f"of about {today_expected:.0f} by now")
               + "</div>")

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
            text = f"{abs(p['pct_vs_pace']):.0f}% behind pace, needs {p['needed_units_per_day']:.1f}/day"
            if clickable(f"drift_{slug(p['category'])}", _compact_row("amber", p["category"], text),
                         f"Open {p['category']}"):
                category_dialog(p["category"])

    html_block(heading("Today so far, by line", "units sold today / expected by now")
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

    if current_store().is_demo and st.button("Ask the AI about this category", icon=":material/forum:",
                                             key="ask_about"):
        status_word = STATUS[p["status"]][0].lower()
        st.session_state["chat_pending"] = (f"{category} is {status_word} this month. Why, and what "
                                            f"should the floor team do about it?")
        st.session_state["open_chat"] = True
        st.rerun()


def _why_tab(p, diag):
    html_block('<div class="cp-kpis">'
               + kpi_card("Sold this month", f"{p['units_sold_so_far']}", f"of {p['monthly_target']} target")
               + kpi_card("Expected by now", f"{p['expected_units_by_now']:.0f}",
                          f"{p['pct_vs_pace']:+.0f}% vs pace")
               + kpi_card("Selling per day", f"{p['actual_units_per_day']:.1f}",
                          f"needs {p['needed_units_per_day']:.1f}/day to hit target")
               + kpi_card("Month-end at this rate",
                          f"{p['projected_month_end_if_current_rate_continues']}",
                          "a projection, not a result")
               + "</div>"
               + progress_bar(p["units_sold_so_far"], p["monthly_target"], p["expected_units_by_now"]))

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
        _not_in_data("stock counts", "add a Stock sheet")
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
        _not_in_data("visitor and bill counts", "add a Visitors sheet and Bills to the Sales sheet")
        return
    recent, baseline = c["recent_last_3_days_and_today"], c["baseline_earlier_this_month"]
    change = c["visitors_change_vs_typical_pct"]
    html_block('<div class="cp-kpis">'
               + kpi_card(f"Visitors to {c['zone']}", f"{recent['zone_visitors']}",
                          f"last 3 days + today, typical {c['typical_visitors_for_recent_days']}"
                          f" ({change:+.0f}%)" if change is not None else "")
               + kpi_card("Share who bought here", f"{recent['conversion_rate_pct']}%",
                          f"was {baseline['conversion_rate_pct']}% earlier this month")
               + kpi_card("Units per sale", f"{recent['units_per_transaction'] or 0}",
                          f"was {baseline['units_per_transaction'] or 0}")
               + "</div>")
    html_block(f'<div class="cp-do"><b>Reading:</b> {esc(c["explanation"])}</div>')
    html_block('<div class="cp-small" style="margin-top:8px">Visitors are counted per floor zone, so '
               'this compares the category with everyone who visited its floor.</div>')


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
        _not_in_data("loyalty tier figures", "add a Loyalty sheet")
    else:
        _tier_table(detail["playbook"])


def _tier_table(playbook):
    table = pd.DataFrame([
        {
            "Tier": t["tier"],
            "Takes up cross-sells": f"{t['cross_sell_response_rate_pct']:.0f}%",
            "Share of shoppers": f"{t['share_of_transactions_pct']}%",
            "Offer at the till": t["offer_at_the_till"],
        }
        for t in playbook["tiers_ranked_by_response"]
    ])
    st.dataframe(table, hide_index=True, width="stretch")
    html_block('<div class="cp-small">Targeting is by loyalty tier only. No individual customer data '
               'is used, by design.</div>')


# --- Other pages ------------------------------------------------------------------------------

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


STATUS_FILTER = {"Behind": "behind", "Drifting": "drifting", "On pace": "on_pace",
                 "Ahead": "ahead", "Too early": "too_early"}
SORT_ORDERS = {
    "By line": None,
    "Worst first": lambda p: p["pct_vs_pace"],
    "Best first": lambda p: -p["pct_vs_pace"],
    "Biggest target": lambda p: -p["monthly_target"],
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
            "Progress": round(p["units_sold_so_far"] / p["monthly_target"] * 100),
            "vs pace": p["pct_vs_pace"],
            "Per day": p["actual_units_per_day"],
            "Needs per day": p["needed_units_per_day"],
            "Month-end (projection)": p["projected_month_end_if_current_rate_continues"],
        }
        for p in shown
    ])
    status_colour = {"Behind": RED, "Drifting": "#8A6300", "On pace": GREEN, "Ahead": GREEN}
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
            "vs pace": st.column_config.NumberColumn("vs pace", format="%+.0f%%"),
            "Per day": st.column_config.NumberColumn(format="%.1f"),
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
    hour = current_hour()
    page_title("Categories", "Every category's month-to-date pace. Tap a card or a row for the "
                             "full picture.")
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
            department = st.segmented_control("Department", ["All"] + current_store().departments,
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
                html_block(heading(line, dept))
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
    hour = current_hour()
    page_title("Stock", "What's on the shelf, what's missing, and what's about to run out.")
    s = current_store()
    if not s.has_stock:
        _not_in_data("stock counts", "add a Stock sheet with units on hand by category and size")
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
               + kpi_card("Likely to run out", f"{len(risky)}", "sizes, before the next delivery")
               + kpi_card("Next delivery", "Not set" if no_schedule else
                          (f"Day {next_delivery}" if next_delivery else "None this month"),
                          "add a Delivery day in Settings" if no_schedule else delivery_weekday)
               + "</div>")

    html_block(heading("Stock problems", "tap for sizes and deliveries"))
    if not problems:
        html_block('<div class="cp-panel"><p>No stockouts or broken size runs right now.</p></div>')
    for r in problems:
        tone = STATUS[pace[r["category"]]["status"]][1]
        if clickable(f"stk_{slug(r['category'])}", _compact_row(tone, r["category"], _stock_problem_text(r)),
                     f"Open {r['category']}"):
            category_dialog(r["category"])

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

    html_block(heading("Likely to run out before the next delivery",
                       "a projection from each size's selling rate over the last 7 days"))
    if no_schedule:
        _not_in_data("the weekday deliveries arrive", "add a Delivery day in the Settings sheet")
    elif not risky:
        html_block('<div class="cp-panel"><p>No size looks likely to run out before the next '
                   'delivery.</p></div>')
    else:
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


# --- Floor and staff page ---------------------------------------------------------------------

READING_LABEL = {
    "normal": "normal",
    "possible_dip": "possible dip, not proven",
    "conversion_problem": "fewer are buying",
    "traffic_problem": "fewer visitors",
    "traffic_and_conversion_down": "fewer visitors and fewer buying",
    "too_few_sales_to_judge": "too few sales to judge",
}


def _hour_chart(pattern):
    peaks = set()
    for window in pattern["peak_windows"]:
        start, end = (int(t.split(":")[0]) for t in window.split("-"))
        peaks.update(range(start, end))
    df = pd.DataFrame([
        {"Hour": f"{h}:00", "Visitors": v, "Peak": "Peak" if h in peaks else "Other"}
        for h, v in pattern["avg_visitors_by_hour"].items()
    ])
    return alt.Chart(df).mark_bar(size=14).encode(
        x=alt.X("Hour:N", sort=None, title=None,
                axis=alt.Axis(labelAngle=0, labelExpr="split(datum.label, ':')[0]")),
        y=alt.Y("Visitors:Q", title=None, axis=alt.Axis(grid=False, labels=False, ticks=False,
                                                        domain=False)),
        color=alt.Color("Peak:N", legend=None,
                        scale=alt.Scale(domain=["Peak", "Other"], range=[TEXT, "#C9C9C4"])),
        tooltip=["Hour", alt.Tooltip("Visitors:Q", format=".1f", title="Average visitors")],
    ).properties(height=120).configure_view(stroke=None).configure_axis(
        labelFont="Inter", labelFontSize=10, labelColor=MUTED, domainColor=BORDER, tickColor=BORDER,
    ).configure(background="#FFFFFF")


def floor_page():
    hour = current_hour()
    page_title("Floor and staff", "Who's coming in, how many are buying, and where to put the team "
                                  "tomorrow.")
    s = current_store()
    if not s.has_footfall:
        _not_in_data("visitor counts", "add a Visitors sheet with visitors per floor, by day or hour")
        return
    zones = zones_at(hour)

    def typical(f):
        if f["typical_visitors_by_this_hour"] is None:
            return "no earlier days of this kind yet"
        change = f" ({f['pct_vs_typical']:+.0f}%)" if f["pct_vs_typical"] is not None else ""
        return f"typical {f['typical_visitors_by_this_hour']:.0f}{change}"

    html_block(heading("Visitors today", f"by {time_label(hour)}, vs a typical day of the same kind")
               + '<div class="cp-kpis">'
               + "".join(kpi_card(z["footfall"]["zone"], f"{z['footfall']['visitors_today_so_far']}",
                                  typical(z["footfall"]))
                         for z in zones)
               + "</div>")

    if not s.has_transactions:
        _not_in_data("bill counts, needed to tell fewer visitors from fewer buyers",
                     "add Bills to the Sales sheet")
    else:
        _visitors_vs_buyers(zones)

    if not s.has_visitor_hours:
        _not_in_data("visitors by hour, needed for tomorrow's busy hours",
                     "add an Hour column to the Visitors sheet")
        return
    if s.today_day >= s.days_in_month:
        return
    _tomorrow(s)


def _visitors_vs_buyers(zones):
    html_block(heading("Visitors vs buyers", "last 3 days + today, vs the first half of the month")
               + '<div class="cp-kpis">'
               + "".join(
                   kpi_card(f"{z['conversion']['zone']}: share who bought",
                            f"{z['conversion']['recent_last_3_days_and_today']['conversion_rate_pct']}%",
                            f"was {z['conversion']['baseline_earlier_this_month']['conversion_rate_pct']}%"
                            f" · {READING_LABEL[z['conversion']['reading']]}")
                   for z in zones)
               + "</div>")


def _tomorrow(s):
    rec, patterns = staffing_for(s.today_day + 1)
    html_block(heading(f"Tomorrow: {rec['weekday']}",
                       f"from the last {len(rec['based_on_days'])} {rec['day_type']}s"))
    for note in rec["notes"]:
        html_block(f'<div class="cp-do">{esc(note)}</div>')

    columns = st.columns(len(patterns))
    for column, (zone, pattern) in zip(columns, patterns.items()):
        with column:
            # e.g. "rough guide (low traffic)" -> " (rough guide: low traffic)"
            note = pattern["reliability"].replace(" (", ": ").rstrip(")")
            reliability = "" if pattern["reliability"] == "good" else f" ({note})"
            html_block(f'<div class="cp-row-name">{esc(zone)}</div>'
                       f'<div class="cp-small">Peaks {esc(", ".join(pattern["peak_windows"]))}'
                       f'{esc(reliability)}</div>')
            st.altair_chart(_hour_chart(pattern), width="stretch", theme=None)

    split = rec["suggested_floor_split_at_busiest_hour_pct"]
    bars = "".join(
        f'<div class="cp-row-text" style="margin-top:6px">{esc(zone)} · {pct}%</div>'
        f'<div class="cp-track"><div class="cp-fill" style="width:{pct}%"></div></div>'
        for zone, pct in split.items())
    html_block(heading(f"Floor team at {rec['store_busiest_hour']}:00, the busiest hour",
                       "split in proportion to where shoppers are")
               + f'<div class="cp-panel">{bars}</div>')
    with st.expander("Which past days is this based on?"):
        st.write(", ".join(rec["based_on_days"]))


# --- Sell page --------------------------------------------------------------------------------------

def sell_page():
    hour = current_hour()
    page_title("Sell", "Cross-sell ideas for categories behind pace, and what each loyalty tier "
                       "responds to.")
    ideas = cross_sell_at(hour)

    html_block(heading("Cross-sell ideas right now", "only for categories that are genuinely behind"))
    if not ideas:
        html_block('<div class="cp-panel"><p>No category is behind pace, so there\'s no cross-sell '
                   'push needed right now.</p></div>')
    for category, idea in ideas.items():
        cause = idea["stock_verdict"] if idea["stock_verdict"] in ("stockout", "broken_size_run") else None
        first = (f'<div class="cp-do"><b>First:</b> {esc(idea["supply_action"])}</div>'
                 if idea.get("supply_action") else "")
        html_block(
            f'<div class="cp-panel">{pill(idea["status"])}{cause_chip(cause)}'
            f'<div class="cp-row-name">{esc(category)}</div>{first}'
            f'<div class="cp-do"><b>At the till:</b> {esc(idea["at_the_till"])}</div></div>'
        )
        if st.button(f"Open {category} details", key=f"sell_open_{slug(category)}", type="tertiary"):
            category_dialog(category)

    html_block(heading("Loyalty tier playbook", "pick any category"))
    if not current_store().has_loyalty:
        _not_in_data("loyalty tier figures", "add a Loyalty sheet")
        return
    options = list(ideas) + [c for c in current_store().categories if c not in ideas]
    chosen = st.selectbox("Category", options, key="sell_category", label_visibility="collapsed")
    _tier_table(playbook_for(chosen))

    with st.expander("Best-responding tier for every category"):
        best = []
        for category in current_store().categories:
            tiers = playbook_for(category)["tiers_ranked_by_response"]
            if not tiers:
                continue
            top = tiers[0]
            best.append({"Category": category, "Best tier": top["tier"],
                         "Takes up cross-sells": f"{top['cross_sell_response_rate_pct']:.0f}%",
                         "Offer at the till": top["offer_at_the_till"]})
        st.dataframe(pd.DataFrame(best), hide_index=True, width="stretch")


def summary_page():
    hour = current_hour()
    s = current_store()
    is_close = hour == s.hours[-1]
    moment = "close" if is_close else time_label(hour).replace(":", "")
    page_title("End-of-day summary" if is_close else "Summary so far today",
               "The plain-English summary that replaces the evening spreadsheet, and the month's "
               "contribution report.")

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
            "Department": l["department"],
            "Units": l["units"],
            "Units share": f"{l['units_pct']}%",
            "Value": inr(l["value"]),
            "Value share": f"{l['value_pct']}%",
        }
        for l in report["lines"]
    ]
    lines.append({"Line": "Store", "Department": "", "Units": report["store_units"],
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


def _read_upload(files):
    """Reads uploaded files into a store (or a plain error) and keeps the result for this session."""
    try:
        store, notes = upload.read_upload(files)
        st.session_state["upload_result"] = {"ok": True, "store": store, "notes": notes}
    except StoreDataError as e:
        st.session_state["upload_result"] = {"ok": False, "error": str(e)}


def _feature_row(name, on, hint):
    badge = '<span class="cp-pill green">On</span>' if on else '<span class="cp-pill neutral">Off</span>'
    extra = f' <span class="cp-small">({esc(hint)})</span>' if hint else ""
    return f'<div class="cp-term">{badge} {esc(name)}{extra}</div>'


def _upload_preview(store, notes):
    html_block(heading("3. Check it", "before switching every page to it"))
    lines, floors = len(store.lines), len(store.departments)
    html_block('<div class="cp-kpis">'
               + kpi_card("Month", f"{store.month_name} {store.month_start.year}",
                          f"data up to day {store.today_day} ({store.date(store.today_day):%a %d %b})")
               + kpi_card("Categories", f"{len(store.categories)}",
                          f"in {lines} line{'s' if lines != 1 else ''} on {floors} floor{'s' if floors != 1 else ''}")
               + kpi_card("Units sold so far", f"{int(store.sales['units_sold'].sum()):,}",
                          f"against {sum(store.targets.values()):,} for the month")
               + "</div>")

    html_block(heading("What your data switches on")
               + '<div class="cp-panel">'
               + "".join(_feature_row(*f) for f in upload.feature_checklist(store))
               + "</div>")
    for note in notes:
        html_block(f'<div class="cp-small">{esc(note)}</div>')

    if store.has_sizes:
        with st.expander("Check the core sizes"):
            html_block('<div class="cp-small">Core sizes are the ones most shoppers need: when they run '
                       'out, a category can\'t sell even while the shelf looks full. Any left blank in '
                       'the Targets sheet were worked out from sales on days every size was in stock. '
                       'With only a few sales that can pick the wrong ones, so check them and type the '
                       'right ones into the template if needed.</div>')
            st.dataframe(pd.DataFrame([
                {"Category": c, "Sizes": ", ".join(store.sizes[c]),
                 "Core sizes": ", ".join(store.core_sizes[c]) or "none yet (no sales)"}
                for c in store.categories
            ]), hide_index=True, width="stretch")

    if st.session_state.get("my_store") is store:
        html_block('<div class="cp-small" style="margin-top:8px">This is the store loaded above.</div>')
    elif st.button("Show my store", key="show_upload", type="primary", icon=":material/arrow_forward:"):
        st.session_state["my_store"] = store
        request_store(True)
        st.switch_page(tour.PAGES["Today"])


def your_data_page():
    page_title("Your data", "See your own store in Category Pulse: fill in the Excel template, upload "
                            "it, and every page switches to your numbers.")
    html_block('<div class="cp-panel"><p>Until you upload your own data, the pages show the '
               '<b>Sample Store</b>: a made-up store with realistic numbers and a few problems '
               'hidden in them, so you can see what Category Pulse does.</p>'
               '<p><b>Before you upload:</b> only use real company figures with your manager\'s '
               'approval. Your file is read for this session only and isn\'t saved, and the AI chat '
               'isn\'t used with uploaded data.</p></div>')

    mine = st.session_state.get("my_store")
    if mine is not None:
        showing = not current_store().is_demo
        html_block(heading("Your store")
                   + f'<div class="cp-panel"><p><b>{esc(mine.name)}</b>: {esc(mine.month_name)} '
                     f'{mine.month_start.year}, up to day {mine.today_day}. '
                   + ("Every page is showing it now." if showing
                      else "Loaded, but the pages are showing the Sample Store.")
                   + "</p></div>")
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            if showing:
                if st.button("Back to the Sample Store", key="mine_to_demo"):
                    request_store(False)
                    st.rerun()
            elif st.button("Show my store", key="mine_show", type="primary"):
                request_store(True)
                st.switch_page(tour.PAGES["Today"])
            if st.button("Remove my data", key="mine_forget", type="tertiary"):
                request_forget()
                st.rerun()

    html_block(heading("1. Get the template")
               + '<div class="cp-panel"><p>One Excel file with a sheet for each kind of data. '
                 '<b>Targets</b> and <b>Sales</b> are required. <b>Stock</b>, <b>Visitors</b>, '
                 '<b>Loyalty</b> and <b>Settings</b> are optional, and each one switches on more of '
                 'the app. The Read me sheet explains every column. The sample file is the Sample '
                 'Store\'s month, filled in, so you can see exactly what goes where.</p></div>')
    with st.container(horizontal=True, gap="small", wrap=True):
        st.download_button("Download the template", data=upload.template_bytes(), mime=XLSX,
                           file_name="category-pulse-template.xlsx", icon=":material/download:")
        with st.spinner("Preparing the sample..."):
            sample = upload.sample_bytes()
        st.download_button("Download the sample file", data=sample, mime=XLSX,
                           file_name="category-pulse-sample-store.xlsx", icon=":material/download:")

    html_block(heading("2. Upload it", "the filled-in template, or one CSV file per sheet, e.g. sales.csv"))
    files = st.file_uploader("Upload your data", type=["xlsx", "csv"], accept_multiple_files=True,
                             key="upload_files", label_visibility="collapsed")
    if files:
        data = [(f.name, f.getvalue()) for f in files]
        fingerprint = hashlib.sha1(b"".join(d for _, d in data)).hexdigest()
        if st.session_state.get("upload_key") != fingerprint:  # read each new upload once
            st.session_state["upload_key"] = fingerprint
            with st.spinner("Reading your file..."):
                _read_upload(data)
    if st.button("Or try it with the sample file", key="try_sample", icon=":material/science:",
                 type="tertiary"):
        with st.spinner("Reading the sample..."):
            _read_upload([("category-pulse-sample-store.xlsx", sample)])

    result = st.session_state.get("upload_result")
    if result is None:
        return
    if not result["ok"]:
        html_block('<div class="cp-alert"><div class="cp-alert-title">That file can\'t be used yet</div>'
                   f'<ul><li>{esc(result["error"])}</li><li>Fix it in the file and upload it again.'
                   '</li></ul></div>')
        return
    _upload_preview(result["store"], result["notes"])


# --- Guide page ------------------------------------------------------------------------------

GUIDE_STATUSES = [
    ("behind", "More than 15% under where it should be by now, by more than normal day-to-day "
               "ups and downs. Needs action."),
    ("drifting", "More than 15% under, but small enough that it could still be chance. Worth "
                 "watching, not yet proven."),
    ("on_pace", "Within 15% of where it should be."),
    ("ahead", "More than 15% over, by more than chance."),
    ("too_early", "Too few units expected so far for a percentage to mean anything."),
]

GUIDE_TERMS = [
    ("Pace", "Units sold so far compared with what the monthly target says should have sold by "
             "now. Month-to-date, because targets are monthly."),
    ("Needs per day", "How many units a day the category must sell from now on to hit its "
                      "target, next to what it's actually selling."),
    ("Month-end at this rate", "A projection, not a result: where the month lands if the current "
                               "daily rate simply continues. It can't know about stockouts or a "
                               "month-end push."),
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
    s = current_store()
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
               + '<div class="cp-panel"><p>The Your data page has an Excel template. Fill in your '
                 'targets and sales (and, if you have them, stock counts, visitor counts and loyalty '
                 'figures), upload it, and every page switches to your store. The more you add, the '
                 'more the app can tell you; it says plainly what\'s missing rather than guessing. To '
                 'keep your data private, the AI chat isn\'t used with it.</p></div>')

    html_block(heading("How the AI chat works")
               + '<div class="cp-panel"><p>Ask Category Pulse answers by looking up the store\'s '
                 'numbers with the same calculations the pages use, and never guesses a figure. '
                 'Under each answer, "How I got this" lists every lookup and the numbers it '
                 f'returned. You can ask up to {DEMO_QUESTION_LIMIT} questions per visit.</p></div>')


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

    # Filled in at the end, so the "questions left" count includes this answer.
    status_line = st.empty()
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
                        "answer": (f"You've used the {DEMO_QUESTION_LIMIT} questions allowed per visit. "
                                   f"Everything else on the site still works.")}
            else:
                # Show the question with a spinner while the AI works, then swap in the answer.
                waiting = st.empty()
                with waiting.container():
                    st.markdown(f'<div class="cp-you">{esc(question)}</div>', unsafe_allow_html=True)
                    with st.spinner("Checking the numbers..."):
                        answer, calls = agent.ask(question, current_hour=hour)
                waiting.empty()
                st.session_state["questions_asked"] += 1
                turn = {"question": question, "answer": answer, "calls": calls}
            st.session_state["chat"].append(turn)
            _render_turn(turn)

    left = max(DEMO_QUESTION_LIMIT - st.session_state["questions_asked"], 0)
    status_line.caption(f"Answers are built from the store's own numbers as of {time_label(hour)}. "
                        f"AI: {agent.PROVIDER['name']} · {left} of {DEMO_QUESTION_LIMIT} questions left.")


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
