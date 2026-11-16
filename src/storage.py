"""
Saving a person's uploaded data to their account, so they don't have to
upload it again on every visit.

What is saved: the columns in use from the files they uploaded, encrypted by
this app before they leave it (Fernet: AES with an authenticity check),
using a key that only the app holds. The database (Supabase) only ever sees
scrambled text, stored against a one-way fingerprint of the person's login
rather than their email. Even the store's name is inside the encrypted part.
Up to five stores per person, each in its own row, plus an encrypted list of
them; saving again replaces a store, and "Delete all my data" removes every row.

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


MAX_STORES = 5


def _row_owner(settings, person, slot):
    """The first store keeps the row it always had; others, and the list of stores, get their own."""
    return _owner(settings, person if slot == "1" else f"{person}#{slot}")


def _put(settings, owner, content):
    _request("POST", settings, prefer="resolution=merge-duplicates", body={
        "owner": owner,
        "name": None,  # kept inside the encrypted payload instead
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "payload": _fernet(settings).encrypt(json.dumps(content).encode()).decode(),
    })


def _get(settings, owner):
    """(the decrypted content, when it was saved) for one row, or None."""
    from cryptography.fernet import InvalidToken

    rows = _request("GET", settings, params={"owner": f"eq.{owner}", "select": "name,saved_at,payload"}).json()
    if not rows:
        return None
    try:
        content = json.loads(_fernet(settings).decrypt(rows[0]["payload"].encode()))
    except InvalidToken:
        raise StorageError("Your saved data couldn't be opened (the app's key has changed). "
                           "Please upload your file again.")
    if isinstance(content, list):  # saved before the name moved inside the encryption
        content = {"name": rows[0].get("name"), "files": content}
    return content, rows[0]["saved_at"]


def _index(settings, person):
    """The person's list of stores: {"stores": [...], "current": slot}. Older accounts have no
    list, only their one store, which becomes the first."""
    found = _get(settings, _row_owner(settings, person, "index"))
    if found:
        return found[0]
    first = _get(settings, _row_owner(settings, person, "1"))
    if not first:
        return {"stores": [], "current": None}
    return {"stores": [{"slot": "1", "name": first[0]["name"], "saved_at": first[1], "summary": None}],
            "current": "1"}


def stores(person):
    """This person's saved stores, in the order they were added, and which one was open last."""
    settings = _settings()
    if settings is None:
        return {"stores": [], "current": None}
    return _index(settings, person)


def new_slot(person):
    """The slot for another store, or None if the person already has MAX_STORES."""
    taken = {s["slot"] for s in stores(person)["stores"]}
    if len(taken) >= MAX_STORES:
        return None
    return next(str(n) for n in range(1, MAX_STORES + 2) if str(n) not in taken)


def save(person, files, name, layouts=None, slot="1", summary=None):
    """
    Saves [(file name, bytes)] as one of this person's stores (`slot`), replacing
    what that store had before. `layouts` remembers how their files' columns were
    read, for next time (smart_import.find_tables); `summary` is a few headline
    figures for the list of stores, so it can be shown without opening each one.
    """
    settings = _settings()
    if settings is None:
        return
    _put(settings, _row_owner(settings, person, slot), {
        "name": name,
        "files": [{"name": n, "data": base64.b64encode(d).decode()} for n, d in files],
        "layouts": layouts or {},
    })
    index = _index(settings, person)
    entry = {"slot": slot, "name": name, "saved_at": datetime.now(timezone.utc).isoformat(), "summary": summary}
    entries = [s for s in index["stores"] if s["slot"] != slot]
    position = next((i for i, s in enumerate(index["stores"]) if s["slot"] == slot), len(entries))
    entries.insert(position, entry)
    _put(settings, _row_owner(settings, person, "index"), {"stores": entries, "current": slot})


def load(person, slot=None):
    """
    One saved store (the one open last, unless `slot` is given): its files, name,
    remembered layouts, when it was saved and its slot. None if nothing is saved.
    """
    settings = _settings()
    if settings is None:
        return None
    index = _index(settings, person)
    slot = slot or index["current"] or "1"
    found = _get(settings, _row_owner(settings, person, slot))
    if not found:
        return None
    content, saved_at = found
    if index["stores"] and index["current"] != slot:
        _put(settings, _row_owner(settings, person, "index"), index | {"current": slot})
    files = [(f["name"], base64.b64decode(f["data"])) for f in content["files"]]
    return {"files": files, "name": content["name"], "layouts": content.get("layouts", {}),
            "saved_at": saved_at, "slot": slot}


def delete(person, slot=None):
    """Removes one store (`slot`), or, with no slot, everything saved for this person."""
    settings = _settings()
    if settings is None:
        return
    index = _index(settings, person)
    slots = [slot] if slot else [s["slot"] for s in index["stores"]] + ["1"]
    for one in dict.fromkeys(slots):
        _request("DELETE", settings, params={"owner": f"eq.{_row_owner(settings, person, one)}"})
    left = [s for s in index["stores"] if s["slot"] not in slots]
    if left:
        current = index["current"] if index["current"] not in slots else left[0]["slot"]
        _put(settings, _row_owner(settings, person, "index"), {"stores": left, "current": current})
    else:
        _request("DELETE", settings, params={"owner": f"eq.{_row_owner(settings, person, 'index')}"})
