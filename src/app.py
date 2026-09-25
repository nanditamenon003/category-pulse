"""
Category Pulse web app: the frame around every page.

Run from the project folder with:  streamlit run src/app.py

Layers:
  1. The frame (this file): name, data label, time button, top menu, and a
     floating "Ask Category Pulse" button on every page.
  2. Pages (views.py): Today, Categories, Stock, Floor and staff, Sell, Summary.
  3. Pop-ups on top of a page: the chat, and (from step 2) category details.
"""

import streamlit as st

import views
from config import STORE_HOURS, STORE_NAME
from ui import CSS, current_hour, esc, html_block, load_data, time_label, today_label

st.set_page_config(page_title="Category Pulse", layout="wide", initial_sidebar_state="collapsed")
st.markdown(CSS, unsafe_allow_html=True)
load_data()  # builds the simulated data on a fresh deployment, once

pages = st.navigation(
    [
        st.Page(views.today_page, title="Today", icon=":material/today:", default=True),
        st.Page(views.categories_page, title="Categories", icon=":material/grid_view:",
                url_path="categories"),
        st.Page(views.coming_soon_page("Stock", "Stockouts, broken size runs and last pieces."),
                title="Stock", icon=":material/inventory_2:", url_path="stock"),
        st.Page(views.coming_soon_page("Floor and staff", "Visitors, buyers and tomorrow's staffing."),
                title="Floor and staff", icon=":material/groups:", url_path="floor"),
        st.Page(views.coming_soon_page("Sell", "Cross-sell ideas and loyalty tier playbooks."),
                title="Sell", icon=":material/sell:", url_path="sell"),
        st.Page(views.summary_page, title="Summary", icon=":material/summarize:", url_path="summary"),
    ],
    position="top",
)

# --- Frame: name, data label and the time button ---------------------------------------
hour = current_hour()
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    html_block(f'<span class="cp-brand">Category Pulse</span>'
               f'<span class="cp-datalabel">{esc(STORE_NAME)}</span>')
    with st.popover(f"{today_label()}, {time_label(hour)}", icon=":material/schedule:"):
        st.select_slider(
            "Step through the day", options=STORE_HOURS, key="hour",
            format_func=time_label,
            help="Everything on the site shows the store as it was at this time.",
        )

pages.run()

# --- Floating chat button, on every page ------------------------------------------------
if st.button("Ask Category Pulse", key="chat_fab", icon=":material/forum:", type="primary"):
    views.chat_dialog()
