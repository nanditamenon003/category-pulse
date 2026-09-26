"""
Category Pulse web app: the frame around every page.

Run from the project folder with:  streamlit run src/app.py

Layers:
  0. The welcome page (account.py) for anyone not signed in: log in, create
     an account, or look around as a guest.
  1. The frame (this file): name, which store's data is showing, time button,
     account menu, top menu, the guided tour card, and a floating "Ask
     Category Pulse" button.
  2. Pages (views.py): Today, Categories, Stock, Floor and staff, Sell,
     Summary, Your data, Guide.
  3. Pop-ups on top of a page: the welcome guide, category details and the chat.

What the pages show:
  - signed in: the person's own uploaded data. Until they upload, each page
    explains what will appear there. A short welcome guide opens once per
    visit and ends with "Take the 2-minute tour" or "Skip".
  - the tour, and guests: the Sample Store, a made-up store for learning.
The AI chat works on any store. For a store's own data it first explains
that the figures it looks up go to the AI provider, and asks for a clear yes.
"""

import streamlit as st

import account
import tour
import views
from ui import CSS, current_hour, esc, html_block, prepare_session, time_label, today_label

st.set_page_config(page_title="Category Pulse", layout="wide", initial_sidebar_state="collapsed")
st.markdown(CSS, unsafe_allow_html=True)

# --- Signed out: only the welcome page -------------------------------------------------------
signed_in = account.is_signed_in()
if not signed_in and not account.is_guest():
    st.navigation([st.Page(account.welcome_page, title="Category Pulse", default=True)],
                  position="hidden").run()
    st.stop()

st.session_state["member"] = signed_in
store = prepare_session()  # before any widget is drawn

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

# --- Frame: name, which data is showing, the time and the account -------------------------
with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    if store is None:
        label = "No data yet"
    elif store.is_demo:
        label = store.name + (" · walkthrough" if signed_in else "")
    else:
        label = store.name + " · uploaded data"
    html_block(f'<span class="cp-brand">Category Pulse</span><span class="cp-datalabel">{esc(label)}</span>')
    with st.container(horizontal=True, gap="small", vertical_alignment="center", width="content"):
        if store is not None and store.hourly:
            hour = current_hour()
            with st.popover(f"{today_label()}, {time_label(hour)}", icon=":material/schedule:"):
                st.select_slider(
                    "Step through the day", options=store.hours, key="hour",
                    format_func=time_label,
                    help="Everything on the site shows the store as it was at this time.",
                )
        elif store is not None:
            html_block(f'<span class="cp-datalabel">Close of {esc(today_label())}</span>')
        account.account_menu()

# --- The welcome guide: once per visit, for someone signed in with no data yet ---------------
if signed_in and store is None and not st.session_state.get("welcomed"):
    st.session_state["welcomed"] = True
    views.welcome_guide()

tour.render(current_page.title)
current_page.run()

# --- Floating chat button, whenever there's a store to ask about -------------------------
# The chat also opens when a category pop-up hands over a question ("Ask the AI
# about this category"): only one pop-up can be open at a time. For a store's
# own data, the chat asks for a clear yes before sending any figures to the AI.
if store is not None:
    clicked = st.button("Ask Category Pulse", key="chat_fab", icon=":material/forum:", type="primary")
    if clicked or st.session_state.pop("open_chat", False):
        views.chat_dialog()
