#!/usr/bin/env python3
"""
HealthGuard Backend Server  –  Merged v7+v8
============================================
Python 3 · stdlib only (no pip required)
  - SQLite database (healthguard.db)
  - PBKDF2-HMAC-SHA256 password hashing
  - JWT-style token auth (HMAC-SHA256, 7-day TTL)
  - REST API for all app data
  - Server-Sent Events (SSE) for real-time notifications
  - Long-poll fallback
  - CORS headers for cross-origin / cross-network access
  - Serves static front-end from ../frontend/
  - Pairing-code system for patient↔caregiver linking

  Deploy: python server.py
  Access: http://localhost:8000  or  http://<your-ip>:8000
"""

import http.server
import json
import sqlite3
import hashlib
import hmac
import base64
import secrets
import os
import time
import threading
import mimetypes
import queue
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
PORT        = 8000
DB_PATH     = os.path.join(os.path.dirname(__file__), 'healthguard.db')
SECRET_FILE = os.path.join(os.path.dirname(__file__), '.secret')
STATIC_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))

# Load or generate secret key
if os.path.exists(SECRET_FILE):
    with open(SECRET_FILE) as f:
        JWT_SECRET = f.read().strip()
else:
    JWT_SECRET = secrets.token_hex(32)
    with open(SECRET_FILE, 'w') as f:
        f.write(JWT_SECRET)

TOKEN_TTL     = 7 * 24 * 3600   # 7 days
SSE_TIMEOUT   = 25               # seconds before SSE heartbeat forces reconnect

# ─────────────────────────────────────────────
# SSE BROKER  (in-memory pub/sub per user)
# ─────────────────────────────────────────────
class SSEBroker:
    def __init__(self):
        self._lock    = threading.Lock()
        self._queues  = {}   # uid -> list[queue.Queue]

    def subscribe(self, uid):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._queues.setdefault(uid, []).append(q)
        return q

    def unsubscribe(self, uid, q):
        with self._lock:
            lst = self._queues.get(uid, [])
            if q in lst:
                lst.remove(q)

    def publish(self, uid, event_type, data):
        payload = json.dumps(data)
        msg = f"event: {event_type}\ndata: {payload}\n\n"
        with self._lock:
            for q in list(self._queues.get(uid, [])):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

broker = SSEBroker()

