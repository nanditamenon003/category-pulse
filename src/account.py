"""
Accounts: the welcome page, signing in and out, and looking around as a guest.

Signing in uses Streamlit's built-in login with Auth0 (an OpenID Connect
provider). Auth0's own page handles creating an account, logging in,
"Continue with Google" and forgotten passwords, so this app never sees or
stores a password: it only learns the person's name and email once they've
signed in. The settings live in .streamlit/secrets.toml (never in git):

    [auth]
    redirect_uri = "http://localhost:8501/oauth2callback"   # the live URL when deployed
    cookie_secret = "a long random string"

    [auth.auth0]
    client_id = "..."
    client_secret = "..."
    server_metadata_url = "https://<your-auth0-domain>/.well-known/openid-configuration"

Without those settings (e.g. someone running a copy from GitHub), the
welcome page offers only "Look around", with the Sample Store.
"""

import streamlit as st

from ui import esc, html_block, logo_uri

PROVIDER = "auth0"

# Small line icons for the feature cards (24px, drawn in the accent colour).
_ICON = {
    "early": '<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    "sizes": '<path d="M12 5a2 2 0 1 1 2 2c-1 0-2 .7-2 2v1"/><path d="M12 10 3 16h18l-9-6Z"/>',
    "why": '<circle cx="9" cy="8" r="3"/><path d="M3.5 19c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5"/>'
           '<path d="M16 5.5a3 3 0 0 1 0 5.2M18.5 19c-.3-1.6-1-2.8-2-3.6"/>',
    "plan": '<rect x="4" y="4" width="16" height="17" rx="2"/><path d="M8 3v3M16 3v3M8 11l2 2 4-4M8 17h8"/>',
    "share": '<path d="M4 18.5V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H8l-4 2.5Z"/>'
             '<path d="M8.5 9h7M8.5 12h4"/>',
    "private": '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
}

FEATURES = [
    ("early", "Catch problems early",
     "Every category tracked against its monthly target, with real problems told apart from normal "
     "ups and downs, so the team acts days sooner."),
    ("sizes", "Spot missing sizes",
     "A full-looking shelf can hide sold-out core sizes. Category Pulse flags broken size runs and "
     "the last piece of a size the moment it happens."),
    ("why", "Know why it's slipping",
     "Sold out, missing sizes, fewer shoppers or fewer buyers: the likely cause, with the evidence, "
     "and one clear thing to do."),
    ("plan", "Tomorrow's plan, done for you",
     "What tomorrow needs to sell, which categories to push, what to request, and where to put the "
     "team at the busy hour."),
    ("share", "Reports ready for WhatsApp",
     "The evening report and the morning huddle plan as short messages. Copy, share, done: no more "
     "evening spreadsheet."),
    ("private", "Your data stays yours",
     "Only the columns the app needs are kept, encrypted, visible only to your account, and "
     "deletable any time."),
]

STEPS = [
    ("Upload your sales", "Your till system's export, as it comes, Excel or CSV. Add stock or targets "
                          "if you have them; the app works with whatever you've got."),
    ("See what needs action", "Every category gets a status, a likely cause and a next step, in units "
                              "or rupees, with a month-end forecast."),
    ("Share the plan", "Send tomorrow's plan to the team and the evening report to your area manager, "
                       "straight to WhatsApp."),
]


def _icon(name):
    return (f'<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_ICON[name]}</svg>')


# A still picture of the app for the welcome page: the Sample Store at 16:00 on day 24.
_PREVIEW = """
<div class="cp-w-visual">
  <div class="cp-w-app">
    <div class="cp-w-app-top"><img src="{logo}" alt="">Today<span>Sun 24 May, 16:00</span></div>
    <div class="cp-w-kpis">
      <div><span>Month so far</span><b>2,456</b><em>of about 2,467 expected</em></div>
      <div><span>Month-end</span><b>3,188</b><em>likely 3,146 to 3,231</em></div>
    </div>
    <div class="cp-w-row red"><span class="cp-pill red">Behind</span><span class="cp-chip">Broken size run</span>
      <b>Men Casual Trousers</b><em>Waists 32 and 34 are gone while 58 pieces sit on the shelf. Request a transfer today.</em></div>
    <div class="cp-w-row red"><span class="cp-pill red">Behind</span><span class="cp-chip">Sold out</span>
      <b>Women Tops</b><em>The day-18 delivery never came. Walk shoppers to Women Shirts.</em></div>
    <div class="cp-w-row green"><span class="cp-pill green">Ahead</span>
      <b>Little Boys Tops</b><em>44% ahead of pace. Keep every size on the floor.</em></div>
  </div>
  <div class="cp-w-bubble"><div class="cp-w-bubble-who">Evening report, shared on WhatsApp</div>
    <b>Sample Store: close of Sun 24 May</b><br>Month: 2,498 of 3,215, right on pace<br>
    Behind: Women Tops (sold out), chinos (core sizes gone)<br>Tomorrow: 84 units keeps the month on course</div>
</div>
"""


def is_configured():
    """True when the sign-in settings are present."""
    try:
        auth = st.secrets.get("auth", {})
        return bool(auth.get("cookie_secret") and auth.get(PROVIDER, {}).get("client_id"))
    except Exception:  # no secrets file at all
        return False


