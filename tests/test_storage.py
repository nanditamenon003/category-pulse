"""
Checks saving to accounts without a real database: a stand-in plays the part
of Supabase's REST interface.

  - what reaches the database is scrambled: no file contents, file names or
    login ids in it
  - loading gives back exactly the files that were saved, for that person only
  - saving again replaces; deleting removes everything
  - data encrypted with a different key is refused with a plain message

Run from the project folder:  python tests/test_storage.py
"""

import json
import logging
import os
import sys
import warnings

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

from cryptography.fernet import Fernet  # noqa: E402

import storage  # noqa: E402


class FakeDatabase:
    """Just enough of Supabase's REST interface for saved_stores."""

    def __init__(self):
        self.rows = {}

    def request(self, method, settings, params=None, body=None, prefer=None):
        owner = (params or {}).get("owner", "").removeprefix("eq.")

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        if method == "POST":
            self.rows[body["owner"]] = dict(body)
            return Response([])
        if method == "GET":
            return Response([self.rows[owner]] if owner in self.rows else [])
        if method == "DELETE":
            self.rows.pop(owner, None)
            return Response([])
        raise AssertionError(method)


def use(db, key):
    storage._settings = lambda: {"supabase_url": "https://example.supabase.co",
                                 "supabase_key": "test", "encryption_key": key}
    storage._request = db.request


def test_round_trip_and_privacy():
    db, key = FakeDatabase(), Fernet.generate_key().decode()
    use(db, key)
    files = [("targets.csv", b"Line,Category,Monthly target\nMen,Polos,420\n"),
             ("sales.csv", b"Date,Line,Category,Units\n01/06/2026,Men,Polos,7\n")]
    storage.save("auth0|person-a", files, "Sunrise Fashion")

    stored = json.dumps(list(db.rows.values()))
    for secret in ("Polos", "targets.csv", "auth0|person-a", "person-a", "Sunrise Fashion"):
        assert secret not in stored, f"{secret!r} is readable in the database"

    loaded = storage.load("auth0|person-a")
    assert loaded["files"] == files and loaded["name"] == "Sunrise Fashion"
    assert storage.load("auth0|person-b") is None, "another person must not see it"

    storage.save("auth0|person-a", files[:1], "Sunrise Fashion")
    assert len(db.rows) == 1 and storage.load("auth0|person-a")["files"] == files[:1]

    storage.delete("auth0|person-a")
    assert storage.load("auth0|person-a") is None and not db.rows
    print("Saved data is scrambled in the database (no contents, file names or login ids), comes back\n"
          "exactly, is visible only to its owner, is replaced on re-save, and is gone after delete.")


def test_wrong_key_is_refused():
    db = FakeDatabase()
    use(db, Fernet.generate_key().decode())
    storage.save("auth0|person-a", [("sales.csv", b"x")], "Store")
    stored_rows = dict(db.rows)

    other = FakeDatabase()
    other.rows = stored_rows
    use(other, Fernet.generate_key().decode())
    # A different key also gives a different owner fingerprint, so nothing is found...
    assert storage.load("auth0|person-a") is None
    # ...and a payload from another key can't be opened.
    owner = storage._owner(storage._settings(), "auth0|person-a")
    other.rows[owner] = next(iter(stored_rows.values())) | {"owner": owner}
    try:
        storage.load("auth0|person-a")
    except storage.StorageError as e:
        print(f"A different key is refused: {e}")
    else:
        raise AssertionError("expected StorageError")


if __name__ == "__main__":
    test_round_trip_and_privacy()
    test_wrong_key_is_refused()
    print("\nAll checks passed.")
