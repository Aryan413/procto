"""
server_webrtc.py  —  ExamShield WebRTC Signaling Bridge
=========================================================
Replaces the MJPEG frame-polling approach with a proper
WebRTC signaling server. The student's browser sends video
directly to the proctor's browser via UDP, server-side YOLO
runs via aiortc on the student machine.

Architecture:
    Student PC  ──(aiortc peer)──►  This Server  ──(SDP relay)──►  Proctor Browser
                                         ▲
                              Flask-SocketIO (signaling only)
                              Cloudflare Tunnel / ngrok

Install:
    pip install flask flask-cors flask-socketio aiortc pyngrok opencv-python

Run:
    python server_webrtc.py

Endpoints:
    WebSocket: /socket.io  (signaling: offer, answer, ice-candidate)
    HTTP GET  /ping        (health check)
    HTTP GET  /stats       (live AI analysis stats)
    HTTP GET  /violations  (violation log)
    HTTP GET  /sessions    (admin: list active students)
    HTTP POST /terminate   (force-end a student exam)
    HTTP GET  /questions   (question bank)
    HTTP POST /questions
    HTTP PUT  /questions/<id>
    HTTP DELETE /questions/<id>
    HTTP GET  /results     (CSV result files)
    HTTP GET  /results/<filename>

Auth:
    All HTTP endpoints: ?key=<token> or X-ExamShield-Key header
    WebSocket rooms:    student joins room=student_id, proctor subscribes

YOLO Integration (optional but recommended):
    If ultralytics is installed, YOLO runs on every video frame
    received from the student via aiortc. Detections are pushed
    via SocketIO to any connected proctor viewing that student.
"""

import os, io, time, threading, secrets, csv, sqlite3
import cv2
import numpy as np
from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room

# ── Optional heavy deps ────────────────────────────────────────────────────
_AIORTC_AVAILABLE = False
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate, VideoStreamTrack
    from aiortc.contrib.media import MediaRelay
    _AIORTC_AVAILABLE = True
except ImportError:
    pass

_YOLO_AVAILABLE = False
_yolo_model = None
try:
    from ultralytics import YOLO as _YOLO
    _yolo_model = _YOLO("yolov8n.pt")
    _YOLO_AVAILABLE = True
    print("[YOLO] Model loaded: yolov8n.pt")
except Exception:
    pass

_NGROK_AVAILABLE = False
try:
    from pyngrok import ngrok as _ngrok
    _NGROK_AVAILABLE = True
except ImportError:
    pass

# ── Config ─────────────────────────────────────────────────────────────────
DB          = "students.db"
SERVER_KEY  = "examshield2024"
PORT        = 5050
_public_url = None

# ── Flask + SocketIO ───────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(32)
CORS(app)
sio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
               logger=False, engineio_logger=False)

# ── Session state ──────────────────────────────────────────────────────────
# Each student gets a unique token; CameraHub stores live stats.
_sessions      = {}   # token -> {"student_id", "hub", "peer_connection"}
_sessions_lock = threading.Lock()
_relay         = MediaRelay() if _AIORTC_AVAILABLE else None

# ── Cloudflare / ngrok bypass header ──────────────────────────────────────
@app.after_request
def _bypass_headers(response):
    response.headers["ngrok-skip-browser-warning"] = "true"
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "X-ExamShield-Key, Content-Type"
    return response

# ══════════════════════════════════════════════════════════════════════════
#  AUTH HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _auth(admin_only=False):
    """Returns (ok: bool, token: str)."""
    token = (request.args.get("key") or
             request.headers.get("X-ExamShield-Key") or "")
    if token == SERVER_KEY:
        return True, token
    if admin_only:
        return False, token
    with _sessions_lock:
        ok = token in _sessions
    return ok, token


def _get_session(token):
    """Resolve session dict from token. Admin key gets first active session."""
    if token == SERVER_KEY:
        with _sessions_lock:
            active = [s for s in _sessions.values() if s.get("hub") and
                      getattr(s["hub"], "running", False)]
            return active[0] if active else None
    with _sessions_lock:
        return _sessions.get(token)


