"""
Checks saving to accounts without a real database: a stand-in plays the part
of Supabase's REST interface.

  - what reaches the database is scrambled: no file contents, file names or
    login ids in it
  - loading gives back exactly the files that were saved, for that person only
  - saving again replaces; deleting removes everything
  - several stores per person: each opens on its own, the list of them is
    scrambled too, removing one keeps the rest, and an account saved before
    there could be several still opens
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
    assert len(db.rows) == 2 and storage.load("auth0|person-a")["files"] == files[:1]  # the store, and the list

    storage.delete("auth0|person-a")
    assert storage.load("auth0|person-a") is None and not db.rows
    print("Saved data is scrambled in the database (no contents, file names or login ids), comes back\n"
          "exactly, is visible only to its owner, is replaced on re-save, and is gone after delete.")


def test_several_stores():
    db, key = FakeDatabase(), Fernet.generate_key().decode()
    use(db, key)
    person = "auth0|area-manager"
    storage.save(person, [("sales.csv", b"mall")], "Phoenix Mall", summary={"behind": 2})
    assert storage.new_slot(person) == "2"
    storage.save(person, [("sales.csv", b"high street")], "High Street", slot="2", summary={"behind": 0})
    listed = storage.stores(person)
    assert [s["name"] for s in listed["stores"]] == ["Phoenix Mall", "High Street"] and listed["current"] == "2"
    assert listed["stores"][0]["summary"] == {"behind": 2}
    assert storage.load(person)["name"] == "High Street", "opens the one used last"
    assert storage.load(person, "1")["files"] == [("sales.csv", b"mall")]
    assert storage.stores(person)["current"] == "1", "opening one makes it the one used last"
    for secret in ("Phoenix", "High Street", "area-manager", "mall"):
        assert secret not in json.dumps(list(db.rows.values())), f"{secret!r} is readable"
    assert storage.stores("auth0|someone-else")["stores"] == []

    # Re-saving keeps a store's place in the list.
    storage.save(person, [("sales.csv", b"mall, later")], "Phoenix Mall", slot="1")
    assert [s["slot"] for s in storage.stores(person)["stores"]] == ["1", "2"]

    for n in range(3, storage.MAX_STORES + 1):
        storage.save(person, [("sales.csv", b"x")], f"Store {n}", slot=storage.new_slot(person))
    assert storage.new_slot(person) is None, "a limit, so the free database isn't filled up"

    storage.delete(person, "1")
    listed = storage.stores(person)
    assert "1" not in [s["slot"] for s in listed["stores"]] and listed["current"] != "1"
    assert storage.load(person, "2")["name"] == "High Street"
    assert storage.new_slot(person) == "1", "a removed store's place can be used again"
    storage.delete(person)
    assert not db.rows and storage.stores(person)["stores"] == []
    print("Several stores: each opens on its own, the list is scrambled, one can be removed\n"
          "without the others, there's a limit of five, and Delete all removes every row.")

    # An account saved before several stores were possible: one row, no list.
    old = FakeDatabase()
    use(old, key)
    owner = storage._owner(storage._settings(), "auth0|older")
    payload = Fernet(key.encode()).encrypt(json.dumps({"name": "Old Store", "files": [], "layouts": {}}).encode()).decode()
    old.rows[owner] = {"owner": owner, "name": None, "saved_at": "2026-10-01T10:00:00+00:00", "payload": payload}
    assert [s["name"] for s in storage.stores("auth0|older")["stores"]] == ["Old Store"]
    assert storage.load("auth0|older")["slot"] == "1"
    storage.save("auth0|older", [("sales.csv", b"x")], "Second", slot=storage.new_slot("auth0|older"))
    assert [s["name"] for s in storage.stores("auth0|older")["stores"]] == ["Old Store", "Second"]
    print("An account saved before there could be several stores still opens, and can add more.")


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
    test_several_stores()
    test_wrong_key_is_refused()
    print("\nAll checks passed.")
