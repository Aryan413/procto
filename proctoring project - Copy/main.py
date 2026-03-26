"""
ExamShield - main.py
====================
Single entry point. Run:  python main.py
"""

import tkinter as tk
from tkinter import messagebox, ttk
import sqlite3, math, random, time, threading, csv, os
import cv2
import numpy as np
from PIL import Image, ImageTk
from ultralytics import YOLO
import mediapipe as mp

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
}

DB = "students.db"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
                    student_id TEXT PRIMARY KEY,
                    password   TEXT,
                    face_data  BLOB)""")
    c.execute("""CREATE TABLE IF NOT EXISTS proctors(
                    proctor_id TEXT PRIMARY KEY,
                    password   TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS questions(
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    opt_a    TEXT,
                    opt_b    TEXT,
                    opt_c    TEXT,
                    opt_d    TEXT,
                    answer   TEXT,
                    marks    INTEGER DEFAULT 1,
                    category TEXT DEFAULT 'General')""")
    c.execute("""CREATE TABLE IF NOT EXISTS violations(
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT,
                    timestamp  TEXT,
                    event      TEXT,
                    detail     TEXT)""")
    try:
        c.execute("INSERT INTO proctors VALUES('admin','admin123')")
    except Exception:
        pass
    if c.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0:
        seed = [
            ("What does CPU stand for?",
             "Central Processing Unit","Central Program Unit",
             "Computer Personal Unit","Control Processing Unit","A",1,"CS"),
            ("Which language is primarily used for web pages?",
             "Python","Java","HTML","C++","C",1,"Web"),
            ("What is 2 raised to the power of 10?",
             "512","1024","2048","256","B",1,"Math"),
            ("Who invented the World Wide Web?",
             "Bill Gates","Tim Berners-Lee","Steve Jobs","Linus Torvalds","B",1,"General"),
            ("What does RAM stand for?",
             "Random Access Memory","Read Access Module",
             "Remote Access Memory","Rapid Access Module","A",1,"CS"),
            ("Which data structure uses LIFO order?",
             "Queue","Stack","Tree","Graph","B",1,"CS"),
            ("What is the binary representation of decimal 5?",
             "101","110","100","111","A",1,"Math"),
            ("Which protocol is used to send emails?",
             "HTTP","FTP","SMTP","SSH","C",1,"Networks"),
        ]
        c.executemany(
            "INSERT INTO questions(question,opt_a,opt_b,opt_c,opt_d,answer,marks,category)"
            " VALUES(?,?,?,?,?,?,?,?)", seed)
    conn.commit()
    conn.close()

def db_get_user(uid, pwd, role="student"):
    conn = sqlite3.connect(DB)
    col = "student_id" if role == "student" else "proctor_id"
    tbl = "users"      if role == "student" else "proctors"
    row = conn.execute(
        f"SELECT * FROM {tbl} WHERE {col}=? AND password=?", (uid, pwd)
    ).fetchone()
    conn.close()
    return row

def db_register(uid, pwd):
    conn = sqlite3.connect(DB)
    try:
        conn.execute("INSERT INTO users(student_id,password) VALUES(?,?)", (uid, pwd))
        conn.commit(); conn.close(); return True
    except sqlite3.IntegrityError:
        conn.close(); return False

def db_get_questions():
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT * FROM questions ORDER BY id").fetchall()
    conn.close(); return rows

def db_add_question(q, a, b, c, d, ans, marks=1, category="General"):
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO questions(question,opt_a,opt_b,opt_c,opt_d,answer,marks,category)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (q, a, b, c, d, ans, marks, category))
    conn.commit(); conn.close()

def db_update_question(qid, q, a, b, c, d, ans, marks, category):
    conn = sqlite3.connect(DB)
    conn.execute(
        "UPDATE questions SET question=?,opt_a=?,opt_b=?,opt_c=?,opt_d=?,"
        "answer=?,marks=?,category=? WHERE id=?",
        (q, a, b, c, d, ans, marks, category, qid))
    conn.commit(); conn.close()

def db_delete_question(qid):
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    conn.commit(); conn.close()

def db_log_violation(student_id, event, detail=""):
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO violations(student_id,timestamp,event,detail) VALUES(?,?,?,?)",
        (student_id, time.strftime("%H:%M:%S"), event, detail))
    conn.commit(); conn.close()

def db_get_violations(student_id):
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT timestamp,event,detail FROM violations WHERE student_id=? ORDER BY id",
        (student_id,)).fetchall()
    conn.close(); return rows

class CameraHub:
    MAX_STRIKES   = 5
    WARNING_SECS  = 4.0
    GAZE_FRAMES   = 15
    GAZE_DIRS     = {"left", "right", "up", "down"}
    MULTI_GRACE   = 1.5
    YOLO_INTERVAL = 5

    def __init__(self, student_id):
        self.student_id     = student_id
        self.latest_frame   = None
        self.running        = True
        self.violations     = []
        self.strike_count   = 0
        self.face_count     = 0
        self.gaze_dir       = "center"
        self.phone_detected = False
        self._lock          = threading.Lock()
        self._thread        = threading.Thread(target=self._run, daemon=True)

    def start(self):  self._thread.start()
    def stop(self):   self.running = False

    def get_frame(self):
        with self._lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def _log(self, event, detail=""):
        ts  = time.strftime("%H:%M:%S")
        msg = f"[{ts}] {event}: {detail}"
        with self._lock:
            self.violations.append(msg)
            if len(self.violations) > 300:
                self.violations = self.violations[-300:]
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

        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        phone_t     = None
        multi_t     = None
        gaze_streak = 0
        gaze_timer  = None
        frame_n     = 0
        last_boxes  = []

        self._log("EXAM_START", self.student_id)
        print(f"[✅ EXAM START] {self.student_id} | CameraHub active (hidden from student)")

        while self.running:
            ret, frame = cap.read()
            if not ret:
                break
            frame_n += 1

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = face_mesh.process(rgb)
            fc  = 0
            if res.multi_face_landmarks:
                h_f, w_f = frame.shape[:2]
                for fl in res.multi_face_landmarks:
                    xs = [lm.x for lm in fl.landmark]
                    if max(xs) - min(xs) >= 0.14:
                        fc += 1
                        for lm in fl.landmark[::5]:
                            cv2.circle(frame,
                                (int(lm.x * w_f), int(lm.y * h_f)),
                                1, (0, 200, 100), -1)

            gaze.refresh(frame)
            gd = gaze.direction()
            if gaze.pupils_located:
                for coords in [gaze.pupil_left_coords(), gaze.pupil_right_coords()]:
                    if coords:
                        cv2.circle(frame, coords, 4, (0, 255, 120), -1)

            if frame_n % self.YOLO_INTERVAL == 0:
                det = yolo(frame, verbose=False)[0]
                last_boxes = []
                for box in det.boxes:
                    nm = yolo.names[int(box.cls[0])]
                    cf = float(box.conf[0])
                    if nm == "cell phone" and cf > 0.45:
                        x1,y1,x2,y2 = map(int, box.xyxy[0])
                        last_boxes.append((x1,y1,x2,y2,cf))
            for x1,y1,x2,y2,cf in last_boxes:
                cv2.rectangle(frame, (x1,y1), (x2,y2), (0,60,255), 2)
                cv2.putText(frame, f"PHONE {cf:.0%}", (x1, y1-8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,60,255), 2)

            if gaze.calibration.is_complete() and gd in self.GAZE_DIRS:
                gaze_streak += 1
                if gaze_streak >= self.GAZE_FRAMES:
                    if gaze_timer is None:
                        gaze_timer = time.time()
                        self._log("GAZE_WARNING", f"Looking {gd}")
                    elif time.time() - gaze_timer >= self.WARNING_SECS:
                        self.strike_count += 1
                        self._log(f"STRIKE {self.strike_count}", f"Gaze away ({gd})")
                        gaze_timer = time.time()
            else:
                gaze_streak = 0
                gaze_timer  = None

            if fc > 1:
                cv2.putText(frame, "MULTIPLE FACES", (50, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,140,255), 2)
                if multi_t is None:
                    multi_t = time.time()
                elif time.time() - multi_t >= self.MULTI_GRACE:
                    self.strike_count += 1
                    self._log(f"STRIKE {self.strike_count}", f"Multiple faces ({fc})")
                    multi_t = time.time()
            else:
                multi_t = None

            if last_boxes:
                if phone_t is None:
                    phone_t = time.time()
                    self.strike_count += 1
                    self._log(f"STRIKE {self.strike_count}", "Phone detected")
            else:
                phone_t = None

            h, w = frame.shape[:2]
            ov = frame.copy()
            cv2.rectangle(ov, (0,0), (w,68), (15,15,15), -1)
            cv2.addWeighted(ov, 0.75, frame, 0.25, 0, frame)
            cv2.putText(frame, f"ID: {self.student_id}", (10,22),
                        cv2.FONT_HERSHEY_DUPLEX, 0.55, (180,180,180), 1)
            fc_c = (80,220,80) if fc == 1 else (0,80,255)
            cv2.putText(frame, f"Faces: {fc}", (10,48), cv2.FONT_HERSHEY_DUPLEX, 0.55, fc_c, 1)
            gd_c = (80,220,80) if gd == "center" else (0,180,255)
            cv2.putText(frame, f"Gaze: {gd}", (w//2-80,22), cv2.FONT_HERSHEY_DUPLEX, 0.55, gd_c, 1)
            st_c = (0,220,80) if self.strike_count == 0 else \
                   (0,160,255) if self.strike_count < self.MAX_STRIKES-1 else (0,50,255)
            cv2.putText(frame, f"Strikes: {self.strike_count}/{self.MAX_STRIKES}",
                        (w-220,22), cv2.FONT_HERSHEY_DUPLEX, 0.55, st_c, 1)

            if self.strike_count >= self.MAX_STRIKES:
                overlay = np.zeros_like(frame); overlay[:] = (0,0,160)
                cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
                cv2.putText(frame, "EXAM TERMINATED",
                            (w//2-210, h//2), cv2.FONT_HERSHEY_DUPLEX, 1.4, (255,255,255), 3)
                self._log("EXAM_TERMINATED", "Max strikes reached")
                with self._lock:
                    self.latest_frame = frame.copy()
                break

            with self._lock:
                self.latest_frame   = frame.copy()
                self.face_count     = fc
                self.gaze_dir       = gd
                self.phone_detected = bool(last_boxes)

        cap.release()
        face_mesh.close()
        print(f"[CameraHub] Stopped — {self.student_id}")

_hub: CameraHub = None

class Particle:
    def __init__(self, w, h, colors):
        self.canvas_w = w; self.canvas_h = h; self.reset(colors)

    def reset(self, colors):
        self.x  = random.uniform(0, self.canvas_w)
        self.y  = random.uniform(0, self.canvas_h)
        self.size = random.uniform(1.5, 4.5)
        self.color = random.choice(colors)
        self.vx = random.uniform(-0.4, 0.4)
        self.vy = random.uniform(-0.4, 0.4)
        self.pulse = random.uniform(0, math.pi*2)
        self.pulse_speed = random.uniform(0.02, 0.06)

    def update(self, mx, my):
        dx, dy = self.x-mx, self.y-my
        dist = math.sqrt(dx*dx+dy*dy) or 1
        if dist < 100:
            f = (100-dist)/100*1.2
            self.vx += dx/dist*f; self.vy += dy/dist*f
        self.vx *= 0.97; self.vy *= 0.97
        sp = math.sqrt(self.vx**2+self.vy**2)
        if sp > 2.5: self.vx=self.vx/sp*2.5; self.vy=self.vy/sp*2.5
        self.x += self.vx; self.y += self.vy
        self.pulse += self.pulse_speed
        if self.x<0 or self.x>self.canvas_w: self.vx*=-1; self.x=max(0,min(self.canvas_w,self.x))
        if self.y<0 or self.y>self.canvas_h: self.vy*=-1; self.y=max(0,min(self.canvas_h,self.y))

class BaseWindow:
    def __init__(self, root, theme):
        self.root = root; self.theme = theme
        self.mouse_x = 260; self.mouse_y = 300; self.animating = True
        self.canvas = tk.Canvas(root, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.particles = [Particle(520, 600, theme["particle_colors"]) for _ in range(55)]
        self.root.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Motion>",
            lambda e: (setattr(self,'mouse_x',e.x), setattr(self,'mouse_y',e.y)))

    def _fade(self, hex_color, alpha):
        bg = self.theme["bg"].lstrip("#"); fg = hex_color.lstrip("#")
        try:
            br,bg_c,bb = int(bg[0:2],16),int(bg[2:4],16),int(bg[4:6],16)
            fr,fg_c,fb = int(fg[0:2],16),int(fg[2:4],16),int(fg[4:6],16)
            a = alpha/255
            return (f"#{int(br+(fr-br)*a):02x}"
                    f"{int(bg_c+(fg_c-bg_c)*a):02x}"
                    f"{int(bb+(fb-bb)*a):02x}")
        except: return hex_color

    def _draw_particles(self):
        self.canvas.delete("particle")
        for p in self.particles:
            p.update(self.mouse_x, self.mouse_y)
            r = p.size + math.sin(p.pulse)*1.2
            self.canvas.create_oval(p.x-r,p.y-r,p.x+r,p.y+r,
                fill=p.color, outline="", tags="particle")
        for i, p1 in enumerate(self.particles):
            for p2 in self.particles[i+1:]:
                dx, dy = p1.x-p2.x, p1.y-p2.y
                d = math.sqrt(dx*dx+dy*dy)
                if d < 90:
                    op = int(255*(1-d/90)*0.35)
                    self.canvas.create_line(p1.x,p1.y,p2.x,p2.y,
                        fill=self._fade(p1.color,op), width=0.8, tags="particle")

    def _draw_card(self):
        self.canvas.delete("card_bg")
        w, h = self.root.winfo_width(), self.root.winfo_height()
        px = max(40, int(w*0.10))
        x0, y0 = px, max(70, int(h*0.13))
        x1, y1 = w-px, h-max(40, int(h*0.07))
        r = 18
        self.canvas.create_rectangle(x0+4,y0+4,x1+4,y1+4,
            fill="#000000", outline="", tags="card_bg")
        fill = self.theme["card_bg"]; outline = self.theme["card_border"]; t = "card_bg"
        self.canvas.create_rectangle(x0+r,y0,x1-r,y1, fill=fill, outline="", tags=t)
        self.canvas.create_rectangle(x0,y0+r,x1,y1-r, fill=fill, outline="", tags=t)
        for cx,cy,s,e in [(x0+r,y0+r,180,270),(x1-r,y0+r,270,360),
                          (x0+r,y1-r,90,180),(x1-r,y1-r,0,90)]:
            self.canvas.create_arc(cx-r,cy-r,cx+r,cy+r,
                start=s, extent=e-s, fill=fill, outline="", tags=t)
        for c in [(x0+r,y0,x1-r,y0+2),(x0+r,y1-2,x1-r,y1),
                  (x0,y0+r,x0+2,y1-r),(x1-2,y0+r,x1,y1-r)]:
            self.canvas.create_rectangle(*c, fill=outline, outline="", tags=t)

    def _animate(self):
        if not self.animating: return
        w, h = self.root.winfo_width(), self.root.winfo_height()
        self.canvas.configure(bg=self.theme["canvas_bg"], width=w, height=h)
        self._draw_particles(); self._draw_card()
        self.root.after(30, self._animate)

    def _on_resize(self, event=None):
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if w < 10 or h < 10: return
        self.canvas.config(width=w, height=h)
        if hasattr(self, 'ui_frame'):
            px = max(40, int(w*0.10)); cw = w-2*px
            fw = min(cw-20, 400)
            self.ui_frame.place(x=px+(cw-fw)//2,
                                y=max(70,int(h*0.13))+max(20,int(h*0.05)), width=fw)
        t = max(55, min(120, int(w*h/8000)))
        while len(self.particles) < t:
            self.particles.append(Particle(w, h, self.theme["particle_colors"]))
        while len(self.particles) > t:
            self.particles.pop()
        for p in self.particles:
            p.canvas_w = w; p.canvas_h = h
            if p.x > w or p.y > h:
                p.x = random.uniform(0,w); p.y = random.uniform(0,h)

    def _make_entry(self, parent, show=None):
        fr = tk.Frame(parent, bg=self.theme["entry_border"], bd=0)
        fr.pack(fill="x", padx=30, pady=(4,0))
        e = tk.Entry(fr, font=("Helvetica",11),
                     bg=self.theme["entry_bg"], fg=self.theme["entry_fg"],
                     insertbackground=self.theme["entry_fg"],
                     bd=0, relief="flat", show=show or "")
        e.pack(fill="x", padx=1, pady=1, ipady=8)
        e.bind("<FocusIn>",  lambda _: fr.configure(bg=self.theme["entry_focus"]))
        e.bind("<FocusOut>", lambda _: fr.configure(bg=self.theme["entry_border"]))
        return e

class MainLogin(BaseWindow):
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ExamShield — Login")
        self.root.geometry("520x640")
        self.root.resizable(True, True)
        self.root.minsize(420, 560)
        self.is_dark = True; self.theme = DARK
        self.role = tk.StringVar(value="student")
        super().__init__(self.root, self.theme)
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._animate()

    def _build_ui(self):
        t = self.theme
        self.ui_frame = tk.Frame(self.root, bg=t["card_bg"], bd=0, highlightthickness=0)
        self.ui_frame.place(x=75, y=110, width=370)

        tk.Label(self.ui_frame, text="🛡️", font=("Segoe UI Emoji",30),
                 bg=t["card_bg"]).pack(pady=(18,0))
        tk.Label(self.ui_frame, text="ExamShield",
                 font=("Helvetica",20,"bold"), bg=t["card_bg"], fg=t["title_fg"]).pack()
        tk.Label(self.ui_frame, text="Secure AI Proctoring System",
                 font=("Helvetica",9), bg=t["card_bg"], fg=t["subtitle_fg"]).pack(pady=(2,14))

        pf = tk.Frame(self.ui_frame, bg=t["card_bg"]); pf.pack(pady=(0,16))
        self.pill_s = tk.Button(pf, text="👨‍🎓  Student", font=("Helvetica",10,"bold"),
            bd=0, relief="flat", cursor="hand2", width=12,
            command=lambda: self._set_role("student"))
        self.pill_s.grid(row=0, column=0, padx=2, ipady=5)
        self.pill_p = tk.Button(pf, text="👨‍🏫  Proctor", font=("Helvetica",10,"bold"),
            bd=0, relief="flat", cursor="hand2", width=12,
            command=lambda: self._set_role("proctor"))
        self.pill_p.grid(row=0, column=1, padx=2, ipady=5)

        self.lbl_id = tk.Label(self.ui_frame, font=("Helvetica",10,"bold"),
                                bg=t["card_bg"], fg=t["label_fg"], anchor="w")
        self.lbl_id.pack(fill="x", padx=30, pady=(6,0))
        self.eid = self._make_entry(self.ui_frame)

        tk.Label(self.ui_frame, text="Password", font=("Helvetica",10,"bold"),
                 bg=t["card_bg"], fg=t["label_fg"], anchor="w").pack(fill="x", padx=30, pady=(10,0))
        self.epw = self._make_entry(self.ui_frame, show="●")

        bf = tk.Frame(self.ui_frame, bg=t["card_bg"]); bf.pack(pady=18)
        self.btn_login = tk.Button(bf, text="Log In ▶", font=("Helvetica",11,"bold"),
            bd=0, relief="flat", cursor="hand2", width=11, command=self._login)
        self.btn_login.grid(row=0, column=0, padx=8, ipady=6)
        self.btn_reg = tk.Button(bf, text="Register ✚", font=("Helvetica",11,"bold"),
            bd=0, relief="flat", cursor="hand2", width=11, command=self._register)
        self.btn_reg.grid(row=0, column=1, padx=8, ipady=6)

        self.btn_tog = tk.Button(self.root, font=("Helvetica",9), bd=0, relief="flat",
            cursor="hand2", command=self._toggle_theme)
        self.btn_tog.place(x=360, y=55, width=140, height=28)

        self._set_role("student")
        self._apply_theme()

    def _set_role(self, r):
        self.role.set(r); t = self.theme
        if r == "student":
            self.pill_s.configure(bg=t["student_accent"], fg=t["pill_active_fg"])
            self.pill_p.configure(bg=t["pill_inactive_bg"], fg=t["pill_inactive_fg"])
            self.lbl_id.configure(text="Student ID")
            self.btn_reg.configure(state="normal",
                bg=t["btn_secondary_bg"], fg=t["btn_secondary_fg"])
        else:
            self.pill_p.configure(bg=t["proctor_accent"], fg=t["pill_active_fg"])
            self.pill_s.configure(bg=t["pill_inactive_bg"], fg=t["pill_inactive_fg"])
            self.lbl_id.configure(text="Proctor ID")
            self.btn_reg.configure(state="disabled",
                bg=t["pill_inactive_bg"], fg=t["pill_inactive_fg"])

    def _apply_theme(self):
        t = self.theme; self.root.configure(bg=t["bg"])
        self.btn_login.configure(bg=t["btn_primary_bg"], fg=t["btn_primary_fg"])
        self.btn_tog.configure(bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"],
                                text=f"{t['mode_icon']}  {t['mode_text']}")
        for e in [self.eid, self.epw]:
            e.configure(bg=t["entry_bg"], fg=t["entry_fg"],
                        insertbackground=t["entry_fg"])
            e.master.configure(bg=t["entry_border"])
        self._set_role(self.role.get())

    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        self.theme = DARK if self.is_dark else LIGHT
        for p in self.particles:
            p.color = random.choice(self.theme["particle_colors"])
        self._apply_theme()

    def _register(self):
        uid = self.eid.get().strip(); pwd = self.epw.get().strip()
        if not uid or not pwd:
            messagebox.showerror("Error","Fill both fields"); return
        if db_register(uid, pwd):
            try:
                from face_auth import capture_face_registration
                messagebox.showinfo("Face Registration",
                    f"Account '{uid}' created!\nNow register your face.")
                self.root.withdraw()
                capture_face_registration(uid)
                self.root.deiconify()
            except ImportError:
                pass
            messagebox.showinfo("Success","Registered! You can now log in.")
        else:
            messagebox.showerror("Error","Student ID already exists.")

    def _login(self):
        global _hub
        uid = self.eid.get().strip(); pwd = self.epw.get().strip()
        if not uid or not pwd:
            messagebox.showerror("Error","Fill both fields"); return
        role = self.role.get()
        if not db_get_user(uid, pwd, role):
            messagebox.showerror("Login Failed","Wrong ID or password."); return

        if role == "student":
            try:
                from face_auth import verify_face
                self.root.withdraw()
                ok = verify_face(uid)
                self.root.deiconify()
                if not ok:
                    messagebox.showerror("Denied","Face verification failed!"); return
            except ImportError:
                pass
            _hub = CameraHub(uid); _hub.start()
            messagebox.showinfo("Verified", f"Welcome, {uid}! Exam starting.")
            self.animating = False; self.root.destroy()
            StudentExam(uid).run()
        else:
            self.animating = False; self.root.destroy()
            ProctorDash(uid, self.is_dark).run()

    def _close(self):
        self.animating = False; self.root.destroy()

    def run(self): self.root.mainloop()

class StudentExam:
    def __init__(self, student_id):
        self.sid   = student_id
        self.qs    = db_get_questions()
        self.qi    = 0
        self.answers = {}
        self.start = time.time()

        self.root = tk.Tk()
        self.root.title("ExamShield — Exam in Progress")
        self.root.geometry("820x640"); self.root.resizable(True, True)
        self.root.minsize(640, 520)
        self.root.configure(bg="#0d1117")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._tick()

    def _build(self):
        bar = tk.Frame(self.root, bg="#161b22", height=56)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text="🛡️  ExamShield — Live Exam",
                 font=("Helvetica",13,"bold"), bg="#161b22", fg="#58d6d6"
                 ).pack(side="left", padx=16, pady=12)
        self.lbl_timer = tk.Label(bar, text="⏱ 00:00",
            font=("Helvetica",11,"bold"), bg="#161b22", fg="#0be881")
        self.lbl_timer.pack(side="right", padx=16)
        self.lbl_prog = tk.Label(bar, font=("Helvetica",10),
            bg="#161b22", fg="#8b949e")
        self.lbl_prog.pack(side="right", padx=8)

        sb = tk.Frame(self.root, bg="#0d1117", height=24)
        sb.pack(fill="x"); sb.pack_propagate(False)
        self.lbl_status = tk.Label(sb, text="● Exam in progress",
            font=("Helvetica",8), bg="#0d1117", fg="#2a2a3a")
        self.lbl_status.pack(side="left", padx=14)

        self.pbar = tk.Canvas(self.root, height=4, bg="#21262d",
                               highlightthickness=0)
        self.pbar.pack(fill="x")

        main = tk.Frame(self.root, bg="#0d1117"); main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1); main.rowconfigure(0, weight=1)

        nav_strip = tk.Frame(main, bg="#161b22", width=110)
        nav_strip.grid(row=0, column=0, sticky="nsew"); nav_strip.pack_propagate(False)
        tk.Label(nav_strip, text="Questions", font=("Helvetica",8,"bold"),
                 bg="#161b22", fg="#8b949e").pack(pady=(10,6))
        self._q_btns = []
        for i in range(len(self.qs)):
            b = tk.Button(nav_strip, text=str(i+1), font=("Helvetica",8,"bold"),
                bg="#21262d", fg="#8b949e", bd=0, relief="flat",
                cursor="hand2", width=4,
                command=lambda idx=i: self._jump_q(idx))
            b.pack(pady=2, padx=8, ipady=3)
            self._q_btns.append(b)

        qf = tk.Frame(main, bg="#0d1117"); qf.grid(row=0, column=1, sticky="nsew")
        inner = tk.Frame(qf, bg="#0d1117"); inner.pack(fill="both", expand=True, padx=36, pady=18)

        self.lbl_qn = tk.Label(inner, font=("Helvetica",10,"bold"),
                                bg="#0d1117", fg="#8b949e", anchor="w")
        self.lbl_qn.pack(fill="x", pady=(0,6))

        self.lbl_cat = tk.Label(inner, font=("Helvetica",8),
                                 bg="#0d1117", fg="#575fcf", anchor="w")
        self.lbl_cat.pack(fill="x", pady=(0,4))

        self.lbl_q = tk.Label(inner, font=("Helvetica",14,"bold"),
                               bg="#0d1117", fg="#f0f6fc",
                               wraplength=580, justify="left", anchor="w")
        self.lbl_q.pack(fill="x", pady=(0,18))

        self.opt_var = tk.StringVar()
        self.opt_btns = []
        for opt in ["A","B","C","D"]:
            b = tk.Radiobutton(inner, variable=self.opt_var, value=opt,
                font=("Helvetica",12), bg="#161b22", fg="#c9d1d9",
                selectcolor="#0d3b2e", activebackground="#161b22",
                activeforeground="#0be881", indicatoron=True,
                bd=0, relief="flat", anchor="w", padx=16, pady=10, cursor="hand2")
            b.pack(fill="x", pady=4, ipady=4)
            self.opt_btns.append(b)

        self.lbl_marks = tk.Label(inner, font=("Helvetica",8),
                                   bg="#0d1117", fg="#ffd93d", anchor="e")
        self.lbl_marks.pack(fill="x", pady=(4,0))

        nf = tk.Frame(self.root, bg="#0d1117"); nf.pack(pady=12)
        self.btn_prev = tk.Button(nf, text="◀  Prev",
            font=("Helvetica",11,"bold"), bg="#21262d", fg="#c9d1d9",
            bd=0, relief="flat", cursor="hand2", width=10, command=self._prev)
        self.btn_prev.grid(row=0, column=0, padx=6, ipady=6)
        self.btn_next = tk.Button(nf, text="Next  ▶",
            font=("Helvetica",11,"bold"), bg="#575fcf", fg="#ffffff",
            bd=0, relief="flat", cursor="hand2", width=10, command=self._next)
        self.btn_next.grid(row=0, column=1, padx=6, ipady=6)
        self.btn_clr = tk.Button(nf, text="Clear",
            font=("Helvetica",10), bg="#21262d", fg="#ff6b9d",
            bd=0, relief="flat", cursor="hand2", width=7, command=self._clear_ans)
        self.btn_clr.grid(row=0, column=2, padx=6, ipady=6)
        self.btn_sub = tk.Button(nf, text="Submit Exam ✓",
            font=("Helvetica",11,"bold"), bg="#0be881", fg="#0d1117",
            bd=0, relief="flat", cursor="hand2", width=14, command=self._submit)
        self.btn_sub.grid(row=0, column=3, padx=6, ipady=6)

        self._load_q()

    def _load_q(self):
        if not self.qs: return
        q = self.qs[self.qi]; n = len(self.qs)
        self.lbl_qn.configure(text=f"Question {self.qi+1} of {n}")
        self.lbl_q.configure(text=q[1])
        cat = q[8] if len(q) > 8 else "General"
        self.lbl_cat.configure(text=f"📁 {cat}")
        marks = q[7] if len(q) > 7 else 1
        self.lbl_marks.configure(text=f"Marks: {marks}")
        for i, b in enumerate(self.opt_btns):
            b.configure(text=f"  {'ABCD'[i]}.  {q[2+i]}", value="ABCD"[i])
        self.opt_var.set(self.answers.get(self.qi, ""))
        ratio = (self.qi+1)/n
        w = self.root.winfo_width() or 820
        self.pbar.delete("all")
        self.pbar.create_rectangle(0,0,int(w*ratio),4,fill="#0be881",outline="")
        self.lbl_prog.configure(text=f"{self.qi+1}/{n}")
        self.btn_prev.configure(state="normal" if self.qi>0   else "disabled")
        self.btn_next.configure(state="normal" if self.qi<n-1 else "disabled")
        for i, b in enumerate(self._q_btns):
            if i == self.qi:
                b.configure(bg="#575fcf", fg="#ffffff")
            elif i in self.answers:
                b.configure(bg="#0be881", fg="#0d1117")
            else:
                b.configure(bg="#21262d", fg="#8b949e")

    def _save(self):
        a = self.opt_var.get()
        if a: self.answers[self.qi] = a

    def _jump_q(self, idx):
        self._save(); self.qi = idx; self._load_q()

    def _prev(self):
        self._save(); self.qi -= 1; self._load_q()

    def _next(self):
        self._save(); self.qi += 1; self._load_q()

    def _clear_ans(self):
        self.opt_var.set("")
        if self.qi in self.answers:
            del self.answers[self.qi]
        self._load_q()

    def _submit(self):
        self._save()
        un = len(self.qs) - len(self.answers)
        if un > 0 and not messagebox.askyesno("Submit?", f"{un} unanswered. Submit anyway?"):
            return
        self._show_results()

    def _show_results(self):
        score = sum(1 for i, q in enumerate(self.qs) if self.answers.get(i) == q[6])
        total = len(self.qs); elapsed = int(time.time()-self.start)
        pct   = int(score/total*100) if total else 0
        grade = "A" if pct>=90 else "B" if pct>=75 else "C" if pct>=60 else "D" if pct>=40 else "F"
        log = f"{self.sid}_result.csv"
        # FIX: use utf-8-sig so Unicode symbols (✓ ✗) save correctly on Windows
        with open(log, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(["Q#","Question","Your Answer","Correct","Result"])
            for i, q in enumerate(self.qs):
                a = self.answers.get(i, "-")
                w.writerow([i+1, q[1], a, q[6], "✓" if a==q[6] else "✗"])
        messagebox.showinfo("Exam Complete",
            f"Score : {score}/{total}  ({pct}%)\n"
            f"Grade : {grade}\n"
            f"Time  : {elapsed//60:02d}:{elapsed%60:02d}\n\n"
            f"Results saved → {log}")
        if _hub: _hub.stop()
        self.root.destroy()

    def _tick(self):
        e = int(time.time()-self.start); m, s = e//60, e%60
        self.lbl_timer.configure(text=f"⏱ {m:02d}:{s:02d}")
        if _hub:
            sc = _hub.strike_count
            col = "#2a2a3a" if sc==0 else "#6a3800" if sc<3 else "#6a0000"
            txt = f"● Exam in progress  |  Warnings: {sc}/{CameraHub.MAX_STRIKES}"
            self.lbl_status.configure(text=txt, fg=col)
        self.root.after(1000, self._tick)

    def _close(self):
        if messagebox.askyesno("Quit","Exit exam? Progress will be lost."):
            if _hub: _hub.stop()
            self.root.destroy()

    def run(self): self.root.mainloop()

class ProctorDash:
    def __init__(self, proctor_id, is_dark=True):
        self.pid = proctor_id; self.is_dark = is_dark
        self.theme = DARK if is_dark else LIGHT
        self.root = tk.Tk()
        self.root.title(f"ExamShield — Proctor Dashboard  [{proctor_id}]")
        self.root.geometry("1160x720"); self.root.resizable(True, True)
        self.root.minsize(960, 600)
        self.root.configure(bg="#0d1117")
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._poll_camera()
        self._poll_violations()

    def _build(self):
        t = self.theme
        bar = tk.Frame(self.root, bg="#161b22", height=56)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text="👨‍🏫  Proctor Dashboard",
                 font=("Helvetica",13,"bold"), bg="#161b22", fg="#ff6b9d"
                 ).pack(side="left", padx=16, pady=12)
        tk.Label(bar, text=f"│  {self.pid}",
                 font=("Helvetica",10), bg="#161b22", fg="#8b949e"
                 ).pack(side="left")
        tk.Button(bar, text="⬅ Logout", font=("Helvetica",9),
            bd=0, relief="flat", cursor="hand2",
            bg="#21262d", fg="#c9d1d9",
            command=self._logout).pack(side="right", padx=12, pady=10, ipady=4)
        self.btn_tog = tk.Button(bar, font=("Helvetica",9),
            bd=0, relief="flat", cursor="hand2",
            bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"],
            text=f"{t['mode_icon']}  {t['mode_text']}",
            command=self._toggle_theme)
        self.btn_tog.pack(side="right", padx=4, pady=10)

        main = tk.Frame(self.root, bg="#0d1117")
        main.pack(fill="both", expand=True, padx=10, pady=8)
        main.columnconfigure(0, weight=3); main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg="#0d1117")
        left.grid(row=0, column=0, sticky="nsew", padx=(0,8))
        left.rowconfigure(1, weight=1)

        tk.Label(left, text="📷  Live Student Camera",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#58d6d6"
                 ).grid(row=0, column=0, sticky="w", pady=(0,4))

        self.cam_lbl = tk.Label(left, bg="#0b0b13", relief="flat",
                                 text="Waiting for student session…",
                                 fg="#3a3a5a", font=("Helvetica",11))
        self.cam_lbl.grid(row=1, column=0, sticky="nsew")

        sf = tk.Frame(left, bg="#0f1520", height=36)
        sf.grid(row=2, column=0, sticky="ew", pady=(4,0))
        sf.pack_propagate(False)
        self.lbl_faces   = tk.Label(sf, text="Faces: —",   font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881"); self.lbl_faces.pack(side="left",  padx=12)
        self.lbl_gaze    = tk.Label(sf, text="Gaze: —",    font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881"); self.lbl_gaze.pack(side="left",   padx=12)
        self.lbl_strikes = tk.Label(sf, text="Strikes: 0", font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881"); self.lbl_strikes.pack(side="left", padx=12)
        self.lbl_phone   = tk.Label(sf, text="Phone: No",  font=("Helvetica",9,"bold"), bg="#0f1520", fg="#0be881"); self.lbl_phone.pack(side="left",   padx=12)

        right = tk.Frame(main, bg="#0d1117")
        right.grid(row=0, column=1, sticky="nsew")

        style = ttk.Style(); style.theme_use("clam")
        style.configure("P.TNotebook",        background="#0d1117", borderwidth=0)
        style.configure("P.TNotebook.Tab",    background="#21262d", foreground="#c9d1d9",
                                               padding=[10,6], font=("Helvetica",8,"bold"))
        style.map("P.TNotebook.Tab",
                  background=[("selected","#575fcf")],
                  foreground=[("selected","#ffffff")])

        nb = ttk.Notebook(right, style="P.TNotebook")
        nb.pack(fill="both", expand=True)

        vf = tk.Frame(nb, bg="#0d1117"); nb.add(vf, text="⚠ Violations")
        self._build_violations_tab(vf)

        aqf = tk.Frame(nb, bg="#0d1117"); nb.add(aqf, text="➕ Add Q")
        self._build_add_q_tab(aqf)

        qbf = tk.Frame(nb, bg="#0d1117"); nb.add(qbf, text="📋 Bank")
        self._build_qbank_tab(qbf)

        rf = tk.Frame(nb, bg="#0d1117"); nb.add(rf, text="📊 Results")
        self._build_results_tab(rf)

    def _build_violations_tab(self, parent):
        tk.Label(parent, text="Real-time Violation Log",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#ff6b9d"
                 ).pack(anchor="w", padx=8, pady=(8,4))
        scr = tk.Scrollbar(parent); scr.pack(side="right", fill="y")
        self.vlog = tk.Text(parent, font=("Courier",8), bg="#060610",
                             fg="#c9d1d9", bd=0, relief="flat",
                             wrap="word", yscrollcommand=scr.set, state="disabled")
        self.vlog.pack(fill="both", expand=True, padx=8, pady=(0,4))
        scr.configure(command=self.vlog.yview)
        tk.Button(parent, text="Clear Log", font=("Helvetica",8),
            bg="#21262d", fg="#8b949e", bd=0, relief="flat", cursor="hand2",
            command=lambda: (self.vlog.configure(state="normal"),
                             self.vlog.delete("1.0","end"),
                             self.vlog.configure(state="disabled"))
            ).pack(pady=(0,6))

    def _build_add_q_tab(self, parent):
        tk.Label(parent, text="Add New Question",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#0be881"
                 ).pack(anchor="w", padx=10, pady=(10,4))

        canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr    = tk.Scrollbar(parent, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._aq = {}

        def lbl(text): tk.Label(inner, text=text, font=("Helvetica",9,"bold"),
                                  bg="#0d1117", fg="#c9d1d9", anchor="w"
                                  ).pack(fill="x", padx=10, pady=(8,0))

        lbl("Question *")
        self._aq["q"] = tk.Text(inner, font=("Helvetica",10),
            bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
            bd=0, relief="flat", height=3)
        self._aq["q"].pack(fill="x", padx=10, pady=(2,0), ipady=4)

        for key, label in [("a","Option A *"),("b","Option B *"),
                            ("c","Option C *"),("d","Option D *")]:
            lbl(label)
            self._aq[key] = tk.Entry(inner, font=("Helvetica",10),
                bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc",
                bd=0, relief="flat")
            self._aq[key].pack(fill="x", padx=10, pady=(2,0), ipady=6)

        lbl("Correct Answer")
        self._aq_ans = tk.StringVar(value="A")
        af = tk.Frame(inner, bg="#0d1117"); af.pack(padx=10, anchor="w", pady=(2,0))
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=self._aq_ans, value=opt,
                font=("Helvetica",10,"bold"), bg="#0d1117", fg="#0be881",
                selectcolor="#0d3b2e", activebackground="#0d1117",
                ).pack(side="left", padx=8)

        row2 = tk.Frame(inner, bg="#0d1117"); row2.pack(fill="x", padx=10, pady=(6,0))
        tk.Label(row2, text="Marks", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["marks"] = tk.Entry(row2, font=("Helvetica",10), width=5,
            bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc", bd=0, relief="flat")
        self._aq["marks"].insert(0,"1")
        self._aq["marks"].pack(side="left", padx=(4,16), ipady=5)
        tk.Label(row2, text="Category", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        self._aq["cat"] = tk.Entry(row2, font=("Helvetica",10), width=12,
            bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc", bd=0, relief="flat")
        self._aq["cat"].insert(0,"General")
        self._aq["cat"].pack(side="left", padx=(4,0), ipady=5)

        tk.Button(inner, text="💾  Save Question",
            font=("Helvetica",10,"bold"), bg="#0be881", fg="#0d1117",
            bd=0, relief="flat", cursor="hand2",
            command=self._save_question
            ).pack(fill="x", padx=10, pady=14, ipady=8)

    def _save_question(self):
        q = self._aq["q"].get("1.0","end").strip()
        a = self._aq["a"].get().strip(); b = self._aq["b"].get().strip()
        c = self._aq["c"].get().strip(); d = self._aq["d"].get().strip()
        ans = self._aq_ans.get()
        cat = self._aq["cat"].get().strip() or "General"
        try: marks = int(self._aq["marks"].get())
        except: marks = 1
        if not all([q, a, b, c, d]):
            messagebox.showerror("Error","Fill all required fields."); return
        db_add_question(q, a, b, c, d, ans, marks, cat)
        messagebox.showinfo("Saved","Question added to bank ✓")
        self._aq["q"].delete("1.0","end")
        for k in ["a","b","c","d"]:
            self._aq[k].delete(0,"end")
        self._aq["marks"].delete(0,"end"); self._aq["marks"].insert(0,"1")
        self._aq["cat"].delete(0,"end");   self._aq["cat"].insert(0,"General")
        self._refresh_qbank()

    def _build_qbank_tab(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Question Bank", font=("Helvetica",10,"bold"),
                 bg="#0d1117", fg="#ffd93d").pack(side="left")
        tk.Button(top, text="↺ Refresh", font=("Helvetica",8),
            bg="#21262d", fg="#8b949e", bd=0, relief="flat", cursor="hand2",
            command=self._refresh_qbank).pack(side="right", ipady=3, padx=4)

        self._qb_canvas = tk.Canvas(parent, bg="#0d1117", highlightthickness=0)
        scr = tk.Scrollbar(parent, command=self._qb_canvas.yview)
        self._qb_canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y")
        self._qb_canvas.pack(fill="both", expand=True, padx=6)
        self._qb_inner = tk.Frame(self._qb_canvas, bg="#0d1117")
        self._qb_win   = self._qb_canvas.create_window((0,0), window=self._qb_inner, anchor="nw")
        self._qb_inner.bind("<Configure>",
            lambda e: self._qb_canvas.configure(scrollregion=self._qb_canvas.bbox("all")))
        self._refresh_qbank()

    def _refresh_qbank(self):
        for w in self._qb_inner.winfo_children(): w.destroy()
        qs = db_get_questions()
        if not qs:
            tk.Label(self._qb_inner, text="No questions yet.",
                     font=("Helvetica",9), bg="#0d1117", fg="#8b949e"
                     ).pack(padx=10, pady=10); return
        for q in qs:
            card = tk.Frame(self._qb_inner, bg="#161b22", relief="flat")
            card.pack(fill="x", padx=4, pady=3)
            txt = q[1][:65]+"…" if len(q[1])>65 else q[1]
            cat = q[8] if len(q)>8 else "—"
            tk.Label(card, text=f"Q{q[0]}: {txt}",
                     font=("Helvetica",9), bg="#161b22", fg="#c9d1d9",
                     anchor="w", wraplength=230, justify="left"
                     ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            info = tk.Frame(card, bg="#161b22"); info.pack(side="left")
            tk.Label(info, text=f"Ans: {q[6]}", font=("Helvetica",8,"bold"),
                     bg="#161b22", fg="#0be881").pack(anchor="e")
            tk.Label(info, text=f"{q[7]}mk  {cat}", font=("Helvetica",7),
                     bg="#161b22", fg="#575fcf").pack(anchor="e")
            tk.Button(card, text="✏", font=("Helvetica",10), bg="#161b22", fg="#ffd93d",
                bd=0, relief="flat", cursor="hand2",
                command=lambda row=q: self._edit_q_window(row)
                ).pack(side="right", padx=2)
            tk.Button(card, text="🗑", font=("Helvetica",10), bg="#161b22", fg="#ff6b9d",
                bd=0, relief="flat", cursor="hand2",
                command=lambda qid=q[0]: self._delete_q(qid)
                ).pack(side="right", padx=2)

    def _delete_q(self, qid):
        if messagebox.askyesno("Delete", f"Delete question {qid}?"):
            db_delete_question(qid); self._refresh_qbank()

    def _edit_q_window(self, row):
        win = tk.Toplevel(self.root)
        win.title(f"Edit Question {row[0]}")
        win.geometry("520x500"); win.configure(bg="#0d1117")
        win.grab_set()

        fields = {}

        def lbl(p, text):
            tk.Label(p, text=text, font=("Helvetica",9,"bold"),
                     bg="#0d1117", fg="#c9d1d9", anchor="w").pack(fill="x", padx=16, pady=(6,0))

        canvas = tk.Canvas(win, bg="#0d1117", highlightthickness=0)
        scr = tk.Scrollbar(win, command=canvas.yview)
        canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg="#0d1117")
        canvas.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        lbl(inner, "Question")
        fields["q"] = tk.Text(inner, font=("Helvetica",10), bg="#161b22",
                               fg="#f0f6fc", insertbackground="#f0f6fc",
                               bd=0, relief="flat", height=3)
        fields["q"].insert("1.0", row[1])
        fields["q"].pack(fill="x", padx=16, pady=(2,0), ipady=4)

        for i, (key, label) in enumerate([("a","Option A"),("b","Option B"),
                                           ("c","Option C"),("d","Option D")]):
            lbl(inner, label)
            fields[key] = tk.Entry(inner, font=("Helvetica",10), bg="#161b22",
                                    fg="#f0f6fc", insertbackground="#f0f6fc",
                                    bd=0, relief="flat")
            fields[key].insert(0, row[2+i])
            fields[key].pack(fill="x", padx=16, pady=(2,0), ipady=6)

        lbl(inner, "Correct Answer")
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
            bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc", bd=0, relief="flat")
        fields["marks"].insert(0, str(row[7]) if len(row)>7 else "1")
        fields["marks"].pack(side="left", padx=(4,16), ipady=5)
        tk.Label(row2, text="Category", font=("Helvetica",9,"bold"),
                 bg="#0d1117", fg="#c9d1d9").pack(side="left")
        fields["cat"] = tk.Entry(row2, font=("Helvetica",10), width=12,
            bg="#161b22", fg="#f0f6fc", insertbackground="#f0f6fc", bd=0, relief="flat")
        fields["cat"].insert(0, row[8] if len(row)>8 else "General")
        fields["cat"].pack(side="left", padx=(4,0), ipady=5)

        def save():
            q   = fields["q"].get("1.0","end").strip()
            a   = fields["a"].get().strip(); b = fields["b"].get().strip()
            c   = fields["c"].get().strip(); d = fields["d"].get().strip()
            ans = ans_var.get()
            cat = fields["cat"].get().strip() or "General"
            try: marks = int(fields["marks"].get())
            except: marks = 1
            if not all([q,a,b,c,d]):
                messagebox.showerror("Error","Fill all fields"); return
            db_update_question(row[0], q, a, b, c, d, ans, marks, cat)
            messagebox.showinfo("Updated","Question updated ✓")
            win.destroy(); self._refresh_qbank()

        tk.Button(inner, text="💾  Update Question",
            font=("Helvetica",10,"bold"), bg="#575fcf", fg="#ffffff",
            bd=0, relief="flat", cursor="hand2",
            command=save).pack(fill="x", padx=16, pady=14, ipady=8)

    def _build_results_tab(self, parent):
        top = tk.Frame(parent, bg="#0d1117"); top.pack(fill="x", padx=8, pady=(8,4))
        tk.Label(top, text="Exam Results & Logs",
                 font=("Helvetica",10,"bold"), bg="#0d1117", fg="#575fcf"
                 ).pack(side="left")
        tk.Button(top, text="↺ Refresh", font=("Helvetica",8),
            bg="#21262d", fg="#8b949e", bd=0, relief="flat", cursor="hand2",
            command=self._refresh_results).pack(side="right", ipady=3, padx=4)
        self._res_frame = tk.Frame(parent, bg="#0d1117")
        self._res_frame.pack(fill="both", expand=True, padx=6)
        self._refresh_results()

    def _refresh_results(self):
        for w in self._res_frame.winfo_children(): w.destroy()
        files = [f for f in os.listdir('.')
                 if f.endswith('_result.csv') or f.endswith('_exam_log.csv')]
        if not files:
            tk.Label(self._res_frame, text="No result files yet.",
                     font=("Helvetica",9), bg="#0d1117", fg="#8b949e"
                     ).pack(padx=10, pady=10); return
        for fname in sorted(files):
            row = tk.Frame(self._res_frame, bg="#161b22")
            row.pack(fill="x", padx=4, pady=3)
            tk.Label(row, text=fname, font=("Courier",9),
                     bg="#161b22", fg="#c9d1d9", anchor="w"
                     ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            tk.Button(row, text="View", font=("Helvetica",8,"bold"),
                bg="#575fcf", fg="#fff", bd=0, relief="flat", cursor="hand2",
                command=lambda f=fname: self._view_file(f)
                ).pack(side="right", padx=6, pady=4, ipady=2)

    def _view_file(self, fname):
        try:
            with open(fname, encoding='utf-8-sig') as f: content = f.read()
        except:
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

    def _poll_camera(self):
        if _hub:
            frame = _hub.get_frame()
            if frame is not None:
                lw = max(self.cam_lbl.winfo_width(),  10)
                lh = max(self.cam_lbl.winfo_height(), 10)
                h, w = frame.shape[:2]
                scale = min(lw/w, lh/h)
                nw, nh = max(1,int(w*scale)), max(1,int(h*scale))
                resized = cv2.resize(frame, (nw, nh))
                img = ImageTk.PhotoImage(
                    Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)))
                self.cam_lbl.configure(image=img, text="")
                self.cam_lbl.image = img
            fc = _hub.face_count; gd = _hub.gaze_dir
            sc = _hub.strike_count; ph = _hub.phone_detected
            self.lbl_faces.configure(
                text=f"Faces: {fc}",
                fg="#0be881" if fc==1 else "#ff4444")
            self.lbl_gaze.configure(
                text=f"Gaze: {gd}",
                fg="#0be881" if gd=="center" else "#ffaa00")
            self.lbl_strikes.configure(
                text=f"Strikes: {sc}/{CameraHub.MAX_STRIKES}",
                fg="#0be881" if sc==0 else "#ffaa00" if sc<3 else "#ff4444")
            self.lbl_phone.configure(
                text=f"Phone: {'⚠ YES' if ph else 'No'}",
                fg="#ff4444" if ph else "#0be881")
        else:
            self.cam_lbl.configure(
                text="No active student session", fg="#3a3a5a", image="")
        self.root.after(100, self._poll_camera)

    def _poll_violations(self):
        if _hub:
            viols = list(_hub.violations)
            self.vlog.configure(state="normal")
            self.vlog.delete("1.0","end")
            for v in viols:
                tag = ("strike" if "STRIKE" in v
                       else "warn"   if "WARNING" in v
                       else "ok"     if "START" in v
                       else "info")
                self.vlog.insert("end", v+"\n", tag)
            self.vlog.tag_configure("strike", foreground="#ff4444")
            self.vlog.tag_configure("warn",   foreground="#ffaa00")
            self.vlog.tag_configure("ok",     foreground="#0be881")
            self.vlog.tag_configure("info",   foreground="#8b949e")
            self.vlog.configure(state="disabled")
            self.vlog.see("end")
        self.root.after(1000, self._poll_violations)

    def _toggle_theme(self):
        self.is_dark = not self.is_dark
        self.theme   = DARK if self.is_dark else LIGHT
        t = self.theme
        self.btn_tog.configure(
            text=f"{t['mode_icon']}  {t['mode_text']}",
            bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"])

    def _logout(self):
        if _hub: _hub.stop()
        self.root.destroy()
        MainLogin().run()

    def _close(self):
        if _hub: _hub.stop()
        self.root.destroy()

    def run(self): self.root.mainloop()

if __name__ == "__main__":
    init_db()
    try:
        from face_auth import init_face_db
        init_face_db()
    except ImportError:
        pass
    MainLogin().run()