def _cleanup():
    with _sessions_lock:
        dead = [t for t, s in _sessions.items()
                if not getattr(s.get("hub"), "running", False)]
        for t in dead:
            del _sessions[t]

# ══════════════════════════════════════════════════════════════════════════
#  SESSION REGISTRATION  (called from main.py)
# ══════════════════════════════════════════════════════════════════════════

def register_student(hub) -> str:
    """
    Called by main.py when a student exam starts.
    Returns unique per-student token.
    """
    token = secrets.token_hex(16)
    with _sessions_lock:
        _sessions[token] = {
            "student_id": hub.student_id,
            "hub":        hub,
            "peer_connection": None,
        }
    print(f"[WebRTC] Student registered: {hub.student_id} → token={token[:8]}…")
    return token

# ══════════════════════════════════════════════════════════════════════════
#  DB HELPERS
# ══════════════════════════════════════════════════════════════════════════

def _ensure_schema():
    conn = sqlite3.connect(DB)
    for col, defn in [("marks", "INTEGER DEFAULT 1"), ("category", "TEXT DEFAULT 'General'")]:
        try:
            conn.execute(f"ALTER TABLE questions ADD COLUMN {col} {defn}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def _rows_to_dicts(rows):
    return [{
        "id":       r[0], "question": r[1],
        "opt_a":    r[2], "opt_b":    r[3],
        "opt_c":    r[4], "opt_d":    r[5],
        "answer":   r[6],
        "marks":    r[7] if len(r) > 7 and r[7] is not None else 1,
        "category": r[8] if len(r) > 8 and r[8] is not None else "General",
    } for r in rows]

# ══════════════════════════════════════════════════════════════════════════
#  WEBRTC SIGNALING  (SocketIO events)
# ══════════════════════════════════════════════════════════════════════════
#
# Flow:
#   1. Proctor browser connects  → emits  "proctor-join"  {key, student_id}
#   2. Student (aiortc) connects → emits  "student-join"  {token}
#   3. Server tells proctor to start: emits "ready-to-offer"
#   4. Proctor creates RTCPeerConnection, emits "offer" {sdp, type}
#   5. Server forwards offer to student aiortc peer
#   6. Student aiortc answers, server forwards "answer" to proctor
#   7. ICE candidates flow both ways via "ice-candidate"
# ══════════════════════════════════════════════════════════════════════════

@sio.on("proctor-join")
def on_proctor_join(data):
    token = data.get("key", "")
    if token != SERVER_KEY:
        with _sessions_lock:
            if token not in _sessions:
                emit("error", {"message": "Invalid key"})
                return
    student_id = data.get("student_id")
    room = f"proctor:{student_id}"
    join_room(room)
    emit("joined", {"room": room, "student_id": student_id})
    # Tell the proctor to create an RTCPeerConnection and send offer
    emit("ready-to-offer", {"student_id": student_id})
    print(f"[WebRTC] Proctor joined room {room}")


@sio.on("student-join")
def on_student_join(data):
    """Called by aiortc Python peer on student machine."""
    token = data.get("token", "")
    with _sessions_lock:
        session = _sessions.get(token)
    if not session:
        emit("error", {"message": "Invalid student token"})
        return
    sid = session["student_id"]
    join_room(f"student:{sid}")
    join_room(f"both:{sid}")
    emit("joined", {"student_id": sid})
    print(f"[WebRTC] Student peer joined: {sid}")


@sio.on("offer")
def on_offer(data):
    """Proctor → Server: relay SDP offer to student."""
    student_id = data.get("student_id")
    sio.emit("offer", data, room=f"student:{student_id}")
    print(f"[WebRTC] Offer relayed to student:{student_id}")


@sio.on("answer")
def on_answer(data):
    """Student → Server: relay SDP answer to proctor."""
    student_id = data.get("student_id")
    sio.emit("answer", data, room=f"proctor:{student_id}")
    print(f"[WebRTC] Answer relayed to proctor:{student_id}")


@sio.on("ice-candidate")
def on_ice(data):
    """Relay ICE candidates between peers."""
    student_id = data.get("student_id")
    role = data.get("from", "proctor")
    target_room = f"student:{student_id}" if role == "proctor" else f"proctor:{student_id}"
    sio.emit("ice-candidate", data, room=target_room)


@sio.on("violation")
def on_violation(data):
    """Student sends a violation event → push to proctor dashboard."""
    student_id = data.get("student_id")
    sio.emit("violation", data, room=f"proctor:{student_id}")


@sio.on("stats-update")
def on_stats_update(data):
    """Student pushes stats (face count, gaze, strikes) → proctor."""
    student_id = data.get("student_id")
    sio.emit("stats-update", data, room=f"proctor:{student_id}")


@sio.on("yolo-frame")
def on_yolo_frame(data):
    """
    Student sends a JPEG frame (base64) for server-side YOLO detection.
    Results are pushed to the proctor room.
    """
    if not _YOLO_AVAILABLE:
        return
    try:
        import base64
        student_id = data.get("student_id")
        frame_b64  = data.get("frame")
        frame_bytes = base64.b64decode(frame_b64)
        nparr = np.frombuffer(frame_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        results = _yolo_model(frame, verbose=False)[0]
        detections = []
        for box in results.boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            label  = _yolo_model.names[cls_id]
            detections.append({"label": label, "confidence": round(conf, 2)})
        sio.emit("yolo-detections", {
            "student_id":  student_id,
            "detections":  detections,
            "timestamp":   time.time(),
        }, room=f"proctor:{student_id}")
    except Exception as e:
        print(f"[YOLO] Frame processing error: {e}")

# ══════════════════════════════════════════════════════════════════════════
#  HTTP ENDPOINTS  (mirroring original server.py API)
# ══════════════════════════════════════════════════════════════════════════

@app.route("/ping")
def ping():
    ok, token = _auth()
    if not ok: abort(403)
    s = _get_session(token)
    hub = s["hub"] if s else None
    resp = {
        "status":     "ok",
        "time":       time.strftime("%H:%M:%S"),
        "student_id": hub.student_id if hub else None,
        "exam_live":  hub is not None and getattr(hub, "running", False),
        "webrtc":     True,
        "aiortc":     _AIORTC_AVAILABLE,
    }
    if token == SERVER_KEY:
        _cleanup()
        with _sessions_lock:
            resp["all_students"] = [
                sess["student_id"] for sess in _sessions.values()
                if getattr(sess.get("hub"), "running", False)
            ]
    return jsonify(resp)


@app.route("/stats")
def stats():
    ok, token = _auth()
    if not ok: abort(403)
    s = _get_session(token)
    hub = s["hub"] if s else None
    if hub is None or not getattr(hub, "running", False):
        return jsonify({"live": False})
    return jsonify({
        "live":           True,
        "student_id":     hub.student_id,
        "face_count":     hub.face_count,
        "gaze_dir":       hub.gaze_dir,
        "strike_count":   hub.strike_count,
        "max_strikes":    hub.MAX_STRIKES,
        "phone_detected": hub.phone_detected,
    })


@app.route("/violations")
def violations():
    ok, token = _auth()
    if not ok: abort(403)
    s = _get_session(token)
    hub = s["hub"] if s else None
    return jsonify({"violations": list(hub.violations) if hub else []})


@app.route("/sessions")
def list_sessions():
    ok, token = _auth(admin_only=True)
    if not ok: abort(403)
    _cleanup()
    base = _public_url or f"http://localhost:{PORT}"
    with _sessions_lock:
        data = [{
            "student_id":  s["student_id"],
            "token":       t,
            "strikes":     s["hub"].strike_count if s.get("hub") else 0,
            "proctor_url": base,
            "connect_key": t,
        } for t, s in _sessions.items() if getattr(s.get("hub"), "running", False)]
    return jsonify({"sessions": data, "base_url": base})


@app.route("/terminate", methods=["POST"])
def terminate():
    ok, token = _auth()
    if not ok: abort(403)
    s = _get_session(token)
    hub = s["hub"] if s else None
    if hub and getattr(hub, "running", False):
        hub.strike_count = hub.MAX_STRIKES
        sio.emit("terminated", {"student_id": hub.student_id},
                 room=f"both:{hub.student_id}")
        return jsonify({"ok": True, "message": f"Exam terminated for {hub.student_id}"})
    return jsonify({"ok": False, "message": "No active session"}), 404


# ── Questions ──────────────────────────────────────────────────────────────

@app.route("/questions", methods=["GET"])
def get_questions():
    ok, _ = _auth()
    if not ok: abort(403)
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()
    return jsonify({"questions": _rows_to_dicts(rows)})


@app.route("/questions", methods=["POST"])
def add_question():
    ok, _ = _auth()
    if not ok: abort(403)
    d = request.json
    if not d:
        return jsonify({"ok": False, "error": "Empty body"}), 400
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
    ok, _ = _auth()
    if not ok: abort(403)
    d = request.json
    if not d: return jsonify({"ok": False, "error": "Empty body"}), 400
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
    ok, _ = _auth()
    if not ok: abort(403)
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    conn.commit()
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close()
    return jsonify({"ok": True, "questions": _rows_to_dicts(rows)})


# ── Results ────────────────────────────────────────────────────────────────

@app.route("/results")
def list_results():
    ok, token = _auth()
    if not ok: abort(403)
    all_files = sorted([f for f in os.listdir(".")
                        if f.endswith("_result.csv") or f.endswith("_exam_log.csv")])
    if token != SERVER_KEY:
        s = _get_session(token)
        if s:
            sid = s["student_id"]
            all_files = [f for f in all_files if f.startswith(sid)]
    return jsonify({"files": all_files})


@app.route("/results/<path:filename>")
def get_result(filename):
    ok, _ = _auth()
    if not ok: abort(403)
    safe = os.path.basename(filename)
    if not (safe.endswith("_result.csv") or safe.endswith("_exam_log.csv")):
        abort(400)
    if not os.path.exists(safe): abort(404)
    return send_file(safe, mimetype="text/csv", as_attachment=True)

# ══════════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════════

def start_server():
    """Called from main.py to start the WebRTC signaling server."""
    _ensure_schema()
    t = threading.Thread(
        target=lambda: sio.run(app, host="0.0.0.0", port=PORT,
                               debug=False, use_reloader=False),
        daemon=True)
    t.start()
    print(f"[Server] ExamShield WebRTC Signaling on port {PORT}")
    _try_tunnel()


def _try_tunnel():
    global _public_url
    if _NGROK_AVAILABLE:
        try:
            _public_url = _ngrok.connect(PORT, "http").public_url
            _print_banner(_public_url)
            return
        except Exception as e:
            print(f"[ngrok] Failed: {e}")
    import socket
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip = "YOUR_IP"
    _public_url = f"http://{ip}:{PORT}"
    _print_banner(_public_url, lan_only=True)


def _print_banner(url, lan_only=False):
    mode = "LAN only" if lan_only else "PUBLIC"
    print(f"\n{'='*64}")
    print(f"  ExamShield WebRTC  [{mode}]")
    print(f"  URL        : {url}")
    print(f"  Admin key  : {SERVER_KEY}")
    print(f"  Proctor UI : {url}/proctor  (open in browser)")
    print(f"  Protocol   : WebRTC + Socket.IO signaling")
    print(f"  YOLO       : {'active ✓' if _YOLO_AVAILABLE else 'not installed (pip install ultralytics)'}")
    print(f"  aiortc     : {'active ✓' if _AIORTC_AVAILABLE else 'not installed (pip install aiortc)'}")
    if lan_only:
        print(f"  For internet access: pip install pyngrok && ngrok config add-authtoken <TOKEN>")
    print(f"{'='*64}\n")


def print_student_url(student_id: str, token: str):
    """Called from main.py after each student exam starts."""
    base = _public_url or f"http://localhost:{PORT}"
    print(f"\n{'─'*64}")
    print(f"  NEW STUDENT  : {student_id}")
    print(f"  Proctor URL  : {base}/proctor")
    print(f"  Student token: {token}")
    print(f"  (Give this token to the proctor — sees ONLY {student_id})")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    print("[Server] Running standalone WebRTC signaling server")
    start_server()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[Server] Stopped.")
