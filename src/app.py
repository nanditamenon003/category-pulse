"""
Category Pulse web app: the frame around every page.

Run from the project folder with:  streamlit run src/app.py

Layers:
  1. The frame (this file): name, data label, time button, top menu, the
     guided tour card, and a floating "Ask Category Pulse" button on every page.
  2. Pages (views.py): Today, Categories, Stock, Floor and staff, Sell, Summary, Guide.
  3. Pop-ups on top of a page: category details and the chat.
"""

import streamlit as st

import tour
import views
from config import STORE_HOURS, STORE_NAME
from ui import CSS, current_hour, esc, html_block, load_data, time_label, today_label

st.set_page_config(page_title="Category Pulse", layout="wide", initial_sidebar_state="collapsed")
st.markdown(CSS, unsafe_allow_html=True)
load_data()  # builds the simulated data on a fresh deployment, once

PAGES = {
    "Today": st.Page(views.today_page, title="Today", icon=":material/today:", default=True),
    "Categories": st.Page(views.categories_page, title="Categories", icon=":material/grid_view:",
                          url_path="categories"),
    "Stock": st.Page(views.stock_page, title="Stock", icon=":material/inventory_2:", url_path="stock"),
    "Floor and staff": st.Page(views.floor_page, title="Floor and staff", icon=":material/groups:",
                               url_path="floor"),
    "Sell": st.Page(views.sell_page, title="Sell", icon=":material/sell:", url_path="sell"),
    "Summary": st.Page(views.summary_page, title="Summary", icon=":material/summarize:",
                       url_path="summary"),
    "Guide": st.Page(views.guide_page, title="Guide", icon=":material/help:", url_path="guide"),
}
tour.PAGES = PAGES
current_page = st.navigation(list(PAGES.values()), position="top")

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

tour.render(current_page.title)
current_page.run()

# --- Floating chat button, on every page ------------------------------------------------
# The chat also opens when a category pop-up hands over a question ("Ask the AI
# about this category"): only one pop-up can be open at a time.
clicked = st.button("Ask Category Pulse", key="chat_fab", icon=":material/forum:", type="primary")
if clicked or st.session_state.pop("open_chat", False):
    views.chat_dialog()
