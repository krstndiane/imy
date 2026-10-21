"""The database on Turso (turso.py), against a stand-in Turso server that speaks the same HTTP.

Run with:  pytest backend/tests -q   (from the project root)
"""
import importlib
import os
import sqlite3
import tempfile

import pytest

from backend import turso
from backend.tests.fakes import FakeTurso


def test_values_rows_and_cursors_behave_like_sqlite():
    with FakeTurso() as t:
        c = turso.Connection(t.url + "/unit", t.token)
        c.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, n REAL, b BLOB, x INTEGER);")
        cur = c.execute("INSERT INTO t (name, n, b, x) VALUES (?, ?, ?, ?)", ("mia ñ 😂", 2.5, b"\x00\xff", None))
        assert cur.lastrowid == 1 and cur.rowcount == 1
        c.executemany("INSERT INTO t (name, x) VALUES (?, ?)", [("a", 10), ("b", True)])
        c.commit()
        row = c.execute("SELECT * FROM t WHERE id = ?", (1,)).fetchone()
        assert row["name"] == "mia ñ 😂" and row["NAME"] == row[1] and row["n"] == 2.5 and row["b"] == b"\x00\xff"
        assert row["x"] is None and dict(row) == {"id": 1, "name": "mia ñ 😂", "n": 2.5, "b": b"\x00\xff", "x": None}
        assert [r["x"] for r in c.execute("SELECT x FROM t WHERE x IS NOT NULL ORDER BY id")] == [10, 1]
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
        assert c.execute("SELECT id FROM t WHERE name = :n", {"n": "a"}).fetchone()[0] == 2   # named arguments
        c.close()


def test_a_block_is_all_or_nothing_and_errors_look_like_sqlite():
    with FakeTurso() as t:
        setup = turso.Connection(t.url + "/tx", t.token)
        setup.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT UNIQUE);")
        setup.close()

        c = turso.Connection(t.url + "/tx", t.token)
        c.execute("INSERT INTO t (name) VALUES ('kept')")
        c.commit()
        c.execute("INSERT INTO t (name) VALUES ('rolled back')")
        with pytest.raises(sqlite3.IntegrityError):                # UNIQUE constraint -> same error as sqlite3
            c.execute("INSERT INTO t (name) VALUES ('kept')")
        c.rollback()
        with pytest.raises(sqlite3.OperationalError):
            c.execute("SELECT * FROM no_such_table")
        c.close()

        check = turso.Connection(t.url + "/tx", t.token)
        assert [r[0] for r in check.execute("SELECT name FROM t")] == ["kept"]
        check.close()


def test_wrong_token_and_unreachable_server_are_clear_errors():
    with FakeTurso() as t:
        with pytest.raises(sqlite3.OperationalError, match="token was refused"):
            turso.Connection(t.url + "/x", "wrong").execute("SELECT 1")
    with pytest.raises(sqlite3.OperationalError, match="couldn't reach"):
        turso.Connection("http://127.0.0.1:9", "t").execute("SELECT 1")


def test_libsql_urls_and_scripts():
    assert turso.http_url("libsql://imy-pdyn.aws-ap-northeast-1.turso.io/") == "https://imy-pdyn.aws-ap-northeast-1.turso.io"
    assert turso.split_script("CREATE TABLE a (x TEXT DEFAULT ';');\n-- note\nCREATE TABLE b (y);") == [
        "CREATE TABLE a (x TEXT DEFAULT ';');", "-- note\nCREATE TABLE b (y);"]


def test_the_whole_app_runs_on_turso(monkeypatch):
    with FakeTurso() as t:
        os.environ["IMY_DATA_DIR"] = tempfile.mkdtemp()
        monkeypatch.setenv("IMY_TURSO_URL", t.url + "/app")
        monkeypatch.setenv("IMY_TURSO_TOKEN", t.token)
        from backend import auth, db as db_mod, main
        importlib.reload(db_mod)
        importlib.reload(auth)
        importlib.reload(main)
        main.init_db()
        main.init_db()                                           # starting twice is fine (migrations are skipped)
        assert db_mod.db_kind() == "turso"
        user = auth.create_user("a@example.com", "testing12345", True, terms=True)
        auth.CURRENT.set(auth.load_session(auth.start_session(user["id"])))
        pid = main.create_person(main.NewPersonIn(answers={"q1": "Mia"}, consent=True))["id"]
        shot = main.import_text(pid, main.TextImportIn(text="Mia: hello bebe\nMe: hi!\nMia: kain ka na", person_name="Mia"))
        assert [m["text"] for m in shot["messages"]] == ["hello bebe", "hi!", "kain ka na"]
        assert main.get_person(pid)["person_message_count"] == 2
        with pytest.raises(sqlite3.IntegrityError):              # a duplicate email is refused by the database
            with db_mod.db() as conn:
                conn.execute("INSERT INTO users (email, password_hash, created_at) VALUES ('a@example.com', 'x', 'now')")
        assert not os.path.exists(db_mod.DB_PATH)                # nothing was written to a local database file
        auth.CURRENT.set(None)
