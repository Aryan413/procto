"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                         ExamShield  v2.1                                    ║
║                  AI-Powered Secure Assessment Platform                       ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Run:   python main.py          (on BOTH machines — student AND proctor)    ║
║  Login: admin / admin123  (proctor)                                          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HOW REMOTE PROCTORING WORKS                                                 ║
║  ① Student runs main.py → logs in as Student → exam/interview starts        ║
║    A Flask server auto-starts on port 6000 — share your IP with proctor     ║
║  ② Proctor runs main.py on their OWN machine → logs in as Proctor           ║
║    Enter student machine's IP when prompted → live dashboard opens           ║
║  ③ Same-machine mode still works (no IP dialog shown if local hub active)   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  FEATURES                                                                    ║
║  ① Face verification at login (face_recognition or MediaPipe fallback)       ║
║  ② Tab-switch detection → instant strike                                     ║
║  ③ Blocked apps list (ChatGPT, browser, notepad, etc.) → strike on detect   ║
║  ④ Keystroke blocking (Ctrl+C/V/A/Tab/Alt+Tab/Win key)                       ║
║  ⑤ Question randomisation (order shuffled per session)                       ║
║  ⑥ Two modes on login: EXAM  and  INTERVIEW                                 ║
║  ⑦ EXAM mode  → student camera hidden; proctor sees live feed + violations  ║
║  ⑧ INTERVIEW mode → both cameras open simultaneously (like Google Meet)     ║
╚══════════════════════════════════════════════════════════════════════════════╝

Folder layout expected:
  main.py
  face_auth.py
  students.db         (auto-created)
  gaze_tracking/
      __init__.py
      gaze_tracking.py   (MediaPipe head-yaw version)
      eye.py  calibration.py  pupil.py

Remote proctoring requires:
  pip install flask requests
