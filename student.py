"""
ExamShield — Student
Run:     python student.py
Requires: pip install requests flask flask-cors opencv-python pillow
"""

import tkinter as tk
from tkinter import messagebox
import threading, json, time, requests, socket
from datetime import datetime

try:
    import cv2
    from PIL import Image, ImageTk
    CAM_OK = True
except ImportError:
    CAM_OK = False

# ══════════════════════════════════════════════════════════════════════
#  ✅ HARDCODED CREDENTIALS — Students never type these
#     Paste your real values from JSONBin.io here:
# ══════════════════════════════════════════════════════════════════════
JSONBIN_API_KEY = "$2a$10$zbvcyOoivJn5m4prv6JuTexhyOA8nWz5YiQA8IoErO3hMUEA7zrHu"   # ← paste your key
JSONBIN_BIN_ID  = "69af043843b1c97be9c5cc3e"          # ← paste your bin ID

# ══════════════════════════════════════════════════════════════════════
#  BUILT-IN FLASK CAMERA SERVER
# ══════════════════════════════════════════════════════════════════════
_cam_frame  = None
_cam_lock   = threading.Lock()
_flask_port = 5050

def _start_flask_server():
    try:
        from flask import Flask, Response, jsonify
        from flask_cors import CORS
        import logging
    except ImportError:
        print("[⚠] Flask not installed! Run: pip install flask flask-cors")
        return

    flask_app = Flask("ExamShieldCam")
    CORS(flask_app)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @flask_app.after_request
    def cf_bypass(resp):
        resp.headers["Access-Control-Allow-Origin"]  = "*"
        resp.headers["Access-Control-Allow-Headers"] = "X-ExamShield-Key, Content-Type"
        return resp

    @flask_app.route("/ping")
    def ping():
        return jsonify(status="ok", student_id=_student_name, exam_live=True)

    @flask_app.route("/frame")
    def frame():
        global _cam_frame
        with _cam_lock:
            f = _cam_frame
        if f is None:
            import numpy as np
            blank = np.zeros((360, 640, 3), dtype="uint8")
            _, buf = cv2.imencode(".jpg", blank, [cv2.IMWRITE_JPEG_QUALITY, 50])
            return Response(buf.tobytes(), mimetype="image/jpeg")
        _, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buf.tobytes(), mimetype="image/jpeg")

    @flask_app.route("/stats")
    def stats():
        return jsonify(
            live=True,
            student_id=_student_name,
            face_count=1,
            gaze_dir="forward",
            strike_count=0,
            max_strikes=5,
            phone_detected=False,
        )

    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=_flask_port,
                                     threaded=True, debug=False),
        daemon=True
    ).start()

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"

    print(f"\n{'═'*60}")
    print(f"  🎥 Camera server running on port {_flask_port}")
    print(f"  🔗 Local:  http://{ip}:{_flask_port}")
    print(f"  ☁️  Run this in a NEW terminal to get Cloudflare link:")
    print(f"  Windows: .\\cloudflared.exe tunnel --url http://localhost:{_flask_port}")
    print(f"  Mac/Lin: ./cloudflared tunnel --url http://localhost:{_flask_port}")
    print(f"  Then give the proctor that https://xxxx.trycloudflare.com link")
    print(f"  Key to use: examshield2024")
    print(f"{'═'*60}\n")


_student_name = "none"

BASE = "https://api.jsonbin.io/v3"

BG  = "#0d0f1a"; SUR = "#141726"; BOR = "#1e2235"
ACC = "#00e5b0"; AC2 = "#7c6af7"; GRN = "#3dffa0"
YEL = "#ffd166"; RED = "#f7426a"; TXT = "#c8cde8"
DIM = "#4a5070"; WHT = "#ffffff"


def hdrs(api_key):
    h = {"Content-Type": "application/json"}
    if api_key: h["X-Master-Key"] = api_key
    return h


