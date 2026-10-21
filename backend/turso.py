"""turso.py - imy's database on Turso (hosted libSQL, which is SQLite), over Turso's HTTP API.

db.py uses this instead of the local data/imy.db file when these are set in .env:

    IMY_TURSO_URL=libsql://<database>-<org>.turso.io     (`turso db show <name> --url`)
    IMY_TURSO_TOKEN=...                                  (`turso db tokens create <name>`)

Why over HTTP: it needs no extra package (nothing native to build on the server), and because
Turso runs SQLite, every query imy already uses works unchanged.

It behaves like Python's sqlite3 connection, the parts imy uses:
  - execute / executemany / executescript, cursors with fetchone / fetchall / lastrowid / rowcount
  - rows you can read by name or by position, and turn into a dict: row["id"], row[0], dict(row)
  - a transaction starts at the first write and is committed (or rolled back) when db() ends, the
    same way sqlite3 does it, so a `with db() as conn:` block is still all-or-nothing
  - a failed statement raises sqlite3.IntegrityError (a UNIQUE or other constraint) or
    sqlite3.OperationalError, so existing error handling keeps working

Every statement is one request to Turso. Connections are kept open between requests (per thread),
so each one costs a single round trip.
"""
import base64
import http.client
import json
import sqlite3
import threading
import urllib.parse

TIMEOUT = 30
_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")
_local = threading.local()          # one open HTTPS connection per thread and host


def http_url(url: str) -> str:
    """libsql://db-org.turso.io -> https://db-org.turso.io (http:// is kept, for a local test server)."""
    url = url.strip().rstrip("/")
    for prefix in ("libsql://", "turso://", "wss://", "ws://"):
        if url.startswith(prefix):
            return ("http://" if prefix == "ws://" else "https://") + url[len(prefix):]
    return url


# ---------------------------------------------------------------- values
def encode(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode()}
    return {"type": "text", "value": str(v)}


def decode(v: dict):
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        return base64.b64decode(v.get("base64") or "")
    return v.get("value")


class Row:
    """Like sqlite3.Row: row["name"] (any case), row[0], len(row), iteration over values, dict(row)."""
    __slots__ = ("_cols", "_vals", "_index")

    def __init__(self, cols: list[str], vals: list, index: dict):
        self._cols, self._vals, self._index = cols, vals, index

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        try:
            return self._vals[self._index[key.lower()]]
        except KeyError:
            raise IndexError(f"No item with that key: {key}") from None

    def keys(self) -> list[str]:
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self) -> int:
        return len(self._vals)

    def __eq__(self, other):
        return isinstance(other, Row) and self._cols == other._cols and self._vals == other._vals

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._cols, self._vals))!r})"


class Cursor:
    def __init__(self, result: dict | None):
        result = result or {}
        cols = [c.get("name") or "" for c in result.get("cols") or []]
        index = {}
        for i, c in enumerate(cols):
            index.setdefault(c.lower(), i)
        self._rows = [Row(cols, [decode(v) for v in r], index) for r in result.get("rows") or []]
        self._pos = 0
        self.rowcount = int(result.get("affected_row_count") or 0) if result else -1
        last = result.get("last_insert_rowid")
        self.lastrowid = int(last) if last not in (None, "") else None
        self.description = tuple((c, None, None, None, None, None, None) for c in cols) or None

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list:
        rows, self._pos = self._rows[self._pos:], len(self._rows)
        return rows

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


# ---------------------------------------------------------------- one database connection (a Turso "stream")
def _error(err: dict) -> Exception:
    message = err.get("message") or "the database refused the request"
    code = (err.get("code") or "").upper()
    if "CONSTRAINT" in code or "constraint failed" in message.lower():
        return sqlite3.IntegrityError(message)
    return sqlite3.OperationalError(message)


