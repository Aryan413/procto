"""
proctor_remote.py  —  ExamShield Remote Proctor
=================================================
Run this on the PROCTOR'S PC.

    pip install requests pillow
    python proctor_remote.py

Enter the URL and key that the student PC printed on startup.
Everything works over HTTPS — no shared database, no LAN needed.
"""

import tkinter as tk
from tkinter import messagebox, ttk
import threading, io, os, csv
import requests
from PIL import Image, ImageTk

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════
DEFAULT_URL = ""
DEFAULT_KEY = "examshield2024"

# ══════════════════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════════════════
D = {
    "bg":"#0d1117","card":"#161b22","border":"#30363d",
    "fg":"#c9d1d9","title":"#ff6b9d","accent":"#58d6d6",
    "green":"#0be881","red":"#ff4444","yellow":"#ffaa00",
    "entry_bg":"#21262d","entry_fg":"#f0f6fc",
    "btn_bg":"#21262d","btn_fg":"#c9d1d9",
    "tab_sel":"#575fcf",
}


# ══════════════════════════════════════════════════════════════════════════
#  API CLIENT
# ══════════════════════════════════════════════════════════════════════════
class API:
    def __init__(self, base_url, key):
        self.base = base_url.rstrip("/")
        self.key  = key
        self.sess = requests.Session()
        self.sess.headers.update({
            "X-ExamShield-Key": key,
            # ✅ Cloudflare Tunnel bypass — realistic browser headers so
            # Cloudflare doesn't show "Just a moment..." interstitial page
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            # ✅ FIXED: Accept */* so Cloudflare also passes image/jpeg frames through
            "Accept": "*/*",
        })

    def _get(self, path, timeout=5, **kw):
        return self.sess.get(f"{self.base}{path}",
                             params={"key": self.key}, timeout=timeout, **kw)

    def _post(self, path, json=None):
        return self.sess.post(f"{self.base}{path}",
                              params={"key": self.key}, json=json, timeout=5)

    def _put(self, path, json=None):
        return self.sess.put(f"{self.base}{path}",
                             params={"key": self.key}, json=json, timeout=5)

    def _delete(self, path):
        return self.sess.delete(f"{self.base}{path}",
                                params={"key": self.key}, timeout=5)

    def _safe_json(self, r):
        try:
            if not r.content or not r.content.strip():
                return {}
            return r.json()
        except Exception:
            return {}

    def ping(self):          return self._safe_json(self._get("/ping"))
    def stats(self):         return self._safe_json(self._get("/stats"))
    def violations(self):    return self._safe_json(self._get("/violations")).get("violations", [])
    def questions(self):     return self._safe_json(self._get("/questions")).get("questions", [])
    def results(self):       return self._safe_json(self._get("/results")).get("files", [])
    def terminate(self):     return self._safe_json(self._post("/terminate"))
    def sessions(self):      return self._safe_json(self._get("/sessions")).get("sessions", [])

    def frame_bytes(self):
        r = self._get("/frame", timeout=8)
        r.raise_for_status()
        # ✅ FIXED: Detect if Cloudflare returned HTML instead of a JPEG frame.
        # This is the #1 cause of the black screen on the proctor side.
        content_type = r.headers.get("Content-Type", "")
        if "text/html" in content_type or (r.content and r.content[:5] == b"<!DOC"):
            raise ValueError(
                "Cloudflare returned an HTML page instead of camera frame.\n"
                "Make sure server.py has the @app.after_request bypass header."
            )
        return r.content

    def get_result(self, fname):
        r = self._get(f"/results/{fname}")
        content = r.text
        if content.strip().startswith("<!") or "<html" in content[:200].lower():
            raise ValueError("Server returned HTML — tunnel may be offline.")
        return content

    def add_question(self, d):
        return self._safe_json(self._post("/questions", json=d))

    def update_question(self, qid, d):
        return self._safe_json(self._put(f"/questions/{qid}", json=d))

    def delete_question(self, qid):
        return self._safe_json(self._delete(f"/questions/{qid}"))


