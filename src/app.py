"""
Category Pulse web app: the frame around every page.

Run from the project folder with:  streamlit run src/app.py

Layers:
  1. The frame (this file): name, which store's data is showing, time button,
     top menu, the guided tour card, and a floating "Ask Category Pulse"
     button on every page.
  2. Pages (views.py): Today, Categories, Stock, Floor and staff, Sell,
     Summary, Your data, Guide.
  3. Pop-ups on top of a page: category details and the chat.

The chat and the tour belong to the Sample Store. With a visitor's own
uploaded data they're switched off: uploaded figures are never sent to an
AI provider.
"""

import streamlit as st

import tour
import views
from ui import (
    CSS,
    apply_store_switch,
    current_hour,
    current_store,
    esc,
    html_block,
    request_store,
    time_label,
    today_label,
)

st.set_page_config(page_title="Category Pulse", layout="wide", initial_sidebar_state="collapsed")
st.markdown(CSS, unsafe_allow_html=True)
apply_store_switch()  # before any widget is drawn
store = current_store()  # builds the Sample Store's data on a fresh deployment, once

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
    "Your data": st.Page(views.your_data_page, title="Your data", icon=":material/upload_file:",
                         url_path="your-data"),
    "Guide": st.Page(views.guide_page, title="Guide", icon=":material/help:", url_path="guide"),
}
tour.PAGES = PAGES
current_page = st.navigation(list(PAGES.values()), position="top")


def _on_store_choice():
    choice = st.session_state.get(f"store_choice_{int(not store.is_demo)}")
    if choice is not None:
        request_store(choice == "Your store")


# --- Frame: name, which data is showing, and the time -----------------------------------
hour = current_hour()
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    label = esc(store.name) + ("" if store.is_demo else " · uploaded data")
    html_block(f'<span class="cp-brand">Category Pulse</span><span class="cp-datalabel">{label}</span>')
    with st.container(horizontal=True, gap="small", vertical_alignment="center", width="content"):
        if st.session_state.get("my_store") is not None:
            # A fresh key whenever the store changes, so the control always shows the store in use.
            st.segmented_control(
                "Data", ["Sample Store", "Your store"], default="Sample Store" if store.is_demo else "Your store",
                key=f"store_choice_{int(not store.is_demo)}", on_change=_on_store_choice,
                label_visibility="collapsed",
            )
        if store.hourly:
            with st.popover(f"{today_label()}, {time_label(hour)}", icon=":material/schedule:"):
                st.select_slider(
                    "Step through the day", options=store.hours, key="hour",
                    format_func=time_label,
                    help="Everything on the site shows the store as it was at this time.",
                )
        else:
            html_block(f'<span class="cp-datalabel">Close of {esc(today_label())}</span>')

tour.render(current_page.title)
current_page.run()

# --- Floating chat button, Sample Store only -------------------------------------------------
# The chat also opens when a category pop-up hands over a question ("Ask the AI
# about this category"): only one pop-up can be open at a time.
if store.is_demo:
    clicked = st.button("Ask Category Pulse", key="chat_fab", icon=":material/forum:", type="primary")
    if clicked or st.session_state.pop("open_chat", False):
        views.chat_dialog()