def split_script(script: str) -> list[str]:
    """A script of several statements -> one string per statement."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip().strip(";").strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


class Connection:
    """One Turso stream: statements in it share a transaction, like one sqlite3 connection."""

    def __init__(self, url: str, token: str, setup: tuple[str, ...] = ("PRAGMA foreign_keys = ON",)):
        self.base = http_url(url)
        self.token = token
        self.baton: str | None = None
        self.in_transaction = False
        self._setup = list(setup)          # sent with the first request of the stream (no extra round trip)

    # -- transport
    def _post(self, body: dict) -> dict:
        parts = urllib.parse.urlsplit(self.base)
        key = (parts.scheme, parts.netloc)
        path = (parts.path.rstrip("/") or "") + "/v2/pipeline"
        payload = json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        for attempt in (1, 2):
            conns = getattr(_local, "conns", None)
            if conns is None:
                conns = _local.conns = {}
            conn = conns.get(key)
            fresh = conn is None
            if fresh:
                cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
                conn = conns[key] = cls(parts.netloc, timeout=TIMEOUT)
            try:
                conn.request("POST", path, payload, headers)
                resp = conn.getresponse()
                data = resp.read()
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError,
                    http.client.CannotSendRequest) as exc:
                conn.close()
                conns.pop(key, None)
                if attempt == 1 and not fresh:   # an idle kept-open connection the server had closed: retry once
                    continue
                raise sqlite3.OperationalError(f"couldn't reach the database: {exc}") from None
            except OSError as exc:
                conn.close()
                conns.pop(key, None)
                raise sqlite3.OperationalError(f"couldn't reach the database: {exc}") from None
            if resp.status != 200:
                text = data.decode("utf-8", "replace")[:300]
                if resp.status in (401, 403):
                    raise sqlite3.OperationalError(f"the database token was refused ({resp.status}): {text}")
                raise sqlite3.OperationalError(f"the database said {resp.status}: {text}")
            return json.loads(data)
        raise sqlite3.OperationalError("couldn't reach the database")

    def _pipeline(self, requests: list[dict]) -> list[dict]:
        if self._setup:
            requests = [{"type": "execute", "stmt": {"sql": s}} for s in self._setup] + requests
            skip, self._setup = len(self._setup), []
        else:
            skip = 0
        reply = self._post({"baton": self.baton, "requests": requests})
        self.baton = reply.get("baton")
        if reply.get("base_url"):
            self.base = http_url(reply["base_url"])
        results = reply.get("results") or []
        for r in results:
            if r.get("type") == "error":
                raise _error(r.get("error") or {})
        return [(r.get("response") or {}).get("result") for r in results[skip:]]

    @staticmethod
    def _stmt(sql: str, params=()) -> dict:
        stmt = {"sql": sql}
        if isinstance(params, dict):
            stmt["named_args"] = [{"name": k, "value": encode(v)} for k, v in params.items()]
        elif params:
            stmt["args"] = [encode(v) for v in params]
        return {"type": "execute", "stmt": stmt}

    def _begin_if_writing(self, sql: str) -> list[dict]:
        first = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if first in _WRITES and not self.in_transaction:
            self.in_transaction = True
            return [{"type": "execute", "stmt": {"sql": "BEGIN"}}]
        return []

    # -- the sqlite3-style API
    def execute(self, sql: str, params=()) -> Cursor:
        begin = self._begin_if_writing(sql)
        results = self._pipeline(begin + [self._stmt(sql, params)])
        return Cursor(results[-1])

    def executemany(self, sql: str, seq) -> Cursor:
        stmts = [self._stmt(sql, p) for p in seq]
        if not stmts:
            return Cursor(None)
        begin = self._begin_if_writing(sql)
        total = 0
        for i in range(0, len(stmts), 200):           # big imports go in a few requests, not thousands
            results = self._pipeline((begin if i == 0 else []) + stmts[i:i + 200])
            total += sum(int((r or {}).get("affected_row_count") or 0) for r in results)
        cur = Cursor(None)
        cur.rowcount = total
        return cur

    def executescript(self, script: str) -> Cursor:
        statements = split_script(script)
        if statements:
            self._pipeline([self._stmt(s) for s in statements])
        return Cursor(None)

    def commit(self) -> None:
        if self.in_transaction:
            self.in_transaction = False
            self._pipeline([{"type": "execute", "stmt": {"sql": "COMMIT"}}])

    def rollback(self) -> None:
        if self.in_transaction:
            self.in_transaction = False
            try:
                self._pipeline([{"type": "execute", "stmt": {"sql": "ROLLBACK"}}])
            except sqlite3.Error:
                pass                                   # the stream is closed below either way

    def close(self) -> None:
        if self.baton:
            try:
                self._post({"baton": self.baton, "requests": [{"type": "close"}]})
            except sqlite3.Error:
                pass
            self.baton = None