# ══════════════════════════════════════════════════════════════════════════
#  PROCTOR PANEL  — a pure Frame, embeddable anywhere
# ══════════════════════════════════════════════════════════════════════════
class ProctorPanel(tk.Frame):
    """
    Self-contained proctor dashboard for ONE student.
    Lives inside any parent widget (Tk, Toplevel, Frame, Notebook tab).
    Uses winfo_toplevel() for dialogs so it works correctly when embedded.
    """
    POLL_FRAME_MS = 66    # ~15 fps
    POLL_STATS_MS = 500
    CAM_W, CAM_H  = 620, 348

    def __init__(self, parent, api: API, info: dict):
        super().__init__(parent, bg=D["bg"])
        self.api   = api
        self.info  = info

        self._connected      = True
        self._frame_fetching = False
        self._last_viols     = []

        self._build()
        self._poll_frame()
        self._poll_stats()

    def stop(self):
        self._connected = False

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        # top bar
        bar = tk.Frame(self, bg=D["card"], height=44)
        bar.pack(fill="x"); bar.pack_propagate(False)

        sid = self.info.get("student_id") or "—"
        tk.Label(bar, text=f"👤  {sid}",
                 font=("Helvetica", 11, "bold"),
                 bg=D["card"], fg=D["accent"]).pack(side="left", padx=14)
        tk.Label(bar, text=self.api.base,
                 font=("Helvetica", 8), bg=D["card"], fg="#555").pack(side="left")

        self.lbl_conn = tk.Label(bar, text="● Connected",
                                  font=("Helvetica", 9, "bold"),
                                  bg=D["card"], fg=D["green"])
        self.lbl_conn.pack(side="right", padx=12)

        tk.Button(bar, text="⚡ Terminate",
                  font=("Helvetica", 8, "bold"), bg="#6a0000", fg="#ff9999",
                  bd=0, relief="flat", cursor="hand2",
                  command=self._terminate).pack(side="right", padx=6, pady=8, ipady=3)

        # main split
        main = tk.Frame(self, bg=D["bg"])
        main.pack(fill="both", expand=True, padx=8, pady=6)
        main.columnconfigure(0, weight=3)
        main.columnconfigure(1, weight=2)
        main.rowconfigure(0, weight=1)

        # LEFT — camera
        left = tk.Frame(main, bg=D["bg"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        tk.Label(left, text="📷  Live Camera",
                 font=("Helvetica", 9, "bold"),
                 bg=D["bg"], fg=D["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 3))

        self.cam_canvas = tk.Canvas(left, bg="#0b0b13",
                                     width=self.CAM_W, height=self.CAM_H,
                                     highlightthickness=1,
                                     highlightbackground=D["border"])
        self.cam_canvas.grid(row=1, column=0, sticky="nsew")
        self.cam_canvas.create_text(self.CAM_W//2, self.CAM_H//2,
                                     text="Waiting for exam session…",
                                     fill="#3a3a5a", font=("Helvetica", 10))

        sf = tk.Frame(left, bg="#0f1520", height=30)
        sf.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        sf.pack_propagate(False)
        self.lbl_faces   = tk.Label(sf, text="Faces: —",   font=("Helvetica", 8, "bold"), bg="#0f1520", fg=D["green"]); self.lbl_faces.pack(side="left", padx=10)
        self.lbl_gaze    = tk.Label(sf, text="Gaze: —",    font=("Helvetica", 8, "bold"), bg="#0f1520", fg=D["green"]); self.lbl_gaze.pack(side="left", padx=10)
        self.lbl_strikes = tk.Label(sf, text="Strikes: 0", font=("Helvetica", 8, "bold"), bg="#0f1520", fg=D["green"]); self.lbl_strikes.pack(side="left", padx=10)
        self.lbl_phone   = tk.Label(sf, text="Phone: No",  font=("Helvetica", 8, "bold"), bg="#0f1520", fg=D["green"]); self.lbl_phone.pack(side="left", padx=10)

        # RIGHT — notebook
        right = tk.Frame(main, bg=D["bg"])
        right.grid(row=0, column=1, sticky="nsew")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("P.TNotebook",     background=D["bg"], borderwidth=0)
        style.configure("P.TNotebook.Tab", background=D["btn_bg"], foreground=D["fg"],
                                            padding=[8, 5], font=("Helvetica", 8, "bold"))
        style.map("P.TNotebook.Tab",
                  background=[("selected", D["tab_sel"])],
                  foreground=[("selected", "#ffffff")])

        nb = ttk.Notebook(right, style="P.TNotebook")
        nb.pack(fill="both", expand=True)

        vf  = tk.Frame(nb, bg=D["bg"]); nb.add(vf,  text="⚠ Violations")
        aqf = tk.Frame(nb, bg=D["bg"]); nb.add(aqf, text="➕ Add Q")
        qbf = tk.Frame(nb, bg=D["bg"]); nb.add(qbf, text="📋 Bank")
        rf  = tk.Frame(nb, bg=D["bg"]); nb.add(rf,  text="📊 Results")

        self._build_violations(vf)
        self._build_add_q(aqf)
        self._build_qbank(qbf)
        self._build_results(rf)

    # ── violations ────────────────────────────────────────────────────────
    def _build_violations(self, parent):
        tk.Label(parent, text="Violation Log",
                 font=("Helvetica", 9, "bold"),
                 bg=D["bg"], fg=D["title"]).pack(anchor="w", padx=8, pady=(6, 2))
        scr = tk.Scrollbar(parent); scr.pack(side="right", fill="y")
        self.vlog = tk.Text(parent, font=("Courier", 8), bg="#060610",
                             fg=D["fg"], bd=0, relief="flat",
                             wrap="word", yscrollcommand=scr.set, state="disabled")
        self.vlog.pack(fill="both", expand=True, padx=8, pady=(0, 2))
        scr.configure(command=self.vlog.yview)
        tk.Button(parent, text="Clear", font=("Helvetica", 8),
                  bg=D["btn_bg"], fg="#8b949e", bd=0, cursor="hand2",
                  command=lambda: (self.vlog.configure(state="normal"),
                                   self.vlog.delete("1.0","end"),
                                   self.vlog.configure(state="disabled"))
                  ).pack(pady=(0, 4))

    # ── add question ──────────────────────────────────────────────────────
    def _build_add_q(self, parent):
        tk.Label(parent, text="Add New Question",
                 font=("Helvetica", 9, "bold"),
                 bg=D["bg"], fg=D["green"]).pack(anchor="w", padx=10, pady=(8, 2))

        cv  = tk.Canvas(parent, bg=D["bg"], highlightthickness=0)
        scr = tk.Scrollbar(parent, command=cv.yview)
        cv.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); cv.pack(fill="both", expand=True)
        inner = tk.Frame(cv, bg=D["bg"])
        cv.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        self._aq = {}

        def lbl(t):
            tk.Label(inner, text=t, font=("Helvetica", 8, "bold"),
                     bg=D["bg"], fg=D["fg"], anchor="w").pack(fill="x", padx=10, pady=(6,0))

        lbl("Question *")
        self._aq["q"] = tk.Text(inner, font=("Helvetica", 9), bg=D["card"],
                                 fg=D["entry_fg"], insertbackground=D["entry_fg"],
                                 bd=0, relief="flat", height=3)
        self._aq["q"].pack(fill="x", padx=10, pady=(2,0), ipady=3)

        for key, label in [("a","Option A *"),("b","Option B *"),
                            ("c","Option C *"),("d","Option D *")]:
            lbl(label)
            self._aq[key] = tk.Entry(inner, font=("Helvetica", 9),
                                      bg=D["card"], fg=D["entry_fg"],
                                      insertbackground=D["entry_fg"], bd=0)
            self._aq[key].pack(fill="x", padx=10, pady=(2,0), ipady=5)

        lbl("Correct Answer")
        self._aq_ans = tk.StringVar(value="A")
        af = tk.Frame(inner, bg=D["bg"]); af.pack(padx=10, anchor="w")
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=self._aq_ans, value=opt,
                           font=("Helvetica", 9, "bold"), bg=D["bg"], fg=D["green"],
                           selectcolor="#0d3b2e", activebackground=D["bg"]
                           ).pack(side="left", padx=6)

        row2 = tk.Frame(inner, bg=D["bg"]); row2.pack(fill="x", padx=10, pady=(4,0))
        tk.Label(row2, text="Marks", font=("Helvetica", 8, "bold"),
                 bg=D["bg"], fg=D["fg"]).pack(side="left")
        self._aq["marks"] = tk.Entry(row2, font=("Helvetica", 9), width=4,
                                      bg=D["card"], fg=D["entry_fg"],
                                      insertbackground=D["entry_fg"], bd=0)
        self._aq["marks"].insert(0, "1")
        self._aq["marks"].pack(side="left", padx=(4,14), ipady=4)
        tk.Label(row2, text="Category", font=("Helvetica", 8, "bold"),
                 bg=D["bg"], fg=D["fg"]).pack(side="left")
        self._aq["cat"] = tk.Entry(row2, font=("Helvetica", 9), width=10,
                                    bg=D["card"], fg=D["entry_fg"],
                                    insertbackground=D["entry_fg"], bd=0)
        self._aq["cat"].insert(0, "General")
        self._aq["cat"].pack(side="left", padx=(4,0), ipady=4)

        tk.Button(inner, text="💾  Save Question",
                  font=("Helvetica", 9, "bold"), bg=D["green"], fg="#0d1117",
                  bd=0, cursor="hand2",
                  command=self._save_question
                  ).pack(fill="x", padx=10, pady=12, ipady=7)

    def _save_question(self):
        q   = self._aq["q"].get("1.0","end").strip()
        a   = self._aq["a"].get().strip(); b = self._aq["b"].get().strip()
        c   = self._aq["c"].get().strip(); d = self._aq["d"].get().strip()
        ans = self._aq_ans.get()
        cat = self._aq["cat"].get().strip() or "General"
        try: marks = int(self._aq["marks"].get())
        except: marks = 1
        if not all([q, a, b, c, d]):
            messagebox.showerror("Error", "Fill all required fields.",
                                 parent=self.winfo_toplevel()); return
        try:
            res = self.api.add_question({"question":q,"opt_a":a,"opt_b":b,
                                          "opt_c":c,"opt_d":d,"answer":ans,
                                          "marks":marks,"category":cat})
            if res.get("ok"):
                messagebox.showinfo("Saved", "Question added ✓",
                                    parent=self.winfo_toplevel())
                self._aq["q"].delete("1.0","end")
                for k in ["a","b","c","d"]: self._aq[k].delete(0,"end")
                self._aq["marks"].delete(0,"end"); self._aq["marks"].insert(0,"1")
                self._aq["cat"].delete(0,"end");   self._aq["cat"].insert(0,"General")
                self._refresh_qbank()
            else:
                messagebox.showerror("Error", res.get("error","Unknown error"),
                                     parent=self.winfo_toplevel())
        except Exception as e:
            messagebox.showerror("Network Error", str(e), parent=self.winfo_toplevel())

    # ── question bank ─────────────────────────────────────────────────────
    def _build_qbank(self, parent):
        top = tk.Frame(parent, bg=D["bg"]); top.pack(fill="x", padx=8, pady=(6,2))
        tk.Label(top, text="Question Bank", font=("Helvetica", 9, "bold"),
                 bg=D["bg"], fg="#ffd93d").pack(side="left")
        tk.Button(top, text="↺ Refresh", font=("Helvetica", 8),
                  bg=D["btn_bg"], fg="#8b949e", bd=0, cursor="hand2",
                  command=self._refresh_qbank).pack(side="right", ipady=2, padx=4)

        self._qb_canvas = tk.Canvas(parent, bg=D["bg"], highlightthickness=0)
        scr = tk.Scrollbar(parent, command=self._qb_canvas.yview)
        self._qb_canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y")
        self._qb_canvas.pack(fill="both", expand=True, padx=4)
        self._qb_inner = tk.Frame(self._qb_canvas, bg=D["bg"])
        self._qb_canvas.create_window((0,0), window=self._qb_inner, anchor="nw")
        self._qb_inner.bind("<Configure>",
            lambda e: self._qb_canvas.configure(scrollregion=self._qb_canvas.bbox("all")))
        self._refresh_qbank()

    def _refresh_qbank(self):
        try:
            qs = self.api.questions()
        except Exception as e:
            for w in self._qb_inner.winfo_children(): w.destroy()
            tk.Label(self._qb_inner, text=f"Error: {e}",
                     font=("Helvetica", 8), bg=D["bg"], fg=D["red"]
                     ).pack(padx=10, pady=8); return
        self._render_qbank(qs)

    def _render_qbank(self, qs):
        for w in self._qb_inner.winfo_children(): w.destroy()
        if not qs:
            tk.Label(self._qb_inner, text="No questions yet.",
                     font=("Helvetica", 9), bg=D["bg"], fg="#8b949e"
                     ).pack(padx=10, pady=10); return
        for q in qs:
            card = tk.Frame(self._qb_inner, bg=D["card"])
            card.pack(fill="x", padx=4, pady=2)
            txt = q["question"][:60]+"…" if len(q["question"])>60 else q["question"]
            tk.Label(card, text=f"Q{q['id']}: {txt}",
                     font=("Helvetica", 8), bg=D["card"], fg=D["fg"],
                     anchor="w", wraplength=200, justify="left"
                     ).pack(side="left", padx=8, pady=5, fill="x", expand=True)
            info = tk.Frame(card, bg=D["card"]); info.pack(side="left")
            tk.Label(info, text=f"Ans: {q['answer']}", font=("Helvetica", 7, "bold"),
                     bg=D["card"], fg=D["green"]).pack(anchor="e")
            tk.Label(info, text=f"{q['marks']}mk {q['category']}", font=("Helvetica", 6),
                     bg=D["card"], fg=D["tab_sel"]).pack(anchor="e")
            tk.Button(card, text="✏", font=("Helvetica", 9), bg=D["card"], fg="#ffd93d",
                      bd=0, cursor="hand2",
                      command=lambda row=q: self._edit_q(row)
                      ).pack(side="right", padx=2)
            tk.Button(card, text="🗑", font=("Helvetica", 9), bg=D["card"], fg=D["title"],
                      bd=0, cursor="hand2",
                      command=lambda qid=q["id"]: self._delete_q(qid)
                      ).pack(side="right", padx=2)

    def _delete_q(self, qid):
        if messagebox.askyesno("Delete", f"Delete question {qid}?",
                               parent=self.winfo_toplevel()):
            try:
                result = self.api.delete_question(qid)
                if "questions" in result:
                    self._render_qbank(result["questions"])
                else:
                    self._refresh_qbank()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=self.winfo_toplevel())

    def _edit_q(self, row):
        top = self.winfo_toplevel()
        win = tk.Toplevel(top)
        win.title(f"Edit Q{row['id']}")
        win.geometry("480x480"); win.configure(bg=D["bg"]); win.grab_set()
        fields = {}

        cv  = tk.Canvas(win, bg=D["bg"], highlightthickness=0)
        scr = tk.Scrollbar(win, command=cv.yview)
        cv.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y"); cv.pack(fill="both", expand=True)
        inner = tk.Frame(cv, bg=D["bg"])
        cv.create_window((0,0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        def lbl(t):
            tk.Label(inner, text=t, font=("Helvetica", 8, "bold"),
                     bg=D["bg"], fg=D["fg"], anchor="w").pack(fill="x", padx=14, pady=(5,0))

        lbl("Question")
        fields["q"] = tk.Text(inner, font=("Helvetica", 9), bg=D["card"],
                               fg=D["entry_fg"], insertbackground=D["entry_fg"],
                               bd=0, height=3)
        fields["q"].insert("1.0", row["question"])
        fields["q"].pack(fill="x", padx=14, pady=(2,0), ipady=4)

        for k, lb in [("a","Option A"),("b","Option B"),("c","Option C"),("d","Option D")]:
            lbl(lb)
            fields[k] = tk.Entry(inner, font=("Helvetica", 9), bg=D["card"],
                                  fg=D["entry_fg"], insertbackground=D["entry_fg"], bd=0)
            fields[k].insert(0, row[f"opt_{k}"])
            fields[k].pack(fill="x", padx=14, pady=(2,0), ipady=5)

        lbl("Correct Answer")
        ans_var = tk.StringVar(value=row["answer"])
        af = tk.Frame(inner, bg=D["bg"]); af.pack(padx=14, anchor="w")
        for opt in ["A","B","C","D"]:
            tk.Radiobutton(af, text=opt, variable=ans_var, value=opt,
                           font=("Helvetica", 9, "bold"), bg=D["bg"], fg=D["green"],
                           selectcolor="#0d3b2e", activebackground=D["bg"]
                           ).pack(side="left", padx=6)

        row2 = tk.Frame(inner, bg=D["bg"]); row2.pack(fill="x", padx=14, pady=(5,0))
        tk.Label(row2, text="Marks", font=("Helvetica", 8), bg=D["bg"], fg=D["fg"]).pack(side="left")
        fields["marks"] = tk.Entry(row2, font=("Helvetica", 9), width=4,
                                    bg=D["card"], fg=D["entry_fg"], bd=0)
        fields["marks"].insert(0, str(row["marks"]))
        fields["marks"].pack(side="left", padx=(4,14), ipady=4)
        tk.Label(row2, text="Category", font=("Helvetica", 8), bg=D["bg"], fg=D["fg"]).pack(side="left")
        fields["cat"] = tk.Entry(row2, font=("Helvetica", 9), width=10,
                                  bg=D["card"], fg=D["entry_fg"], bd=0)
        fields["cat"].insert(0, row["category"])
        fields["cat"].pack(side="left", padx=(4,0), ipady=4)

        def save():
            d = {
                "question": fields["q"].get("1.0","end").strip(),
                "opt_a": fields["a"].get().strip(), "opt_b": fields["b"].get().strip(),
                "opt_c": fields["c"].get().strip(), "opt_d": fields["d"].get().strip(),
                "answer": ans_var.get(),
                "marks": int(fields["marks"].get()) if fields["marks"].get().isdigit() else 1,
                "category": fields["cat"].get().strip() or "General",
            }
            try:
                res = self.api.update_question(row["id"], d)
                if res.get("ok"):
                    messagebox.showinfo("Updated", "Question updated ✓", parent=win)
                    win.destroy(); self._refresh_qbank()
                else:
                    messagebox.showerror("Error", res.get("error","Unknown"), parent=win)
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=win)

        tk.Button(inner, text="💾  Update Question",
                  font=("Helvetica", 9, "bold"), bg=D["tab_sel"], fg="#fff",
                  bd=0, cursor="hand2", command=save
                  ).pack(fill="x", padx=14, pady=12, ipady=7)

    # ── results ───────────────────────────────────────────────────────────
    def _build_results(self, parent):
        top = tk.Frame(parent, bg=D["bg"]); top.pack(fill="x", padx=8, pady=(6,2))
        tk.Label(top, text="Exam Results & Logs",
                 font=("Helvetica", 9, "bold"),
                 bg=D["bg"], fg=D["tab_sel"]).pack(side="left")
        tk.Button(top, text="↺ Refresh", font=("Helvetica", 8),
                  bg=D["btn_bg"], fg="#8b949e", bd=0, cursor="hand2",
                  command=self._refresh_results).pack(side="right", ipady=2, padx=4)

        self._res_canvas = tk.Canvas(parent, bg=D["bg"], highlightthickness=0)
        scr = tk.Scrollbar(parent, command=self._res_canvas.yview)
        self._res_canvas.configure(yscrollcommand=scr.set)
        scr.pack(side="right", fill="y")
        self._res_canvas.pack(fill="both", expand=True, padx=4)
        self._res_inner = tk.Frame(self._res_canvas, bg=D["bg"])
        self._res_canvas.create_window((0,0), window=self._res_inner, anchor="nw")
        self._res_inner.bind("<Configure>",
            lambda e: self._res_canvas.configure(scrollregion=self._res_canvas.bbox("all")))
        self._refresh_results()

    def _refresh_results(self):
        for w in self._res_inner.winfo_children(): w.destroy()
        try:
            files = self.api.results()
        except Exception as e:
            tk.Label(self._res_inner, text=f"Error: {e}",
                     font=("Helvetica", 8), bg=D["bg"], fg=D["red"]
                     ).pack(padx=10, pady=8); return
        if not files:
            tk.Label(self._res_inner,
                     text="No result files yet.\n(Results appear after exam ends)",
                     font=("Helvetica", 9), bg=D["bg"], fg="#8b949e",
                     justify="center").pack(padx=10, pady=20); return
        for fname in files:
            row = tk.Frame(self._res_inner, bg=D["card"])
            row.pack(fill="x", padx=4, pady=3)
            tk.Label(row, text=fname, font=("Courier", 8),
                     bg=D["card"], fg=D["fg"], anchor="w"
                     ).pack(side="left", padx=8, pady=6, fill="x", expand=True)
            tk.Button(row, text="View", font=("Helvetica", 8, "bold"),
                      bg=D["tab_sel"], fg="#fff", bd=0, cursor="hand2",
                      command=lambda f=fname: self._view_result(f)
                      ).pack(side="right", padx=5, pady=4, ipady=2)
            tk.Button(row, text="Save↓", font=("Helvetica", 8),
                      bg=D["btn_bg"], fg=D["fg"], bd=0, cursor="hand2",
                      command=lambda f=fname: self._save_result(f)
                      ).pack(side="right", padx=2, pady=4, ipady=2)

    def _view_result(self, fname):
        try:
            content = self.api.get_result(fname)
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self.winfo_toplevel()); return

        win = tk.Toplevel(self.winfo_toplevel())
        win.title(f"Result: {fname}"); win.geometry("860x520"); win.configure(bg=D["bg"])

        # Header bar with summary
        hdr = tk.Frame(win, bg=D["card"], height=48); hdr.pack(fill="x"); hdr.pack_propagate(False)
        self._render_result_header(hdr, content)

        # Table via ttk.Treeview
        cols = ("q", "question", "your_ans", "correct", "result")
        style = ttk.Style()
        style.configure("R.Treeview", background="#0b0b13", foreground=D["fg"],
                        fieldbackground="#0b0b13", rowheight=26, font=("Helvetica", 9))
        style.configure("R.Treeview.Heading", background=D["card"], foreground=D["accent"],
                        font=("Helvetica", 9, "bold"))
        style.map("R.Treeview", background=[("selected", D["tab_sel"])])

        frame = tk.Frame(win, bg=D["bg"]); frame.pack(fill="both", expand=True, padx=8, pady=6)
        scr_y = tk.Scrollbar(frame); scr_y.pack(side="right", fill="y")
        scr_x = tk.Scrollbar(frame, orient="horizontal"); scr_x.pack(side="bottom", fill="x")
        tv = ttk.Treeview(frame, columns=cols, show="headings", style="R.Treeview",
                          yscrollcommand=scr_y.set, xscrollcommand=scr_x.set)
        tv.pack(fill="both", expand=True)
        scr_y.configure(command=tv.yview)
        scr_x.configure(command=tv.xview)

        tv.heading("q",        text="Q#")
        tv.heading("question", text="Question")
        tv.heading("your_ans", text="Your Answer")
        tv.heading("correct",  text="Correct")
        tv.heading("result",   text="✓/✗")
        tv.column("q",        width=40,  stretch=False, anchor="center")
        tv.column("question", width=380, stretch=True)
        tv.column("your_ans", width=100, stretch=False, anchor="center")
        tv.column("correct",  width=100, stretch=False, anchor="center")
        tv.column("result",   width=50,  stretch=False, anchor="center")

        tv.tag_configure("correct", foreground=D["green"])
        tv.tag_configure("wrong",   foreground=D["red"])

        rows = list(csv.reader(content.splitlines()))
        for row in rows[1:]:   # skip header
            if len(row) < 5: continue
            tag = "correct" if row[4].strip() in ("✓", "1", "True", "correct") else "wrong"
            tv.insert("", "end", values=(row[0], row[1], row[2], row[3], row[4]), tags=(tag,))

    def _render_result_header(self, hdr, content):
        try:
            rows = list(csv.reader(content.splitlines()))
            data = rows[1:]
            total   = len(data)
            correct = sum(1 for r in data if len(r)>=5 and r[4].strip() in ("✓","1","True","correct"))
            pct     = int(correct/total*100) if total else 0
            grade   = "A" if pct>=90 else "B" if pct>=75 else "C" if pct>=60 else "D" if pct>=40 else "F"
            gcol    = D["green"] if pct>=60 else D["yellow"] if pct>=40 else D["red"]
            tk.Label(hdr, text=f"Score: {correct}/{total}  ({pct}%)",
                     font=("Helvetica", 12, "bold"), bg=D["card"], fg=gcol).pack(side="left", padx=16)
            tk.Label(hdr, text=f"Grade: {grade}",
                     font=("Helvetica", 11, "bold"), bg=D["card"], fg=gcol).pack(side="left", padx=8)
            tk.Label(hdr, text=f"✓ {correct} correct   ✗ {total-correct} wrong",
                     font=("Helvetica", 9), bg=D["card"], fg="#8b949e").pack(side="left", padx=16)
        except Exception:
            pass



    def _save_result(self, fname):
        try:
            content = self.api.get_result(fname)
            with open(fname, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Saved", f"Saved as {fname}",
                                parent=self.winfo_toplevel())
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self.winfo_toplevel())

    # ── camera polling ────────────────────────────────────────────────────
    def _poll_frame(self):
        if not self._connected: return
        if not self._frame_fetching:
            self._frame_fetching = True
            threading.Thread(target=self._fetch_frame, daemon=True).start()
        self.after(self.POLL_FRAME_MS, self._poll_frame)

    def _fetch_frame(self):
        try:
            data = self.api.frame_bytes()
            if not data or len(data) < 100:
                return
            img = Image.open(io.BytesIO(data))
            img.load()
            img.thumbnail((self.CAM_W, self.CAM_H), Image.BILINEAR)
            padded = Image.new("RGB", (self.CAM_W, self.CAM_H), (11, 11, 19))
            ox = (self.CAM_W - img.width)  // 2
            oy = (self.CAM_H - img.height) // 2
            padded.paste(img, (ox, oy))
            photo = ImageTk.PhotoImage(padded)
            if self.winfo_exists():
                self.after(0, lambda p=photo: self._set_frame(p))
        except Exception as e:
            if self.winfo_exists():
                self.after(0, lambda m=str(e): self._show_cam_error(m))
        finally:
            self._frame_fetching = False

    def _set_frame(self, photo):
        self.cam_canvas.delete("all")
        self.cam_canvas.create_image(self.CAM_W//2, self.CAM_H//2,
                                      anchor="center", image=photo)
        self.cam_canvas.image = photo

    def _show_cam_error(self, msg):
        self.cam_canvas.delete("all")
        self.cam_canvas.create_text(self.CAM_W//2, self.CAM_H//2 - 10,
            text="⚠ Camera error", fill="#ff4444", font=("Helvetica", 10, "bold"))
        self.cam_canvas.create_text(self.CAM_W//2, self.CAM_H//2 + 14,
            text=msg[:80], fill="#8b949e", font=("Helvetica", 8))

    # ── stats polling ─────────────────────────────────────────────────────
    def _poll_stats(self):
        if not self._connected: return
        def fetch():
            try:
                s = self.api.stats()
                # ✅ FIXED: /violations doesn't exist on student Flask — ignore failures
                try:
                    v = self.api.violations()
                except Exception:
                    v = []
                if self._connected and self.winfo_exists():
                    self.after(0, lambda: self._update_stats(s, v))
            except Exception:
                if self._connected and self.winfo_exists():
                    self.after(0, self._show_disconnected)
        threading.Thread(target=fetch, daemon=True).start()
        self.after(self.POLL_STATS_MS, self._poll_stats)

    def _update_stats(self, s, viols):
        if not self.winfo_exists(): return
        self.lbl_conn.configure(text="● Connected", fg=D["green"])
        if not s.get("live"):
            self.cam_canvas.delete("all")
            self.cam_canvas.create_text(self.CAM_W//2, self.CAM_H//2,
                text="No active student session", fill="#3a3a5a", font=("Helvetica", 10))
            return
        fc = s.get("face_count", 0); gd = s.get("gaze_dir","—")
        sc = s.get("strike_count", 0); mx = s.get("max_strikes", 5)
        ph = s.get("phone_detected", False)
        self.lbl_faces.configure(text=f"Faces: {fc}",
                                  fg=D["green"] if fc==1 else D["red"])
        self.lbl_gaze.configure(text=f"Gaze: {gd}",
                                 fg=D["green"] if gd=="center" else D["yellow"])
        self.lbl_strikes.configure(text=f"Strikes: {sc}/{mx}",
                                    fg=D["green"] if sc==0 else D["yellow"] if sc<3 else D["red"])
        self.lbl_phone.configure(text=f"Phone: {'⚠ YES' if ph else 'No'}",
                                  fg=D["red"] if ph else D["green"])
        if viols != self._last_viols:
            self._last_viols = list(viols)
            self.vlog.configure(state="normal")
            self.vlog.delete("1.0","end")
            for v in viols:
                tag = ("strike" if "STRIKE" in v else
                       "warn"   if "WARNING" in v else
                       "ok"     if "START" in v else "info")
                self.vlog.insert("end", v+"\n", tag)
            self.vlog.tag_configure("strike", foreground=D["red"])
            self.vlog.tag_configure("warn",   foreground=D["yellow"])
            self.vlog.tag_configure("ok",     foreground=D["green"])
            self.vlog.tag_configure("info",   foreground="#8b949e")
            self.vlog.configure(state="disabled")
            self.vlog.see("end")

    def _show_disconnected(self):
        if self.winfo_exists():
            self.lbl_conn.configure(text="● Disconnected", fg=D["red"])

    def _terminate(self):
        if not messagebox.askyesno("Terminate",
                "Force-terminate this student's exam?\nThis cannot be undone.",
                parent=self.winfo_toplevel()):
            return
        try:
            res = self.api.terminate()
            messagebox.showinfo("Done", res.get("message","Terminated"),
                                parent=self.winfo_toplevel())
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self.winfo_toplevel())