"""

# ─────────────────────────────────────────────────────────────────────────────
#  STDLIB
# ─────────────────────────────────────────────────────────────────────────────
import threading, time
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog
import sqlite3, math, random, time, threading, csv, os, sys, subprocess
import ctypes, platform

# ─────────────────────────────────────────────────────────────────────────────
#  HEAVY LIBS
# ─────────────────────────────────────────────────────────────────────────────
import cv2
import numpy as np
from PIL import Image, ImageTk
from ultralytics import YOLO
import mediapipe as mp

# ─────────────────────────────────────────────────────────────────────────────
#  OPTIONAL: requests (needed for remote proctor mode only)
# ─────────────────────────────────────────────────────────────────────────────
_REQUESTS_AVAILABLE = False
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    pass

_NGROK_AVAILABLE = False
try:
    from pyngrok import ngrok as _ngrok
    _NGROK_AVAILABLE = True
except ImportError:
    pass

# Public URL assigned by ngrok (set by start_network_server if ngrok is available)
_public_url = None

# ─────────────────────────────────────────────────────────────────────────────
#  WINDOWS-ONLY keyboard hook (graceful fallback on other OS)
# ─────────────────────────────────────────────────────────────────────────────
_KEYBOARD_HOOK_AVAILABLE = False
try:
    import keyboard
    _KEYBOARD_HOOK_AVAILABLE = True
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────────────────
#  PROCESS MONITOR
# ─────────────────────────────────────────────────────────────────────────────
_PSUTIL_AVAILABLE = False
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    pass

# ══════════════════════════════════════════════════════════════════════════════
#  THEMES
# ══════════════════════════════════════════════════════════════════════════════
DARK = {
    "bg":"#0d1117","canvas_bg":"#0d1117","card_bg":"#161b22","card_border":"#30363d",
    "title_fg":"#58d6d6","subtitle_fg":"#8b949e","label_fg":"#c9d1d9",
    "entry_bg":"#21262d","entry_fg":"#f0f6fc","entry_border":"#30363d","entry_focus":"#58d6d6",
    "btn_primary_bg":"#0be881","btn_primary_fg":"#0d1117",
    "btn_secondary_bg":"#575fcf","btn_secondary_fg":"#ffffff",
    "btn_toggle_bg":"#21262d","btn_toggle_fg":"#c9d1d9",
    "pill_active_bg":"#58d6d6","pill_active_fg":"#0d1117",
    "pill_inactive_bg":"#21262d","pill_inactive_fg":"#8b949e",
    "particle_colors":["#58d6d6","#0be881","#575fcf","#ff6b9d","#ffd93d"],
    "mode_icon":"☀️","mode_text":"Light Mode",
    "proctor_accent":"#ff6b9d","student_accent":"#0be881",
    "interview_accent":"#ffd93d",
}
LIGHT = {
    "bg":"#f0f4f8","canvas_bg":"#f0f4f8","card_bg":"#ffffff","card_border":"#d0d7de",
    "title_fg":"#0969da","subtitle_fg":"#57606a","label_fg":"#24292f",
    "entry_bg":"#f6f8fa","entry_fg":"#24292f","entry_border":"#d0d7de","entry_focus":"#0969da",
    "btn_primary_bg":"#1a7f37","btn_primary_fg":"#ffffff",
    "btn_secondary_bg":"#8250df","btn_secondary_fg":"#ffffff",
    "btn_toggle_bg":"#e7edf3","btn_toggle_fg":"#24292f",
    "pill_active_bg":"#0969da","pill_active_fg":"#ffffff",
    "pill_inactive_bg":"#eaeef2","pill_inactive_fg":"#57606a",
    "particle_colors":["#0969da","#1a7f37","#8250df","#cf222e","#9a6700"],
    "mode_icon":"🌙","mode_text":"Dark Mode",
    "proctor_accent":"#cf222e","student_accent":"#1a7f37",
    "interview_accent":"#9a6700",
}

# ══════════════════════════════════════════════════════════════════════════════
#  BLOCKED APPS
# ══════════════════════════════════════════════════════════════════════════════
BLOCKED_IF_FOREGROUND = {
    "chrome.exe","firefox.exe","msedge.exe","opera.exe","brave.exe",
    "vivaldi.exe","arc.exe","notepad.exe","notepad++.exe","wordpad.exe",
    "winword.exe","soffice.exe","sublime_text.exe",
    "zoom.exe","discord.exe","slack.exe","skype.exe","telegram.exe",
    "whatsapp.exe","signal.exe","teamviewer.exe","anydesk.exe","rustdesk.exe",
    "obs64.exe","obs32.exe","camtasia.exe","bandicam.exe",
}

BLOCKED_WINDOW_TITLES = [
    "chatgpt","claude.ai","gemini","copilot","chegg","quizlet",
    "google translate","grammarly","wolfram","photomath",
]

SYSTEM_WHITELIST = {
    "python.exe","python3.exe","pythonw.exe",
    "explorer.exe","conhost.exe","svchost.exe","taskhostw.exe",
    "runtimebroker.exe","werfault.exe","werfaultsecure.exe",
    "dllhost.exe","sihost.exe","ctfmon.exe","fontdrvhost.exe",
    "dwm.exe","winlogon.exe","csrss.exe","smss.exe","lsass.exe",
    "services.exe","spoolsv.exe","searchindexer.exe","searchhost.exe",
    "systemsettings.exe","startmenuexperiencehost.exe",
    "shellexperiencehost.exe","applicationframehost.exe","textinputhost.exe",
    "userinit.exe","unsecapp.exe","taskmgr.exe","msiexec.exe",
    "bravecrashhandler.exe","bravecrashhandler64.exe",
    "crashpad_handler.exe","crashreporter.exe",
    "chromiumcrashhandler.exe","msedgecrashhndlr.exe",
    "firefoxcrashhandler.exe","googlecrashhandler.exe","googlecrashhandler64.exe",
    "discordcrashhandler.exe","werfault.exe",
    "googleupdate.exe","googleupdatebroker.exe",
    "braveupdater.exe","msedgeupdate.exe","firefoxdefaultbrowser.exe",
    "msedgewebview2.exe",
    "msmpeng.exe","nissrv.exe","securityhealthservice.exe",
    "mbam.exe","mbamservice.exe","avgnt.exe","avguard.exe",
    "nvdisplay.container.exe","nvcontainer.exe","audiodg.exe",
    "amdrsserv.exe","radeoninstaller.exe",
    "notificationplatformhelper.exe","widgets.exe","widgetservice.exe",
    "phonelinkservice.exe","yourphone.exe",
    "wuauclt.exe","musnotifyicon.exe","compattelrunner.exe","diaghost.exe",
    "code.exe","code - insiders.exe",
    "steamservice.exe","epicgameslauncher.exe",
}

# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════
DB = "students.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        student_id TEXT PRIMARY KEY, password TEXT, face_data BLOB)""")
    c.execute("""CREATE TABLE IF NOT EXISTS proctors(
        proctor_id TEXT PRIMARY KEY, password TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT, opt_a TEXT, opt_b TEXT, opt_c TEXT, opt_d TEXT,
        answer TEXT, marks INTEGER DEFAULT 1, category TEXT DEFAULT 'General')""")
    c.execute("""CREATE TABLE IF NOT EXISTS violations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id TEXT, timestamp TEXT, event TEXT, detail TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS proctor_sessions(
        session_code TEXT PRIMARY KEY,
        proctor_id   TEXT,
        mode         TEXT,
        created_at   TEXT,
        active       INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS join_requests(
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_code TEXT,
        student_id   TEXT,
        status       TEXT DEFAULT 'pending',
        requested_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS runtime_questions(
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_code TEXT,
        student_id   TEXT,
        question     TEXT,
        options      TEXT DEFAULT '',
        sent_at      TEXT,
        answered     INTEGER DEFAULT 0,
        answer       TEXT DEFAULT '')""")
    # ── Two-way chat messages ─────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS chat_messages(
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        session_code TEXT,
        student_id   TEXT,
        sender       TEXT,
        message      TEXT,
        sent_at      TEXT)""")
    # Migrate older DBs that lack the options column
    try: c.execute("ALTER TABLE runtime_questions ADD COLUMN options TEXT DEFAULT ''")
    except Exception: pass
    try: c.execute("INSERT INTO proctors VALUES('admin','admin123')")
    except: pass
    if c.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0:
        seed = [
            ("What does CPU stand for?","Central Processing Unit","Central Program Unit",
             "Computer Personal Unit","Control Processing Unit","A",1,"CS"),
            ("Which language is used for web pages?","Python","Java","HTML","C++","C",1,"Web"),
            ("What is 2^10?","512","1024","2048","256","B",1,"Math"),
            ("Who invented the World Wide Web?","Bill Gates","Tim Berners-Lee",
             "Steve Jobs","Linus Torvalds","B",1,"General"),
            ("What does RAM stand for?","Random Access Memory","Read Access Module",
             "Remote Access Memory","Rapid Access Module","A",1,"CS"),
            ("Which data structure uses LIFO?","Queue","Stack","Tree","Graph","B",1,"CS"),
            ("Binary of decimal 5?","101","110","100","111","A",1,"Math"),
            ("Protocol used to send emails?","HTTP","FTP","SMTP","SSH","C",1,"Networks"),
        ]
        c.executemany(
            "INSERT INTO questions(question,opt_a,opt_b,opt_c,opt_d,answer,marks,category)"
            " VALUES(?,?,?,?,?,?,?,?)", seed)
    conn.commit(); conn.close()

def db_get_user(uid, pwd, role="student"):
    conn = sqlite3.connect(DB)
    col = "student_id" if role=="student" else "proctor_id"
    tbl = "users"      if role=="student" else "proctors"
    row = conn.execute(f"SELECT * FROM {tbl} WHERE {col}=? AND password=?",(uid,pwd)).fetchone()
    conn.close(); return row

def db_register(uid, pwd):
    conn = sqlite3.connect(DB)
    try:
        conn.execute("INSERT INTO users(student_id,password) VALUES(?,?)",(uid,pwd))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        conn.close(); return False

def db_get_questions():
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close(); return rows

def db_add_question(q,a,b,c,d,ans,marks=1,cat="General"):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO questions(question,opt_a,opt_b,opt_c,opt_d,answer,marks,category)"
                 " VALUES(?,?,?,?,?,?,?,?)",(q,a,b,c,d,ans,marks,cat))
    conn.commit(); conn.close()

def db_update_question(qid,q,a,b,c,d,ans,marks,cat):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE questions SET question=?,opt_a=?,opt_b=?,opt_c=?,opt_d=?,"
                 "answer=?,marks=?,category=? WHERE id=?",(q,a,b,c,d,ans,marks,cat,qid))
    conn.commit(); conn.close()

def db_delete_question(qid):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM questions WHERE id=?",(qid,)); conn.commit(); conn.close()

def db_log_violation(student_id, event, detail=""):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO violations(student_id,timestamp,event,detail) VALUES(?,?,?,?)",
                 (student_id,time.strftime("%H:%M:%S"),event,detail))
    conn.commit(); conn.close()

# ─────────────────── Session helpers ─────────────────────────────────────────
def db_create_session(proctor_id, mode):
    import random, string
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO proctor_sessions(session_code,proctor_id,mode,created_at) VALUES(?,?,?,?)",
                 (code, proctor_id, mode, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); conn.close()
    return code

def db_get_session(code):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT * FROM proctor_sessions WHERE session_code=? AND active=1",(code,)).fetchone()
    conn.close(); return row

def db_close_session(code):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE proctor_sessions SET active=0 WHERE session_code=?",(code,))
    conn.commit(); conn.close()

# ─────────────────── Join-request helpers ────────────────────────────────────
def db_add_join_request(session_code, student_id):
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO join_requests(session_code,student_id,requested_at) VALUES(?,?,?)",
                 (session_code, student_id, time.strftime("%H:%M:%S")))
    conn.commit(); conn.close()

def db_set_join_status(session_code, student_id, status):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE join_requests SET status=? WHERE session_code=? AND student_id=?",
                 (status, session_code, student_id))
    conn.commit(); conn.close()

def db_get_join_request(session_code, student_id):
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT status FROM join_requests WHERE session_code=? AND student_id=? ORDER BY id DESC LIMIT 1",
                       (session_code, student_id)).fetchone()
    conn.close(); return row[0] if row else None

def db_get_pending_requests(session_code):
    conn = sqlite3.connect(DB)
    # Use subquery to only get students whose LATEST request is still pending
    rows = conn.execute(
        """SELECT DISTINCT student_id FROM join_requests
           WHERE session_code=? AND status='pending'
             AND id = (SELECT MAX(id) FROM join_requests j2
                       WHERE j2.session_code=join_requests.session_code
                         AND j2.student_id=join_requests.student_id)""",
        (session_code,)).fetchall()
    conn.close(); return [r[0] for r in rows]

def db_get_accepted_students(session_code):
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT student_id FROM join_requests WHERE session_code=? AND status='accepted'",
                        (session_code,)).fetchall()
    conn.close(); return [r[0] for r in rows]

# ─────────────────── Runtime question helpers ─────────────────────────────────
def db_push_runtime_question(session_code, student_id, question, options=""):
    """options: pipe-separated choices e.g. 'Paris|London|Berlin|Rome'  (empty = open text)"""
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO runtime_questions(session_code,student_id,question,options,sent_at) VALUES(?,?,?,?,?)",
                 (session_code, student_id, question, options, time.strftime("%H:%M:%S")))
    conn.commit(); conn.close()

def db_get_runtime_questions(session_code, student_id):
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT id,question,options,sent_at,answered,answer FROM runtime_questions "
                        "WHERE session_code=? AND student_id=? ORDER BY id",
                        (session_code, student_id)).fetchall()
    conn.close(); return rows

def db_answer_runtime_question(qid, answer):
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE runtime_questions SET answered=1, answer=? WHERE id=?",(answer, qid))
    conn.commit(); conn.close()

# ─────────────────── Chat helpers ────────────────────────────────────────────
def db_send_chat(session_code, student_id, sender, message):
    """Insert a chat message.  sender = 'student' or 'proctor'."""
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO chat_messages(session_code,student_id,sender,message,sent_at)"
        " VALUES(?,?,?,?,?)",
        (session_code, student_id, sender, message, time.strftime("%H:%M:%S")))
    conn.commit(); conn.close()

def db_get_chat(session_code, student_id, since_id=0):
    """Return messages newer than since_id for this student+session."""
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT id,sender,message,sent_at FROM chat_messages"
        " WHERE session_code=? AND student_id=? AND id>? ORDER BY id",
        (session_code, student_id, since_id)).fetchall()
    conn.close()
    return [{"id": r[0], "sender": r[1], "message": r[2], "sent_at": r[3]} for r in rows]

# Global session state — set when proctor creates / student joins a session
_PROCTOR_SESSION_CODE: str = None   # set on proctor machine
_STUDENT_SESSION_CODE: str = None   # set on student machine
_PROCTOR_SERVER_URL:   str = None   # set on student machine (URL of proctor server)

# ══════════════════════════════════════════════════════════════════════════════
#  SECURITY MONITOR
# ══════════════════════════════════════════════════════════════════════════════
class SecurityMonitor:
    POLL_MS = 1000

    def __init__(self, root, student_id, on_violation):
        self.root          = root
        self.student_id    = student_id
        self.on_violation  = on_violation
        self.running       = True
        self._warned_apps  = set()
        self._thread       = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._setup_key_blocks()
        self._thread.start()

    def stop(self):
        self.running = False
        self._remove_key_blocks()

    def _setup_key_blocks(self):
        if not _KEYBOARD_HOOK_AVAILABLE: return
        blocked = [
            ("ctrl+c","Copy blocked"),("ctrl+v","Paste blocked"),
            ("ctrl+a","Select-all blocked"),("ctrl+x","Cut blocked"),
            ("ctrl+z","Undo blocked"),("alt+tab","Alt+Tab blocked"),
            ("windows","Win key blocked"),("ctrl+tab","Ctrl+Tab blocked"),
            ("ctrl+w","Close-tab blocked"),("ctrl+t","New-tab blocked"),
            ("ctrl+n","New-window blocked"),("ctrl+alt+delete","CAD blocked"),
            ("printscreen","Screenshot blocked"),
        ]
        self._hooks = []
        for keys, msg in blocked:
            try:
                h = keyboard.add_hotkey(keys,
                    lambda m=msg: self.root.after(0, lambda: self.on_violation("KEYSTROKE", m)),
                    suppress=True)
                self._hooks.append(h)
            except Exception:
                pass

    def _remove_key_blocks(self):
        if not _KEYBOARD_HOOK_AVAILABLE: return
        try: keyboard.unhook_all_hotkeys()
        except: pass

    APP_GRACE_SECS  = 5.0
    TAB_GRACE_SECS  = 3.0
    TAB_COOLDOWN    = 8.0

    def _safe_call(self, fn):
        try:
            if self.root.winfo_exists() and self.running:
                self.root.after(0, fn)
        except Exception:
            pass

    def _run(self):
        time.sleep(4)
        _app_first_seen  = {}
        _tab_first_seen  = None
        _last_tab_strike = 0.0

        while self.running:
            time.sleep(self.POLL_MS / 1000)
            if not self.running: break
            now = time.time()

            fg_title     = ""
            fg_proc_name = ""
            if platform.system() == "Windows":
                try:
                    import win32gui, win32process
                    fg_hwnd  = win32gui.GetForegroundWindow()
                    fg_title = win32gui.GetWindowText(fg_hwnd).lower()
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                        proc = psutil.Process(pid) if _PSUTIL_AVAILABLE else None
                        fg_proc_name = (proc.name().lower() if proc else "")
                    except Exception:
                        fg_proc_name = ""
                except ImportError:
                    pass

            exam_has_focus = "examshield" in fg_title or fg_title == ""

            if not exam_has_focus and fg_title and len(fg_title) > 2:
                if _tab_first_seen is None:
                    _tab_first_seen = now
                elif now - _tab_first_seen >= self.TAB_GRACE_SECS:
                    if now - _last_tab_strike >= self.TAB_COOLDOWN:
                        _last_tab_strike = now
                        t = fg_title
                        self._safe_call(lambda t=t: self.on_violation(
                            "TAB_SWITCH", f"Switched to: {t[:40]}"))
            else:
                _tab_first_seen = None

            if fg_title and not exam_has_focus:
                for kw in BLOCKED_WINDOW_TITLES:
                    if kw in fg_title:
                        key = f"title:{kw}"
                        if key not in _app_first_seen:
                            _app_first_seen[key] = now
                        elif (now - _app_first_seen[key] >= self.APP_GRACE_SECS
                              and key not in self._warned_apps):
                            self._warned_apps.add(key)
                            self._safe_call(lambda k=kw: self.on_violation(
                                "BLOCKED_APP", f"Cheating site open: {k}"))

            if fg_proc_name and fg_proc_name not in SYSTEM_WHITELIST:
                if fg_proc_name in BLOCKED_IF_FOREGROUND:
                    key = f"proc:{fg_proc_name}"
                    if key not in _app_first_seen:
                        _app_first_seen[key] = now
                        self._safe_call(lambda p=fg_proc_name: self.on_violation(
                            "APP_WARNING", f"{p} in foreground — monitoring…"))
                    elif (now - _app_first_seen[key] >= self.APP_GRACE_SECS
                          and key not in self._warned_apps):
                        self._warned_apps.add(key)
                        self._safe_call(lambda p=fg_proc_name: self.on_violation(
                            "BLOCKED_APP", f"Student opened {p} during exam"))
                else:
                    _app_first_seen.pop(f"proc:{fg_proc_name}", None)

# ══════════════════════════════════════════════════════════════════════════════
#  CAMERA HUB
# ══════════════════════════════════════════════════════════════════════════════
class CameraHub:
    MAX_STRIKES   = 5
    WARNING_SECS  = 4.0
    GAZE_FRAMES   = 15
    GAZE_DIRS     = {"left","right","up","down"}
    MULTI_GRACE   = 1.5
    YOLO_INTERVAL = 8   # run YOLO less often → frees CPU

    # Display resolution — smaller = faster rendering on proctor side
    DISPLAY_W = 480
    DISPLAY_H = 360

    def __init__(self, student_id):
        self.student_id     = student_id
        # Double-slot: _frame_a / _frame_b — writer flips atomically
        self._frame_a       = None
        self._frame_b       = None
        self._write_to_a    = True        # which slot writer is using
        self.latest_frame   = None        # kept for backwards compat
        self.running        = True
        self.violations     = []
        self.strike_count   = 0
        self.face_count     = 0
        self.gaze_dir       = "center"
        self.phone_detected = False
        self.terminated     = False
        self.frame_version  = 0    # incremented every new frame — proctor uses this to skip dupes
        self._lock          = threading.Lock()
        self._thread        = threading.Thread(target=self._run, daemon=True)

    def start(self):  self._thread.start()
    def stop(self):   self.running = False

    def get_frame(self):
        """Return the most recently completed frame — zero copy via slot flip."""
        with self._lock:
            # read from whichever slot writer is NOT currently writing
            f = self._frame_b if self._write_to_a else self._frame_a
            return f.copy() if f is not None else None

    def add_strike(self, event, detail=""):
        self.strike_count += 1
        self._log(f"STRIKE {self.strike_count}", detail or event)

    def _log(self, event, detail=""):
        ts  = time.strftime("%H:%M:%S")
        msg = f"[{ts}] {event}: {detail}"
        with self._lock:
            self.violations.append(msg)
            if len(self.violations) > 400:
                self.violations = self.violations[-400:]
        db_log_violation(self.student_id, event, detail)
        print(msg)

    def _run(self):
        from gaze_tracking import GazeTracking
        yolo      = YOLO("yolov8n.pt")
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=4,
            min_detection_confidence=0.92,
            min_tracking_confidence=0.92)
        gaze = GazeTracking()
        cap  = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        phone_t=None; multi_t=None; gaze_streak=0; gaze_timer=None
        frame_n=0; last_boxes=[]

        self._log("EXAM_START", self.student_id)
        print(f"[✅ EXAM START] {self.student_id} | Camera hidden from student")

        while self.running:
            ret, frame = cap.read()
            if not ret: break
            frame_n += 1

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)
            fc  = 0
            if res.multi_face_landmarks:
                h_f,w_f = frame.shape[:2]
                for fl in res.multi_face_landmarks:
                    xs=[lm.x for lm in fl.landmark]
                    if max(xs)-min(xs)>=0.14:
                        fc+=1
                        for lm in fl.landmark[::5]:
                            cv2.circle(frame,(int(lm.x*w_f),int(lm.y*h_f)),1,(0,200,100),-1)

            gaze.refresh(frame)
            gd = gaze.direction()
            if gaze.pupils_located:
                for coords in [gaze.pupil_left_coords(),gaze.pupil_right_coords()]:
                    if coords: cv2.circle(frame,coords,4,(0,255,120),-1)

            if frame_n % self.YOLO_INTERVAL == 0:
                det = yolo(frame,verbose=False)[0]; last_boxes=[]
                for box in det.boxes:
                    if yolo.names[int(box.cls[0])]=="cell phone" and float(box.conf[0])>0.45:
                        x1,y1,x2,y2=map(int,box.xyxy[0])
                        last_boxes.append((x1,y1,x2,y2,float(box.conf[0])))
            for x1,y1,x2,y2,cf in last_boxes:
                cv2.rectangle(frame,(x1,y1),(x2,y2),(0,60,255),2)
                cv2.putText(frame,f"PHONE {cf:.0%}",(x1,y1-8),cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,60,255),2)

            if gaze.calibration.is_complete() and gd in self.GAZE_DIRS:
                gaze_streak+=1
                if gaze_streak>=self.GAZE_FRAMES:
                    if gaze_timer is None:
                        gaze_timer=time.time(); self._log("GAZE_WARNING",f"Looking {gd}")
                    elif time.time()-gaze_timer>=self.WARNING_SECS:
                        self.strike_count+=1
                        self._log(f"STRIKE {self.strike_count}",f"Gaze away ({gd})")
                        gaze_timer=time.time()
            else:
                gaze_streak=0; gaze_timer=None

            if fc>1:
                cv2.putText(frame,"MULTIPLE FACES",(50,110),cv2.FONT_HERSHEY_SIMPLEX,0.8,(0,140,255),2)
                if multi_t is None: multi_t=time.time()
                elif time.time()-multi_t>=self.MULTI_GRACE:
                    self.strike_count+=1
                    self._log(f"STRIKE {self.strike_count}",f"Multiple faces ({fc})")
                    multi_t=time.time()
            else: multi_t=None

            if last_boxes:
                if phone_t is None:
                    phone_t=time.time(); self.strike_count+=1
                    self._log(f"STRIKE {self.strike_count}","Phone detected")
            else: phone_t=None

            h,w=frame.shape[:2]
            ov=frame.copy(); cv2.rectangle(ov,(0,0),(w,68),(15,15,15),-1)
            cv2.addWeighted(ov,0.75,frame,0.25,0,frame)
            cv2.putText(frame,f"ID:{self.student_id}",(10,22),cv2.FONT_HERSHEY_DUPLEX,0.55,(180,180,180),1)
            cv2.putText(frame,f"Faces:{fc}",(10,48),cv2.FONT_HERSHEY_DUPLEX,0.55,
                        (80,220,80) if fc==1 else (0,80,255),1)
            cv2.putText(frame,f"Gaze:{gd}",(w//2-80,22),cv2.FONT_HERSHEY_DUPLEX,0.55,
                        (80,220,80) if gd=="center" else (0,180,255),1)
            sc_c=(0,220,80) if self.strike_count==0 else (0,160,255) if self.strike_count<4 else (0,50,255)
            cv2.putText(frame,f"Strikes:{self.strike_count}/{self.MAX_STRIKES}",
                        (w-220,22),cv2.FONT_HERSHEY_DUPLEX,0.55,sc_c,1)

            if self.strike_count>=self.MAX_STRIKES:
                overlay=np.zeros_like(frame); overlay[:]=( 0,0,160)
                cv2.addWeighted(overlay,0.85,frame,0.15,0,frame)
                cv2.putText(frame,"EXAM TERMINATED",(w//2-210,h//2),
                            cv2.FONT_HERSHEY_DUPLEX,1.4,(255,255,255),3)
                self._log("EXAM_TERMINATED","Max strikes reached")
                self.terminated=True
                disp = cv2.resize(frame, (self.DISPLAY_W, self.DISPLAY_H),
                                  interpolation=cv2.INTER_LINEAR)
                with self._lock:
                    self._frame_a = disp; self.latest_frame = disp
                break

            # ── Zero-copy double-slot store ──────────────────────────
            # Downscale to display resolution before storing
            disp = cv2.resize(frame, (self.DISPLAY_W, self.DISPLAY_H),
                              interpolation=cv2.INTER_LINEAR)
            with self._lock:
                if self._write_to_a:
                    self._frame_a = disp
                else:
                    self._frame_b = disp
                self._write_to_a = not self._write_to_a
                self.latest_frame = disp          # backwards compat
                self.face_count   = fc
                self.gaze_dir     = gd
                self.phone_detected = bool(last_boxes)
                self.frame_version += 1

        cap.release(); face_mesh.close()
        print(f"[CameraHub] Stopped — {self.student_id}")


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE AUDIO  — real-time two-way audio via WebSocket (voice_bridge.py)
#
#  The old HTTP-poll InterviewAudio is replaced by VoiceClient which keeps a
#  persistent WebSocket to the bridge server (port 6001).  Latency drops from
#  ~300-800 ms (HTTP poll) to ~30-80 ms (WebSocket push).
#
#  Both exam mode and interview mode get voice — the bridge runs on the
#  PROCTOR machine alongside the Flask HTTP server.
# ══════════════════════════════════════════════════════════════════════════════
_SOUNDDEVICE_AVAILABLE = False
try:
    import sounddevice as sd
    _SOUNDDEVICE_AVAILABLE = True
except ImportError:
    pass

_WEBSOCKET_CLIENT_AVAILABLE = False
try:
    import websocket as _websocket_lib  # websocket-client package
    _WEBSOCKET_CLIENT_AVAILABLE = True
except ImportError:
    pass

_FLASK_SOCK_AVAILABLE = False
try:
    from flask_sock import Sock as _FlaskSock
    _FLASK_SOCK_AVAILABLE = True
except ImportError:
    pass

# Import the voice bridge module (sits next to main.py)
try:
    from voice_bridge import (
        VoiceClient,
        start_voice_bridge,
        make_ws_url,
        SAMPLE_RATE  as _VOICE_SR,
        CHUNK_FRAMES as _VOICE_CF,
        CHANNELS     as _VOICE_CH,
    )
    _VOICE_BRIDGE_AVAILABLE = True
except ImportError:
    _VOICE_BRIDGE_AVAILABLE = False
    print("[⚠] voice_bridge.py not found — place it next to main.py")

# Global voice clients — one per role per session
_voice_student: "VoiceClient | None" = None
_voice_proctor: "VoiceClient | None" = None

# Keep InterviewAudio as a thin shim so any remaining references don't break
class InterviewAudio:
    """Legacy shim — delegates to VoiceClient."""
    def __init__(self, role: str, remote_url: str | None = None):
        self.role = role
        self.remote_url = remote_url
        self._client: "VoiceClient | None" = None

    def start(self):
        if not _VOICE_BRIDGE_AVAILABLE or not self.remote_url:
            print(f"[Audio] VoiceBridge unavailable — audio disabled for {self.role}")
            return
        ws_url = make_ws_url(self.remote_url, ws_port=6001)
        self._client = VoiceClient(role=self.role, bridge_url=ws_url)
        self._client.start()

    def stop(self):
        if self._client:
            self._client.stop()
            self._client = None

    def toggle_mute(self):
        if self._client:
            return self._client.toggle_mute()
        return False

    def set_volume(self, v: float):
        if self._client:
            self._client.set_volume(v)


# Global audio hubs — one per session
_audio_student: InterviewAudio = None
_audio_proctor: InterviewAudio = None

# ══════════════════════════════════════════════════════════════════════════════
#  INTERVIEW CAMERA HUB
# ══════════════════════════════════════════════════════════════════════════════
class InterviewHub:
    MAX_STRIKES   = 5
    GAZE_FRAMES   = 15
    WARNING_SECS  = 4.0
    GAZE_DIRS     = {"left","right","up","down"}
    MULTI_GRACE   = 2.0

    DISPLAY_W = 480
    DISPLAY_H = 360

    def __init__(self, student_id):
        self.student_id      = student_id
        # Double-slot pattern for zero-lag reads
        self._sf_a = None; self._sf_b = None; self._sf_write_a = True
        self._pf_a = None; self._pf_b = None; self._pf_write_a = True
        self.student_frame   = None   # backwards compat
        self.proctor_frame   = None
        self.running         = True
        self.violations      = []
        self.strike_count    = 0
        self.face_count      = 0
        self.gaze_dir        = "center"
        self.terminated      = False
        self._lock           = threading.Lock()
        self._thread         = threading.Thread(target=self._run, daemon=True)

    def start(self):  self._thread.start()
    def stop(self):   self.running = False

    def get_student_frame(self):
        with self._lock:
            f = self._sf_b if self._sf_write_a else self._sf_a
            return f.copy() if f is not None else None

    def get_proctor_frame(self):
        with self._lock:
            f = self._pf_b if self._pf_write_a else self._pf_a
            return f.copy() if f is not None else None

    def set_proctor_frame(self, frame):
        """Called by Flask thread when a new proctor cam frame arrives."""
        disp = cv2.resize(frame, (self.DISPLAY_W, self.DISPLAY_H),
                          interpolation=cv2.INTER_LINEAR)
        with self._lock:
            if self._pf_write_a:
                self._pf_a = disp
            else:
                self._pf_b = disp
            self._pf_write_a = not self._pf_write_a
            self.proctor_frame = disp   # backwards compat

    def add_strike(self, event, detail=""):
        self.strike_count+=1; self._log(f"STRIKE {self.strike_count}", detail or event)

    def _log(self, event, detail=""):
        ts=time.strftime("%H:%M:%S"); msg=f"[{ts}] {event}: {detail}"
        with self._lock:
            self.violations.append(msg)
            if len(self.violations)>400: self.violations=self.violations[-400:]
        db_log_violation(self.student_id, event, detail); print(msg)

    def _run(self):
        from gaze_tracking import GazeTracking
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=4, min_detection_confidence=0.9, min_tracking_confidence=0.9)
        gaze = GazeTracking()

        cap_s = cv2.VideoCapture(0)
        cap_s.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap_s.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap_s.set(cv2.CAP_PROP_FPS, 30)
        cap_s.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        h_ph, w_ph = 480, 640
        _placeholder = np.zeros((h_ph, w_ph, 3), dtype=np.uint8)
        cv2.putText(_placeholder, "Waiting for interviewer camera...",
                    (30, h_ph//2 - 20), cv2.FONT_HERSHEY_DUPLEX, 0.7, (80, 80, 180), 2)
        cv2.putText(_placeholder, "Proctor: connect via main.py",
                    (30, h_ph//2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 60, 140), 1)

        gaze_streak=0; gaze_timer=None; multi_t=None
        self._log("INTERVIEW_START", self.student_id)

        while self.running:
            ret_s, frame_s = cap_s.read()
            if not ret_s: break

            frame_p = self.get_proctor_frame()
            if frame_p is None:
                frame_p = _placeholder.copy()

            rgb = cv2.cvtColor(frame_s, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)
            fc  = 0
            if res.multi_face_landmarks:
                h_f,w_f=frame_s.shape[:2]
                for fl in res.multi_face_landmarks:
                    xs=[lm.x for lm in fl.landmark]
                    if max(xs)-min(xs)>=0.14:
                        fc+=1
                        for lm in fl.landmark[::5]:
                            cv2.circle(frame_s,(int(lm.x*w_f),int(lm.y*h_f)),1,(0,200,100),-1)

            gaze.refresh(frame_s)
            gd=gaze.direction()
            if gaze.pupils_located:
                for coords in [gaze.pupil_left_coords(),gaze.pupil_right_coords()]:
                    if coords: cv2.circle(frame_s,coords,4,(0,255,120),-1)

            if gaze.calibration.is_complete() and gd in self.GAZE_DIRS:
                gaze_streak+=1
                if gaze_streak>=self.GAZE_FRAMES:
                    if gaze_timer is None:
                        gaze_timer=time.time(); self._log("GAZE_WARNING",f"Looking {gd}")
                    elif time.time()-gaze_timer>=self.WARNING_SECS:
                        self.strike_count+=1
                        self._log(f"STRIKE {self.strike_count}",f"Gaze away ({gd})")
                        gaze_timer=time.time()
            else:
                gaze_streak=0; gaze_timer=None

            if fc>1:
                if multi_t is None: multi_t=time.time()
                elif time.time()-multi_t>=self.MULTI_GRACE:
                    self.strike_count+=1
                    self._log(f"STRIKE {self.strike_count}",f"Multiple faces ({fc})")
                    multi_t=time.time()
            else: multi_t=None

            h,w=frame_s.shape[:2]
            ov=frame_s.copy(); cv2.rectangle(ov,(0,0),(w,60),(10,10,10),-1)
            cv2.addWeighted(ov,0.75,frame_s,0.25,0,frame_s)
            cv2.putText(frame_s,f"STUDENT: {self.student_id}",(10,20),
                        cv2.FONT_HERSHEY_DUPLEX,0.55,(180,255,180),1)
            cv2.putText(frame_s,f"Gaze:{gd} | Faces:{fc} | Strikes:{self.strike_count}/{self.MAX_STRIKES}",
                        (10,45),cv2.FONT_HERSHEY_DUPLEX,0.45,
                        (80,220,80) if self.strike_count==0 else (0,150,255),1)

            if self.strike_count>=self.MAX_STRIKES:
                overlay=np.zeros_like(frame_s); overlay[:]=(0,0,160)
                cv2.addWeighted(overlay,0.85,frame_s,0.15,0,frame_s)
                cv2.putText(frame_s,"INTERVIEW TERMINATED",(w//2-240,h//2),
                            cv2.FONT_HERSHEY_DUPLEX,1.2,(255,255,255),3)
                self._log("INTERVIEW_TERMINATED","Max strikes reached")
                self.terminated=True
                disp_s = cv2.resize(frame_s, (self.DISPLAY_W, self.DISPLAY_H),
                                    interpolation=cv2.INTER_LINEAR)
                with self._lock:
                    self._sf_a = disp_s; self.student_frame = disp_s
                    self._pf_a = frame_p; self.proctor_frame = frame_p
                break

            # ── Double-slot store (downscaled) ───────────────────────
            disp_s = cv2.resize(frame_s, (self.DISPLAY_W, self.DISPLAY_H),
                                interpolation=cv2.INTER_LINEAR)
            with self._lock:
                if self._sf_write_a:
                    self._sf_a = disp_s
                else:
                    self._sf_b = disp_s
                self._sf_write_a = not self._sf_write_a
                self.student_frame = disp_s   # backwards compat
                self.face_count = fc
                self.gaze_dir   = gd

        cap_s.release()
        face_mesh.close()
        print(f"[InterviewHub] Stopped — {self.student_id}")

# Global hubs — set when a student logs in on THIS machine
_hub:       CameraHub    = None
_iv_hub:    InterviewHub = None

# ══════════════════════════════════════════════════════════════════════════════
#  PARTICLE / BASE WINDOW  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════
class Particle:
    def __init__(self, w, h, colors):
        self.canvas_w=w; self.canvas_h=h; self.reset(colors)
    def reset(self, colors):
        self.x=random.uniform(0,self.canvas_w); self.y=random.uniform(0,self.canvas_h)
        self.size=random.uniform(1.5,4.5); self.color=random.choice(colors)
        self.vx=random.uniform(-0.4,0.4); self.vy=random.uniform(-0.4,0.4)
        self.pulse=random.uniform(0,math.pi*2); self.pulse_speed=random.uniform(0.02,0.06)
    def update(self, mx, my):
        dx,dy=self.x-mx,self.y-my; dist=math.sqrt(dx*dx+dy*dy) or 1
        if dist<100:
            f=(100-dist)/100*1.2; self.vx+=dx/dist*f; self.vy+=dy/dist*f
        self.vx*=0.97; self.vy*=0.97
        sp=math.sqrt(self.vx**2+self.vy**2)
        if sp>2.5: self.vx=self.vx/sp*2.5; self.vy=self.vy/sp*2.5
        self.x+=self.vx; self.y+=self.vy; self.pulse+=self.pulse_speed
        if self.x<0 or self.x>self.canvas_w: self.vx*=-1; self.x=max(0,min(self.canvas_w,self.x))
        if self.y<0 or self.y>self.canvas_h: self.vy*=-1; self.y=max(0,min(self.canvas_h,self.y))

class BaseWindow:
    def __init__(self, root, theme):
        self.root=root; self.theme=theme
        self.mouse_x=260; self.mouse_y=300; self.animating=True
        self.canvas=tk.Canvas(root,highlightthickness=0)
        self.canvas.place(x=0,y=0,relwidth=1,relheight=1)
        self.particles=[Particle(520,640,theme["particle_colors"]) for _ in range(55)]
        self.root.bind("<Configure>",self._on_resize)
        self.canvas.bind("<Motion>",lambda e:(setattr(self,'mouse_x',e.x),setattr(self,'mouse_y',e.y)))

    def _fade(self, hex_color, alpha):
        bg=self.theme["bg"].lstrip("#"); fg=hex_color.lstrip("#")
        try:
            br,bg_c,bb=int(bg[0:2],16),int(bg[2:4],16),int(bg[4:6],16)
            fr,fg_c,fb=int(fg[0:2],16),int(fg[2:4],16),int(fg[4:6],16)
            a=alpha/255
            return f"#{int(br+(fr-br)*a):02x}{int(bg_c+(fg_c-bg_c)*a):02x}{int(bb+(fb-bb)*a):02x}"
        except: return hex_color

    def _draw_particles(self):
        self.canvas.delete("particle")
        for p in self.particles:
            p.update(self.mouse_x,self.mouse_y)
            r=p.size+math.sin(p.pulse)*1.2
            self.canvas.create_oval(p.x-r,p.y-r,p.x+r,p.y+r,fill=p.color,outline="",tags="particle")
        for i,p1 in enumerate(self.particles):
            for p2 in self.particles[i+1:]:
                dx,dy=p1.x-p2.x,p1.y-p2.y; d=math.sqrt(dx*dx+dy*dy)
                if d<90:
                    op=int(255*(1-d/90)*0.35)
                    self.canvas.create_line(p1.x,p1.y,p2.x,p2.y,
                        fill=self._fade(p1.color,op),width=0.8,tags="particle")

    def _draw_card(self):
        self.canvas.delete("card_bg")
        w,h=self.root.winfo_width(),self.root.winfo_height()
        px=max(40,int(w*0.10)); x0,y0=px,max(70,int(h*0.11)); x1,y1=w-px,h-max(36,int(h*0.06))
        r=18; fill=self.theme["card_bg"]; ol=self.theme["card_border"]; t="card_bg"
        self.canvas.create_rectangle(x0+4,y0+4,x1+4,y1+4,fill="#000000",outline="",tags=t)
        self.canvas.create_rectangle(x0+r,y0,x1-r,y1,fill=fill,outline="",tags=t)
        self.canvas.create_rectangle(x0,y0+r,x1,y1-r,fill=fill,outline="",tags=t)
        for cx,cy,s,e in [(x0+r,y0+r,180,270),(x1-r,y0+r,270,360),(x0+r,y1-r,90,180),(x1-r,y1-r,0,90)]:
            self.canvas.create_arc(cx-r,cy-r,cx+r,cy+r,start=s,extent=e-s,fill=fill,outline="",tags=t)
        for c in [(x0+r,y0,x1-r,y0+2),(x0+r,y1-2,x1-r,y1),(x0,y0+r,x0+2,y1-r),(x1-2,y0+r,x1,y1-r)]:
            self.canvas.create_rectangle(*c,fill=ol,outline="",tags=t)

    def _animate(self):
        if not self.animating: return
        try:
            if not self.root.winfo_exists(): return
            w,h=self.root.winfo_width(),self.root.winfo_height()
            self.canvas.configure(bg=self.theme["canvas_bg"],width=w,height=h)
            self._draw_particles(); self._draw_card()
            self.root.after(30,self._animate)
        except Exception:
            self.animating = False

    def _on_resize(self, event=None):
        w,h=self.root.winfo_width(),self.root.winfo_height()
        if w<10 or h<10: return
        self.canvas.config(width=w,height=h)
        if hasattr(self,'ui_frame'):
            px=max(40,int(w*0.10)); cw=w-2*px
            fw=min(cw-20,420)
            self.ui_frame.place(x=px+(cw-fw)//2,
                                y=max(70,int(h*0.11))+max(18,int(h*0.04)),width=fw)
        t=max(55,min(120,int(w*h/8000)))
        while len(self.particles)<t: self.particles.append(Particle(w,h,self.theme["particle_colors"]))
        while len(self.particles)>t: self.particles.pop()
        for p in self.particles:
            p.canvas_w=w; p.canvas_h=h
            if p.x>w or p.y>h: p.x=random.uniform(0,w); p.y=random.uniform(0,h)

    def _make_entry(self, parent, show=None):
        fr=tk.Frame(parent,bg=self.theme["entry_border"],bd=0)
        fr.pack(fill="x",padx=30,pady=(3,0))
        e=tk.Entry(fr,font=("Helvetica",11),bg=self.theme["entry_bg"],fg=self.theme["entry_fg"],
                   insertbackground=self.theme["entry_fg"],bd=0,relief="flat",show=show or "")
        e.pack(fill="x",padx=1,pady=1,ipady=8)
        e.bind("<FocusIn>",lambda _: fr.configure(bg=self.theme["entry_focus"]))
        e.bind("<FocusOut>",lambda _: fr.configure(bg=self.theme["entry_border"]))
        return e

# ══════════════════════════════════════════════════════════════════════════════
#  SESSION CODE DIALOG — shown to student to enter proctor's session code
# ══════════════════════════════════════════════════════════════════════════════
def _ask_session_code(parent_root, student_id):
    """
    Show a modal dialog asking student for the proctor's session code.
    Defaults to localhost:6000 (same-machine mode).
    Returns (proctor_url, session_code) or None if cancelled.
    """
    # ── Same-machine fast-path: if a proctor session is already active locally ──
    if _PROCTOR_SESSION_CODE:
        # Proctor is on THIS machine — no dialog needed, just approve directly
        db_add_join_request(_PROCTOR_SESSION_CODE, student_id)
        return ("http://127.0.0.1:6000", _PROCTOR_SESSION_CODE)

    if not _REQUESTS_AVAILABLE:
        # Fallback: let student join locally with no URL (same DB, no HTTP)
        code = simpledialog.askstring(
            "Session Code",
            "Enter the Session Code from your proctor:",
            parent=parent_root)
        if not code:
            return None
        code = code.strip().upper()
        sess = db_get_session(code)
        if not sess:
            messagebox.showerror("Invalid", f"Session code '{code}' not found or inactive.",
                                 parent=parent_root)
            return None
        db_add_join_request(code, student_id)
        # Wait for approval by polling local DB
        win2 = tk.Toplevel(parent_root)
        win2.title("Waiting for approval…")
        win2.geometry("340x120"); win2.configure(bg="#0d1117")
        win2.grab_set(); win2.transient(parent_root)
        win2.attributes("-topmost", True)
        tk.Label(win2, text="⏳ Waiting for proctor to accept…",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d").pack(pady=(24,6))
        status_lbl2 = tk.Label(win2, text="", font=("Helvetica",9), bg="#0d1117", fg="#8b949e")
        status_lbl2.pack()
        result2 = [None]
        def _poll_local():
            s = db_get_join_request(code, student_id)
            if s == "accepted":
                result2[0] = ("http://127.0.0.1:6000", code)
                win2.destroy()
            elif s == "rejected":
                status_lbl2.configure(text="❌ Rejected by proctor.", fg="#ff4444")
                win2.after(1500, win2.destroy)
            else:
                win2.after(1500, _poll_local)
        win2.after(1000, _poll_local)
        parent_root.wait_window(win2)
        return result2[0]

    win = tk.Toplevel(parent_root)
    win.title("Join Proctor Session")
    win.geometry("480x340")
    win.configure(bg="#0d1117")
    win.resizable(False, False)
    win.grab_set()
    win.transient(parent_root)

    result = [None]   # [(proctor_url, session_code)]

    tk.Label(win, text="🔗  Join Exam Session",
             font=("Helvetica", 14, "bold"), bg="#0d1117", fg="#58d6d6").pack(pady=(20, 4))
    tk.Label(win,
             text="Enter the proctor's URL/IP and Session Code.",
             font=("Helvetica", 9), bg="#0d1117", fg="#8b949e", justify="center").pack(pady=(0, 8))

    # ── Same-machine shortcut ─────────────────────────────────────────────────
    same_row = tk.Frame(win, bg="#0d1117"); same_row.pack(fill="x", padx=30, pady=(0,6))
    tk.Label(same_row, text="💡 Same computer as proctor?",
             font=("Helvetica",9), bg="#0d1117", fg="#8b949e").pack(side="left")

    def _use_localhost():
        url_var.set("127.0.0.1")
        port_var.set("6000")

    tk.Button(same_row, text="Use localhost", font=("Helvetica",8,"bold"),
              bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
              command=_use_localhost).pack(side="right", ipady=3, ipadx=6)

    row1 = tk.Frame(win, bg="#0d1117"); row1.pack(fill="x", padx=30, pady=2)
    tk.Label(row1, text="Proctor URL/IP:", font=("Helvetica", 10, "bold"),
             bg="#0d1117", fg="#c9d1d9", width=15, anchor="w").pack(side="left")
    url_var = tk.StringVar()
    url_entry = tk.Entry(row1, textvariable=url_var, font=("Helvetica", 11),
                         bg="#21262d", fg="#f0f6fc", insertbackground="#f0f6fc",
                         bd=0, relief="flat", width=22)
    url_entry.pack(side="left", ipady=7, padx=(4, 0))

    row1b = tk.Frame(win, bg="#0d1117"); row1b.pack(fill="x", padx=30, pady=2)
    tk.Label(row1b, text="Port:", font=("Helvetica", 10, "bold"),
             bg="#0d1117", fg="#c9d1d9", width=15, anchor="w").pack(side="left")
    port_var = tk.StringVar(value="6000")
    port_entry = tk.Entry(row1b, textvariable=port_var, font=("Helvetica", 11),
                          bg="#21262d", fg="#f0f6fc", insertbackground="#f0f6fc",
                          bd=0, relief="flat", width=8)
    port_entry.pack(side="left", ipady=7, padx=(4, 0))

    row2 = tk.Frame(win, bg="#0d1117"); row2.pack(fill="x", padx=30, pady=2)
    tk.Label(row2, text="Session Code:", font=("Helvetica", 10, "bold"),
             bg="#0d1117", fg="#c9d1d9", width=15, anchor="w").pack(side="left")
    code_var = tk.StringVar()
    code_entry = tk.Entry(row2, textvariable=code_var, font=("Helvetica", 12, "bold"),
                          bg="#21262d", fg="#ffd93d", insertbackground="#ffd93d",
                          bd=0, relief="flat", width=12)
    code_entry.pack(side="left", ipady=7, padx=(4, 0))

    tk.Label(win, text="💡 Get the URL and Session Code from your proctor",
             font=("Helvetica", 8), bg="#0d1117", fg="#575fcf").pack(pady=(4, 0))

    status_lbl = tk.Label(win, text="", font=("Helvetica", 9),
                          bg="#0d1117", fg="#8b949e")
    status_lbl.pack(pady=(8, 0))

    _polling = [False]

    def _connect():
        raw_url  = url_var.get().strip()
        port_s   = port_var.get().strip()
        code     = code_var.get().strip().upper()
        if not raw_url or not code:
            status_lbl.configure(text="⚠ Fill in all fields", fg="#ffaa00"); return
        try:
            port = int(port_s)
        except ValueError:
            status_lbl.configure(text="⚠ Port must be a number", fg="#ffaa00"); return

        if raw_url.startswith("http://") or raw_url.startswith("https://"):
            base_url = raw_url.rstrip("/")
        else:
            base_url = f"http://{raw_url}:{port}"

        status_lbl.configure(text=f"Connecting to {base_url} …", fg="#58d6d6")
        win.update()

        try:
            r = _requests.get(f"{base_url}/ping", timeout=6)
            pdata = r.json()
            srv_code = pdata.get("session_code", "")
            # Only reject if server reports a *different* active code
            if srv_code and srv_code != code:
                status_lbl.configure(text=f"❌  Wrong session code. Server has: {srv_code}", fg="#ff4444")
                return
        except Exception as e:
            status_lbl.configure(text=f"❌  Cannot reach proctor: {e}", fg="#ff4444")
            return

        # Send join request
        try:
            r2 = _requests.post(f"{base_url}/join_request",
                                json={"student_id": student_id, "session_code": code},
                                timeout=6)
            rj = r2.json()
            if not rj.get("ok"):
                status_lbl.configure(text=f"❌  {rj.get('reason','rejected')}", fg="#ff4444")
                return
            if rj.get("status") == "accepted":
                result[0] = (base_url, code)
                win.destroy()
                return
        except Exception as e:
            status_lbl.configure(text=f"❌  {e}", fg="#ff4444")
            return

        status_lbl.configure(text="⏳ Join request sent — waiting for proctor to accept…", fg="#ffd93d")
        _polling[0] = True
        btn_join.configure(state="disabled")

        def _poll():
            if not _polling[0]: return
            try:
                if not win.winfo_exists(): return
            except Exception:
                return
            try:
                r3 = _requests.get(f"{base_url}/join_status",
                                   params={"student_id": student_id, "session_code": code},
                                   timeout=4)
                s = r3.json().get("status", "pending")
                if s == "accepted":
                    _polling[0] = False
                    result[0] = (base_url, code)
                    status_lbl.configure(text="✅  Accepted! Starting exam…", fg="#0be881")
                    win.after(800, win.destroy)
                elif s == "rejected":
                    _polling[0] = False
                    status_lbl.configure(text="❌  Proctor rejected your request.", fg="#ff4444")
                    btn_join.configure(state="normal")
                else:
                    win.after(2000, _poll)
            except Exception:
                win.after(2000, _poll)

        win.after(2000, _poll)

    btn_row = tk.Frame(win, bg="#0d1117"); btn_row.pack(pady=(12, 0))
    btn_join = tk.Button(btn_row, text="Request to Join ▶", font=("Helvetica", 10, "bold"),
              bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
              width=18, command=_connect)
    btn_join.grid(row=0, column=0, padx=6, ipady=6)
    tk.Button(btn_row, text="Cancel", font=("Helvetica", 10),
              bg="#21262d", fg="#c9d1d9", bd=0, relief="flat", cursor="hand2",
              width=10, command=lambda: [win.destroy()]).grid(row=0, column=1, padx=6, ipady=6)

    url_entry.focus_set()
    code_entry.bind("<Return>", lambda _: _connect())
    parent_root.wait_window(win)
    return result[0]

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOGIN
# ══════════════════════════════════════════════════════════════════════════════
class MainLogin(BaseWindow):
    def __init__(self):
        self.root=tk.Tk()
        self.root.title("ExamShield v2 — Login")
        self.root.geometry("540x700"); self.root.resizable(True,True); self.root.minsize(440,600)
        self.is_dark=True; self.theme=DARK
        self.role=tk.StringVar(value="student")
        self.mode=tk.StringVar(value="exam")
        super().__init__(self.root,self.theme)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW",self._close)
        self._animate()

    def _build_ui(self):
        t=self.theme
        self.ui_frame=tk.Frame(self.root,bg=t["card_bg"],bd=0,highlightthickness=0)
        self.ui_frame.place(x=70,y=110,width=400)

        tk.Label(self.ui_frame,text="🛡️",font=("Segoe UI Emoji",28),bg=t["card_bg"]).pack(pady=(16,0))
        tk.Label(self.ui_frame,text="ExamShield",font=("Helvetica",20,"bold"),
                 bg=t["card_bg"],fg=t["title_fg"]).pack()
        tk.Label(self.ui_frame,text="Secure AI Assessment Platform",
                 font=("Helvetica",9),bg=t["card_bg"],fg=t["subtitle_fg"]).pack(pady=(2,12))

        tk.Label(self.ui_frame,text="SESSION TYPE",font=("Helvetica",8,"bold"),
                 bg=t["card_bg"],fg=t["subtitle_fg"]).pack(pady=(0,4))
        mf=tk.Frame(self.ui_frame,bg=t["card_bg"]); mf.pack(pady=(0,10))
        self.btn_exam=tk.Button(mf,text="📝  Exam",font=("Helvetica",10,"bold"),
            bd=0,relief="flat",cursor="hand2",width=13,command=lambda:self._set_mode("exam"))
        self.btn_exam.grid(row=0,column=0,padx=3,ipady=6)
        self.btn_iv=tk.Button(mf,text="🎙  Interview",font=("Helvetica",10,"bold"),
            bd=0,relief="flat",cursor="hand2",width=13,command=lambda:self._set_mode("interview"))
        self.btn_iv.grid(row=0,column=1,padx=3,ipady=6)

        tk.Label(self.ui_frame,text="LOGIN AS",font=("Helvetica",8,"bold"),
                 bg=t["card_bg"],fg=t["subtitle_fg"]).pack(pady=(4,4))
        pf=tk.Frame(self.ui_frame,bg=t["card_bg"]); pf.pack(pady=(0,10))
        self.pill_s=tk.Button(pf,text="👨‍🎓  Student",font=("Helvetica",10,"bold"),
            bd=0,relief="flat",cursor="hand2",width=13,command=lambda:self._set_role("student"))
        self.pill_s.grid(row=0,column=0,padx=3,ipady=6)
        self.pill_p=tk.Button(pf,text="👨‍🏫  Proctor",font=("Helvetica",10,"bold"),
            bd=0,relief="flat",cursor="hand2",width=13,command=lambda:self._set_role("proctor"))
        self.pill_p.grid(row=0,column=1,padx=3,ipady=6)

        self.lbl_id=tk.Label(self.ui_frame,font=("Helvetica",10,"bold"),
                              bg=t["card_bg"],fg=t["label_fg"],anchor="w")
        self.lbl_id.pack(fill="x",padx=30,pady=(6,0))
        self.eid=self._make_entry(self.ui_frame)

        tk.Label(self.ui_frame,text="Password",font=("Helvetica",10,"bold"),
                 bg=t["card_bg"],fg=t["label_fg"],anchor="w").pack(fill="x",padx=30,pady=(8,0))
        self.epw=self._make_entry(self.ui_frame,show="●")

        bf=tk.Frame(self.ui_frame,bg=t["card_bg"]); bf.pack(pady=14)
        self.btn_login=tk.Button(bf,text="Log In ▶",font=("Helvetica",11,"bold"),
            bd=0,relief="flat",cursor="hand2",width=12,command=self._login)
        self.btn_login.grid(row=0,column=0,padx=6,ipady=6)
        self.btn_reg=tk.Button(bf,text="Register ✚",font=("Helvetica",11,"bold"),
            bd=0,relief="flat",cursor="hand2",width=12,command=self._register)
        self.btn_reg.grid(row=0,column=1,padx=6,ipady=6)

        self.btn_tog=tk.Button(self.root,font=("Helvetica",9),bd=0,relief="flat",
            cursor="hand2",command=self._toggle)
        self.btn_tog.place(x=375,y=55,width=140,height=28)

        self._set_mode("exam"); self._set_role("student"); self._apply()

    def _set_mode(self, m):
        self.mode.set(m); t=self.theme
        exam_col  = t["btn_primary_bg"] if m=="exam" else t["pill_inactive_bg"]
        exam_fg   = t["btn_primary_fg"] if m=="exam" else t["pill_inactive_fg"]
        iv_col    = t["interview_accent"] if m=="interview" else t["pill_inactive_bg"]
        iv_fg     = "#0d1117"  if m=="interview" else t["pill_inactive_fg"]
        self.btn_exam.configure(bg=exam_col, fg=exam_fg)
        self.btn_iv.configure(bg=iv_col, fg=iv_fg)

    def _set_role(self, r):
        self.role.set(r); t=self.theme
        if r=="student":
            self.pill_s.configure(bg=t["student_accent"],fg=t["pill_active_fg"])
            self.pill_p.configure(bg=t["pill_inactive_bg"],fg=t["pill_inactive_fg"])
            self.lbl_id.configure(text="Student ID")
            self.btn_reg.configure(state="normal",bg=t["btn_secondary_bg"],fg=t["btn_secondary_fg"])
        else:
            self.pill_p.configure(bg=t["proctor_accent"],fg=t["pill_active_fg"])
            self.pill_s.configure(bg=t["pill_inactive_bg"],fg=t["pill_inactive_fg"])
            self.lbl_id.configure(text="Proctor ID")
            self.btn_reg.configure(state="disabled",bg=t["pill_inactive_bg"],fg=t["pill_inactive_fg"])

    def _apply(self):
        t=self.theme; self.root.configure(bg=t["bg"])
        self.btn_login.configure(bg=t["btn_primary_bg"],fg=t["btn_primary_fg"])
        self.btn_tog.configure(bg=t["btn_toggle_bg"],fg=t["btn_toggle_fg"],
                                text=f"{t['mode_icon']}  {t['mode_text']}")
        for e in [self.eid,self.epw]:
            e.configure(bg=t["entry_bg"],fg=t["entry_fg"],insertbackground=t["entry_fg"])
            e.master.configure(bg=t["entry_border"])
        self._set_mode(self.mode.get()); self._set_role(self.role.get())

    def _toggle(self):
        self.is_dark=not self.is_dark; self.theme=DARK if self.is_dark else LIGHT
        for p in self.particles: p.color=random.choice(self.theme["particle_colors"])
        self._apply()

    def _register(self):
        uid=self.eid.get().strip(); pwd=self.epw.get().strip()
        if not uid or not pwd: messagebox.showerror("Error","Fill both fields"); return
        if db_register(uid,pwd):
            try:
                from face_auth import capture_face_registration, init_face_db
                init_face_db()   # ensure face_data column exists
                ans = messagebox.askyesno(
                    "Face Registration",
                    f"Account '{uid}' created!\n\n"
                    "Register your face now for biometric login?\n\n"
                    "YES → camera opens (recommended)\n"
                    "NO  → skip (face check bypassed at login)")
                if ans:
                    self.root.withdraw()
                    capture_face_registration(uid)
                    self.root.deiconify()
            except ImportError:
                messagebox.showwarning("Face Auth",
                    "face_auth.py not found — face registration skipped.")
            messagebox.showinfo("Success", "Registration complete! You can now log in.")
        else:
            messagebox.showerror("Error","ID already exists.")

    def _login(self):
        global _hub, _iv_hub
        uid=self.eid.get().strip(); pwd=self.epw.get().strip()
        if not uid or not pwd: messagebox.showerror("Error","Fill both fields"); return
        role=self.role.get(); mode=self.mode.get()
        if not db_get_user(uid,pwd,role):
            messagebox.showerror("Login Failed","Wrong ID or password."); return

        if role=="student":
            # Face verification
            try:
                from face_auth import verify_face
                self.root.withdraw(); ok=verify_face(uid); self.root.deiconify()
                if not ok: messagebox.showerror("Denied","Face verification failed!"); return
            except ImportError: pass

            # Ask for proctor session code
            session_info = _ask_session_code(self.root, uid)
            if session_info is None:
                return   # user cancelled
            proctor_url, session_code = session_info

            if mode=="exam":
                self.animating=False; self.root.destroy()
                ExamWindow(uid, proctor_url=proctor_url, session_code=session_code).run()
            else:
                _iv_hub=InterviewHub(uid); _iv_hub.start()
                self.animating=False; self.root.destroy()
                InterviewStudentWindow(uid, proctor_url=proctor_url, session_code=session_code).run()

        else:
            # ── PROCTOR LOGIN v3 ───────────────────────────────────────
            # Proctor creates a session → session code generated
            # Students will connect TO the proctor using this code
            global _PROCTOR_SESSION_CODE
            _PROCTOR_SESSION_CODE = db_create_session(uid, mode)
            self.animating=False; self.root.destroy()
            MultiStudentProctorWindow(uid, mode, self.is_dark).run()

    def _close(self): self.animating=False; self.root.destroy()
    def run(self): self.root.mainloop()

# --- WebRTC & Signaling Engine (optional) ---
_WEBRTC_AVAILABLE = False
try:
    import socketio as _sio_lib
    from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
    from av import VideoFrame
    _WEBRTC_AVAILABLE = True
except ImportError:
    pass

if _WEBRTC_AVAILABLE:
    class CameraHubTrack(VideoStreamTrack):
        """Bridge between your OpenCV CameraHub and WebRTC."""
        kind = "video"
        def __init__(self, hub):
            super().__init__()
            self.hub = hub

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            frame_bgr = self.hub.get_frame() if self.hub else None
            if frame_bgr is None:
                frame_bgr = np.zeros((360, 480, 3), dtype=np.uint8)
            
            # Convert BGR to RGB for WebRTC
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
            video_frame.pts, video_frame.time_base = pts, time_base
            return video_frame

if _WEBRTC_AVAILABLE:
  class StudentWebRTCPeer:
    """The background engine that streams to the proctor."""
    def __init__(self, server_url, student_id, hub):
        self.server_url = server_url
        self.student_id = student_id
        self.hub = hub
        self._sio = _sio_lib.AsyncClient(ssl_verify=False)
        self._pc = None

    def start(self):
        import asyncio
        threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True).start()

    async def _main(self):
        @self._sio.on("offer")
        async def on_offer(data):
            self._pc = RTCPeerConnection()
            self._pc.addTrack(CameraHubTrack(self.hub))
            await self._pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
            answer = await self._pc.createAnswer()
            await self._pc.setLocalDescription(answer)
            await self._sio.emit("answer", {"student_id": self.student_id, "sdp": self._pc.localDescription.sdp, "type": self._pc.localDescription.type})

        await self._sio.connect(self.server_url, transports=["websocket"])
        await self._sio.emit("student-join", {"student_id": self.student_id})

# ══════════════════════════════════════════════════════════════════════════════
#  EXAM WINDOW  (student — unchanged)
# ══════════════════════════════════════════════════════════════════════════════
class ExamWindow:
    def __init__(self, student_id, proctor_url=None, session_code=None):
        self.sid=student_id
        self.proctor_url=proctor_url
        self.session_code=session_code
        qs=db_get_questions()
        random.shuffle(qs)
        self.qs=qs
        self.qi=0; self.answers={}; self.start=time.time()
        self._runtime_qs_seen=set()
        # Each ExamWindow owns its own CameraHub — supports multiple concurrent students
        self._my_hub = CameraHub(student_id)
        self._my_hub.start()
        # Start WebRTC streaming for this student
        global _webrtc_peer
        _webrtc_peer = StudentWebRTCPeer(f"http://localhost:{PORT}", self.sid, self._my_hub)
        _webrtc_peer.start()
        self.root=tk.Tk()
        self.root.title("ExamShield — Exam in Progress 🔒")
        self.root.geometry("860x680"); self.root.resizable(True,True)
        self.root.minsize(660,540); self.root.configure(bg="#0d1117")
        self.root.state("zoomed") if platform.system()=="Windows" else None
        self.root.protocol("WM_DELETE_WINDOW",self._close)
        self._build()
        self._sec=SecurityMonitor(self.root, student_id, self._on_security_event)
        self._sec.start()
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.root.bind("<FocusIn>",  self._on_focus_in)
        self._focus_lost_time=None
        self._tick()
        self._check_termination()
        if proctor_url:
            self._push_frame_loop()
            self._poll_runtime_questions()

    # ── Push camera frame + stats to proctor server ──────────────────────────
    def _push_frame_loop(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return

        def _push():
            hub = self._my_hub
            if hub:
                # Only push if frame has changed since last push
                with hub._lock:
                    ver = hub.frame_version
                if getattr(self, "_last_pushed_ver", -1) == ver:
                    return   # nothing new
                self._last_pushed_ver = ver
                frame = hub.get_frame()
                if frame is not None:
                    try:
                        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
                        if ok:
                            _requests.post(
                                f"{self.proctor_url}/push_student_frame",
                                params={"student_id": self.sid},
                                data=buf.tobytes(), timeout=1)
                    except Exception: pass
                try:
                    _requests.post(
                        f"{self.proctor_url}/push_student_stats",
                        json={
                            "student_id":   self.sid,
                            "face_count":   hub.face_count,
                            "gaze_dir":     hub.gaze_dir,
                            "strike_count": hub.strike_count,
                            "phone":        hub.phone_detected,
                            "terminated":   hub.terminated,
                            "max_strikes":  CameraHub.MAX_STRIKES,
                            "mode":         "exam",
                        }, timeout=1)
                except Exception: pass
        threading.Thread(target=_push, daemon=True).start()
        self.root.after(40, self._push_frame_loop)

    # ── Push violation to proctor server ─────────────────────────────────────
    def _push_violation_remote(self, event, detail=""):
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _do():
            try:
                _requests.post(f"{self.proctor_url}/push_violation",
                               json={"student_id": self.sid, "event": event, "detail": detail},
                               timeout=2)
            except Exception: pass
        threading.Thread(target=_do, daemon=True).start()

    # ── Poll for runtime questions pushed by proctor ──────────────────────────
    def _poll_runtime_questions(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _fetch():
            try:
                r = _requests.get(f"{self.proctor_url}/runtime_questions",
                                  params={"student_id": self.sid, "session_code": self.session_code},
                                  timeout=3)
                qs = r.json().get("questions", [])
                for q in qs:
                    if not q["answered"] and q["id"] not in self._runtime_qs_seen:
                        self._runtime_qs_seen.add(q["id"])
                        self.root.after(0, lambda qdata=q: self._show_runtime_question(qdata))
            except Exception: pass
        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(5000, self._poll_runtime_questions)

    # ── Show runtime question popup ───────────────────────────────────────────
    def _show_runtime_question(self, qdata):
        qid     = qdata["id"]
        text    = qdata["question"]
        options = [o for o in (qdata.get("options") or "").split("|") if o]
        win = tk.Toplevel(self.root)
        win.title("📌 Proctor Question")
        win.configure(bg="#0d1117")
        win.grab_set()
        win.attributes("-topmost", True)

        tk.Label(win, text="📌 Proctor has sent you a question",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d").pack(pady=(16,4))
        tk.Label(win, text=text, font=("Helvetica",12), bg="#0d1117", fg="#f0f6fc",
                 wraplength=420, justify="center").pack(pady=(0,12), padx=20)

        if options:
            # MCQ mode — radio buttons
            win.geometry("480x320")
            tk.Label(win, text="Select your answer:", font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9").pack()
            ans_var = tk.StringVar(value="")
            btn_frame = tk.Frame(win, bg="#0d1117"); btn_frame.pack(fill="x", padx=30, pady=(4,0))
            labels = ["A","B","C","D"]
            for i, opt in enumerate(options[:4]):
                rb = tk.Radiobutton(btn_frame, text=f"  {labels[i]})  {opt}",
                                    variable=ans_var, value=labels[i],
                                    font=("Helvetica",10), bg="#0d1117", fg="#f0f6fc",
                                    selectcolor="#161b22", activebackground="#0d1117",
                                    activeforeground="#ffd93d", anchor="w")
                rb.pack(fill="x", pady=3)
            def _submit():
                ans = ans_var.get()
                if not ans:
                    messagebox.showwarning("Select", "Please choose an option.", parent=win); return
                def _do():
                    try:
                        _requests.post(f"{self.proctor_url}/answer_runtime_question",
                                       json={"qid": qid, "answer": ans}, timeout=3)
                    except Exception: pass
                threading.Thread(target=_do, daemon=True).start()
                win.destroy()
        else:
            # Open-ended mode — text entry
            win.geometry("460x260")
            tk.Label(win, text="Your Answer:", font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9").pack()
            ans_entry = tk.Entry(win, font=("Helvetica",11), bg="#21262d", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat")
            ans_entry.pack(fill="x", padx=30, pady=(4,0), ipady=7)
            ans_entry.focus_set()
            ans_entry.bind("<Return>", lambda _: _submit())
            def _submit():
                ans = ans_entry.get().strip()
                if not ans: return
                def _do():
                    try:
                        _requests.post(f"{self.proctor_url}/answer_runtime_question",
                                       json={"qid": qid, "answer": ans}, timeout=3)
                    except Exception: pass
                threading.Thread(target=_do, daemon=True).start()
                win.destroy()

        tk.Button(win, text="Submit Answer ✓", font=("Helvetica",10,"bold"),
                  bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=_submit).pack(fill="x", padx=30, pady=12, ipady=7)

    def _on_security_event(self, event, detail):
        if event == "APP_WARNING":
            if self._my_hub: self._my_hub._log("APP_WARNING", detail)
            self._flash_warning(f"🔔 {detail}", color="#4a3800", duration=2000)
            return
        if event == "KEYSTROKE":
            if self._my_hub: self._my_hub._log("KEYSTROKE_BLOCKED", detail)
            self._flash_warning(f"🚫 {detail}", color="#1a1a4a", duration=1500)
            return
        if self._my_hub:
            self._my_hub.add_strike(event, detail)
        self._push_violation_remote(event, detail)
        self._flash_warning(f"⚠ STRIKE: {detail}")

    def _flash_warning(self, msg, color="#6a0000", duration=2500):
        try:
            w=tk.Toplevel(self.root); w.overrideredirect(True)
            w.configure(bg=color)
            w.geometry(f"520x56+{self.root.winfo_x()+130}+{self.root.winfo_y()+8}")
            tk.Label(w,text=msg,font=("Helvetica",10,"bold"),bg=color,fg="#ffffff",
                     wraplength=500).pack(expand=True)
            w.after(duration, w.destroy)
        except Exception: pass

    def _on_focus_out(self, event):
        self._focus_lost_time=time.time()

    def _on_focus_in(self, event):
        if self._focus_lost_time:
            lost=time.time()-self._focus_lost_time
            if lost>0.5:
                self._on_security_event("TAB_SWITCH",f"Window lost focus for {lost:.1f}s")
            self._focus_lost_time=None

    def _check_termination(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if self._my_hub and self._my_hub.terminated:
            self._force_terminate()
            return
        self.root.after(500, self._check_termination)

    def _force_terminate(self):
        self._sec.stop()
        self.root.configure(bg="#1a0000")
        for w in self.root.winfo_children(): w.destroy()
        tk.Label(self.root,text="🚫",font=("Segoe UI Emoji",60),bg="#1a0000").pack(pady=(80,0))
        tk.Label(self.root,text="EXAM TERMINATED",font=("Helvetica",26,"bold"),
                 bg="#1a0000",fg="#ff4444").pack(pady=10)
        tk.Label(self.root,text="You reached 5 strikes.\nYour session has been recorded.",
                 font=("Helvetica",12),bg="#1a0000",fg="#c9d1d9").pack()
        tk.Button(self.root,text="Close",font=("Helvetica",11,"bold"),bg="#333",fg="#fff",
            bd=0,relief="flat",cursor="hand2",command=self.root.destroy).pack(pady=30,ipady=8,padx=60,fill="x")

    def _build(self):
        bar=tk.Frame(self.root,bg="#161b22",height=56); bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar,text="🛡️  ExamShield — EXAM MODE  🔒",font=("Helvetica",13,"bold"),
                 bg="#161b22",fg="#58d6d6").pack(side="left",padx=16,pady=12)
        self.lbl_timer=tk.Label(bar,text="⏱ 00:00",font=("Helvetica",11,"bold"),
                                 bg="#161b22",fg="#0be881")
        self.lbl_timer.pack(side="right",padx=16)
        self.lbl_prog=tk.Label(bar,font=("Helvetica",10),bg="#161b22",fg="#8b949e")
        self.lbl_prog.pack(side="right",padx=8)

        self.strike_bar=tk.Frame(self.root,bg="#0d1117",height=28)
        self.strike_bar.pack(fill="x"); self.strike_bar.pack_propagate(False)
        self.lbl_strikes_disp=tk.Label(self.strike_bar,
            text="● Secure  |  Warnings: 0/5",
            font=("Helvetica",8),bg="#0d1117",fg="#2a2a3a")
        self.lbl_strikes_disp.pack(side="left",padx=14)
        tk.Label(self.strike_bar,text="🔒 Camera Active  |  Tab-Switch Monitored  |  Apps Blocked",
            font=("Helvetica",7),bg="#0d1117",fg="#1a3a1a").pack(side="right",padx=14)

        self.pbar=tk.Canvas(self.root,height=4,bg="#21262d",highlightthickness=0)
        self.pbar.pack(fill="x")

        main=tk.Frame(self.root,bg="#0d1117"); main.pack(fill="both",expand=True)
        main.columnconfigure(1,weight=1); main.columnconfigure(2,weight=0); main.rowconfigure(0,weight=1)

        ns=tk.Frame(main,bg="#161b22",width=100); ns.grid(row=0,column=0,sticky="nsew"); ns.pack_propagate(False)
        tk.Label(ns,text="Qs",font=("Helvetica",8,"bold"),bg="#161b22",fg="#8b949e").pack(pady=(10,4))
        self._qbtns=[]
        for i in range(len(self.qs)):
            b=tk.Button(ns,text=str(i+1),font=("Helvetica",8,"bold"),
                bg="#21262d",fg="#8b949e",bd=0,relief="flat",cursor="hand2",width=4,
                command=lambda idx=i:self._jump(idx))
            b.pack(pady=2,padx=8,ipady=3); self._qbtns.append(b)

        qf=tk.Frame(main,bg="#0d1117"); qf.grid(row=0,column=1,sticky="nsew")
        inner=tk.Frame(qf,bg="#0d1117"); inner.pack(fill="both",expand=True,padx=36,pady=18)

        self.lbl_qn=tk.Label(inner,font=("Helvetica",10,"bold"),bg="#0d1117",fg="#8b949e",anchor="w")
        self.lbl_qn.pack(fill="x",pady=(0,4))
        self.lbl_cat=tk.Label(inner,font=("Helvetica",8),bg="#0d1117",fg="#575fcf",anchor="w")
        self.lbl_cat.pack(fill="x",pady=(0,4))
        self.lbl_q=tk.Label(inner,font=("Helvetica",14,"bold"),bg="#0d1117",fg="#f0f6fc",
                              wraplength=580,justify="left",anchor="w")
        self.lbl_q.pack(fill="x",pady=(0,16))

        self.opt_var=tk.StringVar(); self.opt_btns=[]
        for opt in ["A","B","C","D"]:
            b=tk.Radiobutton(inner,variable=self.opt_var,value=opt,
                font=("Helvetica",12),bg="#161b22",fg="#c9d1d9",
                selectcolor="#0d3b2e",activebackground="#161b22",
                activeforeground="#0be881",indicatoron=True,
                bd=0,relief="flat",anchor="w",padx=16,pady=10,cursor="hand2")
            b.pack(fill="x",pady=3,ipady=4); self.opt_btns.append(b)

        self.lbl_marks=tk.Label(inner,font=("Helvetica",8),bg="#0d1117",fg="#ffd93d",anchor="e")
        self.lbl_marks.pack(fill="x",pady=(4,0))

        # ── Chat panel (column 2) — only visible when connected to proctor ──
        chat_col = tk.Frame(main, bg="#161b22", width=220)
        chat_col.grid(row=0, column=2, sticky="nsew"); chat_col.pack_propagate(False)
        self._build_chat_panel(chat_col)

        nf=tk.Frame(self.root,bg="#0d1117"); nf.pack(pady=10)
        self.btn_prev=tk.Button(nf,text="◀ Prev",font=("Helvetica",11,"bold"),
            bg="#21262d",fg="#c9d1d9",bd=0,relief="flat",cursor="hand2",width=9,command=self._prev)
        self.btn_prev.grid(row=0,column=0,padx=5,ipady=6)
        self.btn_next=tk.Button(nf,text="Next ▶",font=("Helvetica",11,"bold"),
            bg="#575fcf",fg="#ffffff",bd=0,relief="flat",cursor="hand2",width=9,command=self._next)
        self.btn_next.grid(row=0,column=1,padx=5,ipady=6)
        self.btn_clr=tk.Button(nf,text="Clear",font=("Helvetica",10),
            bg="#21262d",fg="#ff6b9d",bd=0,relief="flat",cursor="hand2",width=7,command=self._clear)
        self.btn_clr.grid(row=0,column=2,padx=5,ipady=6)
        self.btn_sub=tk.Button(nf,text="Submit ✓",font=("Helvetica",11,"bold"),
            bg="#0be881",fg="#0d1117",bd=0,relief="flat",cursor="hand2",width=12,command=self._submit)
        self.btn_sub.grid(row=0,column=3,padx=5,ipady=6)
        self._load_q()

    # ── Chat panel (student side — exam mode) ────────────────────────────────
    def _build_chat_panel(self, parent):
        self._chat_last_id = 0
        tk.Label(parent, text="💬 Proctor Chat",
                 font=("Helvetica",9,"bold"), bg="#161b22", fg="#58d6d6"
                 ).pack(anchor="w", padx=8, pady=(8,2))
        scr = tk.Scrollbar(parent); scr.pack(side="right", fill="y")
        self._chat_log = tk.Text(
            parent, font=("Helvetica",8), bg="#0d1117", fg="#c9d1d9",
            bd=0, relief="flat", wrap="word", state="disabled",
            yscrollcommand=scr.set)
        self._chat_log.pack(fill="both", expand=True, padx=(6,0), pady=(0,4))
        scr.configure(command=self._chat_log.yview)
        self._chat_log.tag_configure("me",   foreground="#0be881")
        self._chat_log.tag_configure("them", foreground="#58d6d6")
        self._chat_log.tag_configure("ts",   foreground="#555566")
        # Input row
        inp = tk.Frame(parent, bg="#161b22"); inp.pack(fill="x", padx=6, pady=(0,6))
        self._chat_entry = tk.Entry(
            inp, font=("Helvetica",9), bg="#21262d", fg="#f0f6fc",
            insertbackground="#f0f6fc", bd=0, relief="flat")
        self._chat_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,4))
        tk.Button(inp, text="▶", font=("Helvetica",9,"bold"),
                  bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=self._send_chat_msg
                  ).pack(side="right", ipady=5, ipadx=6)
        self._chat_entry.bind("<Return>", lambda _: self._send_chat_msg())
        if self.proctor_url:
            self._poll_chat()
        else:
            self._chat_entry.configure(state="disabled")
            self._append_chat_sys("Connect to proctor to enable chat")

    def _send_chat_msg(self):
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        msg = self._chat_entry.get().strip()
        if not msg: return
        self._chat_entry.delete(0, "end")
        self._append_chat("You", msg, "me")
        def _post():
            try:
                _requests.post(f"{self.proctor_url}/send_chat", json={
                    "session_code": self.session_code,
                    "student_id":   self.sid,
                    "sender":       "student",
                    "message":      msg,
                }, timeout=2)
            except Exception: pass
        threading.Thread(target=_post, daemon=True).start()

    def _poll_chat(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _fetch():
            try:
                r = _requests.get(f"{self.proctor_url}/get_chat", params={
                    "session_code": self.session_code,
                    "student_id":   self.sid,
                    "since_id":     self._chat_last_id,
                }, timeout=2)
                for m in r.json().get("messages", []):
                    self._chat_last_id = max(self._chat_last_id, m["id"])
                    if m["sender"] == "proctor":
                        self.root.after(0, lambda d=m:
                            self._append_chat("Proctor", d["message"], "them"))
            except Exception: pass
        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(2000, self._poll_chat)

    def _append_chat(self, sender, text, cls):
        try:
            self._chat_log.configure(state="normal")
            ts = time.strftime("%H:%M")
            self._chat_log.insert("end", f"[{ts}] ", "ts")
            self._chat_log.insert("end", f"{sender}: {text}\n", cls)
            self._chat_log.configure(state="disabled")
            self._chat_log.see("end")
        except Exception: pass

    def _append_chat_sys(self, text):
        try:
            self._chat_log.configure(state="normal")
            self._chat_log.insert("end", f"— {text} —\n", "ts")
            self._chat_log.configure(state="disabled")
        except Exception: pass

    def _load_q(self):
        if not self.qs: return
        q=self.qs[self.qi]; n=len(self.qs)
        self.lbl_qn.configure(text=f"Question {self.qi+1} of {n}")
        cat=q[8] if len(q)>8 else "General"
        self.lbl_cat.configure(text=f"📁 {cat}")
        self.lbl_q.configure(text=q[1])
        for i,b in enumerate(self.opt_btns):
            b.configure(text=f"  {'ABCD'[i]}.  {q[2+i]}",value="ABCD"[i])
        marks=q[7] if len(q)>7 else 1
        self.lbl_marks.configure(text=f"Marks: {marks}")
        self.opt_var.set(self.answers.get(self.qi,""))
        ratio=(self.qi+1)/n; w=self.root.winfo_width() or 860
        self.pbar.delete("all")
        self.pbar.create_rectangle(0,0,int(w*ratio),4,fill="#0be881",outline="")
        self.lbl_prog.configure(text=f"{self.qi+1}/{n}")
        self.btn_prev.configure(state="normal" if self.qi>0   else "disabled")
        self.btn_next.configure(state="normal" if self.qi<n-1 else "disabled")
        for i,b in enumerate(self._qbtns):
            if i==self.qi: b.configure(bg="#575fcf",fg="#ffffff")
            elif i in self.answers: b.configure(bg="#0be881",fg="#0d1117")
            else: b.configure(bg="#21262d",fg="#8b949e")

    def _save(self):
        a=self.opt_var.get()
        if a: self.answers[self.qi]=a

    def _jump(self,idx): self._save(); self.qi=idx; self._load_q()
    def _prev(self): self._save(); self.qi-=1; self._load_q()
    def _next(self): self._save(); self.qi+=1; self._load_q()
    def _clear(self):
        self.opt_var.set("")
        if self.qi in self.answers: del self.answers[self.qi]
        self._load_q()

    def _submit(self):
        self._save()
        un=len(self.qs)-len(self.answers)
        if un>0 and not messagebox.askyesno("Submit?",f"{un} unanswered. Submit anyway?"): return
        self._show_results()

    def _show_results(self):
        score=sum(1 for i,q in enumerate(self.qs) if self.answers.get(i)==q[6])
        total=len(self.qs); elapsed=int(time.time()-self.start)
        pct=int(score/total*100) if total else 0
        grade="A" if pct>=90 else "B" if pct>=75 else "C" if pct>=60 else "D" if pct>=40 else "F"
        strikes = self._my_hub.strike_count if self._my_hub else 0
        try: self._sec.stop()
        except Exception: pass
        try:
            if self._my_hub: self._my_hub.stop()
        except Exception: pass
        log = f"{self.sid}_result.csv"
        try:
            with open(log, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f)
                wr.writerow(["Q#","Question","Your Answer","Correct","Result"])
                for i, q in enumerate(self.qs):
                    a = self.answers.get(i, "-")
                    wr.writerow([i+1, q[1], a, q[6], "OK" if a==q[6] else "X"])
        except Exception as e:
            print(f"[CSV] Save failed: {e}")
        messagebox.showinfo("Exam Complete",
            f"Score  : {score}/{total} ({pct}%)\n"
            f"Grade  : {grade}\n"
            f"Time   : {elapsed//60:02d}:{elapsed%60:02d}\n"
            f"Strikes: {strikes}\n\n"
            f"Results saved → {log}")
        try: self.root.destroy()
        except Exception: pass

    def _tick(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        try:
            e=int(time.time()-self.start); m,s=e//60,e%60
            self.lbl_timer.configure(text=f"⏱ {m:02d}:{s:02d}")
            if self._my_hub:
                sc=self._my_hub.strike_count
                col="#2a2a3a" if sc==0 else "#6a3800" if sc<3 else "#6a0000"
                self.lbl_strikes_disp.configure(
                    text=f"● Secure  |  Warnings: {sc}/{CameraHub.MAX_STRIKES}",fg=col)
        except Exception:
            pass
        self.root.after(1000,self._tick)

    def _close(self):
        if messagebox.askyesno("Quit","Exit exam? All progress lost."):
            self._sec.stop()
            if self._my_hub: self._my_hub.stop()
            self.root.destroy()

    def run(self): self.root.mainloop()

# ══════════════════════════════════════════════════════════════════════════════
#  INTERVIEW STUDENT WINDOW  (unchanged)
# ══════════════════════════════════════════════════════════════════════════════
class InterviewStudentWindow:
    def __init__(self, student_id, proctor_url=None, session_code=None):
        self.sid=student_id
        self.proctor_url=proctor_url
        self.session_code=session_code
        self._runtime_qs_seen=set()
        self.root=tk.Tk()
        self.root.title("ExamShield — Interview Mode 🎙")
        self.root.geometry("1100x680"); self.root.resizable(True,True)
        self.root.minsize(900,560); self.root.configure(bg="#0d1117")
        self.root.state("zoomed") if platform.system()=="Windows" else None
        self.root.protocol("WM_DELETE_WINDOW",self._close)
        self._sec=SecurityMonitor(self.root, student_id, self._on_sec)
        self._sec.start()
        self.root.bind("<FocusOut>",self._focus_out)
        self.root.bind("<FocusIn>", self._focus_in)
        self._focus_lost=None
        self._build()
        self._poll_cam()
        self._tick()
        self._check_terminate()
        self._poll_notes()   # poll for notes pushed by remote proctor
        # ── Two-way voice (WebSocket, low-latency) ─────────────────────────
        global _voice_student
        if proctor_url and _VOICE_BRIDGE_AVAILABLE and _SOUNDDEVICE_AVAILABLE:
            ws_url = make_ws_url(proctor_url, ws_port=6001)
            _voice_student = VoiceClient(role="student", bridge_url=ws_url)
            _voice_student.start()
            print(f"[Voice] Student voice client started  ws_url={ws_url}")
        if proctor_url:
            self._push_frame_loop()
            self._poll_runtime_questions()

    # ── Push student cam frame to proctor server ─────────────────────────────
    def _push_frame_loop(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _push():
            if _iv_hub:
                frame = _iv_hub.get_student_frame()
                if frame is not None:
                    try:
                        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 30])
                        if ok:
                            _requests.post(
                                f"{self.proctor_url}/push_student_frame",
                                params={"student_id": self.sid},
                                data=buf.tobytes(), timeout=1)
                    except Exception: pass
                try:
                    _requests.post(
                        f"{self.proctor_url}/push_student_stats",
                        json={
                            "student_id":   self.sid,
                            "face_count":   _iv_hub.face_count,
                            "gaze_dir":     _iv_hub.gaze_dir,
                            "strike_count": _iv_hub.strike_count,
                            "phone":        False,
                            "terminated":   _iv_hub.terminated,
                            "max_strikes":  InterviewHub.MAX_STRIKES,
                            "mode":         "interview",
                        }, timeout=1)
                except Exception: pass
        threading.Thread(target=_push, daemon=True).start()
        self.root.after(40, self._push_frame_loop)

    def _push_violation_remote(self, event, detail=""):
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _do():
            try:
                _requests.post(f"{self.proctor_url}/push_violation",
                               json={"student_id": self.sid, "event": event, "detail": detail},
                               timeout=2)
            except Exception: pass
        threading.Thread(target=_do, daemon=True).start()

    def _poll_runtime_questions(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _fetch():
            try:
                r = _requests.get(f"{self.proctor_url}/runtime_questions",
                                  params={"student_id": self.sid, "session_code": self.session_code},
                                  timeout=3)
                qs = r.json().get("questions", [])
                for q in qs:
                    if not q["answered"] and q["id"] not in self._runtime_qs_seen:
                        self._runtime_qs_seen.add(q["id"])
                        self.root.after(0, lambda qdata=q: self._show_runtime_question(qdata))
            except Exception: pass
        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(5000, self._poll_runtime_questions)

    def _show_runtime_question(self, qdata):
        qid     = qdata["id"]
        text    = qdata["question"]
        options = [o for o in (qdata.get("options") or "").split("|") if o]
        win = tk.Toplevel(self.root)
        win.title("📌 Interviewer Question")
        win.configure(bg="#0d1117")
        win.grab_set()
        win.attributes("-topmost", True)

        tk.Label(win, text="📌 Interviewer sent you a question",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d").pack(pady=(16,4))
        tk.Label(win, text=text, font=("Helvetica",12), bg="#0d1117", fg="#f0f6fc",
                 wraplength=420, justify="center").pack(pady=(0,12), padx=20)

        if options:
            win.geometry("480x320")
            tk.Label(win, text="Select your answer:", font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9").pack()
            ans_var = tk.StringVar(value="")
            btn_frame = tk.Frame(win, bg="#0d1117"); btn_frame.pack(fill="x", padx=30, pady=(4,0))
            labels = ["A","B","C","D"]
            for i, opt in enumerate(options[:4]):
                rb = tk.Radiobutton(btn_frame, text=f"  {labels[i]})  {opt}",
                                    variable=ans_var, value=labels[i],
                                    font=("Helvetica",10), bg="#0d1117", fg="#f0f6fc",
                                    selectcolor="#161b22", activebackground="#0d1117",
                                    activeforeground="#ffd93d", anchor="w")
                rb.pack(fill="x", pady=3)
            def _submit():
                ans = ans_var.get()
                if not ans:
                    messagebox.showwarning("Select", "Please choose an option.", parent=win); return
                def _do():
                    try:
                        _requests.post(f"{self.proctor_url}/answer_runtime_question",
                                       json={"qid": qid, "answer": ans}, timeout=3)
                    except Exception: pass
                threading.Thread(target=_do, daemon=True).start()
                win.destroy()
        else:
            win.geometry("460x260")
            tk.Label(win, text="Your Answer:", font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9").pack()
            ans_entry = tk.Entry(win, font=("Helvetica",11), bg="#21262d", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat")
            ans_entry.pack(fill="x", padx=30, pady=(4,0), ipady=7)
            ans_entry.focus_set()
            ans_entry.bind("<Return>", lambda _: _submit())
            def _submit():
                ans = ans_entry.get().strip()
                if not ans: return
                def _do():
                    try:
                        _requests.post(f"{self.proctor_url}/answer_runtime_question",
                                       json={"qid": qid, "answer": ans}, timeout=3)
                    except Exception: pass
                threading.Thread(target=_do, daemon=True).start()
                win.destroy()

        tk.Button(win, text="Submit Answer ✓", font=("Helvetica",10,"bold"),
                  bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=_submit).pack(fill="x", padx=30, pady=12, ipady=7)

    def _on_sec(self, event, detail):
        if event == "APP_WARNING":
            if _iv_hub: _iv_hub._log("APP_WARNING", detail)
            self._flash(f"🔔 {detail}", color="#4a3800", duration=2000)
            return
        if event == "KEYSTROKE":
            if _iv_hub: _iv_hub._log("KEYSTROKE_BLOCKED", detail)
            self._flash(f"🚫 {detail}", color="#1a1a4a", duration=1500)
            return
        if _iv_hub: _iv_hub.add_strike(event, detail)
        self._push_violation_remote(event, detail)
        self._flash(f"⚠ STRIKE: {detail}")

    def _flash(self, msg, color="#6a0000", duration=2500):
        try:
            w=tk.Toplevel(self.root); w.overrideredirect(True)
            w.configure(bg=color)
            w.geometry(f"520x52+{self.root.winfo_x()+180}+{self.root.winfo_y()+6}")
            tk.Label(w,text=msg,font=("Helvetica",10,"bold"),bg=color,fg="#fff",
                     wraplength=500).pack(expand=True)
            w.after(duration, w.destroy)
        except Exception: pass

    def _focus_out(self,e): self._focus_lost=time.time()
    def _focus_in(self,e):
        if self._focus_lost:
            lost=time.time()-self._focus_lost
            if lost>0.5: self._on_sec("TAB_SWITCH",f"Focus lost {lost:.1f}s")
            self._focus_lost=None

    def _check_terminate(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if _iv_hub and _iv_hub.terminated:
            self._force_end(); return
        self.root.after(500,self._check_terminate)

    def _force_end(self):
        self._sec.stop()
        global _audio_student, _voice_student
        if _audio_student: _audio_student.stop(); _audio_student = None
        if _voice_student: _voice_student.stop(); _voice_student = None
        for w in self.root.winfo_children(): w.destroy()
        self.root.configure(bg="#1a0000")
        tk.Label(self.root,text="🚫",font=("Segoe UI Emoji",60),bg="#1a0000").pack(pady=(80,0))
        tk.Label(self.root,text="INTERVIEW TERMINATED",font=("Helvetica",24,"bold"),
                 bg="#1a0000",fg="#ff4444").pack(pady=10)
        tk.Button(self.root,text="Close",font=("Helvetica",11,"bold"),bg="#333",fg="#fff",
            bd=0,relief="flat",cursor="hand2",command=self.root.destroy).pack(pady=30,ipady=8,padx=80,fill="x")

    # ── Google-Meet-style control bar helpers ────────────────────────────────
    def _make_meet_btn(self, parent, text, bg, fg, cmd, width=44, height=44, font_size=16):
        """Round icon button matching Google Meet control bar style."""
        c = tk.Canvas(parent, width=width, height=height, bg="#202124",
                      highlightthickness=0, cursor="hand2")
        r = min(width, height) // 2
        c.create_oval(2, 2, width-2, height-2, fill=bg, outline="")
        c.create_text(width//2, height//2, text=text,
                      font=("Segoe UI Emoji", font_size), fill=fg)
        c.bind("<Button-1>", lambda e: cmd())
        c.bind("<Enter>",    lambda e: c.itemconfig(1, fill=self._lighten(bg)))
        c.bind("<Leave>",    lambda e: c.itemconfig(1, fill=bg))
        c._bg = bg; c._text_id = 2
        return c

    def _lighten(self, hex_color):
        try:
            h = hex_color.lstrip("#")
            r,g,b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
            r = min(255, r+30); g = min(255, g+30); b = min(255, b+30)
            return f"#{r:02x}{g:02x}{b:02x}"
        except: return hex_color

    def _update_meet_btn(self, canvas, text, bg):
        """Update icon + background of a meet button."""
        canvas._bg = bg
        canvas.itemconfig(1, fill=bg)
        canvas.itemconfig(2, text=text)

    def _build(self):
        GM_BG   = "#202124"   # Google Meet dark background
        GM_SURF = "#292a2d"   # surface cards
        GM_SURF2= "#3c4043"   # lighter surface
        GM_TEXT = "#e8eaed"
        GM_MUTED= "#9aa0a6"
        GM_RED  = "#ea4335"
        GM_GREEN= "#34a853"

        self.root.configure(bg=GM_BG)
        self._mic_muted = False
        self._cam_off   = False
        self._chat_open = False
        self._notes_open= False
        self._start     = time.time()

        # ── Top bar ──────────────────────────────────────────────────────────
        topbar = tk.Frame(self.root, bg=GM_BG, height=56)
        topbar.pack(fill="x", side="top"); topbar.pack_propagate(False)

        tk.Label(topbar, text="ExamShield Interview",
                 font=("Helvetica",13,"bold"), bg=GM_BG, fg=GM_TEXT
                 ).pack(side="left", padx=18, pady=12)
        self.lbl_timer = tk.Label(topbar, text="00:00",
                                   font=("Helvetica",11), bg=GM_BG, fg=GM_MUTED)
        self.lbl_timer.pack(side="left", padx=4)

        # security badge (top-right)
        self.lbl_warn = tk.Label(topbar, text="● Secure  |  Warnings: 0/5",
                                  font=("Helvetica",8), bg=GM_BG, fg="#2a2a3a")
        self.lbl_warn.pack(side="right", padx=14)
        self.lbl_status = tk.Label(topbar,
                                    text="● Connected" if self.proctor_url else "● Local",
                                    font=("Helvetica",9), bg=GM_BG,
                                    fg=GM_GREEN if self.proctor_url else GM_MUTED)
        self.lbl_status.pack(side="right", padx=10)

        # ── Main area — video tiles left, optional sidebar right ─────────────
        self._body = tk.Frame(self.root, bg=GM_BG)
        self._body.pack(fill="both", expand=True, side="top")
        self._body.columnconfigure(0, weight=1)
        self._body.rowconfigure(0, weight=1)

        # Video tile area
        self._tile_area = tk.Frame(self._body, bg=GM_BG)
        self._tile_area.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self._tile_area.columnconfigure(0, weight=1)
        self._tile_area.columnconfigure(1, weight=1)
        self._tile_area.rowconfigure(0, weight=1)

        # Self tile (bottom-left in Meet style — appears as a labelled video box)
        self_tile = tk.Frame(self._tile_area, bg="#1a1a1d",
                             highlightthickness=1, highlightbackground="#3c4043")
        self_tile.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self_tile.rowconfigure(0, weight=1); self_tile.columnconfigure(0, weight=1)
        self.cam_self = tk.Label(self_tile, bg="#1a1a1d",
                                  text="Starting camera…", fg="#5f6368",
                                  font=("Helvetica",10))
        self.cam_self.grid(row=0, column=0, sticky="nsew")
        self_name = tk.Frame(self_tile, bg="#1a1a1d"); self_name.grid(row=1, column=0, sticky="ew")
        tk.Label(self_name, text="  You", font=("Helvetica",9,"bold"),
                 bg="#1a1a1d", fg=GM_TEXT).pack(side="left", padx=8, pady=4)
        self._self_mic_icon = tk.Label(self_name, text="🎤",
                                        font=("Segoe UI Emoji",10), bg="#1a1a1d", fg=GM_GREEN)
        self._self_mic_icon.pack(side="right", padx=8)

        # Interviewer tile
        pro_tile = tk.Frame(self._tile_area, bg="#1a1a1d",
                            highlightthickness=1, highlightbackground="#3c4043")
        pro_tile.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        pro_tile.rowconfigure(0, weight=1); pro_tile.columnconfigure(0, weight=1)
        self.cam_pro = tk.Label(pro_tile, bg="#1a1a1d",
                                 text="Waiting for interviewer…", fg="#5f6368",
                                 font=("Helvetica",10))
        self.cam_pro.grid(row=0, column=0, sticky="nsew")
        pro_name = tk.Frame(pro_tile, bg="#1a1a1d"); pro_name.grid(row=1, column=0, sticky="ew")
        tk.Label(pro_name, text="  Interviewer", font=("Helvetica",9,"bold"),
                 bg="#1a1a1d", fg="#ffd93d").pack(side="left", padx=8, pady=4)

        # ── Sidebar (chat / notes) — hidden by default ────────────────────────
        self._sidebar_frame = tk.Frame(self._body, bg=GM_SURF, width=300)
        # not gridded yet — shown on demand

        # Sidebar notebook-style (tab strip at top)
        self._sidebar_tab = tk.StringVar(value="chat")
        tab_bar = tk.Frame(self._sidebar_frame, bg=GM_SURF2)
        tab_bar.pack(fill="x")
        def _sw_tab(t):
            self._sidebar_tab.set(t)
            for k, (btn, frm) in self._sidebar_panels.items():
                active = (k == t)
                btn.configure(bg=GM_SURF if active else GM_SURF2,
                               fg=GM_TEXT if active else GM_MUTED)
                frm.pack_forget()
            self._sidebar_panels[t][1].pack(fill="both", expand=True)

        self._sidebar_panels = {}
        for tab_key, tab_label in [("chat","Chat"), ("notes","Notes")]:
            btn = tk.Button(tab_bar, text=tab_label, font=("Helvetica",9,"bold"),
                            bg=GM_SURF2, fg=GM_MUTED, bd=0, relief="flat",
                            cursor="hand2", padx=16, pady=8,
                            command=lambda k=tab_key: _sw_tab(k))
            btn.pack(side="left")
            panel_frame = tk.Frame(self._sidebar_frame, bg=GM_SURF)
            self._sidebar_panels[tab_key] = (btn, panel_frame)

        # Chat panel
        chat_frm = self._sidebar_panels["chat"][1]
        tk.Label(chat_frm, text="In-call messages",
                 font=("Helvetica",9,"bold"), bg=GM_SURF, fg=GM_TEXT
                 ).pack(anchor="w", padx=12, pady=(10,4))
        chat_scr = tk.Scrollbar(chat_frm); chat_scr.pack(side="right", fill="y")
        self._chat_log = tk.Text(chat_frm, font=("Helvetica",9), bg=GM_BG, fg=GM_TEXT,
                                  bd=0, relief="flat", wrap="word", state="disabled",
                                  yscrollcommand=chat_scr.set)
        self._chat_log.pack(fill="both", expand=True, padx=(8,0), pady=(0,4))
        chat_scr.configure(command=self._chat_log.yview)
        self._chat_log.tag_configure("me",   foreground="#8ab4f8")
        self._chat_log.tag_configure("them", foreground="#81c995")
        self._chat_log.tag_configure("ts",   foreground="#5f6368")
        chat_inp = tk.Frame(chat_frm, bg=GM_SURF2); chat_inp.pack(fill="x", padx=8, pady=8)
        self._chat_entry = tk.Entry(chat_inp, font=("Helvetica",9),
                                     bg="#3c4043", fg=GM_TEXT,
                                     insertbackground=GM_TEXT, bd=0, relief="flat")
        self._chat_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(8,4))
        tk.Button(chat_inp, text="Send", font=("Helvetica",8,"bold"),
                  bg="#1a73e8", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=self._send_chat_msg).pack(side="right", padx=(0,8), ipady=6, ipadx=8)
        self._chat_entry.bind("<Return>", lambda _: self._send_chat_msg())
        if not self.proctor_url:
            self._chat_entry.configure(state="disabled")
            self._append_chat_sys("Chat available when connected to proctor")

        # Notes panel
        notes_frm = self._sidebar_panels["notes"][1]
        tk.Label(notes_frm, text="Notes from interviewer",
                 font=("Helvetica",9,"bold"), bg=GM_SURF, fg=GM_TEXT
                 ).pack(anchor="w", padx=12, pady=(10,4))
        self.notes = tk.Text(notes_frm, font=("Helvetica",9), bg=GM_BG, fg=GM_TEXT,
                              bd=0, relief="flat", wrap="word", state="disabled")
        self.notes.pack(fill="both", expand=True, padx=8, pady=(0,8))

        # Activate chat tab by default
        _sw_tab("chat")
        if self.proctor_url:
            self._poll_chat()

        # ── Bottom control bar (Google Meet style) ────────────────────────────
        ctrl_bar = tk.Frame(self.root, bg="#202124", height=80)
        ctrl_bar.pack(fill="x", side="bottom"); ctrl_bar.pack_propagate(False)

        # center button cluster
        center = tk.Frame(ctrl_bar, bg=GM_BG); center.pack(expand=True)

        # Mic toggle
        def _toggle_mic():
            self._mic_muted = not self._mic_muted
            global _voice_student
            if _voice_student:
                _voice_student.toggle_mute()
            icon = "🔇" if self._mic_muted else "🎤"
            bg   = GM_RED  if self._mic_muted else GM_SURF2
            self._update_meet_btn(self._btn_mic, icon, bg)
            self._self_mic_icon.configure(
                text="🔇" if self._mic_muted else "🎤",
                fg=GM_RED if self._mic_muted else GM_GREEN)
        self._btn_mic = self._make_meet_btn(center, "🎤", GM_SURF2, GM_TEXT, _toggle_mic)
        self._btn_mic.pack(side="left", padx=8, pady=18)
        tk.Label(center, text="Mic", font=("Helvetica",7), bg=GM_BG,
                 fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Camera toggle
        def _toggle_cam():
            self._cam_off = not self._cam_off
            icon = "🚫" if self._cam_off else "📹"
            bg   = GM_RED if self._cam_off else GM_SURF2
            self._update_meet_btn(self._btn_cam, icon, bg)
            if self._cam_off:
                self.cam_self.configure(image="", text="Camera off", fg="#5f6368")
                self.cam_self.image = None
        self._btn_cam = self._make_meet_btn(center, "📹", GM_SURF2, GM_TEXT, _toggle_cam)
        self._btn_cam.pack(side="left", padx=8, pady=18)
        tk.Label(center, text="Cam", font=("Helvetica",7), bg=GM_BG,
                 fg=GM_MUTED).pack(side="left", padx=(0,8))

        # End call
        def _end_call():
            if messagebox.askyesno("Leave", "Leave the interview?", parent=self.root):
                self._sec.stop()
                if _iv_hub: _iv_hub.stop()
                global _voice_student
                if _voice_student: _voice_student.stop(); _voice_student = None
                self.root.destroy()
        btn_end = self._make_meet_btn(center, "✆", GM_RED, "#fff", _end_call, width=56, height=44, font_size=18)
        btn_end.pack(side="left", padx=16, pady=18)
        tk.Label(center, text="Leave", font=("Helvetica",7), bg=GM_BG,
                 fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Chat toggle
        def _toggle_chat():
            self._chat_open = not self._chat_open
            self._notes_open = False
            if self._chat_open:
                self._sidebar_frame.grid(row=0, column=1, sticky="nsew", padx=(0,8), pady=8)
                self._body.columnconfigure(1, weight=0, minsize=300)
                _sw_tab("chat")
                self._update_meet_btn(self._btn_chat_ctrl, "💬", "#1a73e8")
            else:
                self._sidebar_frame.grid_forget()
                self._update_meet_btn(self._btn_chat_ctrl, "💬", GM_SURF2)
        self._btn_chat_ctrl = self._make_meet_btn(center, "💬", GM_SURF2, GM_TEXT, _toggle_chat)
        self._btn_chat_ctrl.pack(side="left", padx=8, pady=18)
        tk.Label(center, text="Chat", font=("Helvetica",7), bg=GM_BG,
                 fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Notes toggle
        def _toggle_notes():
            self._notes_open = not self._notes_open
            self._chat_open  = False
            if self._notes_open:
                self._sidebar_frame.grid(row=0, column=1, sticky="nsew", padx=(0,8), pady=8)
                self._body.columnconfigure(1, weight=0, minsize=300)
                _sw_tab("notes")
                self._update_meet_btn(self._btn_notes_ctrl, "📝", "#1a73e8")
            else:
                self._sidebar_frame.grid_forget()
                self._update_meet_btn(self._btn_notes_ctrl, "📝", GM_SURF2)
        self._btn_notes_ctrl = self._make_meet_btn(center, "📝", GM_SURF2, GM_TEXT, _toggle_notes)
        self._btn_notes_ctrl.pack(side="left", padx=8, pady=18)
        tk.Label(center, text="Notes", font=("Helvetica",7), bg=GM_BG,
                 fg=GM_MUTED).pack(side="left", padx=(0,8))

    @staticmethod
    def _fast_frame_to_photo(frame, label):
        """Convert BGR numpy frame to ImageTk, skipping resize if already fits."""
        h, w = frame.shape[:2]
        lw = label.winfo_width(); lh = label.winfo_height()
        if lw > 10 and lh > 10 and (abs(lw - w) > 4 or abs(lh - h) > 4):
            scale = min(lw / w, lh / h)
            nw = max(1, int(w * scale)); nh = max(1, int(h * scale))
            frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        return ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))

    def _poll_cam(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return

        if _iv_hub:
            sf = _iv_hub.get_student_frame()
            if sf is not None:
                try:
                    img = self._fast_frame_to_photo(sf, self.cam_self)
                    self.cam_self.configure(image=img, text="")
                    self.cam_self.image = img
                except Exception as e:
                    print(f"[student cam] {e}")

            pf = _iv_hub.get_proctor_frame()
            if pf is not None:
                try:
                    img = self._fast_frame_to_photo(pf, self.cam_pro)
                    self.cam_pro.configure(image=img, text="")
                    self.cam_pro.image = img
                except Exception as e:
                    print(f"[proctor cam] {e}")
            else:
                try:
                    self.cam_pro.configure(image="",
                        text="Waiting for interviewer\nto connect their camera…",
                        fg="#3a3a5a")
                except Exception: pass

        self.root.after(16, self._poll_cam)   # ~60 fps display

    # ── Chat panel (student side — interview mode) ───────────────────────────
    def _build_chat_panel(self, parent):
        self._chat_last_id = 0
        tk.Label(parent, text="💬 Chat with Proctor",
                 font=("Helvetica",9,"bold"), bg="#161b22", fg="#58d6d6"
                 ).pack(anchor="w", padx=8, pady=(8,2))
        scr = tk.Scrollbar(parent); scr.pack(side="right", fill="y")
        self._chat_log = tk.Text(
            parent, font=("Helvetica",8), bg="#0d1117", fg="#c9d1d9",
            bd=0, relief="flat", wrap="word", state="disabled",
            yscrollcommand=scr.set)
        self._chat_log.pack(fill="both", expand=True, padx=(6,0), pady=(0,4))
        scr.configure(command=self._chat_log.yview)
        self._chat_log.tag_configure("me",   foreground="#0be881")
        self._chat_log.tag_configure("them", foreground="#ffd93d")
        self._chat_log.tag_configure("ts",   foreground="#555566")
        inp = tk.Frame(parent, bg="#161b22"); inp.pack(fill="x", padx=6, pady=(0,6))
        self._chat_entry = tk.Entry(
            inp, font=("Helvetica",9), bg="#21262d", fg="#f0f6fc",
            insertbackground="#f0f6fc", bd=0, relief="flat")
        self._chat_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,4))
        tk.Button(inp, text="▶", font=("Helvetica",9,"bold"),
                  bg="#ffd93d", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=self._send_chat_msg
                  ).pack(side="right", ipady=5, ipadx=6)
        self._chat_entry.bind("<Return>", lambda _: self._send_chat_msg())
        if self.proctor_url:
            self._poll_chat()
        else:
            self._chat_entry.configure(state="disabled")
            self._append_chat_sys("Connect to proctor to enable chat")

    def _send_chat_msg(self):
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        msg = self._chat_entry.get().strip()
        if not msg: return
        self._chat_entry.delete(0, "end")
        self._append_chat("You", msg, "me")
        def _post():
            try:
                _requests.post(f"{self.proctor_url}/send_chat", json={
                    "session_code": self.session_code,
                    "student_id":   self.sid,
                    "sender":       "student",
                    "message":      msg,
                }, timeout=2)
            except Exception: pass
        threading.Thread(target=_post, daemon=True).start()

    def _poll_chat(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not self.proctor_url or not _REQUESTS_AVAILABLE: return
        def _fetch():
            try:
                r = _requests.get(f"{self.proctor_url}/get_chat", params={
                    "session_code": self.session_code,
                    "student_id":   self.sid,
                    "since_id":     self._chat_last_id,
                }, timeout=2)
                for m in r.json().get("messages", []):
                    self._chat_last_id = max(self._chat_last_id, m["id"])
                    if m["sender"] == "proctor":
                        self.root.after(0, lambda d=m:
                            self._append_chat("Proctor", d["message"], "them"))
            except Exception: pass
        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(2000, self._poll_chat)

    def _append_chat(self, sender, text, cls):
        try:
            self._chat_log.configure(state="normal")
            ts = time.strftime("%H:%M")
            self._chat_log.insert("end", f"[{ts}] ", "ts")
            self._chat_log.insert("end", f"{sender}: {text}\n", cls)
            self._chat_log.configure(state="disabled")
            self._chat_log.see("end")
        except Exception: pass

    def _append_chat_sys(self, text):
        try:
            self._chat_log.configure(state="normal")
            self._chat_log.insert("end", f"— {text} —\n", "ts")
            self._chat_log.configure(state="disabled")
        except Exception: pass

    def _poll_notes(self):
        """Reload notes file every 3 seconds so proctor-pushed notes appear."""
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        try:
            if os.path.exists("interview_notes.txt"):
                with open("interview_notes.txt", encoding="utf-8") as f:
                    content = f.read()
                self.notes.configure(state="normal")
                self.notes.delete("1.0","end")
                self.notes.insert("1.0", content)
                self.notes.configure(state="disabled")
        except Exception:
            pass
        self.root.after(3000, self._poll_notes)

    def _tick(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        try:
            if not hasattr(self,'_start'): self._start=time.time()
            e=int(time.time()-self._start); m,s=e//60,e%60
            self.lbl_timer.configure(text=f"⏱ {m:02d}:{s:02d}")
            if _iv_hub:
                sc=_iv_hub.strike_count
                col="#1a3a1a" if sc==0 else "#6a3800" if sc<3 else "#6a0000"
                self.lbl_warn.configure(
                    text=f"● Monitoring active  |  Violations: {sc}/{InterviewHub.MAX_STRIKES}",fg=col)
        except Exception:
            pass
        self.root.after(1000,self._tick)

    def _close(self):
        if messagebox.askyesno("Quit","End interview session?"):
            self._sec.stop()
            if _iv_hub: _iv_hub.stop()
            global _audio_student
            if _audio_student: _audio_student.stop(); _audio_student = None
            self.root.destroy()

    def run(self): self.root.mainloop()

# ══════════════════════════════════════════════════════════════════════════════
#  PROCTOR WINDOW
#  — local mode  (remote_url=None): reads _hub / _iv_hub in-process globals
#  — remote mode (remote_url="http://IP:PORT"): polls Flask endpoints on
#    student's machine; pushes proctor cam via /push_proctor_frame
# ══════════════════════════════════════════════════════════════════════════════
class ProctorWindow:
    def __init__(self, proctor_id, mode="exam", is_dark=True, remote_url=None):
        self.pid        = proctor_id
        self.mode       = mode
        self.is_dark    = is_dark
        self.theme      = DARK if is_dark else LIGHT
        self.remote_url = remote_url   # None = local, "http://IP:PORT" = remote

        # ── Remote-mode shared state (populated by background threads) ──
        self._r_running    = True
        self._r_lock       = threading.Lock()
        self._r_frame      = None   # latest annotated student frame (np array)
        self._r_stats      = {}     # dict from /stats
        self._r_violations = []     # list of strings from /violations

        if remote_url:
            if not _REQUESTS_AVAILABLE:
                messagebox.showerror(
                    "Missing Dependency",
                    "Remote mode requires 'requests'.\nFix: pip install requests")
            else:
                self._start_remote_threads()

        self.root = tk.Tk()
        self.root.title(
            f"ExamShield — Proctor Dashboard  [{proctor_id}]  "
            f"{'📝 EXAM' if mode=='exam' else '🎙 INTERVIEW'}"
            + (f"  🌐 REMOTE {remote_url}" if remote_url else "  💻 LOCAL")
        )
        self.root.geometry("1200x740")
        self.root.resizable(True, True)
        self.root.minsize(1000, 620)
        self.root.configure(bg="#0d1117")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._shared_notes = ""
        self._build()
        self._poll_cam()
        self._poll_violations()

    # ── Remote background threads ─────────────────────────────────────────
    def _start_remote_threads(self):
        """Spin up daemon threads that continuously pull data from the student machine."""

        url = self.remote_url

        def _frame_thread():
            while self._r_running:
                try:
                    r = _requests.get(f"{url}/frame", timeout=1)
                    if r.status_code == 200 and r.content:
                        arr   = np.frombuffer(r.content, dtype=np.uint8)
                        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if frame is not None:
                            with self._r_lock:
                                self._r_frame = frame
                except Exception:
                    pass
                time.sleep(0.033)   # ~30 fps over network

        def _stats_thread():
            while self._r_running:
                try:
                    r = _requests.get(f"{url}/stats", timeout=2)
                    if r.status_code == 200:
                        with self._r_lock:
                            self._r_stats = r.json()
                except Exception:
                    pass
                time.sleep(0.50)

        def _violations_thread():
            while self._r_running:
                try:
                    r = _requests.get(f"{url}/violations", timeout=2)
                    if r.status_code == 200:
                        viols = r.json().get("violations", [])
                        with self._r_lock:
                            self._r_violations = viols
                except Exception:
                    pass
                time.sleep(0.80)

        for fn in [_frame_thread, _stats_thread, _violations_thread]:
            threading.Thread(target=fn, daemon=True).start()

        # Interview mode: capture proctor local cam and push to student machine
        if self.mode == "interview":
            def _pro_cam_push_thread():
                cap = cv2.VideoCapture(1)   # try cam index 1 first (external)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(0)
                while self._r_running:
                    ret, frame = cap.read()
                    if ret:
                        ok, buf = cv2.imencode(
                            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                        if ok:
                            try:
                                _requests.post(
                                    f"{url}/push_proctor_frame",
                                    data=buf.tobytes(),
                                    timeout=2)
                            except Exception:
                                pass
                    time.sleep(0.10)
                cap.release()
            threading.Thread(target=_pro_cam_push_thread, daemon=True).start()

        print(f"[ProctorWindow] Remote mode — polling {url}")

    def _stop_remote_threads(self):
        self._r_running = False

    # ── Build UI ──────────────────────────────────────────────────────────
    def _build(self):
        t = self.theme
        bar = tk.Frame(self.root, bg="#161b22", height=56)
        bar.pack(fill="x"); bar.pack_propagate(False)
        mode_icon = "📝" if self.mode=="exam" else "🎙"
        mode_col  = "#ff6b9d" if self.mode=="exam" else "#ffd93d"
        remote_tag = f"  🌐 {self.remote_url}" if self.remote_url else "  💻 LOCAL"
        tk.Label(bar,
                 text=f"👨‍🏫  Proctor Dashboard  {mode_icon}{remote_tag}",
                 font=("Helvetica",12,"bold"), bg="#161b22", fg=mode_col
                 ).pack(side="left", padx=16, pady=12)
        tk.Label(bar, text=f"│  {self.pid}",
                 font=("Helvetica",10), bg="#161b22", fg="#8b949e").pack(side="left")
        tk.Button(bar, text="⬅ Logout", font=("Helvetica",9), bd=0, relief="flat",
                  cursor="hand2", bg="#21262d", fg="#c9d1d9",
                  command=self._logout).pack(side="right", padx=12, pady=10, ipady=4)
        self.btn_tog = tk.Button(bar, font=("Helvetica",9), bd=0, relief="flat",
                                  cursor="hand2", bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"],
                                  text=f"{t['mode_icon']}  {t['mode_text']}",
                                  command=self._toggle)
        self.btn_tog.pack(side="right", padx=4, pady=10)

        main = tk.Frame(self.root, bg="#0d1117")
        main.pack(fill="both", expand=True, padx=10, pady=6)
        main.columnconfigure(0, weight=3); main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg="#0d1117")
        left.grid(row=0, column=0, sticky="nsew", padx=(0,8))
        left.rowconfigure(1, weight=1)

        if self.mode == "exam":
            tk.Label(left, text="📷  Student Camera (Live)",
                     font=("Helvetica",10,"bold"), bg="#0d1117", fg="#58d6d6"
                     ).grid(row=0, column=0, sticky="w", pady=(0,4))
            self.cam_main = tk.Label(left, bg="#0b0b13",
                                      text="Waiting for student login…",
                                      fg="#3a3a5a", font=("Helvetica",11))
            self.cam_main.grid(row=1, column=0, sticky="nsew")
            left.columnconfigure(0, weight=1)
        else:
            tk.Label(left, text="📷  Live Cameras",
                     font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d"
                     ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0,4))
            left.columnconfigure(0, weight=1); left.columnconfigure(1, weight=1)
            sc_lbl = tk.Label(left, bg="#0b0b13", text="Student Camera",
                               fg="#3a3a5a", font=("Helvetica",9))
            sc_lbl.grid(row=1, column=0, sticky="nsew", padx=(0,3))
            self.cam_main = sc_lbl
            pc_lbl = tk.Label(left, bg="#0b0b13", text="Your Camera",
                               fg="#3a3a5a", font=("Helvetica",9))
            pc_lbl.grid(row=1, column=1, sticky="nsew", padx=(3,0))
            self.cam_pro2 = pc_lbl

        sf = tk.Frame(left, bg="#0f1520", height=36)
        sf.grid(row=2, column=0, sticky="ew", pady=(4,0), columnspan=2)
        sf.pack_propagate(False)
        self.lbl_faces   = tk.Label(sf, text="Faces: —",    font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881")
        self.lbl_faces.pack(side="left", padx=10)
        self.lbl_gaze    = tk.Label(sf, text="Gaze: —",     font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881")
        self.lbl_gaze.pack(side="left", padx=10)
        self.lbl_strikes = tk.Label(sf, text="Strikes: 0/5",font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881")
        self.lbl_strikes.pack(side="left", padx=10)
        self.lbl_phone   = tk.Label(sf, text="Phone: No",   font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881")
        self.lbl_phone.pack(side="left", padx=10)
        # Remote indicator in stats bar
        if self.remote_url:
            tk.Label(sf, text=f"🌐 {self.remote_url}",
                     font=("Helvetica",7), bg="#0f1520", fg="#575fcf"
                     ).pack(side="right", padx=10)

        right = tk.Frame(main, bg="#0d1117")
        right.grid(row=0, column=1, sticky="nsew")
        style = ttk.Style(); style.theme_use("clam")
        style.configure("P.TNotebook", background="#0d1117", borderwidth=0)
        style.configure("P.TNotebook.Tab", background="#21262d", foreground="#c9d1d9",
                         padding=[10,6], font=("Helvetica",8,"bold"))
        style.map("P.TNotebook.Tab",
                  background=[("selected","#575fcf")],
                  foreground=[("selected","#ffffff")])
        nb = ttk.Notebook(right, style="P.TNotebook")
        nb.pack(fill="both", expand=True)

        vf = tk.Frame(nb, bg="#0d1117"); nb.add(vf, text="⚠ Violations")
        self._build_violations(vf)

        aqf = tk.Frame(nb, bg="#0d1117"); nb.add(aqf, text="➕ Add Q")
        self._build_add_q(aqf)

        qbf = tk.Frame(nb, bg="#0d1117"); nb.add(qbf, text="📋 Bank")
        self._build_qbank(qbf)

        rf = tk.Frame(nb, bg="#0d1117"); nb.add(rf, text="📊 Results")
        self._build_results(rf)

        if self.mode == "interview":
            nf = tk.Frame(nb, bg="#0d1117"); nb.add(nf, text="📝 Notes")
            self._build_notes(nf)

    # ── Violations panel ──────────────────────────────────────────────────
    def _build_violations(self, p):
        tk.Label(p, text="Real-time Violation Log", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#ff6b9d").pack(anchor="w", padx=8, pady=(8,4))
        scr = tk.Scrollbar(p); scr.pack(side="right", fill="y")
        self.vlog = tk.Text(p, font=("Courier",8), bg="#060610", fg="#c9d1d9",
                             bd=0, relief="flat", wrap="word",
                             yscrollcommand=scr.set, state="disabled")
        self.vlog.pack(fill="both", expand=True, padx=8, pady=(0,4))
        scr.configure(command=self.vlog.yview)
        self.vlog.tag_configure("strike",   foreground="#ff4444", font=("Courier",8,"bold"))
        self.vlog.tag_configure("warn",     foreground="#ffaa00")
        self.vlog.tag_configure("blocked",  foreground="#ff8c00")
        self.vlog.tag_configure("keystroke",foreground="#7090ff")
        self.vlog.tag_configure("appwarn",  foreground="#c8a000")
        self.vlog.tag_configure("ok",       foreground="#0be881")
        self.vlog.tag_configure("info",     foreground="#8b949e")
        tk.Button(p, text="Clear Log", font=("Helvetica",8),
                  bg="#21262d", fg="#8b949e", bd=0, relief="flat", cursor="hand2",
                  command=self._clear_log).pack(pady=(0,6))

    def _clear_log(self):
        try:
            self.vlog.configure(state="normal")
            self.vlog.delete("1.0","end")
            self.vlog.configure(state="disabled")
        except Exception: pass

    # ── Camera poll — REMOTE or LOCAL ────────────────────────────────────
    def _poll_cam(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return

        if self.remote_url:
            # ── REMOTE MODE: read from background thread cache ────────
            with self._r_lock:
                frame  = self._r_frame.copy() if self._r_frame is not None else None
                stats  = dict(self._r_stats)

            if frame is not None:
                self._show_frame(self.cam_main, frame)
            else:
                try:
                    self.cam_main.configure(
                        text="Connecting to student machine…", fg="#575fcf", image="")
                except Exception:
                    pass

            fc = stats.get("face_count",   0)
            gd = stats.get("gaze_dir",     "—")
            sc = stats.get("strike_count", 0)
            ph = stats.get("phone",        False)

            fc_col = "#0be881" if fc==1 else "#ff4444" if fc==0 else "#ffaa00"
            gd_col = "#0be881" if gd=="center" else "#ffaa00"
            sc_col = "#0be881" if sc==0 else "#ffaa00" if sc<3 else "#ff4444"
            ph_col = "#ff4444" if ph else "#0be881"
            try:
                self.lbl_faces.configure(text=f"Faces: {fc}", fg=fc_col)
                self.lbl_gaze.configure(text=f"Gaze: {gd}", fg=gd_col)
                self.lbl_strikes.configure(
                    text=f"Strikes: {sc}/{CameraHub.MAX_STRIKES}", fg=sc_col)
                self.lbl_phone.configure(
                    text=f"Phone: {'⚠ YES' if ph else 'No'}", fg=ph_col)
            except Exception:
                pass

        else:
            # ── LOCAL MODE: read in-process globals ───────────────────
            hub = _hub if self.mode=="exam" else _iv_hub
            if hub:
                try:
                    if self.mode == "exam":
                        frame = hub.get_frame()
                        if frame is not None:
                            self._show_frame(self.cam_main, frame)
                        else:
                            self.cam_main.configure(
                                text="Camera starting…", fg="#575fcf", image="")
                    else:
                        sf = hub.get_student_frame()
                        if sf is not None:
                            self._show_frame(self.cam_main, sf)
                        pf = hub.get_proctor_frame()
                        if pf is not None and hasattr(self, 'cam_pro2'):
                            self._show_frame(self.cam_pro2, pf)

                    fc = hub.face_count
                    gd = hub.gaze_dir
                    sc = hub.strike_count
                    ph = getattr(hub, 'phone_detected', False)

                    fc_col = "#0be881" if fc==1 else "#ff4444" if fc==0 else "#ffaa00"
                    gd_col = "#0be881" if gd=="center" else "#ffaa00"
                    sc_col = "#0be881" if sc==0 else "#ffaa00" if sc<3 else "#ff4444"
                    ph_col = "#ff4444" if ph else "#0be881"
                    self.lbl_faces.configure(text=f"Faces: {fc}", fg=fc_col)
                    self.lbl_gaze.configure(text=f"Gaze: {gd}", fg=gd_col)
                    self.lbl_strikes.configure(
                        text=f"Strikes: {sc}/{CameraHub.MAX_STRIKES}", fg=sc_col)
                    self.lbl_phone.configure(
                        text=f"Phone: {'⚠ YES' if ph else 'No'}", fg=ph_col)
                except Exception as e:
                    print(f"[ProctorCam] {e}")
            else:
                try:
                    self.cam_main.configure(
                        text="Waiting for student to log in…", fg="#3a3a5a", image="")
                except Exception:
                    pass

        self.root.after(16, self._poll_cam)   # ~60 fps

    def _show_frame(self, label, frame):
        try:
            h, w = frame.shape[:2]
            if h==0 or w==0: return
            # Use frame's own dimensions — hubs already downscaled to DISPLAY_W/H
            # Only resize if label is much smaller (e.g. interview split view)
            lw = label.winfo_width()
            lh = label.winfo_height()
            if lw > 10 and lh > 10 and (abs(lw - w) > 4 or abs(lh - h) > 4):
                scale = min(lw/w, lh/h)
                nw = max(1, int(w*scale))
                nh = max(1, int(h*scale))
                frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = ImageTk.PhotoImage(Image.fromarray(rgb))
            label.configure(image=img, text="")
            label.image = img   # prevent GC
        except Exception as e:
            print(f"[_show_frame] {e}")

    # ── Violations poll — REMOTE or LOCAL ────────────────────────────────
    def _poll_violations(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return

        if self.remote_url:
            # ── REMOTE: read from background thread cache ─────────────
            with self._r_lock:
                viols = list(self._r_violations)
        else:
            # ── LOCAL: read from in-process hub ──────────────────────
            hub = _hub if self.mode=="exam" else _iv_hub
            viols = list(hub.violations) if hub else []

        if viols is not None:
            try:
                self.vlog.configure(state="normal")
                self.vlog.delete("1.0","end")
                for v in viols:
                    vu = v.upper()
                    if   "STRIKE"     in vu: tag = "strike"
                    elif "TERMINATED" in vu: tag = "strike"
                    elif "WARNING"    in vu: tag = "warn"
                    elif "BLOCKED_APP"in vu: tag = "blocked"
                    elif "TAB_SWITCH" in vu: tag = "blocked"
                    elif "KEYSTROKE"  in vu: tag = "keystroke"
                    elif "APP_WARNING"in vu: tag = "appwarn"
                    elif "START"      in vu: tag = "ok"
                    else:                    tag = "info"
                    self.vlog.insert("end", v + "\n", tag)
                self.vlog.configure(state="disabled")
                self.vlog.see("end")
            except Exception as e:
                print(f"[ViolationPoll] {e}")

        self.root.after(800, self._poll_violations)

    # ── Add Question ──────────────────────────────────────────────────────
    def _build_add_q(self, parent):
        tk.Label(parent, text="Add New Question", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#0be881").pack(anchor="w", padx=10, pady=(10,4))
        canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._aq = {}

        def lbl(txt):
            tk.Label(inner, text=txt, font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9", anchor="w").pack(fill="x", padx=10, pady=(8,0))

        lbl("Question *")
        self._aq["q"] = tk.Text(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat", height=3)
        self._aq["q"].pack(fill="x", padx=10, pady=(2,0), ipady=4)

        for key, label in [("a","Option A *"),("b","Option B *"),("c","Option C *"),("d","Option D *")]:
            lbl(label)
            self._aq[key] = tk.Entry(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                      insertbackground="#f0f6fc", bd=0, relief="flat")
            self._aq[key].pack(fill="x", padx=10, pady=(2,0), ipady=6)

        lbl("Correct Answer")
        self._aq_ans = tk.StringVar(value="A")
        af = tk.Frame(inner, bg="#0d1117"); af.pack(padx=10, anchor="w", pady=(2,0))
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=self._aq_ans, value=opt,
                           font=("Helvetica",10,"bold"), bg="#0d1117", fg="#0be881",
                           selectcolor="#0d3b2e", activebackground="#0d1117").pack(side="left", padx=8)

        row2 = tk.Frame(inner, bg="#0d1117"); row2.pack(fill="x", padx=10, pady=(8,0))
        tk.Label(row2, text="Marks", font=("Helvetica",9,"bold"), bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["marks"] = tk.Entry(row2, font=("Helvetica",10), width=5,
                                      bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
                                      bd=0, relief="flat")
        self._aq["marks"].insert(0,"1"); self._aq["marks"].pack(side="left", padx=(4,16), ipady=5)
        tk.Label(row2, text="Category", font=("Helvetica",9,"bold"), bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["cat"] = tk.Entry(row2, font=("Helvetica",10), width=12,
                                    bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
                                    bd=0, relief="flat")
        self._aq["cat"].insert(0,"General"); self._aq["cat"].pack(side="left", padx=(4,0), ipady=5)

        tk.Button(inner, text="💾  Save Question", font=("Helvetica",10,"bold"),
                  bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=self._save_q).pack(fill="x", padx=10, pady=14, ipady=8)

    def _save_q(self):
        q = self._aq["q"].get("1.0","end").strip()
        a = self._aq["a"].get().strip(); b = self._aq["b"].get().strip()
        c = self._aq["c"].get().strip(); d = self._aq["d"].get().strip()
        ans = self._aq_ans.get()
        cat = self._aq["cat"].get().strip() or "General"
        try: marks = int(self._aq["marks"].get())
        except: marks = 1
        if not all([q,a,b,c,d]):
            messagebox.showerror("Error","Fill all required fields"); return
        db_add_question(q,a,b,c,d,ans,marks,cat)
        messagebox.showinfo("Saved","Question added ✓")
        self._aq["q"].delete("1.0","end")
        for k in ["a","b","c","d"]: self._aq[k].delete(0,"end")
        self._aq["marks"].delete(0,"end"); self._aq["marks"].insert(0,"1")
        self._aq["cat"].delete(0,"end");   self._aq["cat"].insert(0,"General")
        self._refresh_qbank()

    # ── Question Bank ─────────────────────────────────────────────────────
    def _build_qbank(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Question Bank", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#ffd93d").pack(side="left")
        tk.Button(top, text="↺", font=("Helvetica",10), bg="#21262d", fg="#8b949e",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._refresh_qbank).pack(side="right", ipady=2, padx=4)
        self._qb_canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr = tk.Scrollbar(parent, command=self._qb_canvas.yview)
        self._qb_canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); self._qb_canvas.pack(fill="both", expand=True, padx=4)
        self._qb_inner = tk.Frame(self._qb_canvas, bg="#0d1117")
        self._qb_canvas.create_window((0,0), window=self._qb_inner, anchor="nw")
        self._qb_inner.bind("<Configure>",
            lambda e: self._qb_canvas.configure(scrollregion=self._qb_canvas.bbox("all")))
        self._refresh_qbank()

    def _refresh_qbank(self):
        for w in self._qb_inner.winfo_children(): w.destroy()
        qs = db_get_questions()
        if not qs:
            tk.Label(self._qb_inner, text="No questions.", font=("Helvetica",9),
                     bg="#0d1117", fg="#8b949e").pack(padx=10, pady=10); return
        for q in qs:
            card = tk.Frame(self._qb_inner, bg="#161b22"); card.pack(fill="x", padx=4, pady=3)
            txt  = q[1][:60]+"…" if len(q[1])>60 else q[1]
            cat  = q[8] if len(q)>8 else "—"
            tk.Label(card, text=f"Q{q[0]}: {txt}", font=("Helvetica",9),
                     bg="#161b22", fg="#c9d1d9", anchor="w", wraplength=200, justify="left"
                     ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            info = tk.Frame(card, bg="#161b22"); info.pack(side="left")
            tk.Label(info, text=f"Ans:{q[6]}", font=("Helvetica",8,"bold"),
                     bg="#161b22", fg="#0be881").pack(anchor="e")
            tk.Label(info, text=f"{q[7]}mk {cat}", font=("Helvetica",7),
                     bg="#161b22", fg="#575fcf").pack(anchor="e")
            tk.Button(card, text="✏", font=("Helvetica",10), bg="#161b22", fg="#ffd93d",
                      bd=0, relief="flat", cursor="hand2",
                      command=lambda row=q: self._edit_q(row)).pack(side="right", padx=2)
            tk.Button(card, text="🗑", font=("Helvetica",10), bg="#161b22", fg="#ff6b9d",
                      bd=0, relief="flat", cursor="hand2",
                      command=lambda qid=q[0]: self._del_q(qid)).pack(side="right", padx=2)

    def _del_q(self, qid):
        if messagebox.askyesno("Delete", f"Delete Q{qid}?"):
            db_delete_question(qid); self._refresh_qbank()

    def _edit_q(self, row):
        win = tk.Toplevel(self.root); win.title(f"Edit Q{row[0]}")
        win.geometry("500x480"); win.configure(bg="#0d1117"); win.grab_set()
        fields = {}
        canvas = tk.Canvas(win, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(win, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def lbl(txt):
            tk.Label(inner, text=txt, font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9", anchor="w").pack(fill="x", padx=16, pady=(6,0))

        lbl("Question")
        fields["q"] = tk.Text(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                               insertbackground="#f0f6fc", bd=0, relief="flat", height=3)
        fields["q"].insert("1.0", row[1])
        fields["q"].pack(fill="x", padx=16, pady=(2,0), ipady=4)
        for i, (key, label) in enumerate([("a","Option A"),("b","Option B"),
                                           ("c","Option C"),("d","Option D")]):
            lbl(label)
            fields[key] = tk.Entry(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                    insertbackground="#f0f6fc", bd=0, relief="flat")
            fields[key].insert(0, row[2+i])
            fields[key].pack(fill="x", padx=16, pady=(2,0), ipady=6)
        lbl("Correct Answer")
        ans_var = tk.StringVar(value=row[6])
        af = tk.Frame(inner, bg="#0d1117"); af.pack(padx=16, anchor="w")
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=ans_var, value=opt,
                           font=("Helvetica",10,"bold"), bg="#0d1117", fg="#0be881",
                           selectcolor="#0d3b2e", activebackground="#0d1117"
                           ).pack(side="left", padx=6)
        row2 = tk.Frame(inner, bg="#0d1117"); row2.pack(fill="x", padx=16, pady=(6,0))
        tk.Label(row2, text="Marks", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        fields["marks"] = tk.Entry(row2, font=("Helvetica",10), width=5,
                                    bg="#161b22", fg="#f0f6fc",
                                    insertbackground="#f0f6fc", bd=0, relief="flat")
        fields["marks"].insert(0, str(row[7]) if len(row)>7 else "1")
        fields["marks"].pack(side="left", padx=(4,16), ipady=5)
        tk.Label(row2, text="Category", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        fields["cat"] = tk.Entry(row2, font=("Helvetica",10), width=12,
                                  bg="#161b22", fg="#f0f6fc",
                                  insertbackground="#f0f6fc", bd=0, relief="flat")
        fields["cat"].insert(0, row[8] if len(row)>8 else "General")
        fields["cat"].pack(side="left", padx=(4,0), ipady=5)

        def save():
            q = fields["q"].get("1.0","end").strip()
            a = fields["a"].get().strip(); b = fields["b"].get().strip()
            c = fields["c"].get().strip(); d = fields["d"].get().strip()
            ans = ans_var.get(); cat = fields["cat"].get().strip() or "General"
            try: marks = int(fields["marks"].get())
            except: marks = 1
            if not all([q,a,b,c,d]):
                messagebox.showerror("Error","Fill all fields"); return
            db_update_question(row[0],q,a,b,c,d,ans,marks,cat)
            messagebox.showinfo("Updated","Question updated ✓")
            win.destroy(); self._refresh_qbank()

        tk.Button(inner, text="💾  Update", font=("Helvetica",10,"bold"),
                  bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=save).pack(fill="x", padx=16, pady=14, ipady=8)

    # ── Results ───────────────────────────────────────────────────────────
    def _build_results(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Exam Results & Logs", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#575fcf").pack(side="left")
        tk.Button(top, text="↺", font=("Helvetica",10), bg="#21262d", fg="#8b949e",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._refresh_results).pack(side="right", ipady=2, padx=4)
        self._res_frame = tk.Frame(parent, bg="#0d1117")
        self._res_frame.pack(fill="both", expand=True, padx=4)
        self._refresh_results()

    def _refresh_results(self):
        for w in self._res_frame.winfo_children(): w.destroy()
        files = [f for f in os.listdir('.')
                 if f.endswith('_result.csv') or f.endswith('_exam_log.csv')]
        if not files:
            tk.Label(self._res_frame, text="No result files yet.", font=("Helvetica",9),
                     bg="#0d1117", fg="#8b949e").pack(padx=10, pady=10); return
        canvas = tk.Canvas(self._res_frame, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(self._res_frame, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        for fname in sorted(files):
            row = tk.Frame(inner, bg="#161b22"); row.pack(fill="x", padx=4, pady=3)
            tk.Label(row, text=fname, font=("Courier",9), bg="#161b22", fg="#c9d1d9",
                     anchor="w").pack(side="left", padx=8, pady=6, fill="x", expand=True)
            tk.Button(row, text="View", font=("Helvetica",8,"bold"),
                      bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                      command=lambda f=fname: self._view_file(f)
                      ).pack(side="right", padx=6, pady=4, ipady=2)

    def _view_file(self, fname):
        try:
            with open(fname, encoding='utf-8') as f: content = f.read()
        except: content = "Could not read file."
        win = tk.Toplevel(self.root); win.title(fname)
        win.geometry("640x420"); win.configure(bg="#0d1117")
        scr = tk.Scrollbar(win); scr.pack(side="right", fill="y")
        txt = tk.Text(win, font=("Courier",9), bg="#0b0b13", fg="#c9d1d9",
                      bd=0, wrap="none", yscrollcommand=scr.set)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", content); txt.configure(state="disabled")
        scr.configure(command=txt.yview)

    # ── Interview Notes ───────────────────────────────────────────────────
    def _build_notes(self, parent):
        tk.Label(parent, text="Interview Notes (sent to student)",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d"
                 ).pack(anchor="w", padx=8, pady=(8,4))
        self._notes_box = tk.Text(parent, font=("Helvetica",10),
                                   bg="#161b22", fg="#f0f6fc",
                                   insertbackground="#f0f6fc", bd=0, relief="flat", wrap="word")
        self._notes_box.pack(fill="both", expand=True, padx=8, pady=(0,4))
        tk.Button(parent, text="📤 Push Notes to Student",
                  font=("Helvetica",10,"bold"), bg="#ffd93d", fg="#0d1117",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._push_notes).pack(fill="x", padx=8, pady=(0,8), ipady=7)

    def _push_notes(self):
        if not hasattr(self, '_notes_box'): return
        content = self._notes_box.get("1.0","end").strip()

        if self.remote_url:
            # ── REMOTE: POST to student machine's /push_notes endpoint ──
            if not _REQUESTS_AVAILABLE:
                messagebox.showerror("Error","requests not installed"); return
            try:
                r = _requests.post(
                    f"{self.remote_url}/push_notes",
                    data=content.encode("utf-8"),
                    timeout=4)
                if r.status_code == 200:
                    messagebox.showinfo("Sent","Notes pushed to student ✓")
                else:
                    messagebox.showerror("Error", f"Server responded {r.status_code}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        else:
            # ── LOCAL: write the file directly on the same machine ──────
            try:
                with open("interview_notes.txt","w",encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("Sent","Notes pushed to student ✓")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    # ── Theme / logout / close ────────────────────────────────────────────
    def _toggle(self):
        self.is_dark = not self.is_dark
        self.theme   = DARK if self.is_dark else LIGHT
        t = self.theme
        self.btn_tog.configure(text=f"{t['mode_icon']}  {t['mode_text']}",
                                bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"])

    def _logout(self):
        self._stop_remote_threads()
        if _hub:    _hub.stop()
        if _iv_hub: _iv_hub.stop()
        self.root.destroy()
        MainLogin().run()

    def _close(self):
        self._stop_remote_threads()
        if _hub:    _hub.stop()
        if _iv_hub: _iv_hub.stop()
        self.root.destroy()

    def run(self): self.root.mainloop()

# ══════════════════════════════════════════════════════════════════════════════
#  NETWORK SERVER  v3 — Proctor is the server; students connect to proctor
#
#  Endpoints:
#    GET  /ping                      → {status, proctor, mode, session_code}
#    POST /join_request              → student requests to join {student_id, session_code}
#    GET  /join_status               → student polls {student_id, session_code} → {status}
#    GET  /frame/<student_id>        → JPEG of that student's annotated frame
#    GET  /stats/<student_id>        → JSON stats dict for that student
#    GET  /violations/<student_id>   → JSON list of violation strings
#    GET  /students                  → JSON list of accepted student_ids
#    GET  /pending_requests          → JSON list of pending student_ids
#    POST /accept_student            → proctor accepts {student_id}
#    POST /reject_student            → proctor rejects {student_id}
#    POST /push_proctor_frame        → student receives proctor cam JPEG
#    POST /push_student_frame        → student pushes their own cam frame (with stats)
#    POST /push_notes                → proctor pushes interview notes
#    GET  /runtime_questions         → student polls for runtime questions {student_id, session_code}
#    POST /push_runtime_question     → proctor pushes question {student_id, question}
#    POST /answer_runtime_question   → student answers {qid, answer}
#    POST /push_violation            → student pushes a violation event {student_id, event, detail}
# ══════════════════════════════════════════════════════════════════════════════

# Per-student data stored on the PROCTOR server (in-memory)
# _student_data[student_id] = {
#   "frame": np.array or None,
#   "stats": dict,
#   "violations": list[str],
#   "lock": threading.Lock()
# }
_student_data: dict = {}
_student_data_lock = threading.Lock()

def _get_or_create_student_slot(student_id):
    with _student_data_lock:
        if student_id not in _student_data:
            _student_data[student_id] = {
                "frame": None,
                "stats": {},
                "violations": [],
                "url": None,       # set to "http://<student_ip>:6000" on first frame push
                "lock": threading.Lock()
            }
        return _student_data[student_id]


def start_network_server(port=6000):
    """
    Start a tiny Flask HTTP server in a daemon thread.
    On the PROCTOR machine this is the main server.
    On STUDENT machines it is also started (for local same-machine mode fallback).
    """
    try:
        from flask import Flask, jsonify, Response, request
    except ImportError:
        print("[⚠] flask not installed — remote proctor disabled. Run: pip install flask")
        return

    app = Flask("ExamShieldServer")
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # ── Utility ──────────────────────────────────────────────────────────────
    def _session_ok(session_code):
        return _PROCTOR_SESSION_CODE is not None and session_code == _PROCTOR_SESSION_CODE

    # ── /ping ────────────────────────────────────────────────────────────────
    @app.route("/ping")
    def ping():
        code = _PROCTOR_SESSION_CODE or ""
        return jsonify(
            status       = "ok",
            proctor      = "active" if code else "none",
            mode         = "interview" if _iv_hub else "exam",
            session_code = code
        )

    # ── /join_request (POST) — student sends join request ───────────────────
    @app.route("/join_request", methods=["POST"])
    def join_request():
        data = request.get_json(force=True, silent=True) or {}
        student_id   = data.get("student_id", "").strip()
        session_code = data.get("session_code", "").strip()
        if not student_id or not session_code:
            return jsonify(ok=False, reason="missing fields"), 400
        # Accept if session code matches active proctor global OR exists in DB
        sess = db_get_session(session_code)
        if not sess:
            return jsonify(ok=False, reason="invalid session code"), 403
        # Check if already accepted/pending
        existing = db_get_join_request(session_code, student_id)
        if existing == "accepted":
            return jsonify(ok=True, status="accepted")
        if existing == "pending":
            return jsonify(ok=True, status="pending")
        db_add_join_request(session_code, student_id)
        _get_or_create_student_slot(student_id)
        return jsonify(ok=True, status="pending")

    # ── /join_status (GET) — student polls until accepted/rejected ───────────
    @app.route("/join_status")
    def join_status():
        student_id   = request.args.get("student_id", "")
        session_code = request.args.get("session_code", "")
        if not session_code or not db_get_session(session_code):
            return jsonify(status="invalid")
        status = db_get_join_request(session_code, student_id) or "pending"
        return jsonify(status=status)

    # ── /students (GET) — list of accepted students ──────────────────────────
    @app.route("/students")
    def students():
        code = _PROCTOR_SESSION_CODE or ""
        return jsonify(students=db_get_accepted_students(code))

    # ── /pending_requests (GET) ───────────────────────────────────────────────
    @app.route("/pending_requests")
    def pending_requests():
        code = _PROCTOR_SESSION_CODE or ""
        return jsonify(pending=db_get_pending_requests(code))

    # ── /accept_student (POST) ────────────────────────────────────────────────
    @app.route("/accept_student", methods=["POST"])
    def accept_student():
        data = request.get_json(force=True, silent=True) or {}
        sid = data.get("student_id", "")
        code = _PROCTOR_SESSION_CODE or ""
        db_set_join_status(code, sid, "accepted")
        _get_or_create_student_slot(sid)
        return jsonify(ok=True)

    # ── /reject_student (POST) ────────────────────────────────────────────────
    @app.route("/reject_student", methods=["POST"])
    def reject_student():
        data = request.get_json(force=True, silent=True) or {}
        sid = data.get("student_id", "")
        code = _PROCTOR_SESSION_CODE or ""
        db_set_join_status(code, sid, "rejected")
        return jsonify(ok=True)

    # ── /push_student_frame (POST) — student pushes their cam + stats ────────
    @app.route("/push_student_frame", methods=["POST"])
    def push_student_frame():
        student_id = request.args.get("student_id", "")
        if not student_id:
            return jsonify(ok=False, reason="no student_id"), 400
        data = request.get_data()
        if not data:
            return jsonify(ok=False, reason="no data"), 400
        arr   = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify(ok=False, reason="decode failed"), 400
        slot = _get_or_create_student_slot(student_id)
        with slot["lock"]:
            slot["frame"] = frame
            # Remember the student's server URL the first time we see a frame,
            # so the proctor can push camera frames back in interview mode.
            if not slot.get("url"):
                slot["url"] = f"http://{request.remote_addr}:6000"
        return jsonify(ok=True)

    # ── /push_student_stats (POST) — student pushes stats JSON ───────────────
    @app.route("/push_student_stats", methods=["POST"])
    def push_student_stats():
        data = request.get_json(force=True, silent=True) or {}
        sid = data.get("student_id", "")
        if not sid: return jsonify(ok=False), 400
        slot = _get_or_create_student_slot(sid)
        with slot["lock"]:
            slot["stats"] = data
        return jsonify(ok=True)

    # ── /push_violation (POST) — student pushes violation string ─────────────
    @app.route("/push_violation", methods=["POST"])
    def push_violation():
        data = request.get_json(force=True, silent=True) or {}
        sid    = data.get("student_id", "")
        event  = data.get("event", "VIOLATION")
        detail = data.get("detail", "")
        if not sid: return jsonify(ok=False), 400
        db_log_violation(sid, event, detail)
        ts  = time.strftime("%H:%M:%S")
        msg = f"[{ts}] {event}: {detail}"
        slot = _get_or_create_student_slot(sid)
        with slot["lock"]:
            slot["violations"].append(msg)
            if len(slot["violations"]) > 400:
                slot["violations"] = slot["violations"][-400:]
        return jsonify(ok=True)

    # ── /frame/<student_id> (GET) ─────────────────────────────────────────────
    @app.route("/frame/<student_id>")
    def frame(student_id):
        # Local same-machine mode
        hub = _hub or _iv_hub
        if hub and getattr(hub, 'student_id', None) == student_id:
            f = (hub.get_frame() if hasattr(hub,'get_frame')
                 else hub.get_student_frame())
        else:
            with _student_data_lock:
                slot = _student_data.get(student_id)
            if slot is None:
                return Response("no session", status=204)
            with slot["lock"]:
                f = slot["frame"].copy() if slot["frame"] is not None else None
        if f is None:
            return Response("no frame", status=204)
        ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 55])
        if not ok:
            return Response("encode error", status=500)
        return Response(buf.tobytes(), mimetype="image/jpeg")

    # ── /stats/<student_id> (GET) ──────────────────────────────────────────────
    @app.route("/stats/<student_id>")
    def stats(student_id):
        hub = _hub or _iv_hub
        if hub and getattr(hub, 'student_id', None) == student_id:
            return jsonify(
                active       = True,
                student_id   = hub.student_id,
                face_count   = hub.face_count,
                gaze_dir     = hub.gaze_dir,
                strike_count = hub.strike_count,
                phone        = getattr(hub, 'phone_detected', False),
                terminated   = hub.terminated,
                max_strikes  = CameraHub.MAX_STRIKES,
                mode         = "interview" if _iv_hub else "exam",
            )
        with _student_data_lock:
            slot = _student_data.get(student_id)
        if slot is None:
            return jsonify(active=False)
        with slot["lock"]:
            s = dict(slot["stats"])
        s["active"] = True
        return jsonify(**s)

    # ── /violations/<student_id> (GET) ─────────────────────────────────────────
    @app.route("/violations/<student_id>")
    def violations(student_id):
        hub = _hub or _iv_hub
        if hub and getattr(hub, 'student_id', None) == student_id:
            with hub._lock:
                viols = list(hub.violations)
            return jsonify(violations=viols)
        with _student_data_lock:
            slot = _student_data.get(student_id)
        if slot is None:
            return jsonify(violations=[])
        with slot["lock"]:
            viols = list(slot["violations"])
        return jsonify(violations=viols)

    # ── /push_proctor_frame (POST) — proctor cam → student (interview) ────────
    @app.route("/push_proctor_frame", methods=["POST"])
    def push_proctor_frame():
        if _iv_hub is None:
            return jsonify(ok=False, reason="no interview session"), 204
        data = request.get_data()
        if not data:
            return jsonify(ok=False, reason="no data"), 400
        arr   = np.frombuffer(data, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify(ok=False, reason="decode failed"), 400
        # Use set_proctor_frame → double-slot, downscaled, no lock contention
        _iv_hub.set_proctor_frame(frame)
        return jsonify(ok=True)

    # ── /push_notes (POST) ────────────────────────────────────────────────────
    @app.route("/push_notes", methods=["POST"])
    def push_notes():
        content = request.get_data(as_text=True)
        try:
            with open("interview_notes.txt", "w", encoding="utf-8") as f:
                f.write(content)
            return jsonify(ok=True)
        except Exception as e:
            return jsonify(ok=False, reason=str(e)), 500

    # ── Audio buffers (in-memory ring, per party) ────────────────────────────
    _audio_buf_student = []   # chunks pushed by student
    _audio_buf_proctor = []   # chunks pushed by proctor
    _audio_lock = threading.Lock()
    MAX_AUDIO_CHUNKS = 20     # keep at most ~800 ms buffered (handles network jitter)

    # ── /push_audio_student (POST) — student pushes mic PCM ──────────────────
    @app.route("/push_audio_student", methods=["POST"])
    def push_audio_student():
        data = request.get_data()
        if data:
            with _audio_lock:
                _audio_buf_student.append(data)
                if len(_audio_buf_student) > MAX_AUDIO_CHUNKS:
                    _audio_buf_student.pop(0)
        return "", 204

    # ── /push_audio_proctor (POST) — proctor pushes mic PCM ──────────────────
    @app.route("/push_audio_proctor", methods=["POST"])
    def push_audio_proctor():
        data = request.get_data()
        if data:
            with _audio_lock:
                _audio_buf_proctor.append(data)
                if len(_audio_buf_proctor) > MAX_AUDIO_CHUNKS:
                    _audio_buf_proctor.pop(0)
        return "", 204

    # ── /pull_audio_student (GET) — proctor pulls student audio ──────────────
    @app.route("/pull_audio_student")
    def pull_audio_student():
        with _audio_lock:
            if _audio_buf_student:
                chunk = _audio_buf_student.pop(0)
                return Response(chunk, mimetype="application/octet-stream")
        return Response(b"", status=204)

    # ── /pull_audio_proctor (GET) — student pulls proctor audio ──────────────
    @app.route("/pull_audio_proctor")
    def pull_audio_proctor():
        with _audio_lock:
            if _audio_buf_proctor:
                chunk = _audio_buf_proctor.pop(0)
                return Response(chunk, mimetype="application/octet-stream")
        return Response(b"", status=204)

    # ── /runtime_questions (GET) — student polls ──────────────────────────────
    @app.route("/runtime_questions")
    def get_runtime_questions():
        sid  = request.args.get("student_id", "")
        code = request.args.get("session_code", "")
        if not sid or not code:
            return jsonify(questions=[])
        rows = db_get_runtime_questions(code, sid)
        qs = [{"id": r[0], "question": r[1], "options": r[2], "sent_at": r[3],
               "answered": bool(r[4]), "answer": r[5]} for r in rows]
        return jsonify(questions=qs)

    # ── /push_runtime_question (POST) — proctor pushes question ──────────────
    @app.route("/push_runtime_question", methods=["POST"])
    def push_runtime_question():
        data = request.get_json(force=True, silent=True) or {}
        sid      = data.get("student_id", "")
        question = data.get("question", "")
        options  = data.get("options", "")
        code     = _PROCTOR_SESSION_CODE or ""
        if not sid or not question or not code:
            return jsonify(ok=False), 400
        db_push_runtime_question(code, sid, question, options)
        return jsonify(ok=True)

    # ── /answer_runtime_question (POST) — student answers ────────────────────
    @app.route("/answer_runtime_question", methods=["POST"])
    def answer_runtime_question():
        data = request.get_json(force=True, silent=True) or {}
        qid    = data.get("qid")
        answer = data.get("answer", "")
        if qid is None:
            return jsonify(ok=False), 400
        db_answer_runtime_question(int(qid), answer)
        return jsonify(ok=True)

    # ── /send_chat (POST) — either party sends a chat message ─────────────────
    @app.route("/send_chat", methods=["POST"])
    def send_chat():
        data         = request.get_json(force=True, silent=True) or {}
        session_code = data.get("session_code") or _PROCTOR_SESSION_CODE or ""
        student_id   = data.get("student_id", "").strip()
        sender       = data.get("sender", "").strip()   # "student" or "proctor"
        message      = data.get("message", "").strip()
        if not all([session_code, student_id, sender, message]):
            return jsonify(ok=False, reason="missing fields"), 400
        db_send_chat(session_code, student_id, sender, message)
        return jsonify(ok=True)

    # ── /get_chat (GET) — poll for new messages (both student and proctor) ────
    @app.route("/get_chat")
    def get_chat():
        session_code = request.args.get("session_code") or _PROCTOR_SESSION_CODE or ""
        student_id   = request.args.get("student_id", "")
        since_id     = int(request.args.get("since_id", 0))
        if not session_code or not student_id:
            return jsonify(messages=[])
        return jsonify(messages=db_get_chat(session_code, student_id, since_id))

    # ─── Legacy compatibility endpoints ────────────────────────────────────
    @app.route("/frame")
    def frame_legacy():
        hub = _hub or _iv_hub
        if hub is None:
            return Response("no session", status=204)
        f = (hub.get_frame() if hasattr(hub, 'get_frame')
             else hub.get_student_frame())
        if f is None:
            return Response("no frame", status=204)
        ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 55])
        if not ok:
            return Response("encode error", status=500)
        return Response(buf.tobytes(), mimetype="image/jpeg")

    @app.route("/violations")
    def violations_legacy():
        hub = _hub or _iv_hub
        if hub is None:
            return jsonify(violations=[])
        with hub._lock:
            viols = list(hub.violations)
        return jsonify(violations=viols)

    @app.route("/stats")
    def stats_legacy():
        hub = _hub or _iv_hub
        if hub is None:
            return jsonify(active=False)
        return jsonify(
            active       = True,
            student_id   = hub.student_id,
            face_count   = hub.face_count,
            gaze_dir     = hub.gaze_dir,
            strike_count = hub.strike_count,
            phone        = getattr(hub, 'phone_detected', False),
            terminated   = hub.terminated,
            max_strikes  = CameraHub.MAX_STRIKES,
            mode         = "interview" if _iv_hub else "exam",
        )

    def _run():
        app.run(host="0.0.0.0", port=port, threaded=True)

    threading.Thread(target=_run, daemon=True).start()

    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    global _public_url
    _public_url = None
    if _NGROK_AVAILABLE:
        try:
            tunnel = _ngrok.connect(port, "http")
            _public_url = tunnel.public_url
            if _public_url.startswith("http://"):
                _public_url = "https://" + _public_url[len("http://"):]
        except Exception as e:
            print(f"[ngrok] Could not open tunnel: {e}")

    # ── Start the WebSocket voice bridge on port 6001 ────────────────────────
    _voice_bridge_port = port + 1   # 6001 by default
    if _VOICE_BRIDGE_AVAILABLE:
        start_voice_bridge(port=_voice_bridge_port)
        # Expose the voice bridge port as a module-level for UI use
        global _VOICE_BRIDGE_PORT
        _VOICE_BRIDGE_PORT = _voice_bridge_port
    else:
        print("[⚠] voice_bridge.py missing — voice chat disabled")

    print(f"\n{'═'*60}")
    print(f"  🌐  ExamShield v3 Network Server started on port {port}")
    print(f"  🎙  Voice Bridge (WebSocket) on port {_voice_bridge_port}")
    print(f"  📡  Local IP  →  {local_ip}:{port}  (LAN only)")
    if _public_url:
        print(f"  🌍  Public URL  →  {_public_url}")
    print(f"{'═'*60}\n")
    return local_ip, port


# ══════════════════════════════════════════════════════════════════════════════
#  MULTI-STUDENT PROCTOR WINDOW  (v3)
#  — Proctor is the server; students connect using session code
#  — Shows all connected students' cameras simultaneously
#  — Join request approval, runtime question push, violations per student
# ══════════════════════════════════════════════════════════════════════════════
class MultiStudentProctorWindow:
    TILE_W   = 360
    TILE_H   = 270
    POLL_MS  = 33    # ~30 fps camera render
    STATS_MS = 500   # stats/violations refresh interval

    def __init__(self, proctor_id, mode="exam", is_dark=True):
        self.pid      = proctor_id
        self.mode     = mode
        self.is_dark  = is_dark
        self.theme    = DARK if is_dark else LIGHT

        self.root = tk.Tk()
        self.root.title(f"ExamShield v3 — Proctor Dashboard  [{proctor_id}]  "
                        f"{'📝 EXAM' if mode=='exam' else '🎙 INTERVIEW'}"
                        f"  |  Session: {_PROCTOR_SESSION_CODE}")
        self.root.geometry("1280x800")
        self.root.resizable(True, True)
        self.root.minsize(900, 600)
        self.root.configure(bg="#0d1117")
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self._student_tiles  = {}   # student_id → dict of widgets
        self._selected_sid   = None
        self._pending_notified = set()
        self._stats_counter  = 0   # used to throttle stats refresh

        # Interview-mode toggle state
        self._audio_on = True
        self._cam_on   = True
        # Proctor-cam capture thread state (interview)
        self._pro_cam_running = False
        self._pro_cap         = None

        self._build()
        self._poll_cameras()
        self._poll_join_requests()
        self._show_session_info()

        # ── Start proctor voice (WebSocket bridge) for interview mode ──────
        global _voice_proctor
        if mode == "interview" and _VOICE_BRIDGE_AVAILABLE and _SOUNDDEVICE_AVAILABLE:
            # Proctor connects to their OWN bridge server (ws://localhost:6001)
            _voice_proctor = VoiceClient(
                role="proctor",
                bridge_url=f"ws://127.0.0.1:{getattr(self, '_voice_port', 6001)}"
            )
            def _on_proctor_voice_status(connected, info):
                try:
                    color = "#0be881" if connected else "#ff4444"
                    self._lbl_voice_status.configure(
                        text=f"🎙 {'Connected' if connected else info}",
                        fg=color)
                except Exception:
                    pass
            _voice_proctor.on_status_change = _on_proctor_voice_status
            _voice_proctor.start()
            print("[Voice] Proctor voice client started (ws://localhost:6001)")
        elif mode == "interview":
            print("[Voice] ⚠ voice_bridge or sounddevice not available — audio disabled")

        # ── Start proctor camera push for interview mode ──────────────
        if mode == "interview":
            self._start_pro_cam()

    # ── Session info banner ──────────────────────────────────────────────────
    def _show_session_info(self):
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); local_ip = s.getsockname()[0]; s.close()
        except Exception:
            local_ip = "127.0.0.1"

        lines = [
            f"Session Code:  {_PROCTOR_SESSION_CODE}",
            f"Your IP (LAN): {local_ip}:6000",
        ]
        if _public_url:
            lines.append(f"Public URL:    {_public_url}")
        lines.append("Share the above with students → they run main.py and enter code to join")
        msg = "\n".join(lines)
        messagebox.showinfo("📋 Share With Students", msg, parent=self.root)

    # ── Interview audio / camera toggle helpers ──────────────────────────────
    def _toggle_audio(self):
        global _audio_proctor, _voice_proctor
        self._audio_on = not self._audio_on
        if self._audio_on:
            # Re-start voice
            if _VOICE_BRIDGE_AVAILABLE and _SOUNDDEVICE_AVAILABLE:
                if _voice_proctor:
                    _voice_proctor.stop()
                _voice_proctor = VoiceClient(
                    role="proctor",
                    bridge_url=f"ws://127.0.0.1:{getattr(self, '_voice_port', 6001)}")
                _voice_proctor.start()
            self._btn_audio_toggle.configure(text="🔊  Audio ON",  bg="#0be881", fg="#0d1117")
            self._lbl_audio_status.configure(text="🎤 Mic active", fg="#0be881")
        else:
            # Stop voice
            if _voice_proctor:
                _voice_proctor.stop()
            _voice_proctor = None
            self._btn_audio_toggle.configure(text="🔇  Audio OFF", bg="#3a3a3a", fg="#c9d1d9")
            self._lbl_audio_status.configure(text="🔇 Mic muted", fg="#8b949e")

    def _toggle_mute_proctor(self):
        """Mute/unmute proctor mic without stopping the WS connection."""
        global _voice_proctor
        if _voice_proctor:
            muted = _voice_proctor.toggle_mute()
        else:
            self._muted = not self._muted
            muted = self._muted
        if muted:
            self._btn_mute.configure(text="🔊 Unmute", bg="#ff6b9d", fg="#fff")
            self._lbl_voice_status.configure(text="🔇 Mic muted", fg="#ff6b9d")
        else:
            self._btn_mute.configure(text="🔇 Mute", bg="#21262d", fg="#c9d1d9")
            self._lbl_voice_status.configure(text="🎤 Voice: connected", fg="#0be881")

    def _on_vol_change(self, val):
        """Adjust playback volume on the proctor voice client."""
        global _voice_proctor
        try:
            v = float(val)
            if _voice_proctor:
                _voice_proctor.set_volume(v)
        except Exception:
            pass

    def _toggle_pro_cam(self):
        self._cam_on = not self._cam_on
        if self._cam_on:
            self._start_pro_cam()
            self._btn_cam_toggle.configure(text="📹  Cam ON",  bg="#0be881", fg="#0d1117")
        else:
            self._stop_pro_cam()
            self._btn_cam_toggle.configure(text="📹  Cam OFF", bg="#3a3a3a", fg="#c9d1d9")

    def _start_pro_cam(self):
        """Capture proctor webcam and push frames to the student server."""
        if self._pro_cam_running:
            return
        self._pro_cam_running = True
        def _run():
            cap = cv2.VideoCapture(1)    # prefer external cam
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            self._pro_cap = cap
            while self._pro_cam_running:
                ret, frame = cap.read()
                if ret and self._cam_on:
                    ok, buf = cv2.imencode(
                        ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                    if ok and _REQUESTS_AVAILABLE:
                        # Collect student URLs: same-machine → 127.0.0.1,
                        # remote students → their stored IP from push_student_frame.
                        # Always include localhost so same-machine mode works.
                        with _student_data_lock:
                            urls = list({
                                s.get("url", "http://127.0.0.1:6000")
                                for s in _student_data.values()
                            })
                        if not urls:
                            urls = ["http://127.0.0.1:6000"]
                        raw = buf.tobytes()
                        for url in urls:
                            try:
                                _requests.post(
                                    f"{url}/push_proctor_frame",
                                    data=raw, timeout=0.5)
                            except Exception:
                                pass
                time.sleep(0.033)   # ~30 fps
            cap.release()
            self._pro_cap = None
        threading.Thread(target=_run, daemon=True).start()

    def _stop_pro_cam(self):
        self._pro_cam_running = False

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        t = self.theme
        # ── Top bar ──
        bar = tk.Frame(self.root, bg="#161b22", height=52)
        bar.pack(fill="x"); bar.pack_propagate(False)
        mode_icon = "📝" if self.mode=="exam" else "🎙"
        mode_col  = "#ff6b9d" if self.mode=="exam" else "#ffd93d"
        tk.Label(bar, text=f"👨‍🏫  Proctor Dashboard  {mode_icon}  Session: {_PROCTOR_SESSION_CODE}",
                 font=("Helvetica",12,"bold"), bg="#161b22", fg=mode_col).pack(side="left", padx=16)
        tk.Label(bar, text=f"│  {self.pid}", font=("Helvetica",10),
                 bg="#161b22", fg="#8b949e").pack(side="left")
        tk.Button(bar, text="⬅ Logout", font=("Helvetica",9), bd=0, relief="flat",
                  cursor="hand2", bg="#21262d", fg="#c9d1d9",
                  command=self._logout).pack(side="right", padx=12, pady=10, ipady=4)
        tk.Button(bar, text="📋 Show Session Info", font=("Helvetica",9), bd=0, relief="flat",
                  cursor="hand2", bg="#575fcf", fg="#fff",
                  command=self._show_session_info).pack(side="right", padx=4, pady=10, ipady=4)

        if self.mode == "interview":
            self._build_interview_panel()
        else:
            self._build_exam_panel()

    def _build_exam_panel(self):
        """Full exam proctor panel — student grid + violations/Q bank/results tabs."""
        # ── Main paned layout ──
        paned = tk.PanedWindow(self.root, orient="horizontal", bg="#0d1117",
                               sashwidth=6, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=4, pady=4)

        # ── Left: student grid (responsive columns) ──
        left_outer = tk.Frame(paned, bg="#0d1117")
        paned.add(left_outer, minsize=400)

        hdr_row = tk.Frame(left_outer, bg="#0d1117"); hdr_row.pack(fill="x", padx=8, pady=(4,2))
        tk.Label(hdr_row, text="📷  Connected Students",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#58d6d6").pack(side="left")
        self._student_count_lbl = tk.Label(hdr_row, text="(0 online)",
                 font=("Helvetica",8), bg="#0d1117", fg="#8b949e")
        self._student_count_lbl.pack(side="left", padx=6)

        self._no_students_lbl = tk.Label(left_outer,
            text="No students connected yet.\nShare the session code so students can join.",
            font=("Helvetica",10), bg="#0d1117", fg="#3a3a5a", justify="center")
        self._no_students_lbl.pack(pady=40)

        # Scrollable canvas that holds the grid
        cam_wrap = tk.Frame(left_outer, bg="#0d1117"); cam_wrap.pack(fill="both", expand=True)
        cam_canvas = tk.Canvas(cam_wrap, bg="#0d1117", highlightthickness=0)
        scr_y = tk.Scrollbar(cam_wrap, orient="vertical", command=cam_canvas.yview)
        scr_y.pack(side="right", fill="y")
        cam_canvas.pack(side="left", fill="both", expand=True)
        cam_canvas.configure(yscrollcommand=scr_y.set)
        self._cam_inner = tk.Frame(cam_canvas, bg="#0d1117")
        self._cam_win_id = cam_canvas.create_window((0,0), window=self._cam_inner, anchor="nw")
        self._cam_inner.bind("<Configure>",
            lambda e: cam_canvas.configure(scrollregion=cam_canvas.bbox("all")))
        cam_canvas.bind("<Configure>", self._on_grid_resize)
        self._cam_canvas = cam_canvas
        self._grid_cols  = 2   # default 2-column grid

        # ── Right: detail panel (violations, Q bank, runtime Q, results) ──
        right = tk.Frame(paned, bg="#0d1117")
        paned.add(right, minsize=360)

        style = ttk.Style(); style.theme_use("clam")
        style.configure("P.TNotebook", background="#0d1117", borderwidth=0)
        style.configure("P.TNotebook.Tab", background="#21262d", foreground="#c9d1d9",
                        padding=[10,6], font=("Helvetica",8,"bold"))
        style.map("P.TNotebook.Tab",
                  background=[("selected","#575fcf")],
                  foreground=[("selected","#ffffff")])
        nb = ttk.Notebook(right, style="P.TNotebook")
        nb.pack(fill="both", expand=True)

        vf = tk.Frame(nb, bg="#0d1117"); nb.add(vf, text="⚠ Violations")
        self._build_violations_panel(vf)

        rqf = tk.Frame(nb, bg="#0d1117"); nb.add(rqf, text="📌 Runtime Q")
        self._build_runtime_q_panel(rqf)

        aqf = tk.Frame(nb, bg="#0d1117"); nb.add(aqf, text="➕ Add Q")
        self._build_add_q(aqf)

        qbf = tk.Frame(nb, bg="#0d1117"); nb.add(qbf, text="📋 Bank")
        self._build_qbank(qbf)

        rf = tk.Frame(nb, bg="#0d1117"); nb.add(rf, text="📊 Results")
        self._build_results(rf)

    # ── Google-Meet-style helpers (proctor) ──────────────────────────────────
    def _make_meet_btn_p(self, parent, text, bg, fg, cmd, width=44, height=44, font_size=16):
        c = tk.Canvas(parent, width=width, height=height, bg="#202124",
                      highlightthickness=0, cursor="hand2")
        c.create_oval(2, 2, width-2, height-2, fill=bg, outline="")
        c.create_text(width//2, height//2, text=text,
                      font=("Segoe UI Emoji", font_size), fill=fg)
        c.bind("<Button-1>", lambda e: cmd())
        c.bind("<Enter>",    lambda e: c.itemconfig(1, fill=self._lighten_p(bg)))
        c.bind("<Leave>",    lambda e: c.itemconfig(1, fill=bg))
        c._bg = bg
        return c

    def _lighten_p(self, hex_color):
        try:
            h = hex_color.lstrip("#")
            r,g,b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
            return f"#{min(255,r+30):02x}{min(255,g+30):02x}{min(255,b+30):02x}"
        except: return hex_color

    def _update_meet_btn_p(self, canvas, text, bg):
        canvas._bg = bg
        canvas.itemconfig(1, fill=bg)
        canvas.itemconfig(2, text=text)

    def _build_interview_panel(self):
        """Google Meet-style interview panel for the proctor/interviewer."""
        GM_BG    = "#202124"
        GM_SURF  = "#292a2d"
        GM_SURF2 = "#3c4043"
        GM_TEXT  = "#e8eaed"
        GM_MUTED = "#9aa0a6"
        GM_RED   = "#ea4335"
        GM_GREEN = "#34a853"

        self.root.configure(bg=GM_BG)
        self._pro_mic_muted = False
        self._pro_cam_muted = False
        self._pro_chat_open = False
        self._pro_ppl_open  = False

        # ── Body: video grid + optional sidebar ──────────────────────────────
        body = tk.Frame(self.root, bg=GM_BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self._pro_body = body

        # Scrollable candidate tile area
        left_outer = tk.Frame(body, bg=GM_BG)
        left_outer.grid(row=0, column=0, sticky="nsew")
        left_outer.columnconfigure(0, weight=1); left_outer.rowconfigure(1, weight=1)

        # Top status strip
        status_strip = tk.Frame(left_outer, bg="#2d2e30", height=32)
        status_strip.grid(row=0, column=0, sticky="ew"); status_strip.pack_propagate(False)
        self._student_count_lbl = tk.Label(status_strip, text="0 participants",
                 font=("Helvetica",9), bg="#2d2e30", fg=GM_MUTED)
        self._student_count_lbl.pack(side="left", padx=12, pady=6)
        self._no_students_lbl = tk.Label(status_strip,
                text="Waiting for candidates to join…",
                font=("Helvetica",9), bg="#2d2e30", fg=GM_MUTED)
        self._no_students_lbl.pack(side="left", padx=4)

        cam_wrap = tk.Frame(left_outer, bg=GM_BG)
        cam_wrap.grid(row=1, column=0, sticky="nsew")
        cam_canvas = tk.Canvas(cam_wrap, bg=GM_BG, highlightthickness=0)
        scr_y = tk.Scrollbar(cam_wrap, orient="vertical", command=cam_canvas.yview)
        scr_y.pack(side="right", fill="y")
        cam_canvas.pack(side="left", fill="both", expand=True)
        cam_canvas.configure(yscrollcommand=scr_y.set)
        self._cam_inner = tk.Frame(cam_canvas, bg=GM_BG)
        self._cam_win_id = cam_canvas.create_window((0,0), window=self._cam_inner, anchor="nw")
        self._cam_inner.bind("<Configure>",
            lambda e: cam_canvas.configure(scrollregion=cam_canvas.bbox("all")))
        cam_canvas.bind("<Configure>", self._on_grid_resize)
        self._cam_canvas = cam_canvas
        self._grid_cols  = 2

        # ── Sidebar (hidden by default) ───────────────────────────────────────
        self._pro_sidebar = tk.Frame(body, bg=GM_SURF, width=320)
        self._pro_sidebar_panels = {}

        tab_bar_s = tk.Frame(self._pro_sidebar, bg=GM_SURF2)
        tab_bar_s.pack(fill="x")

        def _sw_pro_tab(t):
            for k, (btn, frm) in self._pro_sidebar_panels.items():
                active = (k == t)
                btn.configure(bg=GM_SURF if active else GM_SURF2,
                               fg=GM_TEXT if active else GM_MUTED)
                frm.pack_forget()
            self._pro_sidebar_panels[t][1].pack(fill="both", expand=True)

        for tab_key, tab_label in [("chat","Chat"), ("participants","Participants"), ("question","Ask")]:
            btn = tk.Button(tab_bar_s, text=tab_label, font=("Helvetica",9,"bold"),
                            bg=GM_SURF2, fg=GM_MUTED, bd=0, relief="flat",
                            cursor="hand2", padx=10, pady=8,
                            command=lambda k=tab_key: _sw_pro_tab(k))
            btn.pack(side="left")
            pf = tk.Frame(self._pro_sidebar, bg=GM_SURF)
            self._pro_sidebar_panels[tab_key] = (btn, pf)

        # Chat panel (proctor → student)
        chat_frm = self._pro_sidebar_panels["chat"][1]
        tk.Label(chat_frm, text="In-call messages",
                 font=("Helvetica",9,"bold"), bg=GM_SURF, fg=GM_TEXT
                 ).pack(anchor="w", padx=12, pady=(10,4))

        # Per-student selector
        cand_row = tk.Frame(chat_frm, bg=GM_SURF); cand_row.pack(fill="x", padx=8, pady=(0,4))
        tk.Label(cand_row, text="To:", font=("Helvetica",8),
                 bg=GM_SURF, fg=GM_MUTED).pack(side="left")
        self._chat_target_var = tk.StringVar(value="(select candidate)")
        self._chat_target_menu = tk.OptionMenu(cand_row, self._chat_target_var, "(select candidate)")
        self._chat_target_menu.configure(bg=GM_SURF2, fg=GM_TEXT, font=("Helvetica",8),
                                          bd=0, relief="flat", highlightthickness=0,
                                          activebackground=GM_SURF2)
        self._chat_target_menu["menu"].configure(bg=GM_SURF2, fg=GM_TEXT)
        self._chat_target_menu.pack(side="left", padx=(6,0), fill="x", expand=True)

        cscr = tk.Scrollbar(chat_frm); cscr.pack(side="right", fill="y")
        self._pro_chat_log = tk.Text(chat_frm, font=("Helvetica",9), bg=GM_BG, fg=GM_TEXT,
                                      bd=0, relief="flat", wrap="word", state="disabled",
                                      yscrollcommand=cscr.set)
        self._pro_chat_log.pack(fill="both", expand=True, padx=(8,0), pady=(0,4))
        cscr.configure(command=self._pro_chat_log.yview)
        self._pro_chat_log.tag_configure("me",   foreground="#8ab4f8")
        self._pro_chat_log.tag_configure("them", foreground="#81c995")
        self._pro_chat_log.tag_configure("ts",   foreground="#5f6368")
        cinp = tk.Frame(chat_frm, bg=GM_SURF2); cinp.pack(fill="x", padx=8, pady=8)
        self._pro_chat_entry = tk.Entry(cinp, font=("Helvetica",9),
                                         bg="#3c4043", fg=GM_TEXT,
                                         insertbackground=GM_TEXT, bd=0, relief="flat")
        self._pro_chat_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(8,4))

        def _send_pro_chat():
            sid = self._chat_target_var.get()
            msg = self._pro_chat_entry.get().strip()
            if not msg or sid in ("(select candidate)", ""): return
            self._pro_chat_entry.delete(0, "end")
            self._append_pro_chat("You", msg, "me")
            def _post():
                try:
                    _requests.post("http://127.0.0.1:6000/send_chat", json={
                        "session_code": _PROCTOR_SESSION_CODE or "",
                        "student_id":   sid, "sender": "proctor", "message": msg,
                    }, timeout=2)
                except Exception: pass
            threading.Thread(target=_post, daemon=True).start()
        tk.Button(cinp, text="Send", font=("Helvetica",8,"bold"),
                  bg="#1a73e8", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=_send_pro_chat).pack(side="right", padx=(0,8), ipady=6, ipadx=8)
        self._pro_chat_entry.bind("<Return>", lambda _: _send_pro_chat())
        self._poll_pro_chat()

        # Participants panel
        ppl_frm = self._pro_sidebar_panels["participants"][1]
        tk.Label(ppl_frm, text="Participants",
                 font=("Helvetica",10,"bold"), bg=GM_SURF, fg=GM_TEXT
                 ).pack(anchor="w", padx=12, pady=(12,6))
        ppl_scr = tk.Scrollbar(ppl_frm); ppl_scr.pack(side="right", fill="y")
        self._ppl_log = tk.Text(ppl_frm, font=("Helvetica",9), bg=GM_BG, fg=GM_TEXT,
                                 bd=0, relief="flat", wrap="word", state="disabled",
                                 yscrollcommand=ppl_scr.set)
        self._ppl_log.pack(fill="both", expand=True, padx=(8,0), pady=(0,8))
        ppl_scr.configure(command=self._ppl_log.yview)

        # Notes push
        notes_frm_p = self._pro_sidebar_panels["participants"][1]  # reuse same ref kept separate
        # Question / Ask panel
        ask_frm = self._pro_sidebar_panels["question"][1]
        self._build_runtime_q_panel_interview(ask_frm)

        _sw_pro_tab("chat")

        # ── Bottom control bar ────────────────────────────────────────────────
        ctrl_bar = tk.Frame(self.root, bg=GM_BG, height=80)
        ctrl_bar.pack(fill="x", side="bottom"); ctrl_bar.pack_propagate(False)
        center_p = tk.Frame(ctrl_bar, bg=GM_BG); center_p.pack(expand=True)

        # Mic
        def _toggle_pro_mic():
            self._pro_mic_muted = not self._pro_mic_muted
            global _voice_proctor
            if _voice_proctor: _voice_proctor.toggle_mute()
            icon = "🔇" if self._pro_mic_muted else "🎤"
            bg   = GM_RED if self._pro_mic_muted else GM_SURF2
            self._update_meet_btn_p(self._pbtn_mic, icon, bg)
            self._lbl_voice_status.configure(
                text="🔇 Muted" if self._pro_mic_muted else "🎤 Live",
                fg=GM_RED if self._pro_mic_muted else GM_GREEN)
        self._pbtn_mic = self._make_meet_btn_p(center_p, "🎤", GM_SURF2, GM_TEXT, _toggle_pro_mic)
        self._pbtn_mic.pack(side="left", padx=8, pady=18)
        tk.Label(center_p, text="Mic", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Camera
        def _toggle_pro_cam_btn():
            self._pro_cam_muted = not self._pro_cam_muted
            if self._pro_cam_muted:
                self._stop_pro_cam()
                self._update_meet_btn_p(self._pbtn_cam, "🚫", GM_RED)
                self._btn_cam_toggle = self._pbtn_cam  # keep ref in sync
            else:
                self._start_pro_cam()
                self._update_meet_btn_p(self._pbtn_cam, "📹", GM_SURF2)
        self._pbtn_cam = self._make_meet_btn_p(center_p, "📹", GM_SURF2, GM_TEXT, _toggle_pro_cam_btn)
        self._pbtn_cam.pack(side="left", padx=8, pady=18)
        # keep legacy refs so _toggle_audio etc. still work
        self._btn_cam_toggle = self._pbtn_cam
        tk.Label(center_p, text="Cam", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # End call
        def _end_call_pro():
            from tkinter import messagebox as _mb
            if _mb.askyesno("Leave", "End this interview session?", parent=self.root):
                self._logout()
        btn_end_p = self._make_meet_btn_p(center_p, "✆", GM_RED, "#fff", _end_call_pro, width=56, height=44, font_size=18)
        btn_end_p.pack(side="left", padx=16, pady=18)
        tk.Label(center_p, text="End", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Chat toggle
        def _toggle_pro_chat():
            self._pro_chat_open = not self._pro_chat_open
            self._pro_ppl_open  = False
            if self._pro_chat_open:
                self._pro_sidebar.grid(row=0, column=1, sticky="nsew", padx=(0,8), pady=0)
                body.columnconfigure(1, weight=0, minsize=320)
                _sw_pro_tab("chat")
                self._update_meet_btn_p(self._pbtn_chat, "💬", "#1a73e8")
            else:
                self._pro_sidebar.grid_forget()
                self._update_meet_btn_p(self._pbtn_chat, "💬", GM_SURF2)
        self._pbtn_chat = self._make_meet_btn_p(center_p, "💬", GM_SURF2, GM_TEXT, _toggle_pro_chat)
        self._pbtn_chat.pack(side="left", padx=8, pady=18)
        tk.Label(center_p, text="Chat", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Participants toggle
        def _toggle_ppl():
            self._pro_ppl_open  = not self._pro_ppl_open
            self._pro_chat_open = False
            if self._pro_ppl_open:
                self._pro_sidebar.grid(row=0, column=1, sticky="nsew", padx=(0,8), pady=0)
                body.columnconfigure(1, weight=0, minsize=320)
                _sw_pro_tab("participants")
                self._update_meet_btn_p(self._pbtn_ppl, "👥", "#1a73e8")
            else:
                self._pro_sidebar.grid_forget()
                self._update_meet_btn_p(self._pbtn_ppl, "👥", GM_SURF2)
        self._pbtn_ppl = self._make_meet_btn_p(center_p, "👥", GM_SURF2, GM_TEXT, _toggle_ppl)
        self._pbtn_ppl.pack(side="left", padx=8, pady=18)
        tk.Label(center_p, text="People", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Ask question toggle
        def _toggle_ask():
            # opens sidebar to "question" tab
            self._pro_chat_open = False; self._pro_ppl_open = False
            self._pro_sidebar.grid(row=0, column=1, sticky="nsew", padx=(0,8), pady=0)
            body.columnconfigure(1, weight=0, minsize=320)
            _sw_pro_tab("question")
            self._update_meet_btn_p(self._pbtn_ask, "❓", "#1a73e8")
        self._pbtn_ask = self._make_meet_btn_p(center_p, "❓", GM_SURF2, GM_TEXT, _toggle_ask)
        self._pbtn_ask.pack(side="left", padx=8, pady=18)
        tk.Label(center_p, text="Ask", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Notes push
        def _open_notes_push():
            win = tk.Toplevel(self.root)
            win.title("Push Notes to Candidates")
            win.geometry("440x280"); win.configure(bg="#202124")
            win.attributes("-topmost", True)
            tk.Label(win, text="Notes / Feedback for candidates",
                     font=("Helvetica",10,"bold"), bg="#202124", fg=GM_TEXT).pack(anchor="w", padx=12, pady=(12,4))
            nb = tk.Text(win, font=("Helvetica",10), bg="#292a2d", fg=GM_TEXT,
                         insertbackground=GM_TEXT, bd=0, relief="flat", wrap="word")
            nb.pack(fill="both", expand=True, padx=12, pady=(0,4))
            def _push():
                content = nb.get("1.0","end").strip()
                try:
                    with open("interview_notes.txt","w",encoding="utf-8") as f: f.write(content)
                    from tkinter import messagebox as _mb
                    _mb.showinfo("Sent","Notes pushed to candidate(s) ✓", parent=win)
                    win.destroy()
                except Exception as e:
                    from tkinter import messagebox as _mb
                    _mb.showerror("Error", str(e), parent=win)
            tk.Button(win, text="Push Notes to All Candidates",
                      font=("Helvetica",10,"bold"), bg="#1a73e8", fg="#fff",
                      bd=0, relief="flat", cursor="hand2",
                      command=_push).pack(fill="x", padx=12, pady=(0,12), ipady=8)
        btn_notes_p = self._make_meet_btn_p(center_p, "📝", GM_SURF2, GM_TEXT, _open_notes_push)
        btn_notes_p.pack(side="left", padx=8, pady=18)
        tk.Label(center_p, text="Notes", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack(side="left", padx=(0,8))

        # Volume
        vol_frame = tk.Frame(ctrl_bar, bg=GM_BG); vol_frame.pack(side="right", padx=16)
        tk.Label(vol_frame, text="Vol", font=("Helvetica",7), bg=GM_BG, fg=GM_MUTED).pack()
        self._vol_var = tk.DoubleVar(value=1.0)
        tk.Scale(vol_frame, from_=0.0, to=3.0, resolution=0.1, orient="vertical",
                 variable=self._vol_var, bg=GM_BG, fg=GM_MUTED, troughcolor=GM_SURF2,
                 highlightthickness=0, bd=0, length=60, showvalue=False,
                 command=self._on_vol_change).pack()

        # Voice status label (bottom-left)
        vs_text = "🎤 Live" if (_VOICE_BRIDGE_AVAILABLE and _SOUNDDEVICE_AVAILABLE) else "⚠ Voice N/A"
        vs_col  = GM_GREEN if (_VOICE_BRIDGE_AVAILABLE and _SOUNDDEVICE_AVAILABLE) else "#ff6b9d"
        self._lbl_voice_status = tk.Label(ctrl_bar, text=vs_text,
                                           font=("Helvetica",8), bg=GM_BG, fg=vs_col)
        self._lbl_voice_status.pack(side="left", padx=14)
        # keep legacy refs alive
        self._btn_audio_toggle = self._pbtn_mic
        self._btn_mute         = self._pbtn_mic
        self._muted            = False
        self._lbl_audio_status = self._lbl_voice_status

    def _append_pro_chat(self, sender, text, cls):
        try:
            self._pro_chat_log.configure(state="normal")
            ts = time.strftime("%H:%M")
            self._pro_chat_log.insert("end", f"[{ts}] ", "ts")
            self._pro_chat_log.insert("end", f"{sender}: {text}\n", cls)
            self._pro_chat_log.configure(state="disabled")
            self._pro_chat_log.see("end")
        except Exception: pass

    def _poll_pro_chat(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        if not _REQUESTS_AVAILABLE: return
        sid = self._chat_target_var.get() if hasattr(self, "_chat_target_var") else ""
        if sid in ("(select candidate)", ""):
            self.root.after(3000, self._poll_pro_chat); return
        def _fetch():
            try:
                r = _requests.get("http://127.0.0.1:6000/get_chat", params={
                    "session_code": _PROCTOR_SESSION_CODE or "",
                    "student_id":   sid,
                    "since_id":     getattr(self, "_pro_chat_last_id", 0),
                }, timeout=2)
                for m in r.json().get("messages", []):
                    self._pro_chat_last_id = max(getattr(self,"_pro_chat_last_id",0), m["id"])
                    if m["sender"] == "student":
                        self.root.after(0, lambda d=m:
                            self._append_pro_chat(sid, d["message"], "them"))
            except Exception: pass
        self._pro_chat_last_id = getattr(self, "_pro_chat_last_id", 0)
        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(2000, self._poll_pro_chat)

    def _update_chat_candidate_menu(self):
        """Refresh the chat-to dropdown with current student list."""
        try:
            menu = self._chat_target_menu["menu"]
            menu.delete(0, "end")
            for sid in self._student_tiles.keys():
                menu.add_command(label=sid,
                    command=lambda s=sid: self._chat_target_var.set(s))
        except Exception: pass

    def _update_participants_panel(self):
        """Refresh the participants text log."""
        try:
            self._ppl_log.configure(state="normal")
            self._ppl_log.delete("1.0","end")
            for sid in self._student_tiles.keys():
                self._ppl_log.insert("end", f"  👤  {sid}\n")
            self._ppl_log.configure(state="disabled")
        except Exception: pass
        """Compact runtime-question section for the interview sidebar."""
        tk.Label(parent, text="📌  Send Question to Candidate",
                 font=("Helvetica",10,"bold"), bg="#161b22", fg="#ffd93d"
                 ).pack(anchor="w", padx=10, pady=(8,2))

        sel_frame = tk.Frame(parent, bg="#161b22"); sel_frame.pack(fill="x", padx=10, pady=(0,4))
        tk.Label(sel_frame, text="To:", font=("Helvetica",9,"bold"),
                 bg="#161b22", fg="#c9d1d9").pack(side="left")
        self._rq_target_var = tk.StringVar(value="(select candidate)")
        self._rq_target_menu = tk.OptionMenu(sel_frame, self._rq_target_var, "(select candidate)")
        self._rq_target_menu.configure(bg="#21262d", fg="#f0f6fc", font=("Helvetica",9),
                                        bd=0, relief="flat", activebackground="#30363d",
                                        highlightthickness=0)
        self._rq_target_menu["menu"].configure(bg="#21262d", fg="#f0f6fc")
        self._rq_target_menu.pack(side="left", padx=(6,0), fill="x", expand=True)
        tk.Button(sel_frame, text="All", font=("Helvetica",8,"bold"),
                  bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=lambda: self._rq_target_var.set("ALL")).pack(side="right", ipady=2, padx=2)

        tk.Label(parent, text="Question:", font=("Helvetica",9,"bold"),
                 bg="#161b22", fg="#c9d1d9").pack(anchor="w", padx=10, pady=(4,0))
        self._rq_text = tk.Text(parent, font=("Helvetica",10), bg="#21262d", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat", height=3)
        self._rq_text.pack(fill="x", padx=10, pady=(2,4), ipady=4)

        tk.Label(parent, text="Options A–D (leave blank = open-ended):",
                 font=("Helvetica",8), bg="#161b22", fg="#8b949e").pack(anchor="w", padx=10)
        self._rq_opts = {}
        for letter in ("A","B","C","D"):
            row = tk.Frame(parent, bg="#161b22"); row.pack(fill="x", padx=10, pady=1)
            tk.Label(row, text=f"{letter}:", width=2, font=("Helvetica",9,"bold"),
                     bg="#161b22", fg="#ffd93d").pack(side="left")
            ent = tk.Entry(row, font=("Helvetica",9), bg="#21262d", fg="#f0f6fc",
                           insertbackground="#f0f6fc", bd=0, relief="flat")
            ent.pack(side="left", fill="x", expand=True, ipady=3, padx=(4,0))
            self._rq_opts[letter] = ent

        tk.Button(parent, text="📤  Send Question",
                  font=("Helvetica",10,"bold"), bg="#ffd93d", fg="#0d1117",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._push_runtime_question).pack(fill="x", padx=10, pady=(8,4), ipady=6)

        sep2 = tk.Frame(parent, bg="#30363d", height=1); sep2.pack(fill="x", padx=10, pady=4)

        tk.Label(parent, text="Candidate Answers:",
                 font=("Helvetica",9,"bold"), bg="#161b22", fg="#0be881").pack(anchor="w", padx=10, pady=(2,0))
        scr = tk.Scrollbar(parent); scr.pack(side="right", fill="y")
        self._rq_ans_log = tk.Text(parent, font=("Courier",8), bg="#0d1117", fg="#c9d1d9",
                                    bd=0, relief="flat", wrap="word",
                                    yscrollcommand=scr.set, state="disabled")
        self._rq_ans_log.pack(fill="both", expand=True, padx=(10,0), pady=(0,8))
        scr.configure(command=self._rq_ans_log.yview)
        self._poll_runtime_answers()

    # ── Grid layout helpers ───────────────────────────────────────────────────
    def _on_grid_resize(self, event):
        """Re-flow grid columns when canvas width changes."""
        try:
            self._cam_canvas.itemconfig(self._cam_win_id, width=event.width)
            # Determine how many columns fit
            tile_min_w = 260
            cols = max(1, event.width // tile_min_w)
            if cols != self._grid_cols:
                self._grid_cols = cols
                self._reflow_grid()
        except Exception: pass

    def _reflow_grid(self):
        """Re-grid all existing tiles based on current column count."""
        sids = list(self._student_tiles.keys())
        for i, sid in enumerate(sids):
            tile = self._student_tiles[sid]
            r, c = divmod(i, self._grid_cols)
            tile["card"].grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        for c in range(self._grid_cols):
            self._cam_inner.columnconfigure(c, weight=1)
        try:
            self._student_count_lbl.configure(text=f"({len(sids)} online)")
        except Exception: pass

    # ── Add/update student tile ───────────────────────────────────────────────
    def _add_student_tile(self, sid):
        if sid in self._student_tiles:
            return
        self._no_students_lbl.pack_forget()

        idx = len(self._student_tiles)
        r, c = divmod(idx, self._grid_cols)

        # Detect if we're in interview mode (Meet style) or exam mode
        _is_meet = self.mode == "interview"
        _card_bg   = "#1a1a1d" if _is_meet else "#161b22"
        _border_c  = "#3c4043" if _is_meet else "#30363d"
        _hdr_bg    = "#1a1a1d" if _is_meet else "#161b22"
        _name_col  = "#e8eaed" if _is_meet else "#58d6d6"
        _stats_bg  = "#202124" if _is_meet else "#0f1520"

        card = tk.Frame(self._cam_inner, bg=_card_bg, bd=0,
                        highlightthickness=2, highlightbackground=_border_c)
        card.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        for col in range(self._grid_cols):
            self._cam_inner.columnconfigure(col, weight=1)

        # ── Header row ──
        hdr = tk.Frame(card, bg=_hdr_bg); hdr.pack(fill="x", padx=4, pady=(4,0))
        name_lbl = tk.Label(hdr, text=f"👤 {sid}", font=("Helvetica",9,"bold"),
                            bg=_hdr_bg, fg=_name_col)
        name_lbl.pack(side="left")
        # Expand button — opens full modal for this student
        tk.Button(hdr, text="⛶", font=("Helvetica",10), bg=_hdr_bg, fg=_name_col,
                  bd=0, relief="flat", cursor="hand2", padx=2,
                  command=lambda s=sid: self._open_student_modal(s)).pack(side="right")
        tk.Button(hdr, text="💬", font=("Helvetica",9), bg=_hdr_bg, fg="#ff6b9d",
                  bd=0, relief="flat", cursor="hand2", padx=2,
                  command=lambda s=sid: self._open_student_modal(s)).pack(side="right")
        tk.Button(hdr, text="Select", font=("Helvetica",8),
                  bg=_hdr_bg, fg="#c9d1d9", bd=0, relief="flat", cursor="hand2",
                  command=lambda s=sid: self._select_student(s)).pack(side="right", padx=2)
        kick_btn = tk.Button(hdr, text="✕", font=("Helvetica",8),
                             bg="#3a0000", fg="#ff6b6b", bd=0, relief="flat", cursor="hand2",
                             command=lambda s=sid: self._kick_student(s))
        kick_btn.pack(side="right", padx=2)

        # ── Camera feed — fills the upper half of the tile ──
        cam_frame = tk.Frame(card, bg=_card_bg, height=200)
        cam_frame.pack(fill="x", padx=4, pady=(2,0))
        cam_frame.pack_propagate(False)   # enforce the 200px height
        cam_lbl = tk.Label(cam_frame, bg=_card_bg,
                           text="Waiting for camera…", fg="#5f6368" if _is_meet else "#3a3a5a",
                           font=("Helvetica",8))
        cam_lbl.pack(fill="both", expand=True)

        # ── Stats row ──
        stats_row = tk.Frame(card, bg=_stats_bg); stats_row.pack(fill="x")
        faces_lbl   = tk.Label(stats_row, text="Faces:—", font=("Helvetica",7,"bold"),
                               bg=_stats_bg, fg="#0be881")
        faces_lbl.pack(side="left", padx=4)
        gaze_lbl    = tk.Label(stats_row, text="Gaze:—", font=("Helvetica",7,"bold"),
                               bg=_stats_bg, fg="#0be881")
        gaze_lbl.pack(side="left", padx=4)
        strikes_lbl = tk.Label(stats_row, text="Strikes:0", font=("Helvetica",7,"bold"),
                               bg=_stats_bg, fg="#0be881")
        strikes_lbl.pack(side="left", padx=4)

        self._student_tiles[sid] = {
            "card": card, "cam_lbl": cam_lbl,
            "faces_lbl": faces_lbl, "gaze_lbl": gaze_lbl, "strikes_lbl": strikes_lbl,
            "name_lbl": name_lbl
        }
        try:
            n = len(self._student_tiles)
            self._student_count_lbl.configure(text=f"{n} participant{'s' if n!=1 else ''}")
        except Exception: pass
        # Update chat candidate menu and participants panel
        try: self._update_rq_student_menu()
        except Exception: pass
        try: self._update_chat_candidate_menu()
        except Exception: pass
        try: self._update_participants_panel()
        except Exception: pass

    def _open_student_modal(self, sid):
        """Open a full-screen modal showing this student's camera feed + violations + chat."""
        win = tk.Toplevel(self.root)
        win.title(f"📷 Student: {sid}")
        win.geometry("1100x640")
        win.configure(bg="#0d1117")
        win.attributes("-topmost", True)

        # Top bar
        bar = tk.Frame(win, bg="#161b22", height=44); bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text=f"👤  {sid}  — Live View",
                 font=("Helvetica",12,"bold"), bg="#161b22", fg="#58d6d6").pack(side="left", padx=12)
        tk.Button(bar, text="✕ Close", font=("Helvetica",9), bg="#21262d", fg="#c9d1d9",
                  bd=0, relief="flat", cursor="hand2",
                  command=win.destroy).pack(side="right", padx=10, pady=6, ipady=3)

        main = tk.Frame(win, bg="#0d1117"); main.pack(fill="both", expand=True, padx=8, pady=6)
        main.columnconfigure(0, weight=3); main.columnconfigure(1, weight=2); main.columnconfigure(2, weight=2)
        main.rowconfigure(0, weight=1)

        # Camera
        cam = tk.Label(main, bg="#0b0b13", text="Loading…", fg="#3a3a5a", font=("Helvetica",10))
        cam.grid(row=0, column=0, sticky="nsew", padx=(0,4))

        # Violations log
        vf = tk.Frame(main, bg="#0d1117"); vf.grid(row=0, column=1, sticky="nsew", padx=(0,4))
        tk.Label(vf, text="⚠ Violations", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#ff6b9d").pack(anchor="w", padx=6, pady=(4,2))
        scr = tk.Scrollbar(vf); scr.pack(side="right", fill="y")
        vlog = tk.Text(vf, font=("Courier",8), bg="#060610", fg="#c9d1d9",
                       bd=0, relief="flat", wrap="word",
                       yscrollcommand=scr.set, state="disabled")
        vlog.pack(fill="both", expand=True, padx=(6,0))
        scr.configure(command=vlog.yview)
        vlog.tag_configure("strike",   foreground="#ff4444", font=("Courier",8,"bold"))
        vlog.tag_configure("warn",     foreground="#ffaa00")
        vlog.tag_configure("blocked",  foreground="#ff8c00")
        vlog.tag_configure("ok",       foreground="#0be881")
        vlog.tag_configure("info",     foreground="#8b949e")

        # ── Chat panel (proctor side) ─────────────────────────────────────
        cf = tk.Frame(main, bg="#161b22"); cf.grid(row=0, column=2, sticky="nsew")
        tk.Label(cf, text="💬 Chat with Student",
                 font=("Helvetica",9,"bold"), bg="#161b22", fg="#ff6b9d"
                 ).pack(anchor="w", padx=8, pady=(8,2))
        cscr = tk.Scrollbar(cf); cscr.pack(side="right", fill="y")
        chat_log = tk.Text(cf, font=("Helvetica",8), bg="#0d1117", fg="#c9d1d9",
                           bd=0, relief="flat", wrap="word", state="disabled",
                           yscrollcommand=cscr.set)
        chat_log.pack(fill="both", expand=True, padx=(6,0), pady=(0,4))
        cscr.configure(command=chat_log.yview)
        chat_log.tag_configure("me",   foreground="#ff6b9d")
        chat_log.tag_configure("them", foreground="#58d6d6")
        chat_log.tag_configure("ts",   foreground="#555566")
        cinp = tk.Frame(cf, bg="#161b22"); cinp.pack(fill="x", padx=6, pady=(0,6))
        chat_entry = tk.Entry(cinp, font=("Helvetica",9), bg="#21262d", fg="#f0f6fc",
                              insertbackground="#f0f6fc", bd=0, relief="flat")
        chat_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,4))

        # Per-modal chat state
        _modal_chat_last_id = [0]

        def _append_chat_modal(sender, text, cls):
            try:
                chat_log.configure(state="normal")
                ts = time.strftime("%H:%M")
                chat_log.insert("end", f"[{ts}] ", "ts")
                chat_log.insert("end", f"{sender}: {text}\n", cls)
                chat_log.configure(state="disabled")
                chat_log.see("end")
            except Exception: pass

        def _send_proctor_msg(evt=None):
            msg = chat_entry.get().strip()
            if not msg: return
            chat_entry.delete(0, "end")
            _append_chat_modal("You", msg, "me")
            def _post():
                try:
                    _requests.post("http://127.0.0.1:6000/send_chat", json={
                        "session_code": _PROCTOR_SESSION_CODE or "",
                        "student_id":   sid,
                        "sender":       "proctor",
                        "message":      msg,
                    }, timeout=2)
                except Exception: pass
            threading.Thread(target=_post, daemon=True).start()

        tk.Button(cinp, text="▶", font=("Helvetica",9,"bold"),
                  bg="#ff6b9d", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=_send_proctor_msg
                  ).pack(side="right", ipady=5, ipadx=6)
        chat_entry.bind("<Return>", _send_proctor_msg)

        def _poll_chat_modal():
            try:
                if not win.winfo_exists(): return
            except Exception: return
            if not _REQUESTS_AVAILABLE: return
            def _fetch():
                try:
                    r = _requests.get("http://127.0.0.1:6000/get_chat", params={
                        "session_code": _PROCTOR_SESSION_CODE or "",
                        "student_id":   sid,
                        "since_id":     _modal_chat_last_id[0],
                    }, timeout=2)
                    for m in r.json().get("messages", []):
                        _modal_chat_last_id[0] = max(_modal_chat_last_id[0], m["id"])
                        if m["sender"] == "student":
                            win.after(0, lambda d=m:
                                _append_chat_modal(sid, d["message"], "them"))
                except Exception: pass
            threading.Thread(target=_fetch, daemon=True).start()
            win.after(2000, _poll_chat_modal)

        # ── Shared modal update loop (camera + violations + chat polling) ──
        def _update_modal():
            try:
                if not win.winfo_exists(): return
            except Exception: return
            # Update camera
            tile = self._student_tiles.get(sid)
            if tile:
                lbl = tile["cam_lbl"]
                if hasattr(lbl, "image") and lbl.image:
                    cam.configure(image=lbl.image, text="")
                    cam.image = lbl.image
            # Update violations
            try:
                conn = sqlite3.connect(DB)
                rows = conn.execute(
                    "SELECT timestamp,event,detail FROM violations "
                    "WHERE student_id=? ORDER BY id DESC LIMIT 100", (sid,)).fetchall()
                conn.close()
                vlog.configure(state="normal"); vlog.delete("1.0","end")
                for ts, ev, det in rows:
                    vu = ev.upper()
                    tag = ("strike" if "STRIKE" in vu or "TERMINATED" in vu
                           else "warn" if "WARNING" in vu
                           else "blocked" if "BLOCKED" in vu or "TAB" in vu
                           else "ok" if "START" in vu else "info")
                    vlog.insert("end", f"[{ts}] {ev}: {det}\n", tag)
                vlog.configure(state="disabled"); vlog.see("end")
            except Exception: pass
            win.after(100, _update_modal)   # 10fps for modal

        win.after(50, _update_modal)
        win.after(100, _poll_chat_modal)

    def _select_student(self, sid):
        self._selected_sid = sid
        for s, tile in self._student_tiles.items():
            col = "#58d6d6" if s == sid else "#30363d"
            tile["card"].configure(highlightbackground=col)
        self._refresh_violations()

    def _kick_student(self, sid):
        if messagebox.askyesno("Kick Student", f"Remove {sid} from session?", parent=self.root):
            db_set_join_status(_PROCTOR_SESSION_CODE, sid, "rejected")
            tile = self._student_tiles.pop(sid, None)
            if tile:
                tile["card"].grid_forget()
                tile["card"].destroy()
            if self._selected_sid == sid:
                self._selected_sid = None
            self._reflow_grid()
            if not self._student_tiles:
                self._no_students_lbl.pack(pady=40)
                try: self._student_count_lbl.configure(text="(0 online)")
                except Exception: pass

    # ── Violations panel ──────────────────────────────────────────────────────
    def _build_violations_panel(self, p):
        top = tk.Frame(p, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,2))
        tk.Label(top, text="Violations (select student to filter)",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ff6b9d").pack(side="left")
        tk.Button(top, text="↺", font=("Helvetica",10), bg="#21262d", fg="#8b949e",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._refresh_violations).pack(side="right", ipady=2, padx=4)
        tk.Button(top, text="All", font=("Helvetica",8), bg="#21262d", fg="#c9d1d9",
                  bd=0, relief="flat", cursor="hand2",
                  command=lambda: self._show_all_violations()).pack(side="right", ipady=2, padx=2)

        self._sel_lbl = tk.Label(p, text="No student selected",
                                 font=("Helvetica",8), bg="#0d1117", fg="#575fcf")
        self._sel_lbl.pack(anchor="w", padx=10)

        scr = tk.Scrollbar(p); scr.pack(side="right", fill="y")
        self.vlog = tk.Text(p, font=("Courier",8), bg="#060610", fg="#c9d1d9",
                            bd=0, relief="flat", wrap="word",
                            yscrollcommand=scr.set, state="disabled")
        self.vlog.pack(fill="both", expand=True, padx=8, pady=(0,4))
        scr.configure(command=self.vlog.yview)
        self.vlog.tag_configure("strike",    foreground="#ff4444", font=("Courier",8,"bold"))
        self.vlog.tag_configure("warn",      foreground="#ffaa00")
        self.vlog.tag_configure("blocked",   foreground="#ff8c00")
        self.vlog.tag_configure("keystroke", foreground="#7090ff")
        self.vlog.tag_configure("appwarn",   foreground="#c8a000")
        self.vlog.tag_configure("ok",        foreground="#0be881")
        self.vlog.tag_configure("info",      foreground="#8b949e")
        tk.Button(p, text="Clear Log", font=("Helvetica",8),
                  bg="#21262d", fg="#8b949e", bd=0, relief="flat", cursor="hand2",
                  command=lambda: (self.vlog.configure(state="normal"),
                                   self.vlog.delete("1.0","end"),
                                   self.vlog.configure(state="disabled"))).pack(pady=(0,6))

    def _refresh_violations(self):
        if not hasattr(self, 'vlog'): return   # interview mode has no violations panel
        sid = self._selected_sid
        if sid:
            self._sel_lbl.configure(text=f"Showing violations for: {sid}")
        else:
            self._sel_lbl.configure(text="All students (select a student to filter)")
        self._show_all_violations(sid)

    def _show_all_violations(self, sid=None):
        conn = sqlite3.connect(DB)
        if sid:
            rows = conn.execute(
                "SELECT timestamp,event,detail FROM violations WHERE student_id=? ORDER BY id DESC LIMIT 200",
                (sid,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT student_id||' | '||timestamp,event,detail FROM violations ORDER BY id DESC LIMIT 200"
            ).fetchall()
        conn.close()

        try:
            self.vlog.configure(state="normal")
            self.vlog.delete("1.0","end")
            for ts, ev, det in rows:
                vu = ev.upper()
                if   "STRIKE"      in vu: tag = "strike"
                elif "TERMINATED"  in vu: tag = "strike"
                elif "WARNING"     in vu: tag = "warn"
                elif "BLOCKED_APP" in vu: tag = "blocked"
                elif "TAB_SWITCH"  in vu: tag = "blocked"
                elif "KEYSTROKE"   in vu: tag = "keystroke"
                elif "APP_WARNING" in vu: tag = "appwarn"
                elif "START"       in vu: tag = "ok"
                else:                    tag = "info"
                self.vlog.insert("end", f"[{ts}] {ev}: {det}\n", tag)
            self.vlog.configure(state="disabled")
            self.vlog.see("end")
        except Exception as e:
            print(f"[Violations] {e}")

    # ── Runtime question panel ────────────────────────────────────────────────
    def _build_runtime_q_panel(self, p):
        tk.Label(p, text="📌 Push Runtime Question to Student",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d").pack(anchor="w", padx=10, pady=(10,4))
        tk.Label(p, text="Select a student tile first, then compose an MCQ question below.",
                 font=("Helvetica",8), bg="#0d1117", fg="#8b949e").pack(anchor="w", padx=10)

        # Student selector
        sel_frame = tk.Frame(p, bg="#0d1117"); sel_frame.pack(fill="x", padx=10, pady=(6,0))
        tk.Label(sel_frame, text="Send to:", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._rq_target_var = tk.StringVar(value="(select student)")
        self._rq_target_menu = tk.OptionMenu(sel_frame, self._rq_target_var, "(select student)")
        self._rq_target_menu.configure(bg="#21262d", fg="#f0f6fc", font=("Helvetica",9),
                                        bd=0, relief="flat", activebackground="#30363d",
                                        highlightthickness=0)
        self._rq_target_menu["menu"].configure(bg="#21262d", fg="#f0f6fc")
        self._rq_target_menu.pack(side="left", padx=(6,0))
        tk.Button(sel_frame, text="All Students", font=("Helvetica",8,"bold"),
                  bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                  command=lambda: self._rq_target_var.set("ALL")).pack(side="right", ipady=2, padx=2)

        # Question text
        tk.Label(p, text="Question:", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(anchor="w", padx=10, pady=(8,0))
        self._rq_text = tk.Text(p, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat", height=3)
        self._rq_text.pack(fill="x", padx=10, pady=(2,0), ipady=4)

        # MCQ options — A B C D
        tk.Label(p, text="MCQ Options (leave blank to send as open-ended question):",
                 font=("Helvetica",8,"bold"), bg="#0d1117", fg="#58d6d6").pack(anchor="w", padx=10, pady=(8,0))
        self._rq_opts = {}
        opts_frame = tk.Frame(p, bg="#0d1117"); opts_frame.pack(fill="x", padx=10, pady=(2,4))
        for letter in ("A","B","C","D"):
            row = tk.Frame(opts_frame, bg="#0d1117"); row.pack(fill="x", pady=2)
            tk.Label(row, text=f"{letter}:", width=2, font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#ffd93d").pack(side="left")
            ent = tk.Entry(row, font=("Helvetica",9), bg="#21262d", fg="#f0f6fc",
                           insertbackground="#f0f6fc", bd=0, relief="flat")
            ent.pack(side="left", fill="x", expand=True, ipady=4, padx=(4,0))
            self._rq_opts[letter] = ent

        tk.Button(p, text="📤 Push MCQ to Student(s)",
                  font=("Helvetica",10,"bold"), bg="#ffd93d", fg="#0d1117",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._push_runtime_question).pack(fill="x", padx=10, pady=8, ipady=7)

        # Answers log
        tk.Label(p, text="Student Answers:", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#0be881").pack(anchor="w", padx=10, pady=(4,0))
        scr2 = tk.Scrollbar(p); scr2.pack(side="right", fill="y")
        self._rq_ans_log = tk.Text(p, font=("Courier",8), bg="#060610", fg="#c9d1d9",
                                    bd=0, relief="flat", wrap="word",
                                    yscrollcommand=scr2.set, state="disabled")
        self._rq_ans_log.pack(fill="both", expand=True, padx=(8,0), pady=(0,4))
        scr2.configure(command=self._rq_ans_log.yview)
        self._poll_runtime_answers()

    def _update_rq_student_menu(self):
        menu = self._rq_target_menu["menu"]
        menu.delete(0, "end")
        for sid in self._student_tiles.keys():
            menu.add_command(label=sid, command=lambda s=sid: self._rq_target_var.set(s))
        if self._selected_sid:
            self._rq_target_var.set(self._selected_sid)

    def _push_runtime_question(self):
        question = self._rq_text.get("1.0", "end").strip()
        target   = self._rq_target_var.get()
        if not question:
            messagebox.showerror("Error", "Enter a question first", parent=self.root); return
        if target in ("(select student)", ""):
            messagebox.showerror("Error", "Select a target student or 'All Students'", parent=self.root); return
        # Collect MCQ options — pack non-empty ones as pipe-separated string
        raw_opts = [self._rq_opts[l].get().strip() for l in ("A","B","C","D")]
        filled   = [o for o in raw_opts if o]
        options  = "|".join(raw_opts) if len(filled) >= 2 else ""
        code = _PROCTOR_SESSION_CODE or ""
        if target == "ALL":
            sids = list(self._student_tiles.keys())
        else:
            sids = [target]
        for sid in sids:
            db_push_runtime_question(code, sid, question, options)
        self._rq_text.delete("1.0","end")
        for ent in self._rq_opts.values(): ent.delete(0,"end")
        messagebox.showinfo("Sent", f"MCQ pushed to {len(sids)} student(s) ✓", parent=self.root)

    def _poll_runtime_answers(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        code = _PROCTOR_SESSION_CODE or ""
        all_sids = list(self._student_tiles.keys())
        try:
            self._rq_ans_log.configure(state="normal")
            self._rq_ans_log.delete("1.0","end")
            for sid in all_sids:
                rows = db_get_runtime_questions(code, sid)
                for r in rows:
                    qid, q, opts, sent_at, answered, ans = r
                    if answered:
                        # Resolve MCQ answer letter → full option text if possible
                        opt_list = [o for o in (opts or "").split("|") if o]
                        if opt_list and ans in ("A","B","C","D"):
                            idx = ord(ans)-ord("A")
                            ans_disp = f"{ans}) {opt_list[idx]}" if idx < len(opt_list) else ans
                        else:
                            ans_disp = ans
                        self._rq_ans_log.insert("end",
                            f"[{sent_at}] {sid}:\n  Q: {q[:60]}…\n  A: {ans_disp}\n\n")
            self._rq_ans_log.configure(state="disabled")
        except Exception: pass
        self.root.after(5000, self._poll_runtime_answers)

    # ── Camera polling ────────────────────────────────────────────────────────
    def _poll_cameras(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return

        # 1. Check for new accepted students — throttled (DB read every ~500 ms)
        self._stats_counter += self.POLL_MS
        if self._stats_counter >= self.STATS_MS:
            self._stats_counter = 0
            accepted = db_get_accepted_students(_PROCTOR_SESSION_CODE or "")
            for sid in accepted:
                if sid not in self._student_tiles:
                    self._add_student_tile(sid)
                    self._update_rq_student_menu()
            self._refresh_violations()

        # 2. Update each student tile
        for sid, tile in list(self._student_tiles.items()):
            # Get frame from in-process hub (local) or _student_data (remote push)
            hub = _hub or _iv_hub
            if hub and getattr(hub, 'student_id', None) == sid:
                frame = (hub.get_frame() if hasattr(hub,'get_frame')
                         else hub.get_student_frame())
                fc = hub.face_count; gd = hub.gaze_dir; sc = hub.strike_count
            else:
                with _student_data_lock:
                    slot = _student_data.get(sid)
                if slot:
                    with slot["lock"]:
                        frame = slot["frame"].copy() if slot["frame"] is not None else None
                        s = dict(slot["stats"])
                    fc = s.get("face_count", 0)
                    gd = s.get("gaze_dir", "—")
                    sc = s.get("strike_count", 0)
                else:
                    frame = None; fc = 0; gd = "—"; sc = 0

            # Update camera thumbnail — letterbox-fit to exact tile dimensions
            if frame is not None:
                try:
                    lbl = tile["cam_lbl"]
                    h, w = frame.shape[:2]
                    if h > 0 and w > 0:
                        lw = lbl.winfo_width(); lh = lbl.winfo_height()
                        if lw > 10 and lh > 10:
                            # Scale to fill the label exactly, preserving aspect ratio (letterbox)
                            scale = min(lw / w, lh / h)
                            nw = max(1, int(w * scale)); nh = max(1, int(h * scale))
                            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                            # Pad to exact label size with black bars
                            canvas = np.zeros((lh, lw, 3), dtype=np.uint8)
                            y0 = (lh - nh) // 2; x0 = (lw - nw) // 2
                            canvas[y0:y0+nh, x0:x0+nw] = resized
                            disp = canvas
                        else:
                            disp = frame
                        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
                        img = ImageTk.PhotoImage(Image.fromarray(rgb))
                        lbl.configure(image=img, text="")
                        lbl.image = img
                except Exception: pass

            # Update stats labels
            try:
                fc_col = "#0be881" if fc==1 else "#ff4444" if fc==0 else "#ffaa00"
                sc_col = "#0be881" if sc==0 else "#ffaa00" if sc<3 else "#ff4444"
                tile["faces_lbl"].configure(text=f"Faces:{fc}", fg=fc_col)
                tile["gaze_lbl"].configure(text=f"Gaze:{gd}")
                tile["strikes_lbl"].configure(text=f"Strikes:{sc}", fg=sc_col)
                # Highlight card red if terminated
                if sc >= CameraHub.MAX_STRIKES:
                    tile["card"].configure(highlightbackground="#ff4444")
                    tile["name_lbl"].configure(fg="#ff4444", text=f"🚫 {sid} TERMINATED")
            except Exception: pass

        self.root.after(self.POLL_MS, self._poll_cameras)

    # ── Join request polling (proctor side) ───────────────────────────────────
    def _poll_join_requests(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception: return
        pending = db_get_pending_requests(_PROCTOR_SESSION_CODE or "")
        for sid in pending:
            if sid not in self._pending_notified:
                self._pending_notified.add(sid)
                self.root.after(0, lambda s=sid: self._show_join_request(s))
        self.root.after(2000, self._poll_join_requests)

    def _show_join_request(self, student_id):
        win = tk.Toplevel(self.root)
        win.title("👋 Join Request")
        win.geometry("360x180")
        win.configure(bg="#0d1117")
        win.attributes("-topmost", True)
        # NOTE: NO grab_set() here — allows multiple join-request popups to coexist
        tk.Label(win, text="👋 Student Wants to Join",
                 font=("Helvetica",13,"bold"), bg="#0d1117", fg="#ffd93d").pack(pady=(20,6))
        tk.Label(win, text=f"Student ID:  {student_id}",
                 font=("Helvetica",11), bg="#0d1117", fg="#f0f6fc").pack()
        tk.Label(win, text=f"Session: {_PROCTOR_SESSION_CODE}",
                 font=("Helvetica",9), bg="#0d1117", fg="#8b949e").pack()
        btn_row = tk.Frame(win, bg="#0d1117"); btn_row.pack(pady=16)
        def _accept():
            db_set_join_status(_PROCTOR_SESSION_CODE, student_id, "accepted")
            _get_or_create_student_slot(student_id)
            self._add_student_tile(student_id)
            self._update_rq_student_menu()
            win.destroy()
        def _reject():
            db_set_join_status(_PROCTOR_SESSION_CODE, student_id, "rejected")
            # Allow the student to re-request — remove from seen set so the
            # next pending row for this student triggers a fresh popup.
            self._pending_notified.discard(student_id)
            win.destroy()
        tk.Button(btn_row, text="✅ Accept", font=("Helvetica",10,"bold"),
                  bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  width=12, command=_accept).grid(row=0, column=0, padx=8, ipady=6)
        tk.Button(btn_row, text="❌ Reject", font=("Helvetica",10,"bold"),
                  bg="#6a0000", fg="#ff6b6b", bd=0, relief="flat", cursor="hand2",
                  width=12, command=_reject).grid(row=0, column=1, padx=8, ipady=6)

    # ── Add Question ──────────────────────────────────────────────────────────
    def _build_add_q(self, parent):
        tk.Label(parent, text="Add New Question", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#0be881").pack(anchor="w", padx=10, pady=(10,4))
        canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        self._aq = {}
        def lbl(txt):
            tk.Label(inner, text=txt, font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9", anchor="w").pack(fill="x", padx=10, pady=(8,0))
        lbl("Question *")
        self._aq["q"] = tk.Text(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                 insertbackground="#f0f6fc", bd=0, relief="flat", height=3)
        self._aq["q"].pack(fill="x", padx=10, pady=(2,0), ipady=4)
        for key, label in [("a","Option A *"),("b","Option B *"),("c","Option C *"),("d","Option D *")]:
            lbl(label)
            self._aq[key] = tk.Entry(inner, font=("Helvetica",10), bg="#161b22", fg="#f0f6fc",
                                      insertbackground="#f0f6fc", bd=0, relief="flat")
            self._aq[key].pack(fill="x", padx=10, pady=(2,0), ipady=6)
        lbl("Correct Answer")
        self._aq_ans = tk.StringVar(value="A")
        af = tk.Frame(inner, bg="#0d1117"); af.pack(padx=10, anchor="w", pady=(2,0))
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=self._aq_ans, value=opt,
                           font=("Helvetica",10,"bold"), bg="#0d1117", fg="#0be881",
                           selectcolor="#0d3b2e", activebackground="#0d1117").pack(side="left", padx=8)
        row2 = tk.Frame(inner, bg="#0d1117"); row2.pack(fill="x", padx=10, pady=(8,0))
        tk.Label(row2, text="Marks", font=("Helvetica",9,"bold"), bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["marks"] = tk.Entry(row2, font=("Helvetica",10), width=5,
                                      bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
                                      bd=0, relief="flat")
        self._aq["marks"].insert(0,"1"); self._aq["marks"].pack(side="left", padx=(4,16), ipady=5)
        tk.Label(row2, text="Category", font=("Helvetica",9,"bold"), bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["cat"] = tk.Entry(row2, font=("Helvetica",10), width=12,
                                    bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
                                    bd=0, relief="flat")
        self._aq["cat"].insert(0,"General"); self._aq["cat"].pack(side="left", padx=(4,0), ipady=5)
        tk.Button(inner, text="💾  Save Question", font=("Helvetica",10,"bold"),
                  bg="#0be881", fg="#0d1117", bd=0, relief="flat", cursor="hand2",
                  command=self._save_q).pack(fill="x", padx=10, pady=14, ipady=8)

    def _save_q(self):
        q = self._aq["q"].get("1.0","end").strip()
        a = self._aq["a"].get().strip(); b = self._aq["b"].get().strip()
        c = self._aq["c"].get().strip(); d = self._aq["d"].get().strip()
        ans = self._aq_ans.get()
        cat = self._aq["cat"].get().strip() or "General"
        try: marks = int(self._aq["marks"].get())
        except: marks = 1
        if not all([q,a,b,c,d]):
            messagebox.showerror("Error","Fill all required fields"); return
        db_add_question(q,a,b,c,d,ans,marks,cat)
        messagebox.showinfo("Saved","Question added ✓")
        self._aq["q"].delete("1.0","end")
        for k in ["a","b","c","d"]: self._aq[k].delete(0,"end")
        self._aq["marks"].delete(0,"end"); self._aq["marks"].insert(0,"1")
        self._aq["cat"].delete(0,"end");   self._aq["cat"].insert(0,"General")
        self._refresh_qbank()

    # ── Question Bank ─────────────────────────────────────────────────────────
    def _build_qbank(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Question Bank", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#ffd93d").pack(side="left")
        tk.Button(top, text="↺", font=("Helvetica",10), bg="#21262d", fg="#8b949e",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._refresh_qbank).pack(side="right", ipady=2, padx=4)
        self._qb_canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr = tk.Scrollbar(parent, command=self._qb_canvas.yview)
        self._qb_canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); self._qb_canvas.pack(fill="both", expand=True, padx=4)
        self._qb_inner = tk.Frame(self._qb_canvas, bg="#0d1117")
        self._qb_canvas.create_window((0,0), window=self._qb_inner, anchor="nw")
        self._qb_inner.bind("<Configure>",
            lambda e: self._qb_canvas.configure(scrollregion=self._qb_canvas.bbox("all")))
        self._refresh_qbank()

    def _refresh_qbank(self):
        for w in self._qb_inner.winfo_children(): w.destroy()
        qs = db_get_questions()
        if not qs:
            tk.Label(self._qb_inner, text="No questions.", font=("Helvetica",9),
                     bg="#0d1117", fg="#8b949e").pack(padx=10, pady=10); return
        for q in qs:
            card = tk.Frame(self._qb_inner, bg="#161b22"); card.pack(fill="x", padx=4, pady=3)
            txt = q[1][:60]+"…" if len(q[1])>60 else q[1]
            cat = q[8] if len(q)>8 else "—"
            tk.Label(card, text=f"Q{q[0]}: {txt}", font=("Helvetica",9),
                     bg="#161b22", fg="#c9d1d9", anchor="w", wraplength=200, justify="left"
                     ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            info = tk.Frame(card, bg="#161b22"); info.pack(side="left")
            tk.Label(info, text=f"Ans:{q[6]}", font=("Helvetica",8,"bold"),
                     bg="#161b22", fg="#0be881").pack(anchor="e")
            tk.Label(info, text=f"{q[7]}mk {cat}", font=("Helvetica",7),
                     bg="#161b22", fg="#575fcf").pack(anchor="e")
            tk.Button(card, text="🗑", font=("Helvetica",10), bg="#161b22", fg="#ff6b9d",
                      bd=0, relief="flat", cursor="hand2",
                      command=lambda qid=q[0]: self._del_q(qid)).pack(side="right", padx=2)

    def _del_q(self, qid):
        if messagebox.askyesno("Delete", f"Delete Q{qid}?"):
            db_delete_question(qid); self._refresh_qbank()

    # ── Results ────────────────────────────────────────────────────────────────
    def _build_results(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Exam Results & Logs", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#575fcf").pack(side="left")
        tk.Button(top, text="↺", font=("Helvetica",10), bg="#21262d", fg="#8b949e",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._refresh_results).pack(side="right", ipady=2, padx=4)
        self._res_frame = tk.Frame(parent, bg="#0d1117")
        self._res_frame.pack(fill="both", expand=True, padx=4)
        self._refresh_results()

    def _refresh_results(self):
        for w in self._res_frame.winfo_children(): w.destroy()
        files = [f for f in os.listdir('.')
                 if f.endswith('_result.csv') or f.endswith('_exam_log.csv')]
        if not files:
            tk.Label(self._res_frame, text="No result files yet.", font=("Helvetica",9),
                     bg="#0d1117", fg="#8b949e").pack(padx=10, pady=10); return
        canvas = tk.Canvas(self._res_frame, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(self._res_frame, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        for fname in sorted(files):
            row = tk.Frame(inner, bg="#161b22"); row.pack(fill="x", padx=4, pady=3)
            tk.Label(row, text=fname, font=("Courier",9), bg="#161b22", fg="#c9d1d9",
                     anchor="w").pack(side="left", padx=8, pady=6, fill="x", expand=True)
            tk.Button(row, text="View", font=("Helvetica",8,"bold"),
                      bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                      command=lambda f=fname: self._view_file(f)
                      ).pack(side="right", padx=6, pady=4, ipady=2)

    def _view_file(self, fname):
        try:
            with open(fname, encoding='utf-8') as f: content = f.read()
        except: content = "Could not read file."
        win = tk.Toplevel(self.root); win.title(fname)
        win.geometry("640x420"); win.configure(bg="#0d1117")
        scr = tk.Scrollbar(win); scr.pack(side="right", fill="y")
        txt = tk.Text(win, font=("Courier",9), bg="#0b0b13", fg="#c9d1d9",
                      bd=0, wrap="none", yscrollcommand=scr.set)
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        txt.insert("end", content); txt.configure(state="disabled")
        scr.configure(command=txt.yview)

    # ── Interview Notes ────────────────────────────────────────────────────────
    def _build_notes(self, parent):
        tk.Label(parent, text="Interview Notes (sent to student)",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ffd93d"
                 ).pack(anchor="w", padx=8, pady=(8,4))
        self._notes_box = tk.Text(parent, font=("Helvetica",10),
                                   bg="#161b22", fg="#f0f6fc",
                                   insertbackground="#f0f6fc", bd=0, relief="flat", wrap="word")
        self._notes_box.pack(fill="both", expand=True, padx=8, pady=(0,4))
        tk.Button(parent, text="📤 Push Notes to Student",
                  font=("Helvetica",10,"bold"), bg="#ffd93d", fg="#0d1117",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._push_notes).pack(fill="x", padx=8, pady=(0,8), ipady=7)

    def _push_notes(self):
        if not hasattr(self, '_notes_box'): return
        content = self._notes_box.get("1.0","end").strip()
        try:
            with open("interview_notes.txt","w",encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Sent","Notes pushed to student ✓")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── Logout / close ─────────────────────────────────────────────────────────
    def _logout(self):
        db_close_session(_PROCTOR_SESSION_CODE or "")
        self._stop_pro_cam()
        if _hub:    _hub.stop()
        if _iv_hub: _iv_hub.stop()
        global _audio_proctor, _voice_proctor
        if _audio_proctor:  _audio_proctor.stop();  _audio_proctor = None
        if _voice_proctor:  _voice_proctor.stop();   _voice_proctor = None
        self.root.destroy()
        MainLogin().run()

    def _close(self):
        db_close_session(_PROCTOR_SESSION_CODE or "")
        self._stop_pro_cam()
        if _hub:    _hub.stop()
        if _iv_hub: _iv_hub.stop()
        global _audio_proctor, _voice_proctor
        if _audio_proctor:  _audio_proctor.stop();  _audio_proctor = None
        if _voice_proctor:  _voice_proctor.stop();   _voice_proctor = None
        self.root.destroy()

    def run(self): self.root.mainloop()

if __name__ == "__main__":
    init_db()
    try:
        from face_auth import init_face_db
        init_face_db()
    except ImportError:
        pass

    # Dependency warnings
    if not _KEYBOARD_HOOK_AVAILABLE:
        print("[⚠] 'keyboard' not installed — keystroke blocking disabled")
        print("    Fix: pip install keyboard")
    if not _PSUTIL_AVAILABLE:
        print("[⚠] 'psutil' not installed — app blocking disabled")
        print("    Fix: pip install psutil")
    if not _REQUESTS_AVAILABLE:
        print("[⚠] 'requests' not installed — remote proctor mode disabled")
        print("    Fix: pip install requests")
    if not _SOUNDDEVICE_AVAILABLE:
        print("[⚠] 'sounddevice' not installed — two-way voice disabled")
        print("    Fix: pip install sounddevice")
    if not _WEBSOCKET_CLIENT_AVAILABLE:
        print("[⚠] 'websocket-client' not installed — WebSocket voice disabled")
        print("    Fix: pip install websocket-client")
    if not _FLASK_SOCK_AVAILABLE:
        print("[⚠] 'flask-sock' not installed — WebSocket voice bridge disabled")
        print("    Fix: pip install flask-sock")
    if not _VOICE_BRIDGE_AVAILABLE:
        print("[⚠] 'voice_bridge.py' not found — place it next to main.py")
    if not _NGROK_AVAILABLE:
        print("[⚠] 'pyngrok' not installed — internet (cross-network) proctoring disabled")
        print("    Fix: pip install pyngrok")
        print("    Then: ngrok config add-authtoken <YOUR_TOKEN>  (free at ngrok.com)")
    try:
        import win32gui
    except ImportError:
        print("[⚠] 'pywin32' not installed — win32 tab-switch detection disabled")
        print("    Fix: pip install pywin32")

    # Start Flask server on all machines — harmless until a student logs in
    start_network_server(port=6000)

    MainLogin().run()