class Student(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ExamShield — Live Exam")
        self.geometry("820x620")
        self.configure(bg=BG)
        self.resizable(True, True)

        # ✅ Credentials come from the hardcoded constants above
        self.api_key  = JSONBIN_API_KEY
        self.bin_id   = JSONBIN_BIN_ID
        self.s_name   = ""
        self.questions = []
        self.answers   = []
        self.cur_q     = 0
        self.time_left = 0
        self.warnings  = 0
        self._submitted   = False
        self._timer_cb    = None
        self._poll_cb     = None
        self._cam         = None
        self._cam_running = False
        self._cam_label   = None

        self._screen_login()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────
    # SCREEN: LOGIN  (name only — no API key / Bin ID fields)
    # ─────────────────────────────────────────────────────────
    def _screen_login(self):
        self._clear()
        outer = tk.Frame(self, bg=BG)
        outer.place(relx=.5, rely=.5, anchor="center")

        tk.Label(outer, text="ExamShield", font=("Courier New",18,"bold"),
                 bg=BG, fg=ACC).pack()
        tk.Label(outer, text="Enter your name to join the exam",
                 font=("Segoe UI",10), bg=BG, fg=DIM).pack(pady=(2,20))

        card = tk.Frame(outer, bg=SUR,
                        highlightbackground=BOR, highlightthickness=1)
        card.pack(ipadx=24, ipady=18)

        # ✅ Only ONE field — student name
        tk.Label(card, text="Your Name / Student ID", bg=SUR, fg=DIM,
                 font=("Segoe UI",9)
                 ).grid(row=0, column=0, sticky="w", padx=14, pady=(10,0), columnspan=2)
        self.v_name = tk.StringVar()
        tk.Entry(card, textvariable=self.v_name, width=38,
                 bg=BG, fg=TXT, insertbackground=TXT,
                 font=("Courier New",10), relief="flat",
                 highlightbackground=BOR, highlightthickness=1
                 ).grid(row=1, column=0, columnspan=2, padx=14, sticky="ew")

        tk.Button(card, text="  Join Exam →  ", command=self._do_join,
                  bg=ACC, fg=BG, font=("Segoe UI",11,"bold"),
                  relief="flat", cursor="hand2", padx=12, pady=8
                  ).grid(row=2, column=0, columnspan=2,
                         sticky="ew", padx=14, pady=(16,4))

        self.lbl_err = tk.Label(card, text="", bg=SUR, fg=RED,
                                font=("Segoe UI",9), wraplength=340)
        self.lbl_err.grid(row=3, column=0, columnspan=2, pady=4)

    def _do_join(self):
        global _student_name
        name = self.v_name.get().strip()
        if not name:
            self.lbl_err.config(text="Please enter your name.")
            return

        # ✅ Validate that credentials were actually filled in by the developer
        if "REPLACE_WITH" in self.api_key or "REPLACE_WITH" in self.bin_id:
            self.lbl_err.config(
                text="❌ Developer: open student.py and fill in\n"
                     "JSONBIN_API_KEY and JSONBIN_BIN_ID at the top of the file.")
            return

        self.s_name   = name
        _student_name = name
        self._screen_waiting()
        threading.Thread(target=self._register_student, daemon=True).start()

    # ─────────────────────────────────────────────────────────
    # SCREEN: WAITING
    # ─────────────────────────────────────────────────────────
    def _screen_waiting(self):
        self._clear()
        f = tk.Frame(self, bg=BG)
        f.place(relx=.5, rely=.5, anchor="center")
        tk.Label(f, text="ExamShield", font=("Courier New",16,"bold"),
                 bg=BG, fg=ACC).pack()
        tk.Label(f, text="Waiting for exam to start…",
                 font=("Segoe UI",13,"bold"), bg=BG, fg=TXT).pack(pady=14)
        self.lbl_wait = tk.Label(f, text="Connecting to server…",
                                 font=("Segoe UI",10), bg=BG, fg=DIM)
        self.lbl_wait.pack()

    def _set_wait(self, msg):
        try: self.lbl_wait.config(text=msg)
        except: pass

    def _register_student(self):
        try:
            r = requests.get(f"{BASE}/b/{self.bin_id}/latest",
                             headers=hdrs(self.api_key), timeout=12)
            if not r.ok:
                self.after(0, lambda: self._set_wait(
                    f"❌ Could not connect (HTTP {r.status_code})\n{r.text[:80]}"))
                return
            data = r.json().get("record", {})
        except Exception as e:
            self.after(0, lambda: self._set_wait(f"❌ Network error: {e}")); return

        if "students" not in data: data["students"] = {}
        data["students"][self.s_name] = {
            "joined_at": int(time.time()), "status": "waiting"}
        try:
            r2 = requests.put(f"{BASE}/b/{self.bin_id}", json=data,
                              headers=hdrs(self.api_key), timeout=12)
            if r2.ok:
                self.after(0, lambda: self._set_wait(
                    f"✅ Registered as '{self.s_name}'. Waiting for proctor to publish…"))
                self._poll_cb = self.after(4000, self._poll_exam)
            else:
                self.after(0, lambda: self._set_wait(
                    f"❌ Register failed (HTTP {r2.status_code})"))
        except Exception as e:
            self.after(0, lambda: self._set_wait(f"❌ {e}"))

    def _poll_exam(self):
        threading.Thread(target=self._check_exam, daemon=True).start()

    def _check_exam(self):
        try:
            r = requests.get(f"{BASE}/b/{self.bin_id}/latest",
                             headers=hdrs(self.api_key), timeout=10)
            if r.ok:
                exam = r.json().get("record",{}).get("exam",{})
                if exam.get("published") and exam.get("questions"):
                    self.after(0, lambda: self._start_exam(exam))
                    return
        except: pass
        self._poll_cb = self.after(5000, self._poll_exam)

    # ─────────────────────────────────────────────────────────
    # SCREEN: EXAM
    # ─────────────────────────────────────────────────────────
    def _start_exam(self, exam):
        if self._poll_cb: self.after_cancel(self._poll_cb)
        self.questions = exam["questions"]
        self.answers   = [None] * len(self.questions)
        self.time_left = int(exam.get("duration", 30)) * 60
        self.cur_q     = 0
        self._build_exam_ui()
        self._show_q(0)
        self._tick()
        self._start_camera()
        self.bind("<FocusOut>", self._warn)

    def _build_exam_ui(self):
        self._clear()

        hdr = tk.Frame(self, bg=SUR, highlightbackground=BOR, highlightthickness=1)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ExamShield — Live Exam",
                 font=("Courier New",14,"bold"), bg=SUR, fg=ACC
                 ).pack(side="left", padx=14, pady=8)
        self.lbl_qnum  = tk.Label(hdr, text="", font=("Segoe UI",10),
                                  bg=SUR, fg=DIM)
        self.lbl_qnum.pack(side="left", padx=8)
        self.lbl_timer = tk.Label(hdr, text="00:00",
                                  font=("Courier New",14,"bold"), bg=SUR, fg=ACC)
        self.lbl_timer.pack(side="right", padx=16)

        self._cam_label = tk.Label(hdr, bg=SUR,
                                   text="📷 No camera", fg=DIM,
                                   font=("Segoe UI",8))
        self._cam_label.pack(side="right", padx=8, pady=4)

        sb = tk.Frame(self, bg=BG); sb.pack(fill="x")
        tk.Label(sb, text="● Exam in progress", bg=BG, fg=GRN,
                 font=("Segoe UI",9)).pack(side="left", padx=12, pady=3)
        self.lbl_warn = tk.Label(sb, text="Warnings: 0/5",
                                 bg=BG, fg=DIM, font=("Segoe UI",9))
        self.lbl_warn.pack(side="left", padx=8)

        tk.Canvas(self, height=3, bg=BOR, highlightthickness=0
                  ).pack(fill="x", side="top")

        body = tk.Frame(self, bg=BG); body.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(body, bg=SUR, width=70,
                                highlightbackground=BOR, highlightthickness=1)
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        tk.Label(self.sidebar, text="Qs", bg=SUR, fg=DIM,
                 font=("Segoe UI",8)).pack(pady=(8,4))
        self.nav_frame = tk.Frame(self.sidebar, bg=SUR)
        self.nav_frame.pack(fill="both", expand=True, padx=6)

        self.content = tk.Frame(body, bg=BG)
        self.content.pack(side="left", fill="both", expand=True, padx=22, pady=14)

        self.lbl_qhdr  = tk.Label(self.content, text="", font=("Segoe UI",9),
                                   bg=BG, fg=DIM, anchor="w")
        self.lbl_qhdr.pack(fill="x")
        self.lbl_qcat  = tk.Label(self.content, text="", font=("Segoe UI",9),
                                   bg=BG, fg=AC2, anchor="w")
        self.lbl_qcat.pack(fill="x", pady=(2,8))
        self.lbl_qtext = tk.Label(self.content, text="",
                                   font=("Segoe UI",14,"bold"),
                                   bg=BG, fg=TXT, wraplength=580,
                                   justify="left", anchor="w")
        self.lbl_qtext.pack(fill="x", pady=(0,14))
        self.opts_frame = tk.Frame(self.content, bg=BG)
        self.opts_frame.pack(fill="x")
        self.lbl_marks = tk.Label(self.content, text="", font=("Courier New",9),
                                   bg=BG, fg=YEL, anchor="e")
        self.lbl_marks.pack(fill="x", pady=6)

        foot = tk.Frame(self, bg=SUR, highlightbackground=BOR, highlightthickness=1)
        foot.pack(fill="x", side="bottom")
        btn = dict(font=("Segoe UI",10), relief="flat", cursor="hand2", padx=12, pady=6)
        tk.Button(foot, text="◀ Prev",   command=self._prev_q,
                  bg=SUR, fg=TXT, **btn).pack(side="left", padx=8, pady=8)
        tk.Button(foot, text="Next ▶",   command=self._next_q,
                  bg=AC2, fg=WHT, **btn).pack(side="left", padx=4, pady=8)
        tk.Button(foot, text="Clear",    command=self._clear_ans,
                  bg=SUR, fg=DIM,
                  highlightbackground=BOR, highlightthickness=1,
                  **btn).pack(side="left", padx=4, pady=8)
        tk.Button(foot, text="  Submit Exam ✓  ", command=self._confirm_submit,
                  bg=GRN, fg=BG, font=("Segoe UI",11,"bold"),
                  relief="flat", cursor="hand2",
                  padx=14, pady=6).pack(side="right", padx=12, pady=8)

    def _show_q(self, idx):
        self.cur_q = idx
        q = self.questions[idx]
        n = len(self.questions)
        self.lbl_qhdr.config( text=f"Question {idx+1} of {n}")
        self.lbl_qcat.config( text=q.get("category","General"))
        self.lbl_qtext.config(text=q["text"])
        self.lbl_marks.config(text=f"Marks: {q.get('marks',1)}")
        self.lbl_qnum.config( text=f"{idx+1}/{n}")

        for w in self.opts_frame.winfo_children(): w.destroy()
        for i, opt in enumerate(q["options"]):
            sel    = self.answers[idx] == i
            bg_c   = "#182b24" if sel else SUR
            fg_c   = ACC if sel else TXT
            bdr_c  = ACC if sel else BOR
            row = tk.Frame(self.opts_frame, bg=bg_c,
                           highlightbackground=bdr_c, highlightthickness=1,
                           cursor="hand2")
            row.pack(fill="x", pady=4)
            row.bind("<Button-1>", lambda e, i=i: self._pick(i))

            cv = tk.Canvas(row, width=18, height=18, bg=bg_c, highlightthickness=0)
            cv.pack(side="left", padx=(12,8), pady=12)
            cv.create_oval(2,2,16,16, outline=fg_c, width=2)
            if sel: cv.create_oval(5,5,13,13, fill=ACC, outline="")
            cv.bind("<Button-1>", lambda e, i=i: self._pick(i))

            lbl = tk.Label(row, text=opt, bg=bg_c, fg=fg_c,
                           font=("Segoe UI",11), anchor="w",
                           wraplength=500, justify="left")
            lbl.pack(side="left", fill="x", expand=True, pady=12, padx=(0,12))
            lbl.bind("<Button-1>", lambda e, i=i: self._pick(i))

        self._refresh_nav()

    def _pick(self, i):
        self.answers[self.cur_q] = i; self._show_q(self.cur_q)

    def _clear_ans(self):
        self.answers[self.cur_q] = None; self._show_q(self.cur_q)

    def _prev_q(self):
        if self.cur_q > 0: self._show_q(self.cur_q - 1)

    def _next_q(self):
        if self.cur_q < len(self.questions)-1: self._show_q(self.cur_q + 1)

    def _refresh_nav(self):
        for w in self.nav_frame.winfo_children(): w.destroy()
        for i in range(len(self.questions)):
            is_cur = i == self.cur_q
            is_ans = self.answers[i] is not None
            bg_c   = AC2 if is_cur else ("#182b24" if is_ans else SUR)
            fg_c   = WHT if is_cur else (ACC if is_ans else DIM)
            bdr    = AC2 if is_cur else (ACC if is_ans else BOR)
            tk.Button(self.nav_frame, text=str(i+1), width=3,
                      bg=bg_c, fg=fg_c, font=("Courier New",9,"bold"),
                      relief="flat", cursor="hand2",
                      highlightbackground=bdr, highlightthickness=1,
                      command=lambda i=i: self._show_q(i)
                      ).pack(pady=2)

    def _tick(self):
        if self._submitted: return
        self.time_left -= 1
        m, s = divmod(max(self.time_left, 0), 60)
        self.lbl_timer.config(text=f"{m:02d}:{s:02d}",
                              fg=RED if self.time_left<=60 else
                                 YEL if self.time_left<=300 else ACC)
        if self.time_left <= 0:
            messagebox.showinfo("Time Up","Time is up! Submitting…")
            self._submit(); return
        self._timer_cb = self.after(1000, self._tick)

    def _warn(self, e=None):
        if self._submitted: return
        self.warnings += 1
        self.lbl_warn.config(text=f"Warnings: {self.warnings}/5")
        if self.warnings >= 5:
            messagebox.showwarning("Max Warnings","Auto-submitting exam!"); self._submit()

    def _confirm_submit(self):
        unanswered = self.answers.count(None)
        msg = f"Submit exam?\n{len(self.questions)-unanswered}/{len(self.questions)} answered."
        if unanswered: msg += f"\n\n⚠ {unanswered} unanswered."
        if messagebox.askyesno("Submit", msg): self._submit()

    def _submit(self):
        if self._submitted: return
        self._submitted = True
        if self._timer_cb: self.after_cancel(self._timer_cb)
        self._stop_camera()
        score = sum(q.get("marks",1) for i,q in enumerate(self.questions)
                    if self.answers[i] == q["correct"])
        total = sum(q.get("marks",1) for q in self.questions)
        threading.Thread(target=self._do_submit, args=(score,total), daemon=True).start()

    def _do_submit(self, score, total):
        try:
            r = requests.get(f"{BASE}/b/{self.bin_id}/latest",
                             headers=hdrs(self.api_key), timeout=12)
            data = r.json().get("record",{}) if r.ok else {}
            if "results" not in data: data["results"] = {}
            data["results"][self.s_name] = {
                "score": score, "total": total,
                "answers": self.answers, "submitted_at": int(time.time())}
            if self.s_name in data.get("students",{}):
                data["students"][self.s_name]["status"] = "submitted"
            requests.put(f"{BASE}/b/{self.bin_id}", json=data,
                         headers=hdrs(self.api_key), timeout=12)
        except: pass
        self.after(0, lambda: self._screen_result(score, total))

    # ─────────────────────────────────────────────────────────
    # SCREEN: RESULT
    # ─────────────────────────────────────────────────────────
    def _screen_result(self, score, total):
        self._clear()
        f = tk.Frame(self, bg=BG); f.place(relx=.5, rely=.5, anchor="center")
        pct   = round(score/total*100) if total else 0
        color = GRN if pct>=60 else YEL if pct>=40 else RED

        tk.Label(f, text="Exam Submitted!", font=("Segoe UI",16,"bold"),
                 bg=BG, fg=TXT).pack(pady=(0,12))

        c = tk.Canvas(f, width=160, height=160, bg=BG, highlightthickness=0)
        c.pack()
        cx=cy=80; r=65; ext=359.9*pct/100
        c.create_arc(cx-r,cy-r,cx+r,cy+r, start=90, extent=-359.9,
                     style="arc", outline=BOR, width=12)
        if ext > 0:
            c.create_arc(cx-r,cy-r,cx+r,cy+r, start=90, extent=-ext,
                         style="arc", outline=color, width=12)
        c.create_text(cx, cy-10, text=f"{score}/{total}",
                      fill=color, font=("Segoe UI",20,"bold"))
        c.create_text(cx, cy+14, text=f"{pct}%",
                      fill=DIM, font=("Segoe UI",11))

        tk.Label(f, text=f"Student: {self.s_name}", font=("Segoe UI",10),
                 bg=BG, fg=DIM).pack(pady=(12,2))
        tk.Label(f, text="✅ Result sent to proctor", font=("Segoe UI",10),
                 bg=BG, fg=GRN).pack(pady=(0,12))

        bk = tk.Frame(f, bg=SUR, highlightbackground=BOR, highlightthickness=1)
        bk.pack(fill="x", pady=4)
        tk.Label(bk, text="Question Breakdown", bg=SUR, fg=DIM,
                 font=("Segoe UI",9,"bold")).pack(anchor="w", padx=12, pady=(8,4))
        for i,q in enumerate(self.questions):
            ok   = self.answers[i] == q["correct"]
            row  = tk.Frame(bk, bg=SUR); row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=f"Q{i+1}", bg=SUR, fg=DIM,
                     font=("Courier New",9), width=4).pack(side="left")
            tk.Label(row, text="✓" if ok else "✗", bg=SUR,
                     fg=GRN if ok else RED,
                     font=("Courier New",9,"bold"), width=2).pack(side="left")
            ans = q["options"][self.answers[i]] if self.answers[i] is not None else "—"
            tk.Label(row, text=ans[:55], bg=SUR, fg=TXT,
                     font=("Segoe UI",9)).pack(side="left")
        tk.Label(bk, text="", bg=SUR).pack(pady=2)

    # ─────────────────────────────────────────────────────────
    # CAMERA
    # ─────────────────────────────────────────────────────────
    def _start_camera(self):
        global _cam_frame, _cam_lock
        if not CAM_OK:
            if self._cam_label:
                self._cam_label.config(text="📷 Install opencv-python + pillow")
            return
        self._cam = cv2.VideoCapture(0)
        if not self._cam.isOpened():
            if self._cam_label:
                self._cam_label.config(text="📷 No camera found")
            return
        self._cam_running = True
        threading.Thread(target=self._cam_loop, daemon=True).start()

    def _cam_loop(self):
        global _cam_frame, _cam_lock
        while self._cam_running:
            try:
                ret, frame = self._cam.read()
                if not ret:
                    time.sleep(0.1); continue
                with _cam_lock:
                    _cam_frame = frame.copy()
                small = cv2.resize(frame, (160, 120))
                small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                img   = Image.fromarray(small)
                photo = ImageTk.PhotoImage(img)
                self.after(0, lambda p=photo: self._update_cam(p))
            except Exception:
                pass
            time.sleep(0.05)

    def _update_cam(self, photo):
        try:
            if self._cam_label and self._cam_label.winfo_exists():
                self._cam_label.config(image=photo, text="")
                self._cam_label._photo = photo
        except Exception:
            pass

    def _stop_camera(self):
        global _cam_frame
        self._cam_running = False
        with _cam_lock:
            _cam_frame = None
        if self._cam:
            self._cam.release()
            self._cam = None

    def _on_close(self):
        self._stop_camera()
        self.destroy()

    def _clear(self):
        for w in self.winfo_children(): w.destroy()


if __name__ == "__main__":
    _start_flask_server()
    Student().mainloop()