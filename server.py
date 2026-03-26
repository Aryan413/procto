"""
server.py  —  ExamShield Network Bridge  (FIXED)
=================================================
Run this on the STUDENT PC alongside main.py.

    pip install flask flask-cors pyngrok
    python server.py

FIXES APPLIED:
  1. DB migration — adds marks/category columns if missing
     (was crashing with 'table questions has no column named marks')
  2. Multi-student support — each student login gets a unique token
  3. Per-student URLs — proctor token sees ONLY that student's session
  4. /frame uses 75 JPEG quality for better rendering
  5. /questions safe even with legacy DB schema (no crash on old DB)
  6. /results filtered by student for per-student tokens
  7. /sessions admin endpoint lists all active students

Endpoints (require ?key=<token> or X-ExamShield-Key header):
  GET  /ping
  GET  /frame
  GET  /stats
  GET  /violations
  GET  /questions
  POST /questions
  PUT  /questions/<id>
  DELETE /questions/<id>
  GET  /results
  GET  /results/<filename>
  POST /terminate
  GET  /sessions              (admin key only)
"""

from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS
import sqlite3, time, os, io, threading, secrets
import cv2
import numpy as np

# ── shared state ──────────────────────────────────────────────────────────
_sessions      = {}          # token -> CameraHub
_sessions_lock = threading.Lock()
_hub_ref       = None        # legacy single-hub ref
_hub_lock      = threading.Lock()

DB         = "students.db"
SERVER_KEY = "examshield2024"
PORT       = 5050
_ngrok_url = None

app = Flask(__name__)
CORS(app)

