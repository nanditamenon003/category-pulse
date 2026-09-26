"""
Daily limits on AI chat questions, to protect the prepaid AI credit.

Each signed-in account gets QUESTIONS_PER_DAY, and everyone together gets
ALL_QUESTIONS_PER_DAY as a backstop. The counts are kept on the server (not
in the visitor's browser session), so refreshing the page doesn't reset them.
They start again each day, and when the app restarts (for example after the
free hosting has put it to sleep), which errs on the side of letting people
ask.
"""

import threading
from datetime import date

import streamlit as st

from config import ALL_QUESTIONS_PER_DAY, QUESTIONS_PER_DAY


@st.cache_resource
def _counts():
    """Shared by every visitor to this running app: {"lock", "day", "by_person", "total"}."""
    return {"lock": threading.Lock(), "day": None, "by_person": {}, "total": 0}


def _today(counts):
    """Starts the counts afresh on a new day. Call with the lock held."""
    today = date.today().isoformat()
    if counts["day"] != today:
        counts.update(day=today, by_person={}, total=0)


def questions_left(person):
    """(questions this person can still ask today, whether everyone's daily limit is reached)."""
    counts = _counts()
    with counts["lock"]:
        _today(counts)
        used = counts["by_person"].get(person, 0)
        return max(QUESTIONS_PER_DAY - used, 0), counts["total"] >= ALL_QUESTIONS_PER_DAY


def record_question(person):
    counts = _counts()
    with counts["lock"]:
        _today(counts)
        counts["by_person"][person] = counts["by_person"].get(person, 0) + 1
        counts["total"] += 1
