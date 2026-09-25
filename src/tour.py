"""
The guided tour: a short walkthrough of the site for first-time visitors.

Each stop names the page it belongs to, what to say, and what to outline on
the page. A card at the top of the page shows the current stop with Back /
Next / End tour; Next moves to the right page by itself.
"""

import streamlit as st

from ui import ACCENT, esc, html_block

TOUR = [
    {
        "page": "Today",
        "title": "The store at a glance",
        "text": ("This is the store on day 24 of a 31-day May, at the time on the clock button above. The four "
                 "numbers say how the month is going, where it will end if nothing changes (a "
                 "projection), how many categories need action, and how today is going."),
        "highlight": ".cp-kpis",
    },
    {
        "page": "Today",
        "title": "What needs action, and why",
        "text": ("Only categories that are genuinely behind are listed, each with its likely cause. "
                 "Tap the THM Non Denim Bottom card: the pop-up shows why (its core waist sizes are "
                 "gone), the stock by size, the shoppers, and what to sell instead. Close it, then "
                 "press Next."),
        "highlight": '[class*="st-key-click_today_THM_Non_Denim_Bottom"] .cp-row',
    },
    {
        "page": "Categories",
        "title": "Every category, three ways",
        "text": ("All 29 categories as cards, a table or a chart. The bar shows sold vs the monthly "
                 "target, and the tick shows where it should be by now. Use Filters to narrow it "
                 "down, and tap any card or row for its detail."),
        "highlight": ".st-key-cat_view",
    },
    {
        "page": "Stock",
        "title": "Stock problems before they cost sales",
        "text": ("Stockouts, broken size runs (the shelf looks full but the core sizes are gone), "
                 "today's last pieces in the order they happened, and sizes likely to run out "
                 "before the next delivery."),
        "highlight": '[class*="st-key-click_stk_"] .cp-row',
    },
    {
        "page": "Floor and staff",
        "title": "Traffic, buying and tomorrow's rota",
        "text": ("Are fewer people coming in, or are fewer of them buying? Below that, tomorrow's "
                 "busy hours for each floor, learned from past days of the same kind, and how to "
                 "split the team at the busiest hour."),
        "highlight": ".cp-kpis",
    },
    {
        "page": "Sell",
        "title": "What to say at the till",
        "text": ("For each category that's behind: the supply fix first, then a script for the till "
                 "that names the loyalty tier most likely to respond and the offer that tier "
                 "prefers. Targeting is by tier only, never by individual customer."),
        "highlight": ".cp-panel",
    },
    {
        "page": "Summary",
        "title": "The end-of-day summary, and the chat",
        "text": ("The 30-second summary that replaces the evening spreadsheet, plus the month's "
                 "contribution report, both downloadable. And on every page, Ask Category Pulse "
                 "(bottom right) answers questions from the same numbers, showing how it got each "
                 "answer. That's the tour."),
        "highlight": ".cp-digest, .st-key-chat_fab button",
    },
]

# Set by app.py on every run: page title -> st.Page, so the tour can move between pages.
PAGES = {}


def start():
    """Start the tour from the first stop, moving to its page if needed."""
    st.session_state["tour_step"] = 0
    st.switch_page(PAGES[TOUR[0]["page"]])


def _go(step):
    if step is None or step >= len(TOUR):
        st.session_state["tour_step"] = None
        st.rerun()
    st.session_state["tour_step"] = step
    st.switch_page(PAGES[TOUR[step]["page"]])


def render(current_page_title):
    """Draw the tour card (or a 'return to the tour' bar) at the top of the page."""
    step = st.session_state.get("tour_step")
    if step is None:
        return
    stop = TOUR[step]

    if stop["page"] != current_page_title:
        with st.container(key="tour_card"):
            html_block(f'<div class="cp-tour"><div class="cp-tour-step">Tour paused</div>'
                       f'<div class="cp-small">Step {step + 1} of {len(TOUR)} is on the '
                       f'{esc(stop["page"])} page.</div></div>')
            with st.container(horizontal=True, gap="small"):
                if st.button("Back to the tour", key="tour_resume", type="primary"):
                    _go(step)
                if st.button("End tour", key="tour_end_paused", type="tertiary"):
                    _go(None)
        return

    html_block(f"<style>{stop['highlight']} {{ outline: 3px solid {ACCENT}; "
               f"outline-offset: 4px; }}</style>")
    with st.container(key="tour_card"):
        html_block(f'<div class="cp-tour"><div class="cp-tour-step">Tour · step {step + 1} of '
                   f'{len(TOUR)}</div><div class="cp-tour-title">{esc(stop["title"])}</div>'
                   f'<div class="cp-tour-text">{esc(stop["text"])}</div></div>')
        last = step == len(TOUR) - 1
        with st.container(horizontal=True, gap="small"):
            if step > 0 and st.button("Back", key="tour_back", icon=":material/arrow_back:"):
                _go(step - 1)
            if st.button("Finish" if last else "Next", key="tour_next", type="primary",
                         icon=None if last else ":material/arrow_forward:", icon_position="right"):
                _go(None if last else step + 1)
            if not last and st.button("End tour", key="tour_end", type="tertiary"):
                _go(None)
