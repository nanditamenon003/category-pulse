"""
Checks "new since yesterday" and the legal pages:

  - on the Sample Store, each change is a real difference between two closes
  - nothing is claimed with no previous day, or from month-to-date totals
  - the privacy page names everyone who receives data, and is honest that the
    app holds the key

Run from the project folder:  python tests/test_changes.py
"""

import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")

import pandas as pd  # noqa: E402

import diagnosis  # noqa: E402
import digest  # noqa: E402
import kpi  # noqa: E402
import stock  # noqa: E402
import upload  # noqa: E402
from store import demo_store  # noqa: E402

CATEGORIES = ("Polos", "Shirts")
LEGAL = os.path.join(os.path.dirname(__file__), "..", "legal")


def statuses(store, day):
    return {p["category"]: p["status"] for p in kpi.get_category_pace(day, store.hours[-1], store=store)}


def test_changes_are_real():
    demo = demo_store()
    seen = 0
    for day in range(2, demo.today_day + 1):
        changes = diagnosis.get_changes(day, store=demo)
        now, before = statuses(demo, day), statuses(demo, day - 1)
        for change in changes:
            seen += 1
            c = change["category"]
            if "fell behind" in change["text"]:
                assert now[c] == "behind" and before[c] != "behind", (day, change)
            if "back on track" in change["text"]:
                assert before[c] == "behind" and now[c] in ("on_pace", "ahead"), (day, change)
            if "sold out" in change["text"]:
                assert stock.check_size_runs(c, day, demo.hours[-1], store=demo)["verdict"] == "stockout", (day, change)
            if "core sizes" in change["text"]:
                assert stock.check_size_runs(c, day, demo.hours[-1], store=demo)["verdict"] == "broken_size_run", (day, change)
        # Every category newly behind is listed: nothing is missed.
        listed = {c["category"] for c in changes if "fell behind" in c["text"]}
        assert listed == {c for c, s in now.items() if s == "behind" and before[c] != "behind"}, day
        assert [c["bad"] for c in changes] == sorted((c["bad"] for c in changes), reverse=True), "bad news first"
    print(f"Every change across the month checks out against the two closes ({seen} changes).")

    for day in range(2, demo.today_day + 1):
        changes = diagnosis.get_changes(day, store=demo)
        if changes:
            print(f"  e.g. day {day}: " + "; ".join(c["text"] for c in changes[:3]))
            break
    latest = diagnosis.get_changes(store=demo)
    report = digest.whatsapp_report(store=demo)
    assert ("*New today:*" in report) == bool(latest)
    print(f"  On the latest day: {[c['text'] for c in latest] or 'nothing new'}")


def test_nothing_is_claimed_without_a_previous_day():
    rows = [{"Date": f"{d:02d}/06/2026", "Category": c, "Qty": 3} for d in range(1, 21) for c in CATEGORIES]
    frame = pd.DataFrame(rows)
    targets = pd.DataFrame({"Category": CATEGORIES, "Target": [90, 90]}).to_csv(index=False).encode()
    store, _ = upload.read_upload([("sales.csv", frame.to_csv(index=False).encode()), ("targets.csv", targets)])
    assert diagnosis.get_changes(1, store=store) is None
    assert diagnosis.get_changes(store=store) == []  # steady sales: nothing new, and no stock to check

    part = frame[frame["Date"].str[:2].astype(int) >= 10]
    store, _ = upload.read_upload([("sales.csv", part.to_csv(index=False).encode()), ("targets.csv", targets)])
    assert diagnosis.get_changes(10, store=store) is None, "the file's first day has no day before it"

    totals = frame.groupby("Category", as_index=False)["Qty"].sum().assign(Date="20/06/2026")
    store, _ = upload.read_upload([("sales.csv", totals.to_csv(index=False).encode()), ("targets.csv", targets)])
    assert store.summary_only and diagnosis.get_changes(store=store) is None
    assert "New today" not in digest.whatsapp_report(store=store)
    print("With no real previous day, nothing is claimed.")


def test_legal_pages_match_the_app():
    with open(os.path.join(LEGAL, "privacy.md"), encoding="utf-8") as f:
        privacy = f.read()
    with open(os.path.join(LEGAL, "terms.md"), encoding="utf-8") as f:
        terms = f.read()
    for service in ("Auth0", "Supabase", "Streamlit", "DeepSeek"):
        assert service in privacy, service
    assert "holds the encryption key" in privacy and "could technically open it" in privacy
    assert "Delete my data" in privacy and "Only upload data you're allowed to use" in privacy
    assert "prototype" in terms and "estimates" in terms
    print("The privacy page names everyone who receives data, and that the app holds the key.")


if __name__ == "__main__":
    test_changes_are_real()
    test_nothing_is_claimed_without_a_previous_day()
    test_legal_pages_match_the_app()
    print("\nAll checks passed.")