def is_signed_in():
    return is_configured() and bool(st.user.is_logged_in)


def is_guest():
    return not is_signed_in() and st.session_state.get("guest", False)


def person_id():
    """A stable id for the signed-in person (for the daily chat limit), or None."""
    if not is_signed_in():
        return None
    return st.user.get("sub") or st.user.get("email")


def first_name():
    """The person's first name if Auth0 knows it (e.g. from Google), otherwise None."""
    user = st.user
    name = str(user.get("given_name") or user.get("name") or "").strip()
    return name.split()[0] if name and "@" not in name else None


def sign_in():
    st.login(PROVIDER)


def _guest():
    st.session_state["guest"] = True


def welcome_page():
    """The front page for anyone not signed in: what the app does, and the ways in."""
    configured = is_configured()
    left, right = st.columns([1.08, 1], gap="large", vertical_alignment="center")
    with left:
        html_block(f'<div class="cp-w-brand"><img src="{logo_uri("logo-mark-120.png")}" alt="">'
                   f'<span>Category Pulse</span></div>'
                   '<div class="cp-w-hero">Catch the categories falling behind, '
                   '<span class="cp-grad">while there\'s still time to act.</span></div>'
                   '<div class="cp-w-sub">Upload your sales export. Category Pulse shows what\'s behind, '
                   'why it\'s slipping, and exactly what to do tomorrow, ready to share with your '
                   'team.</div>')
        with st.container(horizontal=True, gap="small", wrap=True, key="welcome_actions"):
            if configured:
                st.button("Create a free account", key="welcome_signup", type="primary", on_click=sign_in)
                st.button("Log in", key="welcome_login", on_click=sign_in)
            st.button("Look around first", key="welcome_guest", on_click=_guest,
                      type="tertiary" if configured else "primary", icon=":material/visibility:")
        if configured:
            html_block('<div class="cp-small">Sign-in is run securely by Auth0 (you can also continue '
                       'with Google), so this app never sees your password. "Look around first" opens '
                       'a sample store, no account needed.</div>')
        else:
            html_block('<div class="cp-small">Sign-in isn\'t set up on this copy of the app, so you can '
                       'look around a sample store without an account.</div>')
        html_block('<div class="cp-w-facts">'
                   '<span class="cp-w-fact"><b>Any</b> till export</span>'
                   '<span class="cp-w-fact">Units <b>or ₹</b></span>'
                   '<span class="cp-w-fact">Made for <b>phones</b></span>'
                   '<span class="cp-w-fact"><b>Free</b> to try</span></div>')
    with right:
        html_block(_PREVIEW.format(logo=logo_uri("logo-mark-120.png")))

    html_block('<div class="cp-w-h">From sales export to a plan, in minutes</div>'
               '<div class="cp-w-hsub">No new system and no typing figures in. It reads the reports '
               'your store already has.</div>'
               '<div class="cp-w-steps">'
               + "".join(f'<div class="cp-w-step"><div class="cp-w-num">{i}</div><b>{esc(title)}</b>'
                         f'<div>{esc(text)}</div></div>' for i, (title, text) in enumerate(STEPS, 1))
               + "</div>"
               '<div class="cp-w-h">What it does for your store</div>'
               '<div class="cp-w-hsub">The evening spreadsheet, the morning huddle and the "why is it '
               'behind?" question, handled.</div>'
               '<div class="cp-w-feats">'
               + "".join(f'<div class="cp-w-feat"><div class="cp-w-icon">{_icon(icon)}</div><b>{esc(title)}</b>'
                         f'<div>{esc(text)}</div></div>' for icon, title, text in FEATURES)
               + "</div>"
               '<div class="cp-w-cta"><div><b>See it on a sample store</b>'
               '<div>A made-up store with a month of sales and a few problems hidden in it. '
               'Two minutes, no sign-up.</div></div></div>')
    with st.container(key="welcome_try"):
        st.button("Explore the sample store", key="welcome_guest_bottom", type="primary", on_click=_guest,
                  icon=":material/arrow_forward:")
    html_block('<div class="cp-w-foot">Your data stays private: only the columns the app needs are kept, '
               'encrypted, visible only to your account, never shared or sold, and deletable any time. '
               'The AI chat only sees your figures after you say yes.</div>')


def account_menu():
    """The account button in the top bar: who's signed in, and Log out (or, for guests, sign in)."""
    if is_signed_in():
        email = st.user.get("email") or ""
        with st.popover(first_name() or "Account", icon=":material/account_circle:"):
            if email:
                html_block(f'<div class="cp-small">Signed in as {esc(email)}</div>')
            if st.button("Log out", key="account_logout", icon=":material/logout:"):
                # Forget this visit's uploaded data and choices, then end the sign-in.
                st.session_state.clear()
                st.logout()
    else:
        with st.popover("Guest", icon=":material/account_circle:"):
            html_block('<div class="cp-small">You\'re looking around without an account. Create one '
                       'to upload your own store\'s data.</div>')
            if is_configured():
                st.button("Log in or create account", key="account_login", type="primary",
                          on_click=sign_in)
            if st.button("Back to the welcome page", key="account_leave", type="tertiary"):
                st.session_state.clear()
                st.rerun()
