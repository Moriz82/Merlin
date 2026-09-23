"""Local accounts. No credential is stored in a browser or an audit event."""
import hashlib
import hmac
import secrets
import time
import uuid


ROLES = {"Harbinger": {"captain", "tester"}, "Merlin": {"lead_scribe", "scribe"}}


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + result


def add_user(store, name, role, password, app):
    if role not in ROLES[app] or len(password) < 12 or not name.strip() or len(name) > 80:
        raise ValueError("Use a valid role, name, and password with at least 12 characters.")
    id = str(uuid.uuid4())
    with store.tx() as c:
        c.execute("INSERT INTO users VALUES(?,?,?,?)", (id, name, role, password_hash(password)))
        store.event(c, "operator", "user.created", [id], role=role)
    return id


def login(store, name, password, address):
    now = time.time()
    key = hashlib.sha256((address + "\0" + name).encode()).hexdigest()
    with store.tx() as c:
        limit = c.execute("SELECT * FROM login_limits WHERE key=?", (key,)).fetchone()
        count = limit["count"] if limit and limit["reset"] > now else 0
        if count >= 5:
            return None, "throttled"
        user = c.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
        saved = user["password"] if user else password_hash("unavailable-synthetic-salt", "00" * 16)
        valid = hmac.compare_digest(password_hash(password, saved.split(":")[0]), saved)
        if not user or not valid:
            c.execute("INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET count=excluded.count,reset=excluded.reset", (key, count + 1, now + 300))
            store.event(c, "anonymous", "login.denied", [], outcome="denied")
            return None, "denied"
        c.execute("DELETE FROM login_limits WHERE key=?", (key,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, now + 8 * 3600, now))
        store.event(c, user["id"], "login.accepted", [], outcome="accepted")
        return token, csrf


def session(store, token, *, touch=True):
    if not token:
        return None
    with store.connect() as c:
        row = c.execute("SELECT sessions.*,users.name,users.role FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row["expires"] < time.time() or row["touched"] < time.time() - 1800:
            return None
        if touch:
            c.execute("UPDATE sessions SET touched=? WHERE token=?", (time.time(), row["token"]))
        return dict(row)