# ✅ FIXED: Cloudflare Tunnel bypass
# Without this, Cloudflare intercepts every request and returns
# an HTML challenge page instead of your camera frame / JSON data.
# This tells Cloudflare to skip its browser-check and pass requests straight through.
@app.after_request
def add_cloudflare_bypass(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "X-ExamShield-Key, Content-Type"
    return response


# ── session management ─────────────────────────────────────────────────────

def set_hub(hub):
    """Called by main.py when a student exam starts. Returns a unique token."""
    global _hub_ref
    with _hub_lock:
        _hub_ref = hub
    token = secrets.token_hex(16)
    with _sessions_lock:
        _sessions[token] = hub
    return token


def _get_hub(token):
    """Resolve hub from token. Admin key gets latest hub."""
    if token == SERVER_KEY:
        with _hub_lock:
            return _hub_ref
    with _sessions_lock:
        return _sessions.get(token)


def _cleanup():
    with _sessions_lock:
        dead = [t for t, h in _sessions.items() if not getattr(h, 'running', False)]
        for t in dead:
            del _sessions[t]


def _auth(admin_only=False):
    token = (request.args.get("key") or
             request.headers.get("X-ExamShield-Key") or "")
    if token == SERVER_KEY:
        return True, token
    if admin_only:
        return False, token
    with _sessions_lock:
        ok = token in _sessions
    return ok, token


# ── DB migration ───────────────────────────────────────────────────────────

def _ensure_schema():
    """FIX 1: Add missing columns to existing DB without destroying data."""
    conn = sqlite3.connect(DB)
    for col, definition in [
        ("marks",    "INTEGER DEFAULT 1"),
        ("category", "TEXT DEFAULT 'General'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {col} {definition}")
            conn.commit()
            print(f"[Server] DB migrated: added '{col}' column to questions")
        except Exception:
            pass  # already exists
    conn.close()


# ── endpoints ─────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    ok, token = _auth()
    if not ok: abort(403)
    h = _get_hub(token)
    resp = {
        "status":    "ok",
        "time":      time.strftime("%H:%M:%S"),
        "student_id": h.student_id if h else None,
        "exam_live":  h is not None and getattr(h, 'running', False),
    }
    if token == SERVER_KEY:
        _cleanup()
        with _sessions_lock:
            resp["all_students"] = [hub.student_id for hub in _sessions.values()
                                    if getattr(hub, 'running', False)]
    return jsonify(resp)


@app.route("/frame")
def frame():
    ok, token = _auth()
    if not ok: abort(403)

    TARGET_W, TARGET_H = 640, 360

    def blank():
        _, buf = cv2.imencode(".jpg", np.zeros((TARGET_H, TARGET_W, 3), dtype="uint8"),
                              [cv2.IMWRITE_JPEG_QUALITY, 50])
        return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")

    h = _get_hub(token)
    if h is None:
        return blank()
    f = h.get_frame()
    if f is None:
        return blank()

    # Resize to fixed 640×360 on server side — saves bandwidth, gives proctor consistent size
    fh, fw = f.shape[:2]
    scale = min(TARGET_W / fw, TARGET_H / fh)
    nw, nh = int(fw * scale), int(fh * scale)
    if (nw, nh) != (fw, fh):
        f = cv2.resize(f, (nw, nh), interpolation=cv2.INTER_LINEAR)

    _, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return send_file(io.BytesIO(buf.tobytes()), mimetype="image/jpeg")


@app.route("/stats")
def stats():
    ok, token = _auth()
    if not ok: abort(403)
    h = _get_hub(token)
    if h is None or not getattr(h, 'running', False):
        return jsonify({"live": False})
    return jsonify({
        "live":           True,
        "student_id":     h.student_id,
        "face_count":     h.face_count,
        "gaze_dir":       h.gaze_dir,
        "strike_count":   h.strike_count,
        "max_strikes":    h.MAX_STRIKES,
        "phone_detected": h.phone_detected,
    })


@app.route("/violations")
def violations():
    ok, token = _auth()
    if not ok: abort(403)
    h = _get_hub(token)
    return jsonify({"violations": list(h.violations) if h else []})


# ── questions ─────────────────────────────────────────────────────────────

def _rows_to_dicts(rows):
    """FIX 5: Safe column access regardless of schema version."""
    result = []
    for r in rows:
        result.append({
            "id":       r[0],
            "question": r[1],
            "opt_a":    r[2],
            "opt_b":    r[3],
            "opt_c":    r[4],
            "opt_d":    r[5],
            "answer":   r[6],
            "marks":    r[7] if len(r) > 7 and r[7] is not None else 1,
            "category": r[8] if len(r) > 8 and r[8] is not None else "General",
        })
    return result


@app.route("/questions", methods=["GET"])
def get_questions():
    ok, token = _auth()
    if not ok: abort(403)
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()
    return jsonify({"questions": _rows_to_dicts(rows)})


@app.route("/questions", methods=["POST"])
def add_question():
    ok, token = _auth()
    if not ok: abort(403)
    d = request.json
    if not d:
        return jsonify({"ok": False, "error": "Empty request body"}), 400
    try:
        conn = sqlite3.connect(DB)
        conn.execute(
            "INSERT INTO questions(question,opt_a,opt_b,opt_c,opt_d,answer,marks,category)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (d["question"], d["opt_a"], d["opt_b"], d["opt_c"], d["opt_d"],
             d["answer"], int(d.get("marks", 1)), d.get("category", "General")))
        conn.commit()
        rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
        conn.close()
        return jsonify({"ok": True, "questions": _rows_to_dicts(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/questions/<int:qid>", methods=["PUT"])
def update_question(qid):
    ok, token = _auth()
    if not ok: abort(403)
    d = request.json
    if not d:
        return jsonify({"ok": False, "error": "Empty request body"}), 400
    try:
        conn = sqlite3.connect(DB)
        conn.execute(
            "UPDATE questions SET question=?,opt_a=?,opt_b=?,opt_c=?,opt_d=?,"
            "answer=?,marks=?,category=? WHERE id=?",
            (d["question"], d["opt_a"], d["opt_b"], d["opt_c"], d["opt_d"],
             d["answer"], int(d.get("marks", 1)), d.get("category", "General"), qid))
        conn.commit()
        rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
        conn.close()
        return jsonify({"ok": True, "questions": _rows_to_dicts(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/questions/<int:qid>", methods=["DELETE"])
def delete_question(qid):
    ok, token = _auth()
    if not ok: abort(403)
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    conn.commit()
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()
    return jsonify({"ok": True, "questions": _rows_to_dicts(rows)})


# ── results ───────────────────────────────────────────────────────────────

@app.route("/results")
def list_results():
    ok, token = _auth()
    if not ok: abort(403)
    all_files = sorted([f for f in os.listdir(".")
                        if f.endswith("_result.csv") or f.endswith("_exam_log.csv")])
    # FIX 6: non-admin tokens only see their own student's files
    if token != SERVER_KEY:
        h = _get_hub(token)
        if h:
            sid = h.student_id
            all_files = [f for f in all_files if f.startswith(sid)]
    return jsonify({"files": all_files})


@app.route("/results/<path:filename>")
def get_result(filename):
    ok, token = _auth()
    if not ok: abort(403)
    safe = os.path.basename(filename)
    if not (safe.endswith("_result.csv") or safe.endswith("_exam_log.csv")):
        abort(400)
    if not os.path.exists(safe):
        abort(404)
    return send_file(safe, mimetype="text/csv", as_attachment=True)


# ── control ───────────────────────────────────────────────────────────────

@app.route("/terminate", methods=["POST"])
def terminate():
    ok, token = _auth()
    if not ok: abort(403)
    h = _get_hub(token)
    if h and getattr(h, 'running', False):
        h.strike_count = h.MAX_STRIKES
        return jsonify({"ok": True, "message": f"Exam terminated for {h.student_id}"})
    return jsonify({"ok": False, "message": "No active session"}), 404


@app.route("/sessions")
def list_sessions():
    """FIX 7: Admin-only — list all active students and their tokens."""
    ok, token = _auth(admin_only=True)
    if not ok: abort(403)
    _cleanup()
    base = _ngrok_url or f"http://localhost:{PORT}"
    with _sessions_lock:
        data = [
            {
                "student_id": h.student_id,
                "token":      t,
                "strikes":    h.strike_count,
                "proctor_url": base,
                "connect_key": t,
            }
            for t, h in _sessions.items() if getattr(h, 'running', False)
        ]
    return jsonify({"sessions": data, "base_url": base})


# ── startup ───────────────────────────────────────────────────────────────

def start_server():
    """Start Flask server (called from main.py)."""
    _ensure_schema()
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=PORT,
                               debug=False, use_reloader=False),
        daemon=True)
    t.start()
    print(f"[Server] ExamShield API running on port {PORT}")
    _try_ngrok()


def _try_ngrok():
    global _ngrok_url
    try:
        from pyngrok import ngrok
        _ngrok_url = ngrok.connect(PORT, "http").public_url
        print(f"\n{'='*60}")
        print(f"  NGROK URL  : {_ngrok_url}")
        print(f"  ADMIN KEY  : {SERVER_KEY}")
        print(f"  Admin connects with key '{SERVER_KEY}' to see ALL students")
        print(f"  Per-student tokens printed when each exam starts")
        print(f"{'='*60}\n")
    except Exception:
        import socket
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "YOUR_IP"
        _ngrok_url = f"http://{ip}:{PORT}"
        print(f"\n{'='*60}")
        print(f"  No pyngrok — LAN only")
        print(f"  LOCAL URL  : {_ngrok_url}")
        print(f"  ADMIN KEY  : {SERVER_KEY}")
        print(f"  Install:    pip install pyngrok")
        print(f"{'='*60}\n")


def print_student_url(student_id, token):
    """Called from main.py after exam starts to print per-student proctor link."""
    base = _ngrok_url or f"http://localhost:{PORT}"
    print(f"\n{'─'*60}")
    print(f"  NEW STUDENT  : {student_id}")
    print(f"  PROCTOR URL  : {base}")
    print(f"  STUDENT TOKEN: {token}")
    print(f"  (Proctor uses this token — sees ONLY {student_id})")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    print("[Server] Running standalone")
    start_server()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Server] Stopped.")