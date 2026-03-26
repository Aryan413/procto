import tkinter as tk
from tkinter import messagebox
import sqlite3
import math
import random
import time

from proctor import start_proctoring
from face_auth import init_face_db, capture_face_registration, verify_face

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('students.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            student_id TEXT PRIMARY KEY,
            password TEXT
        )
    ''')
    conn.commit()
    conn.close()

# ─── THEME ───────────────────────────────────────────────────────────────────

DARK_THEME = {
    "bg": "#0d1117",
    "canvas_bg": "#0d1117",
    "card_bg": "#161b22",
    "card_border": "#30363d",
    "title_fg": "#58d6d6",
    "subtitle_fg": "#8b949e",
    "label_fg": "#c9d1d9",
    "entry_bg": "#21262d",
    "entry_fg": "#f0f6fc",
    "entry_border": "#30363d",
    "entry_focus": "#58d6d6",
    "btn_login_bg": "#0be881",
    "btn_login_fg": "#0d1117",
    "btn_register_bg": "#575fcf",
    "btn_register_fg": "#ffffff",
    "btn_toggle_bg": "#21262d",
    "btn_toggle_fg": "#c9d1d9",
    "particle_colors": ["#58d6d6", "#0be881", "#575fcf", "#ff6b9d", "#ffd93d"],
    "mode_icon": "☀️",
    "mode_text": "Light Mode",
}

LIGHT_THEME = {
    "bg": "#f0f4f8",
    "canvas_bg": "#f0f4f8",
    "card_bg": "#ffffff",
    "card_border": "#d0d7de",
    "title_fg": "#0969da",
    "subtitle_fg": "#57606a",
    "label_fg": "#24292f",
    "entry_bg": "#f6f8fa",
    "entry_fg": "#24292f",
    "entry_border": "#d0d7de",
    "entry_focus": "#0969da",
    "btn_login_bg": "#1a7f37",
    "btn_login_fg": "#ffffff",
    "btn_register_bg": "#8250df",
    "btn_register_fg": "#ffffff",
    "btn_toggle_bg": "#e7edf3",
    "btn_toggle_fg": "#24292f",
    "particle_colors": ["#0969da", "#1a7f37", "#8250df", "#cf222e", "#9a6700"],
    "mode_icon": "🌙",
    "mode_text": "Dark Mode",
}

# ─── PARTICLE ────────────────────────────────────────────────────────────────

class Particle:
    def __init__(self, canvas_w, canvas_h, colors):
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.reset(colors)

    def reset(self, colors):
        self.x = random.uniform(0, self.canvas_w)
        self.y = random.uniform(0, self.canvas_h)
        self.size = random.uniform(1.5, 4.5)
        self.color = random.choice(colors)
        self.vx = random.uniform(-0.4, 0.4)
        self.vy = random.uniform(-0.4, 0.4)
        self.pulse = random.uniform(0, math.pi * 2)
        self.pulse_speed = random.uniform(0.02, 0.06)

    def update(self, mouse_x, mouse_y):
        dx = self.x - mouse_x
        dy = self.y - mouse_y
        dist = math.sqrt(dx * dx + dy * dy) or 1
        if dist < 100:
            force = (100 - dist) / 100 * 1.2
            self.vx += (dx / dist) * force
            self.vy += (dy / dist) * force
        self.vx *= 0.97
        self.vy *= 0.97
        speed = math.sqrt(self.vx**2 + self.vy**2)
        if speed > 2.5:
            self.vx = (self.vx / speed) * 2.5
            self.vy = (self.vy / speed) * 2.5
        self.x += self.vx
        self.y += self.vy
        self.pulse += self.pulse_speed
        if self.x < 0 or self.x > self.canvas_w:
            self.vx *= -1
            self.x = max(0, min(self.canvas_w, self.x))
        if self.y < 0 or self.y > self.canvas_h:
            self.vy *= -1
            self.y = max(0, min(self.canvas_h, self.y))

# ─── APP ─────────────────────────────────────────────────────────────────────

class LoginApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ExamShield — Secure Login")
        self.root.geometry("520x600")
        self.root.resizable(True, True)
        self.root.minsize(420, 520)

        self.is_dark = True
        self.theme = DARK_THEME
        self.mouse_x = 260
        self.mouse_y = 300
        self.animating = True

        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.particles = [
            Particle(520, 600, self.theme["particle_colors"])
            for _ in range(55)
        ]
        self.root.bind("<Configure>", self._on_resize)

        self._build_ui()
        self._apply_theme()

        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._animate()

    def _on_mouse_move(self, event):
        self.mouse_x = event.x
        self.mouse_y = event.y

    def _fade_color(self, hex_color, alpha_0_255):
        bg = self.theme["bg"].lstrip("#")
        fg = hex_color.lstrip("#")
        try:
            br, bg_c, bb = int(bg[0:2],16), int(bg[2:4],16), int(bg[4:6],16)
            fr, fg_c, fb = int(fg[0:2],16), int(fg[2:4],16), int(fg[4:6],16)
            a = alpha_0_255 / 255
            r = int(br + (fr - br) * a)
            g = int(bg_c + (fg_c - bg_c) * a)
            b = int(bb + (fb - bb) * a)
            return f"#{r:02x}{g:02x}{b:02x}"
        except:
            return hex_color

    def _draw_particles(self):
        self.canvas.delete("particle")
        for p in self.particles:
            p.update(self.mouse_x, self.mouse_y)
            r = p.size + math.sin(p.pulse) * 1.2
            self.canvas.create_oval(
                p.x - r, p.y - r, p.x + r, p.y + r,
                fill=p.color, outline="", tags="particle"
            )
        for i, p1 in enumerate(self.particles):
            for p2 in self.particles[i+1:]:
                dx = p1.x - p2.x
                dy = p1.y - p2.y
                dist = math.sqrt(dx*dx + dy*dy)
                if dist < 90:
                    opacity = int(255 * (1 - dist/90) * 0.35)
                    col = self._fade_color(p1.color, opacity)
                    self.canvas.create_line(
                        p1.x, p1.y, p2.x, p2.y,
                        fill=col, width=0.8, tags="particle"
                    )

    def _draw_card(self):
        self.canvas.delete("card_bg")
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        pad_x = max(40, int(w * 0.10))
        x0, y0 = pad_x, max(70, int(h * 0.13))
        x1, y1 = w - pad_x, h - max(40, int(h * 0.07))
        r = 18
        self.canvas.create_rectangle(x0+4, y0+4, x1+4, y1+4,
                                      fill="#000000", outline="", tags="card_bg")
        self._rounded_rect(x0, y0, x1, y1, r,
                            self.theme["card_bg"], self.theme["card_border"])

    def _rounded_rect(self, x0, y0, x1, y1, r, fill, outline):
        tag = "card_bg"
        self.canvas.create_rectangle(x0+r, y0, x1-r, y1, fill=fill, outline="", tags=tag)
        self.canvas.create_rectangle(x0, y0+r, x1, y1-r, fill=fill, outline="", tags=tag)
        for ox, oy in [(x0,y0),(x1-2*r,y0),(x0,y1-2*r),(x1-2*r,y1-2*r)]:
            self.canvas.create_oval(ox, oy, ox+2*r, oy+2*r, fill=fill, outline="", tags=tag)
        self.canvas.create_line(x0+r, y0, x1-r, y0, fill=outline, tags=tag)
        self.canvas.create_line(x0+r, y1, x1-r, y1, fill=outline, tags=tag)
        self.canvas.create_line(x0, y0+r, x0, y1-r, fill=outline, tags=tag)
        self.canvas.create_line(x1, y0+r, x1, y1-r, fill=outline, tags=tag)

    def _on_resize(self, event=None):
        if not hasattr(self, 'ui_frame'):
            return
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if w < 10 or h < 10:
            return

        # Resize canvas to fill window
        self.canvas.config(width=w, height=h)

        # Reposition card and ui_frame
        pad_x = max(40, int(w * 0.10))
        card_w = w - 2 * pad_x
        card_x = pad_x
        card_y = max(70, int(h * 0.13))
        frame_w = min(card_w - 20, 400)
        frame_x = card_x + (card_w - frame_w) // 2
        frame_y = card_y + max(20, int(h * 0.05))
        self.ui_frame.place(x=frame_x, y=frame_y, width=frame_w)

        # Update ALL particles with new bounds
        # Respawn particles that are now outside the window
        for p in self.particles:
            p.canvas_w = w
            p.canvas_h = h
            if p.x > w or p.y > h:
                p.x = random.uniform(0, w)
                p.y = random.uniform(0, h)

        # Add extra particles if window got much bigger
        target = max(55, int(w * h / 8000))
        target = min(target, 120)   # cap at 120
        while len(self.particles) < target:
            self.particles.append(Particle(w, h, self.theme["particle_colors"]))
        # Remove excess if window shrunk
        while len(self.particles) > target:
            self.particles.pop()

    def _animate(self):
        if not self.animating:
            return
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        self.canvas.configure(bg=self.theme["canvas_bg"], width=w, height=h)
        self._draw_particles()
        self._draw_card()
        self.root.after(30, self._animate)

    def _build_ui(self):
        self.ui_frame = tk.Frame(self.root, bg=self.theme["card_bg"],
                                  bd=0, highlightthickness=0)
        self.ui_frame.place(x=75, y=105, width=370)  # resized dynamically via _on_resize

        self.lbl_icon = tk.Label(self.ui_frame, text="🛡️",
                                   font=("Segoe UI Emoji", 30),
                                   bg=self.theme["card_bg"])
        self.lbl_icon.pack(pady=(18, 0))

        self.lbl_title = tk.Label(self.ui_frame, text="ExamShield",
                                    font=("Georgia", 22, "bold"),
                                    bg=self.theme["card_bg"],
                                    fg=self.theme["title_fg"])
        self.lbl_title.pack()

        self.lbl_sub = tk.Label(self.ui_frame,
                                  text="AI-Powered Secure Proctoring",
                                  font=("Helvetica", 9),
                                  bg=self.theme["card_bg"],
                                  fg=self.theme["subtitle_fg"])
        self.lbl_sub.pack(pady=(2, 14))

        self.divider = tk.Frame(self.ui_frame, height=1, bg=self.theme["card_border"])
        self.divider.pack(fill="x", padx=20, pady=(0, 16))

        self.lbl_uid = tk.Label(self.ui_frame, text="Student ID",
                                  font=("Helvetica", 10, "bold"),
                                  bg=self.theme["card_bg"],
                                  fg=self.theme["label_fg"], anchor="w")
        self.lbl_uid.pack(fill="x", padx=30)
        self.entry_username = self._make_entry(self.ui_frame, show=None)

        self.lbl_pwd = tk.Label(self.ui_frame, text="Password",
                                  font=("Helvetica", 10, "bold"),
                                  bg=self.theme["card_bg"],
                                  fg=self.theme["label_fg"], anchor="w")
        self.lbl_pwd.pack(fill="x", padx=30, pady=(10, 0))
        self.entry_password = self._make_entry(self.ui_frame, show="*")

        btn_frame = tk.Frame(self.ui_frame, bg=self.theme["card_bg"])
        btn_frame.pack(pady=20)

        self.btn_login = tk.Button(
            btn_frame, text="Log In",
            font=("Helvetica", 11, "bold"),
            bg=self.theme["btn_login_bg"],
            fg=self.theme["btn_login_fg"],
            width=11, height=1, bd=0, relief="flat",
            cursor="hand2", command=self.attempt_login
        )
        self.btn_login.grid(row=0, column=0, padx=8, ipady=6)

        self.btn_register = tk.Button(
            btn_frame, text="Register",
            font=("Helvetica", 11, "bold"),
            bg=self.theme["btn_register_bg"],
            fg=self.theme["btn_register_fg"],
            width=11, height=1, bd=0, relief="flat",
            cursor="hand2", command=self.register_student
        )
        self.btn_register.grid(row=0, column=1, padx=8, ipady=6)

        self.btn_toggle = tk.Button(
            self.root,
            text=f"{self.theme['mode_icon']}  {self.theme['mode_text']}",
            font=("Helvetica", 9),
            bg=self.theme["btn_toggle_bg"],
            fg=self.theme["btn_toggle_fg"],
            bd=0, relief="flat", cursor="hand2",
            command=self.toggle_theme
        )
        self.btn_toggle.place(x=360, y=55, width=140, height=28)

    def _make_entry(self, parent, show=None):
        frame = tk.Frame(parent, bg=self.theme["entry_border"], bd=0)
        frame.pack(padx=30, pady=4, fill="x")
        kwargs = dict(
            font=("Courier New", 12),
            bg=self.theme["entry_bg"],
            fg=self.theme["entry_fg"],
            insertbackground=self.theme["entry_fg"],
            bd=0, relief="flat", highlightthickness=0
        )
        if show:
            kwargs["show"] = show
        e = tk.Entry(frame, **kwargs)
        e.pack(fill="x", padx=1, pady=1, ipady=7, ipadx=8)

        def on_focus_in(ev, f=frame):
            f.configure(bg=self.theme["entry_focus"])
        def on_focus_out(ev, f=frame):
            f.configure(bg=self.theme["entry_border"])

        e.bind("<FocusIn>", on_focus_in)
        e.bind("<FocusOut>", on_focus_out)
        e.bind("<Return>", lambda ev: self.attempt_login())
        return e

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.theme = DARK_THEME if self.is_dark else LIGHT_THEME
        for p in self.particles:
            p.color = random.choice(self.theme["particle_colors"])
        self._apply_theme()

    def _apply_theme(self):
        t = self.theme
        self.root.configure(bg=t["bg"])
        self.canvas.configure(bg=t["canvas_bg"])
        self.ui_frame.configure(bg=t["card_bg"])
        self.lbl_icon.configure(bg=t["card_bg"])
        self.lbl_title.configure(bg=t["card_bg"], fg=t["title_fg"])
        self.lbl_sub.configure(bg=t["card_bg"], fg=t["subtitle_fg"])
        self.divider.configure(bg=t["card_border"])
        self.lbl_uid.configure(bg=t["card_bg"], fg=t["label_fg"])
        self.lbl_pwd.configure(bg=t["card_bg"], fg=t["label_fg"])
        for entry_w in [self.entry_username, self.entry_password]:
            entry_w.configure(bg=t["entry_bg"], fg=t["entry_fg"],
                               insertbackground=t["entry_fg"])
            entry_w.master.configure(bg=t["entry_border"])
        self.btn_login.configure(bg=t["btn_login_bg"], fg=t["btn_login_fg"])
        self.btn_login.master.configure(bg=t["card_bg"])
        self.btn_register.configure(bg=t["btn_register_bg"], fg=t["btn_register_fg"])
        self.btn_toggle.configure(
            bg=t["btn_toggle_bg"], fg=t["btn_toggle_fg"],
            text=f"{t['mode_icon']}  {t['mode_text']}"
        )

    # ── LOGIC ─────────────────────────────────────────────────────────────────

    def register_student(self):
        student_id = self.entry_username.get().strip()
        password   = self.entry_password.get().strip()
        if not student_id or not password:
            messagebox.showerror("Error", "Please fill in both fields.")
            return
        try:
            conn = sqlite3.connect('students.db')
            conn.execute("INSERT INTO users (student_id, password) VALUES (?, ?)",
                         (student_id, password))
            conn.commit()
            conn.close()
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "This Student ID is already registered.")
            return
        messagebox.showinfo("Face Registration",
            f"Account created!\n\nNow register your face.\nLook at the camera and press OK.")
        self.root.withdraw()
        ok = capture_face_registration(student_id)
        self.root.deiconify()
        if ok:
            messagebox.showinfo("Success", "Face registered! You can now log in.")
        else:
            messagebox.showwarning("Warning", "Account created but face not captured.")

    def attempt_login(self):
        student_id = self.entry_username.get().strip()
        password   = self.entry_password.get().strip()
        if not student_id or not password:
            messagebox.showerror("Error", "Please enter both fields.")
            return
        conn = sqlite3.connect('students.db')
        row  = conn.execute(
            "SELECT * FROM users WHERE student_id=? AND password=?",
            (student_id, password)
        ).fetchone()
        conn.close()
        if not row:
            messagebox.showerror("Login Failed",
                "Invalid Student ID or Password.")
            return
        messagebox.showinfo("Face Verification",
            "Password accepted!\n\nNow verifying your identity.\nLook at the camera and press OK.")
        self.root.withdraw()
        verified = verify_face(student_id)
        self.root.deiconify()
        if not verified:
            messagebox.showerror("Access Denied",
                "Face verification failed!\nYou do not match the registered student.")
            return
        messagebox.showinfo("Verified",
            f"Identity confirmed! Welcome, {student_id}.\nThe exam will now begin.")
        self.animating = False
        self.root.destroy()
        start_proctoring(student_id)

    def _on_close(self):
        self.animating = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    init_face_db()
    app = LoginApp()
    app.run()