# ─────────────────────────────────────────────
# DATABASE INIT
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            email       TEXT UNIQUE NOT NULL,
            pw_hash     TEXT NOT NULL,
            role        TEXT NOT NULL CHECK(role IN ('patient','caregiver')),
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patients (
            id           TEXT PRIMARY KEY,
            user_id      TEXT REFERENCES users(id),
            caregiver_id TEXT REFERENCES users(id),
            name         TEXT NOT NULL,
            age          TEXT DEFAULT '',
            condition    TEXT DEFAULT '',
            contact      TEXT DEFAULT '',
            notes        TEXT DEFAULT '',
            added_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS medicines (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pid        TEXT NOT NULL,
            name       TEXT NOT NULL,
            dosage     TEXT NOT NULL,
            time       TEXT NOT NULL,
            notes      TEXT DEFAULT '',
            by_role    TEXT DEFAULT 'patient',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS appointments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pid        TEXT NOT NULL,
            doctor     TEXT NOT NULL,
            date       TEXT NOT NULL,
            time       TEXT NOT NULL,
            notes      TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pid         TEXT NOT NULL,
            label       TEXT NOT NULL,
            file_name   TEXT NOT NULL,
            file_data   TEXT DEFAULT '',
            mime_type   TEXT DEFAULT 'application/octet-stream',
            size        INTEGER DEFAULT 0,
            uploaded_at TEXT NOT NULL
        );


        CREATE TABLE IF NOT EXISTS sos_locations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            pid        TEXT NOT NULL,
            lat        REAL NOT NULL,
            lng        REAL NOT NULL,
            accuracy   REAL DEFAULT 0,
            ts         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS water_settings (
            user_id  TEXT PRIMARY KEY REFERENCES users(id),
            minutes  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            pid     TEXT NOT NULL,
            type    TEXT NOT NULL,
            msg     TEXT NOT NULL,
            ts      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id      TEXT PRIMARY KEY,
            to_user TEXT NOT NULL REFERENCES users(id),
            type    TEXT NOT NULL,
            title   TEXT NOT NULL,
            body    TEXT NOT NULL,
            ts      TEXT NOT NULL,
            read    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS pairing_codes (
            code        TEXT PRIMARY KEY,
            patient_id  TEXT NOT NULL REFERENCES patients(id),
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            used        INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(to_user, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_hist_pid   ON history(pid, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_med_pid    ON medicines(pid);
        CREATE INDEX IF NOT EXISTS idx_appt_pid   ON appointments(pid);
        CREATE INDEX IF NOT EXISTS idx_rep_pid    ON reports(pid);
        CREATE INDEX IF NOT EXISTS idx_pair_code  ON pairing_codes(code);
        """)
        # Migrate older DBs: add columns if missing (ignore errors if already exist)
        for col_sql in [
            "ALTER TABLE reports ADD COLUMN file_data TEXT DEFAULT ''",
            "ALTER TABLE reports ADD COLUMN mime_type TEXT DEFAULT 'application/octet-stream'",
        ]:
            try:
                db.execute(col_sql)
            except Exception:
                pass  # Column already exists
    print(f"[DB] Database ready: {DB_PATH}")
    print(f"[Static] Serving from: {STATIC_DIR}")

# ─────────────────────────────────────────────
# SECURITY HELPERS
# ─────────────────────────────────────────────
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk   = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000)
    return salt + '$' + dk.hex()

def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, dk_hex = stored.split('$', 1)
        dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False

def make_token(user_id: str, role: str) -> str:
    payload = json.dumps({'uid': user_id, 'role': role, 'exp': int(time.time()) + TOKEN_TTL})
    b64     = base64.urlsafe_b64encode(payload.encode()).decode()
    sig     = hmac.new(JWT_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return b64 + '.' + sig

def verify_token(token: str):
    try:
        b64, sig = token.rsplit('.', 1)
        expected = hmac.new(JWT_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(b64 + '==').decode())
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def new_id(prefix=''):
    return prefix + secrets.token_urlsafe(8)

# ─────────────────────────────────────────────
# NOTIFICATION HELPER  (DB + SSE push)
# ─────────────────────────────────────────────
def push_notification(to_user: str, n_type: str, title: str, body: str):
    nid  = new_id('n')
    ts   = now_iso()
    data = {'id': nid, 'to': to_user, 'type': n_type, 'title': title, 'body': body, 'ts': ts, 'read': 0}
    with get_db() as db:
        db.execute(
            'INSERT INTO notifications(id,to_user,type,title,body,ts,read) VALUES(?,?,?,?,?,?,0)',
            (nid, to_user, n_type, title, body, ts)
        )
        db.execute("""DELETE FROM notifications WHERE to_user=? AND id NOT IN
            (SELECT id FROM notifications WHERE to_user=? ORDER BY ts DESC LIMIT 500)""",
            (to_user, to_user))
    # Push real-time via SSE
    broker.publish(to_user, 'notification', data)

# ─────────────────────────────────────────────
# HTTP REQUEST HANDLER
# ─────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization')

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n)) if n else {}

    def _auth(self, qs=None):
        # 1. Authorization header (standard API calls)
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            return verify_token(auth[7:])
        # 2. URL query param ?token= or ?_t=<token>  (used by EventSource / SSE)
        if qs:
            t = qs.get('token', qs.get('_t', [None]))[0]
            if t:
                return verify_token(t)
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip('/')
        qs     = parse_qs(parsed.query)

        if path == '/api/events':
            return self._sse_stream(qs)
        if path.startswith('/api'):
            return self._api_get(path, qs)
        return self._static(path)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        if path.startswith('/api'):
            return self._api_post(path)
        self._json(404, {'error': 'Not found'})

    def do_DELETE(self):
        path = urlparse(self.path).path.rstrip('/')
        if path.startswith('/api'):
            return self._api_delete(path)
        self._json(404, {'error': 'Not found'})

    # ──────────────────────────────────────────
    # SSE ENDPOINT  GET /api/events
    # ──────────────────────────────────────────
    def _sse_stream(self, qs):
        claim = self._auth(qs)
        if not claim:
            self._json(401, {'error': 'Unauthorized'})
            return

        uid = claim['uid']
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self._cors()
        self.end_headers()

        q = broker.subscribe(uid)
        try:
            # Send an initial connected event
            self.wfile.write(b'event: connected\ndata: {}\n\n')
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=SSE_TIMEOUT)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat to keep connection alive
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            broker.unsubscribe(uid, q)

    # ──────────────────────────────────────────
    # STATIC FILE SERVING
    # ──────────────────────────────────────────
    def _static(self, path):
        if path in ('', '/'):
            path = '/index.html'
        filepath = os.path.normpath(STATIC_DIR + path)
        if not filepath.startswith(STATIC_DIR):
            self._json(403, {'error': 'Forbidden'})
            return
        if not os.path.isfile(filepath):
            # SPA fallback
            filepath = os.path.join(STATIC_DIR, 'index.html')
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or 'application/octet-stream'
        try:
            with open(filepath, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._json(500, {'error': str(e)})

    # ──────────────────────────────────────────
    # GET ROUTES
    # ──────────────────────────────────────────
    def _api_get(self, path, qs):
        claim = self._auth(qs)  # pass qs so ?token= param is accepted

        # /api/me
        if path == '/api/me':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            with get_db() as db:
                u = db.execute('SELECT id,name,email,role FROM users WHERE id=?', (claim['uid'],)).fetchone()
                if not u: return self._json(404, {'error': 'User not found'})
                result = dict(u)
                if u['role'] == 'patient':
                    pt = db.execute('SELECT id,caregiver_id FROM patients WHERE user_id=?', (claim['uid'],)).fetchone()
                    if pt:
                        result['patient_id'] = pt['id']
                        result['caregiver_id'] = pt['caregiver_id']
            return self._json(200, result)

        # /api/patients
        if path == '/api/patients':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            with get_db() as db:
                if claim['role'] == 'caregiver':
                    rows = db.execute('SELECT * FROM patients WHERE caregiver_id=?', (claim['uid'],)).fetchall()
                else:
                    rows = db.execute('SELECT * FROM patients WHERE user_id=?', (claim['uid'],)).fetchall()
            return self._json(200, [dict(r) for r in rows])

        # /api/patients/<pid>/<resource>  -- robust segment matching
        _gpts = [p for p in path.split('/') if p]
        if len(_gpts) >= 4 and _gpts[0]=='api' and _gpts[1]=='patients':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            _gpid = _gpts[2]
            _gres = _gpts[3] if len(_gpts)>3 else ''

            if _gres == 'medicines':
                with get_db() as db:
                    rows = db.execute('SELECT * FROM medicines WHERE pid=? ORDER BY time', (_gpid,)).fetchall()
                return self._json(200, [dict(r) for r in rows])

            if _gres == 'appointments':
                with get_db() as db:
                    rows = db.execute('SELECT * FROM appointments WHERE pid=? ORDER BY date,time', (_gpid,)).fetchall()
                return self._json(200, [dict(r) for r in rows])

            if _gres == 'reports' and len(_gpts)==4:
                with get_db() as db:
                    rows = db.execute('SELECT id,pid,label,file_name,mime_type,size,uploaded_at FROM reports WHERE pid=? ORDER BY uploaded_at DESC', (_gpid,)).fetchall()
                return self._json(200, [dict(r) for r in rows])

            if _gres == 'reports' and len(_gpts)==5:
                # GET /api/patients/<pid>/reports/<rid>/download
                rid = _gpts[4]
                with get_db() as db:
                    row = db.execute('SELECT file_data,mime_type,file_name FROM reports WHERE id=?', (rid,)).fetchone()
                if not row or not row['file_data']:
                    return self._json(404, {'error': 'File not found'})
                import base64 as _b64
                try:
                    raw = _b64.b64decode(row['file_data'].split(',')[-1])
                except Exception:
                    return self._json(500, {'error': 'Corrupt file data'})
                self.send_response(200)
                self.send_header('Content-Type', row['mime_type'] or 'application/octet-stream')
                self.send_header('Content-Disposition', f'inline; filename="{row["file_name"]}"')
                self.send_header('Content-Length', str(len(raw)))
                self._cors()
                self.end_headers()
                self.wfile.write(raw)
                return

            if _gres == 'sos':
                with get_db() as db:
                    row = db.execute('SELECT * FROM sos_locations WHERE pid=? ORDER BY ts DESC LIMIT 1', (_gpid,)).fetchone()
                return self._json(200, dict(row) if row else {})

        # /api/patients/<pid>/history
        _hpts = [p for p in path.split('/') if p]
        if len(_hpts)>=4 and _hpts[0]=='api' and _hpts[1]=='patients' and _hpts[3]=='history':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            pid = _hpts[2]
            with get_db() as db:
                rows = db.execute('SELECT * FROM history WHERE pid=? ORDER BY ts DESC LIMIT 200', (pid,)).fetchall()
            return self._json(200, [dict(r) for r in rows])

        # /api/water
        if path == '/api/water':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            with get_db() as db:
                row = db.execute('SELECT minutes FROM water_settings WHERE user_id=?', (claim['uid'],)).fetchone()
            return self._json(200, {'minutes': row['minutes'] if row else None})

        # /api/notifications
        if path == '/api/notifications':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            since = qs.get('since', [None])[0]
            with get_db() as db:
                if since:
                    rows = db.execute(
                        'SELECT * FROM notifications WHERE to_user=? AND ts>? ORDER BY ts DESC LIMIT 100',
                        (claim['uid'], since)
                    ).fetchall()
                else:
                    rows = db.execute(
                        'SELECT * FROM notifications WHERE to_user=? ORDER BY ts DESC LIMIT 100',
                        (claim['uid'],)
                    ).fetchall()
            return self._json(200, [dict(r) for r in rows])

        # /api/users/by-email?email=
        if path == '/api/users/by-email':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            email = qs.get('email', [''])[0].strip().lower()
            with get_db() as db:
                u = db.execute('SELECT id,name,email,role FROM users WHERE email=?', (email,)).fetchone()
            return self._json(200, dict(u)) if u else self._json(404, {'error': 'User not found'})

        # /api/users/<id>
        if path.startswith('/api/users/'):
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            uid = path.split('/')[-1]
            with get_db() as db:
                u = db.execute('SELECT id,name,email,role FROM users WHERE id=?', (uid,)).fetchone()
            return self._json(200, dict(u)) if u else self._json(404, {'error': 'User not found'})

        # /api/pairing/my-code  (patient generates their pairing code)
        if path == '/api/pairing/my-code':
            if not claim: return self._json(401, {'error': 'Unauthorized'})
            with get_db() as db:
                pt = db.execute('SELECT * FROM patients WHERE user_id=?', (claim['uid'],)).fetchone()
                if not pt: return self._json(404, {'error': 'Patient record not found'})
                # Invalidate old codes
                db.execute('UPDATE pairing_codes SET used=1 WHERE patient_id=? AND used=0', (pt['id'],))
                code = secrets.token_hex(3).upper()  # 6-char hex code e.g. A3F9B2
                expires = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59).isoformat()
                db.execute(
                    'INSERT INTO pairing_codes(code,patient_id,created_at,expires_at,used) VALUES(?,?,?,?,0)',
                    (code, pt['id'], now_iso(), expires)
                )
            return self._json(200, {'code': code, 'expires_at': expires})

        self._json(404, {'error': 'Route not found'})

    # ──────────────────────────────────────────
    # POST ROUTES
    # ──────────────────────────────────────────
    def _api_post(self, path):
        print(f"[POST] path={path!r}", flush=True)
        claim = self._auth()

        # /api/register
        if path == '/api/register':
            body  = self._read_body()
            name  = (body.get('name') or '').strip()
            email = (body.get('email') or '').strip().lower()
            pw    = body.get('password') or ''
            role  = body.get('role') or ''
            if not name:  return self._json(400, {'error': 'Name is required.'})
            if not email: return self._json(400, {'error': 'Email is required.'})
            if len(pw)<6: return self._json(400, {'error': 'Password must be at least 6 characters.'})
            if role not in ('patient','caregiver'):
                return self._json(400, {'error': 'Role must be patient or caregiver.'})
            uid = new_id('u')
            try:
                with get_db() as db:
                    db.execute(
                        'INSERT INTO users(id,name,email,pw_hash,role,created_at) VALUES(?,?,?,?,?,?)',
                        (uid, name, email, hash_password(pw), role, now_iso())
                    )
                    if role == 'patient':
                        db.execute(
                            'INSERT INTO patients(id,user_id,caregiver_id,name,added_at) VALUES(?,?,NULL,?,?)',
                            ('pr'+uid, uid, name, now_iso())
                        )
            except sqlite3.IntegrityError:
                return self._json(409, {'error': 'An account with this email already exists.'})
            return self._json(201, {'ok': True, 'message': 'Account created. Please sign in.'})

        # /api/login
        if path == '/api/login':
            body  = self._read_body()
            email = (body.get('email') or '').strip().lower()
            pw    = body.get('password') or ''
            with get_db() as db:
                u = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            if not u or not verify_password(pw, u['pw_hash']):
                return self._json(401, {'error': 'Invalid email or password.'})
            token = make_token(u['id'], u['role'])
            return self._json(200, {
                'token': token,
                'user': {'id': u['id'], 'name': u['name'], 'email': u['email'], 'role': u['role']}
            })

        # All routes below require auth
        if not claim:
            return self._json(401, {'error': 'Unauthorized'})

        body = self._read_body()

        # /api/patients/link  (link by email)
        if path == '/api/patients/link':
            email = (body.get('email') or '').strip().lower()
            with get_db() as db:
                pu = db.execute('SELECT id,name FROM users WHERE email=? AND role=?', (email,'patient')).fetchone()
                if not pu:
                    return self._json(404, {'error': 'No patient account found with that email.'})
                rec = db.execute('SELECT * FROM patients WHERE user_id=?', (pu['id'],)).fetchone()
                if not rec:
                    pid = 'pr' + pu['id']
                    db.execute(
                        'INSERT INTO patients(id,user_id,caregiver_id,name,added_at) VALUES(?,?,?,?,?)',
                        (pid, pu['id'], claim['uid'], pu['name'], now_iso())
                    )
                    rec = db.execute('SELECT * FROM patients WHERE id=?', (pid,)).fetchone()
                else:
                    if rec['caregiver_id'] and rec['caregiver_id'] != claim['uid']:
                        return self._json(409, {'error': 'That patient is already linked to another caregiver.'})
                    db.execute('UPDATE patients SET caregiver_id=? WHERE id=?', (claim['uid'], rec['id']))
                    rec = db.execute('SELECT * FROM patients WHERE id=?', (rec['id'],)).fetchone()
            # Notify patient
            with get_db() as db:
                cg = db.execute('SELECT name FROM users WHERE id=?', (claim['uid'],)).fetchone()
            push_notification(pu['id'], 'caregiver_linked',
                f"🔗 Caregiver linked",
                f"{cg['name'] if cg else 'A caregiver'} has linked to your account.")
            return self._json(200, {'ok': True, 'patient': dict(rec)})

        # /api/patients/link-code  (link by pairing code)
        if path == '/api/patients/link-code':
            code = (body.get('code') or '').strip().upper()
            with get_db() as db:
                pc = db.execute(
                    'SELECT * FROM pairing_codes WHERE code=? AND used=0 AND expires_at>?',
                    (code, now_iso())
                ).fetchone()
                if not pc:
                    return self._json(404, {'error': 'Invalid or expired pairing code.'})
                rec = db.execute('SELECT * FROM patients WHERE id=?', (pc['patient_id'],)).fetchone()
                if not rec:
                    return self._json(404, {'error': 'Patient record not found.'})
                if rec['caregiver_id'] and rec['caregiver_id'] != claim['uid']:
                    return self._json(409, {'error': 'That patient is already linked to another caregiver.'})
                db.execute('UPDATE patients SET caregiver_id=? WHERE id=?', (claim['uid'], rec['id']))
                db.execute('UPDATE pairing_codes SET used=1 WHERE code=?', (code,))
                rec = db.execute('SELECT * FROM patients WHERE id=?', (rec['id'],)).fetchone()
            # Notify patient
            if rec['user_id']:
                with get_db() as db:
                    cg = db.execute('SELECT name FROM users WHERE id=?', (claim['uid'],)).fetchone()
                push_notification(rec['user_id'], 'caregiver_linked',
                    f"🔗 Caregiver linked",
                    f"{cg['name'] if cg else 'A caregiver'} has linked to your account via pairing code.")
            return self._json(200, {'ok': True, 'patient': dict(rec)})

        # /api/patients  (manual add)
        if path == '/api/patients':
            pid = new_id('pm')
            with get_db() as db:
                db.execute(
                    'INSERT INTO patients(id,user_id,caregiver_id,name,age,condition,contact,notes,added_at) VALUES(?,NULL,?,?,?,?,?,?,?)',
                    (pid, claim['uid'],
                     (body.get('name') or '').strip(),
                     str(body.get('age') or ''),
                     body.get('condition') or '',
                     body.get('contact') or '',
                     body.get('notes') or '',
                     now_iso())
                )
                rec = db.execute('SELECT * FROM patients WHERE id=?', (pid,)).fetchone()
            return self._json(201, dict(rec))

        # /api/patients/<pid>/medicines
        _pts = [p for p in path.split('/') if p]  # remove empty
        # _pts e.g. ['api','patients','<pid>','medicines']
        if len(_pts)==4 and _pts[0]=='api' and _pts[1]=='patients' and _pts[3]=='medicines':
            pid = _pts[2]
            with get_db() as db:
                db.execute(
                    'INSERT INTO medicines(pid,name,dosage,time,notes,by_role,created_at) VALUES(?,?,?,?,?,?,?)',
                    (pid, body.get('name',''), body.get('dosage',''), body.get('time',''),
                     body.get('notes',''), body.get('by','patient'), now_iso())
                )
                row = db.execute('SELECT * FROM medicines WHERE rowid=last_insert_rowid()').fetchone()
                self._add_history(db, pid, 'medicine',
                    f'Medicine "{row["name"]}" ({row["dosage"]}) at {row["time"]}' +
                    (' — by caregiver' if row['by_role']=='caregiver' else ''))
            return self._json(201, dict(row))

        # /api/patients/<pid>/appointments
        if len(_pts)==4 and _pts[0]=='api' and _pts[1]=='patients' and _pts[3]=='appointments':
            pid = _pts[2]
            with get_db() as db:
                db.execute(
                    'INSERT INTO appointments(pid,doctor,date,time,notes,created_at) VALUES(?,?,?,?,?,?)',
                    (pid, body.get('doctor',''), body.get('date',''),
                     body.get('time',''), body.get('notes',''), now_iso())
                )
                row = db.execute('SELECT * FROM appointments WHERE rowid=last_insert_rowid()').fetchone()
                self._add_history(db, pid, 'appointment',
                    f'Appointment with {row["doctor"]} on {row["date"]} at {row["time"]}')
            return self._json(201, dict(row))

        # /api/patients/<pid>/reports
        if len(_pts)==4 and _pts[0]=='api' and _pts[1]=='patients' and _pts[3]=='reports':
            pid = _pts[2]
            with get_db() as db:
                db.execute(
                    'INSERT INTO reports(pid,label,file_name,file_data,mime_type,size,uploaded_at) VALUES(?,?,?,?,?,?,?)',
                    (pid, body.get('label',''), body.get('fileName',''),
                     body.get('fileData',''), body.get('mimeType','application/octet-stream'),
                     int(body.get('size',0)), now_iso())
                )
                row = db.execute('SELECT id,pid,label,file_name,mime_type,size,uploaded_at FROM reports WHERE rowid=last_insert_rowid()').fetchone()
                self._add_history(db, pid, 'report', f'Report "{row["label"]}" uploaded')
            return self._json(201, dict(row))

        # /api/patients/<pid>/sos
        if len(_pts)==4 and _pts[0]=='api' and _pts[1]=='patients' and _pts[3]=='sos':
            pid = _pts[2]
            lat = body.get('lat', 0)
            lng = body.get('lng', 0)
            with get_db() as db:
                db.execute(
                    'INSERT INTO sos_locations(pid,lat,lng,accuracy,ts) VALUES(?,?,?,?,?)',
                    (pid, lat, lng, body.get('accuracy',0), now_iso())
                )
                self._add_history(db, pid, 'sos', f'SOS sent — Lat {lat:.5f}, Lng {lng:.5f}')
                # Find caregiver to notify
                pt = db.execute('SELECT * FROM patients WHERE id=?', (pid,)).fetchone()
            if pt and pt['caregiver_id']:
                with get_db() as db:
                    ptu = db.execute('SELECT name FROM users WHERE id=?', (pt['user_id'],)).fetchone() if pt['user_id'] else None
                pname = ptu['name'] if ptu else 'Patient'
                push_notification(pt['caregiver_id'], 'sos_alert',
                    f'🚨 SOS — {pname}',
                    f'{pname} needs help! Lat {lat:.5f}, Lng {lng:.5f}')
            return self._json(201, {'ok': True})

        # /api/patients/<pid>/history/clear
        if len(_pts)==5 and _pts[0]=='api' and _pts[1]=='patients' and _pts[3]=='history' and _pts[4]=='clear':
            pid = _pts[2]
            with get_db() as db:
                db.execute('DELETE FROM history WHERE pid=?', (pid,))
            return self._json(200, {'ok': True})

        # /api/water
        if path == '/api/water':
            mins = body.get('minutes')
            with get_db() as db:
                if mins:
                    db.execute('INSERT OR REPLACE INTO water_settings(user_id,minutes) VALUES(?,?)',
                               (claim['uid'], int(mins)))
                else:
                    db.execute('DELETE FROM water_settings WHERE user_id=?', (claim['uid'],))
            return self._json(200, {'ok': True})

        # /api/notifications  (push a notification to another user)
        if path == '/api/notifications':
            nid = new_id('n')
            to  = body.get('to','')
            push_notification(to, body.get('type',''), body.get('title',''), body.get('body',''))
            return self._json(201, {'ok': True, 'id': nid})

        # /api/notifications/read-all
        if path == '/api/notifications/read-all':
            with get_db() as db:
                db.execute('UPDATE notifications SET read=1 WHERE to_user=?', (claim['uid'],))
            return self._json(200, {'ok': True})

        # /api/notifications/<id>/read
        if '/notifications/' in path and path.endswith('/read'):
            nid = path.split('/')[-2]
            with get_db() as db:
                db.execute('UPDATE notifications SET read=1 WHERE id=?', (nid,))
            return self._json(200, {'ok': True})

        self._json(404, {'error': 'Route not found'})

    # ──────────────────────────────────────────
    # DELETE ROUTES
    # ──────────────────────────────────────────
    def _api_delete(self, path):
        claim = self._auth()
        if not claim:
            return self._json(401, {'error': 'Unauthorized'})

        _dpts = [p for p in path.split('/') if p]
        # ['api','patients','<pid>'] or ['api','patients','<pid>','medicines','<mid>']

        if len(_dpts)==3 and _dpts[0]=='api' and _dpts[1]=='patients':
            pid = _dpts[2]
            with get_db() as db:
                db.execute('DELETE FROM patients WHERE id=?', (pid,))
                for t in ('medicines','appointments','reports','sos_locations','history'):
                    db.execute(f'DELETE FROM {t} WHERE pid=?', (pid,))
            return self._json(200, {'ok': True})

        if len(_dpts)==5 and _dpts[0]=='api' and _dpts[1]=='patients' and _dpts[3]=='medicines':
            mid = _dpts[4]
            with get_db() as db:
                db.execute('DELETE FROM medicines WHERE id=?', (mid,))
            return self._json(200, {'ok': True})

        if len(_dpts)==5 and _dpts[0]=='api' and _dpts[1]=='patients' and _dpts[3]=='appointments':
            aid = _dpts[4]
            with get_db() as db:
                db.execute('DELETE FROM appointments WHERE id=?', (aid,))
            return self._json(200, {'ok': True})

        if len(_dpts)==5 and _dpts[0]=='api' and _dpts[1]=='patients' and _dpts[3]=='reports':
            rid = _dpts[4]
            with get_db() as db:
                db.execute('DELETE FROM reports WHERE id=?', (rid,))
            return self._json(200, {'ok': True})

        self._json(404, {'error': 'Route not found'})

    def _add_history(self, db, pid, type_, msg):
        db.execute('INSERT INTO history(pid,type,msg,ts) VALUES(?,?,?,?)',
                   (pid, type_, msg, now_iso()))

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True

    # Use ThreadingMixIn so SSE connections don't block the server
    class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True

    with ThreadedHTTPServer(('0.0.0.0', PORT), Handler) as srv:
        import socket
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            local_ip = '<your-ip>'

        print(f"\n{'='*56}")
        print(f"  🏥  HealthGuard Server  (merged v7+v8)")
        print(f"{'='*56}")
        print(f"  Local:    http://localhost:{PORT}")
        print(f"  Network:  http://{local_ip}:{PORT}")
        print(f"  Database: {DB_PATH}")
        print(f"  Static:   {STATIC_DIR}")
        print(f"{'='*56}")
        print(f"  Open http://localhost:{PORT} in your browser")
        print(f"  Ctrl+C to stop\n")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n[Server stopped]")
