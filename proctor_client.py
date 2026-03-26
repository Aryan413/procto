"""
proctor_client.py  —  ExamShield Simple Proctor Connect
========================================================
Run this on the PROCTOR'S PC.

    pip install requests pillow
    python proctor_client.py

Works with:
  - Local IP:   http://192.168.x.x:5050   key: examshield2024
  - Cloudflare: https://xxxx.trycloudflare.com   key: examshield2024

For the full proctor dashboard (violations, questions, results):
    python proctor_remote.py
"""

import tkinter as tk
from tkinter import messagebox
import threading, io, subprocess, sys
import requests

try:
    from PIL import Image, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ── Theme ──────────────────────────────────────────────────────────────────
BG   = "#0d1117"
CARD = "#161b22"
BOR  = "#30363d"
FG   = "#c9d1d9"
ACC  = "#58d6d6"
GRN  = "#0be881"
RED  = "#ff4444"
YEL  = "#ffaa00"
ENT  = "#21262d"

DEFAULT_KEY = "examshield2024"
DEFAULT_PORT = 5050


# ── Main App ───────────────────────────────────────────────────────────────
class ProctorClient:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ExamShield — Proctor Connect")
        self.root.geometry("540x480")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self._running   = False
        self._api       = None
        self._cam_after = None

        self._build_connect_screen()

    # ── Connect Screen ─────────────────────────────────────────────────────
    def _build_connect_screen(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.geometry("540x380")

        tk.Label(self.root, text="🛡️", font=("Segoe UI Emoji", 30),
                 bg=BG).pack(pady=(22, 0))
        tk.Label(self.root, text="ExamShield Remote Proctor",
                 font=("Helvetica", 15, "bold"), bg=BG, fg="#ff6b9d").pack()
        tk.Label(self.root,
                 text="Enter the URL and key shown in the student terminal",
                 font=("Helvetica", 9), bg=BG, fg=FG).pack(pady=(3, 14))

        frm = tk.Frame(self.root, bg=CARD, padx=26, pady=16,
                       highlightbackground=BOR, highlightthickness=1)
        frm.pack(fill="x", padx=28)

        # URL field
        tk.Label(frm, text="Server URL", font=("Helvetica", 9, "bold"),
                 bg=CARD, fg=FG, anchor="w").pack(fill="x")
        self.e_url = tk.Entry(frm, font=("Helvetica", 11),
                              bg=ENT, fg=FG, insertbackground=FG, bd=0)
        self.e_url.pack(fill="x", ipady=7, pady=(2, 10))
        self.e_url.insert(0, "https://xxxx.trycloudflare.com  OR  http://192.168.x.x:5050")
        self.e_url.bind("<FocusIn>",
            lambda e: self.e_url.delete(0, "end") if "OR" in self.e_url.get() else None)

        # Key field
        tk.Label(frm, text="Server Key", font=("Helvetica", 9, "bold"),
                 bg=CARD, fg=FG, anchor="w").pack(fill="x")
        self.e_key = tk.Entry(frm, font=("Helvetica", 11),
                              bg=ENT, fg=FG, insertbackground=FG, bd=0)
        self.e_key.pack(fill="x", ipady=7, pady=(2, 0))
        self.e_key.insert(0, DEFAULT_KEY)

        # Status
        self.lbl_status = tk.Label(self.root, text="",
                                   font=("Helvetica", 9), bg=BG, fg=YEL)
        self.lbl_status.pack(pady=8)

        # Buttons row
        brow = tk.Frame(self.root, bg=BG)
        brow.pack(pady=(0, 10))

        tk.Button(brow, text="  🔌 Connect  ",
                  font=("Helvetica", 11, "bold"),
                  bg="#ff6b9d", fg="#0d1117",
                  bd=0, cursor="hand2",
                  command=self._do_connect
                  ).pack(side="left", padx=6, ipady=7)

        tk.Button(brow, text="  📊 Full Dashboard  ",
                  font=("Helvetica", 10),
                  bg=CARD, fg=ACC,
                  bd=0, cursor="hand2",
                  highlightbackground=BOR, highlightthickness=1,
                  command=self._open_full_dashboard
                  ).pack(side="left", padx=6, ipady=7)

        # Help text
        help_txt = (
            "Tip: For full features (violations, questions, results)\n"
            "use 'Full Dashboard' button above, or run: python proctor_remote.py"
        )
        tk.Label(self.root, text=help_txt,
                 font=("Helvetica", 8), bg=BG, fg="#555577",
                 justify="center").pack(pady=(0, 10))

    # ── Connect Logic ──────────────────────────────────────────────────────
    def _do_connect(self):
        url = self.e_url.get().strip()
        key = self.e_key.get().strip()

        # Reject placeholder text
        if not url or "OR" in url:
            messagebox.showerror("Error", "Please enter the Server URL first.")
            return
        if not key:
            messagebox.showerror("Error", "Please enter the Server Key.")
            return

        # Auto-fix URL
        url = self._fix_url(url, key)

        self.lbl_status.configure(text="Connecting…", fg=YEL)
        self.root.update()

        threading.Thread(target=self._try_connect, args=(url, key), daemon=True).start()

    def _fix_url(self, url, key):
        """Auto-add https:// for Cloudflare, http:// + port for plain IPs."""
        url = url.rstrip("/")
        if "trycloudflare.com" in url:
            if not url.startswith("http"):
                url = "https://" + url
        else:
            if not url.startswith("http"):
                url = "http://" + url
            if ":" not in url.split("//")[1]:   # no port specified
                url = f"{url}:{DEFAULT_PORT}"
        return url

    def _try_connect(self, url, key):
        try:
            sess = requests.Session()
            sess.headers.update({
                "X-ExamShield-Key":          key,
                "ngrok-skip-browser-warning": "true",
                "User-Agent":                "ExamShield-Proctor/1.0",
            })
            r = sess.get(f"{url}/ping", params={"key": key}, timeout=6)
            data = r.json()
            if data.get("status") == "ok":
                self._api = (url, key, sess)
                self.root.after(0, lambda: self._on_connected(url, key, data, sess))
            else:
                self.root.after(0, lambda: self.lbl_status.configure(
                    text="Server replied but not ExamShield?", fg=RED))
        except requests.exceptions.ConnectionError:
            self.root.after(0, lambda: self.lbl_status.configure(
                text="❌ Cannot reach server. Is it running? Is the URL correct?", fg=RED))
        except Exception as e:
            self.root.after(0, lambda: self.lbl_status.configure(
                text=f"❌ {str(e)[:80]}", fg=RED))

    def _on_connected(self, url, key, data, sess):
        self.lbl_status.configure(text="✅ Connected!", fg=GRN)
        sid   = data.get("student_id") or "none"
        live  = data.get("exam_live", False)
        all_s = data.get("all_students", [])

        # Build the camera view screen
        self.root.after(400, lambda: self._build_camera_screen(url, key, sess, sid, live))

    # ── Camera Screen ──────────────────────────────────────────────────────
    def _build_camera_screen(self, url, key, sess, sid, live):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.geometry("700x600")
        self._running = True

        # Top bar
        bar = tk.Frame(self.root, bg=CARD, height=44)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text=f"👤  Student: {sid}",
                 font=("Helvetica", 11, "bold"),
                 bg=CARD, fg=ACC).pack(side="left", padx=14)

        self.lbl_conn2 = tk.Label(bar, text="● Live",
                                   font=("Helvetica", 9, "bold"),
                                   bg=CARD, fg=GRN)
        self.lbl_conn2.pack(side="right", padx=12)

        tk.Button(bar, text="⬅ Disconnect",
                  font=("Helvetica", 8), bg=CARD, fg="#8b949e",
                  bd=0, cursor="hand2",
                  command=self._disconnect).pack(side="right", padx=6, pady=8)

        # Camera canvas
        self.cam_canvas = tk.Canvas(self.root, bg="#0b0b13",
                                    width=660, height=450,
                                    highlightthickness=1,
                                    highlightbackground=BOR)
        self.cam_canvas.pack(padx=10, pady=(6, 0))

        if not PIL_OK:
            self.cam_canvas.create_text(330, 225,
                text="Install Pillow for video:\npip install pillow",
                fill="#ff4444", font=("Helvetica", 11), justify="center")
        else:
            self.cam_canvas.create_text(330, 225,
                text="Waiting for camera frame…",
                fill="#3a3a5a", font=("Helvetica", 10))

        # Stats bar
        sbar = tk.Frame(self.root, bg="#0f1520", height=30)
        sbar.pack(fill="x", padx=10, pady=(3, 0))
        sbar.pack_propagate(False)
        self.lbl_faces   = tk.Label(sbar, text="Faces: —",   font=("Helvetica", 8, "bold"), bg="#0f1520", fg=GRN);  self.lbl_faces.pack(side="left", padx=10)
        self.lbl_gaze    = tk.Label(sbar, text="Gaze: —",    font=("Helvetica", 8, "bold"), bg="#0f1520", fg=GRN);  self.lbl_gaze.pack(side="left", padx=10)
        self.lbl_strikes = tk.Label(sbar, text="Strikes: 0", font=("Helvetica", 8, "bold"), bg="#0f1520", fg=GRN);  self.lbl_strikes.pack(side="left", padx=10)
        self.lbl_phone   = tk.Label(sbar, text="Phone: No",  font=("Helvetica", 8, "bold"), bg="#0f1520", fg=GRN);  self.lbl_phone.pack(side="left", padx=10)

        # Store connection info and start polling
        self._url  = url
        self._key  = key
        self._sess = sess
        self._poll_frame()
        self._poll_stats()

    # ── Video Polling ──────────────────────────────────────────────────────
    def _poll_frame(self):
        if not self._running: return
        threading.Thread(target=self._fetch_frame, daemon=True).start()
        self._cam_after = self.root.after(80, self._poll_frame)   # ~12 fps

    def _fetch_frame(self):
        if not PIL_OK: return
        try:
            r = self._sess.get(f"{self._url}/frame",
                               params={"key": self._key}, timeout=5)
            if r.status_code == 200 and len(r.content) > 100:
                img = Image.open(io.BytesIO(r.content))
                img.thumbnail((660, 450), Image.BILINEAR)
                # Centre on black background
                bg_img = Image.new("RGB", (660, 450), (11, 11, 19))
                ox = (660 - img.width)  // 2
                oy = (450 - img.height) // 2
                bg_img.paste(img, (ox, oy))
                photo = ImageTk.PhotoImage(bg_img)
                if self.root.winfo_exists():
                    self.root.after(0, lambda p=photo: self._set_frame(p))
        except Exception as e:
            if self.root.winfo_exists():
                self.root.after(0, lambda m=str(e): self._cam_error(m))

    def _set_frame(self, photo):
        if not hasattr(self, "cam_canvas") or not self.cam_canvas.winfo_exists():
            return
        self.cam_canvas.delete("all")
        self.cam_canvas.create_image(330, 225, anchor="center", image=photo)
        self.cam_canvas.image = photo   # prevent garbage collection
        self.lbl_conn2.configure(text="● Live", fg=GRN)

    def _cam_error(self, msg):
        if not hasattr(self, "cam_canvas") or not self.cam_canvas.winfo_exists():
            return
        self.cam_canvas.delete("all")
        self.cam_canvas.create_text(330, 215,
            text="⚠ Camera unavailable", fill=RED, font=("Helvetica", 10, "bold"))
        self.cam_canvas.create_text(330, 240,
            text=msg[:80], fill="#8b949e", font=("Helvetica", 8))
        self.lbl_conn2.configure(text="● No Frame", fg=YEL)

    # ── Stats Polling ──────────────────────────────────────────────────────
    def _poll_stats(self):
        if not self._running: return
        threading.Thread(target=self._fetch_stats, daemon=True).start()
        self.root.after(800, self._poll_stats)

    def _fetch_stats(self):
        try:
            r = self._sess.get(f"{self._url}/stats",
                               params={"key": self._key}, timeout=4)
            s = r.json()
            if self.root.winfo_exists():
                self.root.after(0, lambda: self._update_stats(s))
        except Exception:
            pass

    def _update_stats(self, s):
        if not s.get("live"): return
        fc = s.get("face_count", 0)
        gd = s.get("gaze_dir", "—")
        sc = s.get("strike_count", 0)
        mx = s.get("max_strikes", 5)
        ph = s.get("phone_detected", False)
        self.lbl_faces.configure(text=f"Faces: {fc}",
                                  fg=GRN if fc == 1 else RED)
        self.lbl_gaze.configure(text=f"Gaze: {gd}",
                                 fg=GRN if gd == "center" else YEL)
        self.lbl_strikes.configure(text=f"Strikes: {sc}/{mx}",
                                    fg=GRN if sc == 0 else YEL if sc < 3 else RED)
        self.lbl_phone.configure(text=f"Phone: {'⚠ YES' if ph else 'No'}",
                                  fg=RED if ph else GRN)

    # ── Disconnect ─────────────────────────────────────────────────────────
    def _disconnect(self):
        self._running = False
        if self._cam_after:
            self.root.after_cancel(self._cam_after)
        self._build_connect_screen()

    # ── Open Full Dashboard ────────────────────────────────────────────────
    def _open_full_dashboard(self):
        """Launch proctor_remote.py in a separate process."""
        script = "proctor_remote.py"
        if not os.path.exists(script):
            messagebox.showerror("Not Found",
                f"'{script}' not found in the same folder.\n"
                "Make sure proctor_remote.py is in the same directory.")
            return
        subprocess.Popen([sys.executable, script])

    def run(self):
        self.root.mainloop()


import os   # needed for _open_full_dashboard

if __name__ == "__main__":
    ProctorClient().run()