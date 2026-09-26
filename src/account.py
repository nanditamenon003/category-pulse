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

from ui import esc, html_block

PROVIDER = "auth0"

FEATURES = [
    ("Catch problems early",
     "Every category tracked against its monthly target, with real problems told apart from "
     "normal ups and downs."),
    ("Know why",
     "Sold out, missing sizes, fewer visitors or fewer buyers: the likely cause, with the "
     "evidence behind it."),
    ("Know what to do",
     "One clear action per problem, what to say at the till, and where to put the team "
     "tomorrow."),
]


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


def welcome_page():
    """The front page for anyone not signed in."""
    html_block('<div class="cp-hero">'
               '<div class="cp-hero-brand">Category Pulse</div>'
               '<div class="cp-hero-title">Know which categories are falling behind, why, and what '
               'to do about it, while there\'s still time to act.</div>'
               '<div class="cp-hero-sub">A floor assistant for clothing stores. Upload your targets '
               'and sales from any system, and every category gets a status, a likely cause and '
               'a next step.</div></div>')

    configured = is_configured()
    with st.container(horizontal=True, gap="small", wrap=True, key="welcome_actions"):
        if configured:
            st.button("Log in", key="welcome_login", type="primary", on_click=sign_in)
            st.button("Create account", key="welcome_signup", on_click=sign_in)
        if st.button("Look around without an account", key="welcome_guest",
                     type="secondary" if not configured else "tertiary"):
            st.session_state["guest"] = True
            st.rerun()
    if configured:
        html_block('<div class="cp-small">Both open a secure sign-in page run by Auth0, where you can '
                   'also continue with Google. To create an account, choose <b>Sign up</b> there. '
                   'This app never sees your password.</div>')
    else:
        html_block('<div class="cp-small">Sign-in isn\'t set up on this copy of the app, so you can '
                   'look around without an account.</div>')

    html_block('<div class="cp-features">' + "".join(
        f'<div class="cp-feature"><div class="cp-feature-title">{esc(title)}</div>'
        f'<div class="cp-feature-text">{esc(text)}</div></div>'
        for title, text in FEATURES) + "</div>")


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
