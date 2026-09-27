"""
Saving a person's uploaded data to their account, so they don't have to
upload it again on every visit.

What is saved: the files they uploaded, exactly as uploaded, encrypted by
this app before they leave it (Fernet: AES with an authenticity check),
using a key that only the app holds. The database (Supabase) only ever sees
scrambled text, stored against a one-way fingerprint of the person's login
rather than their email. Even the store's name is inside the encrypted part.
One saved store per person; saving again replaces it, and "Delete my data"
removes it completely.

Settings, in .streamlit/secrets.toml (never in git):

    [storage]
    supabase_url = "https://<project>.supabase.co"
    supabase_key = "<the project's secret / service_role key>"
    encryption_key = "<generated once; keep the same everywhere>"

and one table in the Supabase project (row-level security on, with no
policies, so only this app's secret key can reach it):

    create table public.saved_stores (
      owner text primary key,
      name text,
      saved_at timestamptz not null default now(),
      payload text not null
    );
    alter table public.saved_stores enable row level security;

Without those settings, saving is off and uploads last for the visit only.
"""

import base64
import hashlib
import json
from datetime import datetime, timezone

import streamlit as st

TABLE = "saved_stores"


class StorageError(Exception):
    """Saving or loading failed; the message is safe to show."""


def _settings():
    try:
        section = st.secrets.get("storage", {})
    except Exception:  # no secrets file at all
        return None
    keys = ("supabase_url", "supabase_key", "encryption_key")
    if not all(str(section.get(k, "")).strip() and "PASTE" not in str(section.get(k)) for k in keys):
        return None
    return {k: str(section[k]).strip() for k in keys}


def is_configured():
    return _settings() is not None


def _fernet(settings):
    from cryptography.fernet import Fernet

    return Fernet(settings["encryption_key"].encode())


def _owner(settings, person):
    """A one-way fingerprint of the login id, so the database never holds who it belongs to."""
    return hashlib.sha256(f"{settings['encryption_key']}:{person}".encode()).hexdigest()


def _request(method, settings, params=None, body=None, prefer=None):
    import requests

    headers = {"apikey": settings["supabase_key"],
               "Authorization": f"Bearer {settings['supabase_key']}",
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = requests.request(method, f"{settings['supabase_url'].rstrip('/')}/rest/v1/{TABLE}",
                                    params=params, json=body, headers=headers, timeout=20)
    except requests.RequestException:
        raise StorageError("Couldn't reach the saved-data service. Your data still works for this visit.")
    if response.status_code >= 400:
        raise StorageError(f"The saved-data service refused the request ({response.status_code}). "
                           f"Your data still works for this visit.")
    return response


def save(person, files, name):
    """Saves [(file name, bytes)] for this person, replacing anything saved before."""
    settings = _settings()
    if settings is None:
        return
    packed = json.dumps({
        "name": name,
        "files": [{"name": n, "data": base64.b64encode(d).decode()} for n, d in files],
    }).encode()
    _request("POST", settings, prefer="resolution=merge-duplicates", body={
        "owner": _owner(settings, person),
        "name": None,  # kept inside the encrypted payload instead
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "payload": _fernet(settings).encrypt(packed).decode(),
    })


def load(person):
    """This person's saved files and when they were saved, or None if nothing is saved."""
    from cryptography.fernet import InvalidToken

    settings = _settings()
    if settings is None:
        return None
    rows = _request("GET", settings, params={"owner": f"eq.{_owner(settings, person)}",
                                            "select": "name,saved_at,payload"}).json()
    if not rows:
        return None
    try:
        packed = _fernet(settings).decrypt(rows[0]["payload"].encode())
    except InvalidToken:
        raise StorageError("Your saved data couldn't be opened (the app's key has changed). "
                           "Please upload your file again.")
    content = json.loads(packed)
    if isinstance(content, list):  # saved before the name moved inside the encryption
        content = {"name": rows[0].get("name"), "files": content}
    files = [(f["name"], base64.b64decode(f["data"])) for f in content["files"]]
    return {"files": files, "name": content["name"], "saved_at": rows[0]["saved_at"]}


def delete(person):
    """Removes everything saved for this person."""
    settings = _settings()
    if settings is None:
        return
    _request("DELETE", settings, params={"owner": f"eq.{_owner(settings, person)}"})