# ══════════════════════════════════════════════════════════════════════════
#  MULTI-STUDENT MONITOR  — single window, one tab per student
# ══════════════════════════════════════════════════════════════════════════
class MultiMonitor:
    def __init__(self, api: API, students: list):
        self.api    = api
        self._tabs  = {}   # student_id → ProctorPanel

        self.root = tk.Tk()
        self.root.title("ExamShield — Multi-Student Monitor")
        self.root.geometry("1300x800")
        self.root.configure(bg=D["bg"])
        self.root.resizable(True, True)

        self._build()
        for sid in students:
            self._ensure_tab(sid)
        self._auto_refresh()

    def _build(self):
        bar = tk.Frame(self.root, bg=D["card"], height=46)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text="🛡️  ExamShield — Multi-Student Monitor",
                 font=("Helvetica", 12, "bold"),
                 bg=D["card"], fg=D["title"]).pack(side="left", padx=14)
        self._lbl_count = tk.Label(bar, text="0 students",
                 font=("Helvetica", 9), bg=D["card"], fg=D["accent"])
        self._lbl_count.pack(side="right", padx=14)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("M.TNotebook",     background=D["bg"], borderwidth=0)
        style.configure("M.TNotebook.Tab", background=D["btn_bg"], foreground=D["fg"],
                                            padding=[14, 7], font=("Helvetica", 9, "bold"))
        style.map("M.TNotebook.Tab",
                  background=[("selected", D["tab_sel"])],
                  foreground=[("selected", "#ffffff")])

        self._nb = ttk.Notebook(self.root, style="M.TNotebook")
        self._nb.pack(fill="both", expand=True)

    def _ensure_tab(self, student_id):
        if student_id in self._tabs:
            return
        try:
            sessions = self.api.sessions()
            token = next((s["token"] for s in sessions
                          if s["student_id"] == student_id), None)
        except Exception:
            token = None
        if token is None:
            return
        student_api = API(self.api.base, token)
        try:
            info = student_api.ping()
        except Exception:
            return

        frame = tk.Frame(self._nb, bg=D["bg"])
        self._nb.add(frame, text=f"  👤 {student_id}  ")
        panel = ProctorPanel(frame, student_api, info)
        panel.pack(fill="both", expand=True)
        self._tabs[student_id] = panel
        self._lbl_count.configure(text=f"{len(self._tabs)} student(s)")
        self._nb.select(frame)   # switch to new student tab

    def _auto_refresh(self):
        if not self.root.winfo_exists(): return
        try:
            sessions = self.api.sessions()
            for s in sessions:
                self._ensure_tab(s["student_id"])
        except Exception:
            pass
        self.root.after(3000, self._auto_refresh)

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════
#  CONNECT SCREEN
# ══════════════════════════════════════════════════════════════════════════
class ConnectScreen:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ExamShield — Remote Proctor Connect")
        self.root.geometry("500x400")
        self.root.configure(bg=D["bg"])
        self.root.resizable(False, False)
        self._build()

    def _build(self):
        tk.Label(self.root, text="🛡️", font=("Segoe UI Emoji", 28),
                 bg=D["bg"]).pack(pady=(24, 0))
        tk.Label(self.root, text="ExamShield Remote Proctor",
                 font=("Helvetica", 15, "bold"),
                 bg=D["bg"], fg=D["title"]).pack()
        tk.Label(self.root, text="Enter the URL and key shown on the student PC",
                 font=("Helvetica", 9), bg=D["bg"], fg=D["fg"]).pack(pady=(4, 14))

        frm = tk.Frame(self.root, bg=D["card"], padx=26, pady=16)
        frm.pack(fill="x", padx=28)

        tk.Label(frm, text="Server URL", font=("Helvetica", 9, "bold"),
                 bg=D["card"], fg=D["fg"], anchor="w").pack(fill="x")
        self.e_url = tk.Entry(frm, font=("Helvetica", 11),
                              bg=D["entry_bg"], fg=D["entry_fg"],
                              insertbackground=D["entry_fg"], bd=0)
        self.e_url.pack(fill="x", ipady=7, pady=(2, 10))
        self.e_url.insert(0, DEFAULT_URL or "http://192.168.x.x:5050")

        tk.Label(frm, text="Server Key", font=("Helvetica", 9, "bold"),
                 bg=D["card"], fg=D["fg"], anchor="w").pack(fill="x")
        self.e_key = tk.Entry(frm, font=("Helvetica", 11),
                              bg=D["entry_bg"], fg=D["entry_fg"],
                              insertbackground=D["entry_fg"], bd=0)
        self.e_key.pack(fill="x", ipady=7, pady=(2, 0))
        self.e_key.insert(0, DEFAULT_KEY)

        self.lbl_status = tk.Label(self.root, text="",
                                   font=("Helvetica", 9), bg=D["bg"], fg=D["yellow"])
        self.lbl_status.pack(pady=6)

        tk.Button(self.root, text="Connect  ▶",
                  font=("Helvetica", 11, "bold"),
                  bg=D["title"], fg="#0d1117",
                  bd=0, cursor="hand2",
                  command=self._connect).pack(pady=(0, 8), ipady=7, padx=58, fill="x")

    def _connect(self):
        url = self.e_url.get().strip()
        key = self.e_key.get().strip()
        if not url or not key:
            messagebox.showerror("Error", "Fill in both fields."); return
        self.lbl_status.configure(text="Connecting…", fg=D["yellow"])
        self.root.update()
        try:
            api  = API(url, key)
            info = api.ping()
            if info.get("status") != "ok":
                raise ValueError("Server returned unexpected response")
            self.lbl_status.configure(text="Connected!", fg=D["green"])
            self.root.after(300, lambda: self._launch(api, info, key))
        except Exception as e:
            self.lbl_status.configure(text=f"Failed: {e}", fg=D["red"])

    def _launch(self, api, info, key):
        # ✅ FIXED: Both admin key and per-student token open a direct
        # ProctorPanel pointed at the student's Flask server URL.
        # The old MultiMonitor path called api.sessions() which doesn't
        # exist on the student Flask — causing "0 students" and black screen.
        self.root.destroy()
        win = tk.Tk()
        win.title("ExamShield — Remote Proctor Dashboard")
        win.geometry("1160x720")
        win.configure(bg=D["bg"])
        panel = ProctorPanel(win, api, info)
        panel.pack(fill="both", expand=True)
        win.mainloop()

    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════
#  ENTRY
# ══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ConnectScreen().run()