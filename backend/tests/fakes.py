"""Stand-in servers for the tests: Supabase Storage and Turso, speaking the same HTTP as the real
ones (the parts imy uses), so storage.py and turso.py are tested over real HTTP without the internet.

    with FakeSupabase() as sb:   os.environ["SUPABASE_URL"] = sb.url ...   sb.objects == {"imy-uploads/12/a.png": b"..."}
    with FakeTurso() as t:       os.environ["IMY_TURSO_URL"] = t.url + "/somedb" ...
"""
import base64
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server:
    handler = None

    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.httpd.fake = self
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- Supabase Storage
class _StorageHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status: int, body=b"", ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _authorised(self) -> bool:
        fake = self.server.fake
        ok = self.headers.get("Authorization") == f"Bearer {fake.key}" and self.headers.get("apikey") == fake.key
        if not ok:
            self._send(401, {"statusCode": "401", "error": "Unauthorized", "message": "Invalid JWT"})
        return ok

    def do_POST(self):
        fake, path = self.server.fake, self.path
        if not self._authorised():
            return
        body = self._body()
        if path == "/storage/v1/bucket":
            spec = json.loads(body)
            if spec["id"] in fake.buckets:
                return self._send(400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})
            fake.buckets[spec["id"]] = spec
            return self._send(200, {"name": spec["id"]})
        if path.startswith("/storage/v1/object/list/"):
            bucket = path.rsplit("/", 1)[1]
            spec = json.loads(body)
            prefix = spec.get("prefix", "").strip("/")
            names, seen = [], set()
            for full in sorted(fake.objects):
                b, _, key = full.partition("/")
                if b != bucket or not key.startswith(prefix + "/" if prefix else ""):
                    continue
                rest = key[len(prefix) + 1:] if prefix else key
                head, slash, _ = rest.partition("/")
                if slash:                                    # a folder: listed once, without an id
                    if head not in seen:
                        seen.add(head)
                        names.append({"name": head, "id": None})
                else:
                    names.append({"name": head, "id": str(uuid.uuid4())})
            off, lim = spec.get("offset", 0), spec.get("limit", 100)
            return self._send(200, names[off:off + lim])
        if path.startswith("/storage/v1/object/"):
            bucket_key = path[len("/storage/v1/object/"):]
            bucket = bucket_key.split("/", 1)[0]
            if bucket not in fake.buckets:
                return self._send(400, {"statusCode": "404", "error": "Bucket not found", "message": "Bucket not found"})
            if bucket_key in fake.objects and self.headers.get("x-upsert") != "true":
                return self._send(400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})
            fake.objects[bucket_key] = body
            fake.types[bucket_key] = self.headers.get("Content-Type")
            return self._send(200, {"Key": bucket_key})
        self._send(404, {"message": "not found"})

    def do_GET(self):
        fake = self.server.fake
        if not self._authorised():
            return
        key = self.path[len("/storage/v1/object/"):]
        if key not in fake.objects:
            return self._send(400, {"statusCode": "404", "error": "not_found", "message": "Object not found"})
        self._send(200, fake.objects[key], fake.types.get(key) or "application/octet-stream")

    def do_DELETE(self):
        fake = self.server.fake
        if not self._authorised():
            return
        bucket = self.path[len("/storage/v1/object/"):]
        gone = []
        for key in json.loads(self._body()).get("prefixes", []):
            if fake.objects.pop(f"{bucket}/{key}", None) is not None:
                gone.append({"name": key})
        self._send(200, gone)


class FakeSupabase(_Server):
    handler = _StorageHandler

    def __init__(self, key: str = "service-key"):
        self.key = key
        self.buckets: dict = {}
        self.objects: dict[str, bytes] = {}         # "bucket/path" -> bytes
        self.types: dict[str, str] = {}

    def files(self, bucket: str = "imy-uploads") -> list[str]:
        return sorted(k.split("/", 1)[1] for k in self.objects if k.startswith(bucket + "/"))


# ---------------------------------------------------------------- Turso (Hrana over HTTP, /v2/pipeline)
def _value(v):
    t = v["type"]
    if t == "null":
        return None
    if t == "integer":
        assert isinstance(v["value"], str), "integers travel as strings"
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        return base64.b64decode(v["base64"])
    return v["value"]


def _out(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    return {"type": "text", "value": v}


class _TursoHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        fake = self.server.fake
        path = self.path.strip("/")
        if self.headers.get("Authorization") != f"Bearer {fake.token}":
            return self._send(401, b"Unauthorized", "text/plain")
        if not path.endswith("v2/pipeline"):
            return self._send(404, b"not found", "text/plain")
        db_name = path[:-len("v2/pipeline")].strip("/")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        fake.requests += 1
        with fake.lock:
            baton = body.get("baton")
            conn = fake.streams.pop(baton, None) if baton else None
            if baton and conn is None:
                return self._send(400, json.dumps({"message": "stream expired"}).encode())
        if conn is None:
            conn = sqlite3.connect(fake.path(db_name or "default"), isolation_level=None, check_same_thread=False,
                                   timeout=30)
        results, closed = [], False
        for req in body.get("requests", []):
            if req["type"] == "close":
                conn.close()
                closed = True
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            stmt = req["stmt"]
            args = [_value(a) for a in stmt.get("args", [])]
            if stmt.get("named_args"):
                args = {a["name"].lstrip(":@$"): _value(a["value"]) for a in stmt["named_args"]}
            try:
                cur = conn.execute(stmt["sql"], args)
                rows = cur.fetchall()
                cols = [{"name": d[0], "decltype": None} for d in (cur.description or [])]
                results.append({"type": "ok", "response": {"type": "execute", "result": {
                    "cols": cols, "rows": [[_out(v) for v in r] for r in rows],
                    "affected_row_count": max(cur.rowcount, 0),
                    "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid is not None else None}}})
            except sqlite3.Error as exc:
                code = "SQLITE_CONSTRAINT" if isinstance(exc, sqlite3.IntegrityError) else "SQLITE_ERROR"
                results.append({"type": "error", "error": {"message": str(exc), "code": code}})
        new_baton = None
        if not closed:
            new_baton = uuid.uuid4().hex
            with fake.lock:
                fake.streams[new_baton] = conn
        self._send(200, json.dumps({"baton": new_baton, "base_url": None, "results": results}).encode())

    def _send(self, status, data: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeTurso(_Server):
    handler = _TursoHandler

    def __init__(self, token: str = "turso-token"):
        self.token = token
        self.folder = tempfile.mkdtemp()
        self.streams: dict = {}
        self.lock = threading.Lock()
        self.requests = 0

    def path(self, name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "default"
        return os.path.join(self.folder, safe + ".db")


# ---------------------------------------------------------------- Resend (POST /emails)
class _ResendHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        fake = self.server.fake
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.headers.get("Authorization") != f"Bearer {fake.key}":
            return self._send(401, {"statusCode": 401, "name": "validation_error", "message": "API key is invalid"})
        if self.path != "/emails":
            return self._send(404, {"statusCode": 404, "name": "not_found", "message": "Not found"})
        domain = body.get("from", "").rsplit("@", 1)[-1].rstrip(">").strip()
        if domain not in fake.verified:
            return self._send(403, {"statusCode": 403, "name": "validation_error",
                                    "message": f"The {domain} domain is not verified. Please, add and verify your domain."})
        fake.sent.append(body)
        self._send(200, {"id": str(uuid.uuid4())})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeResend(_Server):
    handler = _ResendHandler

    def __init__(self, key: str = "re_test", verified=("pdyntech.com",)):
        self.key, self.verified, self.sent = key, set(verified), []
