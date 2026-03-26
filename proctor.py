"""
ExamShield — Proctor
Run:     python proctor.py
Requires: pip install requests
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading, json, time, requests
from datetime import datetime

BASE    = "https://api.jsonbin.io/v3"
CFG_FILE = "es_config.json"
QS_FILE  = "es_questions.json"

BG  = "#0d0f1a"; SUR = "#141726"; BOR = "#1e2235"
ACC = "#7c6af7"; GRN = "#3dffa0"; YEL = "#ffd166"
RED = "#f7426a"; TXT = "#c8cde8"; DIM = "#4a5070"; WHT = "#ffffff"

def jload(path, default):
    try:
        with open(path) as f: return json.load(f)
    except: return default

def jsave(path, data):
    with open(path, "w") as f: json.dump(data, f, indent=2)

def hdrs(api_key):
    return {"Content-Type": "application/json", "X-Master-Key": api_key}


class Proctor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ExamShield — Proctor [admin]")
        self.geometry("960x680")
        self.configure(bg=BG)
        self.cfg = jload(CFG_FILE, {"api_key": "", "bin_id": ""})
        self.qs  = jload(QS_FILE, [])
        self._running = True
        self._build_ui()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._quit)

    # ── UI BUILD ─────────────────────────────────────────────
    def _build_ui(self):
        # topbar
        top = tk.Frame(self, bg=SUR, height=46)
        top.pack(fill="x"); top.pack_propagate(False)
        tk.Label(top, text="ExamShield Proctor", font=("Courier New",13,"bold"),
                 bg=SUR, fg=RED).pack(side="left", padx=14)
        tk.Label(top, text="| admin", font=("Segoe UI",10), bg=SUR, fg=DIM).pack(side="left")
        self.lbl_online = tk.Label(top, text="No students online",
                                   font=("Segoe UI",10), bg=SUR, fg=DIM)
        self.lbl_online.pack(side="right", padx=14)

        # notebook
        st = ttk.Style(); st.theme_use("default")
        st.configure("P.TNotebook", background=SUR, borderwidth=0)
        st.configure("P.TNotebook.Tab", background=SUR, foreground=DIM,
                     font=("Segoe UI",10), padding=[14,6])
        st.map("P.TNotebook.Tab",
               background=[("selected", BG)], foreground=[("selected", ACC)])
        nb = ttk.Notebook(self, style="P.TNotebook")
        nb.pack(fill="both", expand=True)

        self._tab_waiting(nb)
        self._tab_questions(nb)
        self._tab_results(nb)
        self._tab_config(nb)

    # ── TAB: WAITING ─────────────────────────────────────────
    def _tab_waiting(self, nb):
        f = tk.Frame(nb, bg=BG); nb.add(f, text="  👥 Waiting  ")
        self.wait_empty = tk.Label(
            f, bg=BG, fg=DIM, font=("Segoe UI",11), justify="center",
            text="Waiting for students to log in…\n\n"
                 "Publish an exam, then share your Bin ID + API key with students.")
        self.wait_empty.pack(expand=True)
        self.wait_grid = tk.Frame(f, bg=BG)
        self.wait_grid.pack(fill="both", expand=True, padx=16, pady=16)

    # ── TAB: QUESTIONS ───────────────────────────────────────
    def _tab_questions(self, nb):
        f = tk.Frame(nb, bg=BG); nb.add(f, text="  📋 Questions  ")

        frm = tk.LabelFrame(f, text=" Add Question ", bg=BG, fg=ACC,
                            font=("Segoe UI",9), relief="flat",
                            highlightbackground=BOR, highlightthickness=1,
                            padx=10, pady=6)
        frm.pack(fill="x", padx=14, pady=(10,0))

        # question text
        tk.Label(frm,text="Question:",bg=BG,fg=DIM,font=("Segoe UI",9)
                 ).grid(row=0,column=0,sticky="nw",padx=(0,8),pady=2)
        self.v_qtext = tk.Text(frm, height=2, width=56, bg=SUR, fg=TXT,
                               insertbackground=TXT, font=("Segoe UI",10),
                               relief="flat", highlightbackground=BOR, highlightthickness=1)
        self.v_qtext.grid(row=0, column=1, sticky="ew", pady=2)

        # helper: labelled entry row
        def erow(label, row, default=""):
            tk.Label(frm,text=label,bg=BG,fg=DIM,font=("Segoe UI",9)
                     ).grid(row=row,column=0,sticky="w",padx=(0,8),pady=2)
            v = tk.StringVar(value=default)
            e = tk.Entry(frm, textvariable=v, width=56, bg=SUR, fg=TXT,
                         insertbackground=TXT, font=("Segoe UI",9),
                         relief="flat", highlightbackground=BOR, highlightthickness=1)
            e.grid(row=row, column=1, sticky="ew", pady=2)
            return v

        self.v_cat  = erow("Category:", 1, "General")
        self.v_oa   = erow("Option A:",  2)
        self.v_ob   = erow("Option B:",  3)
        self.v_oc   = erow("Option C:",  4)
        self.v_od   = erow("Option D:",  5)

        tk.Label(frm,text="Correct:",bg=BG,fg=DIM,font=("Segoe UI",9)
                 ).grid(row=6,column=0,sticky="w",pady=2)
        self.v_correct = tk.IntVar(value=0)
        cr = tk.Frame(frm, bg=BG); cr.grid(row=6, column=1, sticky="w")
        for i,l in enumerate(["A","B","C","D"]):
            tk.Radiobutton(cr, text=l, variable=self.v_correct, value=i,
                           bg=BG, fg=TXT, selectcolor=ACC, activebackground=BG,
                           font=("Segoe UI",9)).pack(side="left", padx=4)

        tk.Label(frm,text="Marks:",bg=BG,fg=DIM,font=("Segoe UI",9)
                 ).grid(row=7,column=0,sticky="w",pady=2)
        self.v_marks = tk.IntVar(value=1)
        tk.Spinbox(frm, from_=1, to=10, textvariable=self.v_marks, width=4,
                   bg=SUR, fg=TXT, insertbackground=TXT, font=("Segoe UI",9),
                   relief="flat").grid(row=7, column=1, sticky="w")

        tk.Button(frm, text="  + Add Question  ", command=self._add_q,
                  bg=ACC, fg=WHT, font=("Segoe UI",9), relief="flat",
                  cursor="hand2", padx=6, pady=4
                  ).grid(row=8, column=1, sticky="w", pady=(8,0))

        # list
        lf = tk.Frame(f, bg=BG); lf.pack(fill="both", expand=True, padx=14, pady=6)
        self.q_lb = tk.Listbox(lf, bg=SUR, fg=TXT, font=("Courier New",9),
                               selectbackground=ACC, selectforeground=WHT,
                               relief="flat", highlightbackground=BOR,
                               highlightthickness=1, activestyle="none", height=7)
        sb = tk.Scrollbar(lf, command=self.q_lb.yview, bg=SUR)
        self.q_lb.config(yscrollcommand=sb.set)
        self.q_lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        br = tk.Frame(f, bg=BG); br.pack(fill="x", padx=14, pady=(0,2))
        tk.Button(br, text="🗑 Remove Selected", command=self._del_q,
                  bg=RED, fg=WHT, font=("Segoe UI",9), relief="flat",
                  cursor="hand2", padx=8, pady=4).pack(side="left")

        pb = tk.Frame(f, bg=SUR, highlightbackground=BOR, highlightthickness=1)
        pb.pack(fill="x", padx=14, pady=6)
        self.lbl_qcount = tk.Label(pb, text="0 questions", bg=SUR, fg=DIM,
                                   font=("Segoe UI",9))
        self.lbl_qcount.pack(side="left", padx=10, pady=8)
        tk.Label(pb, text="Duration (min):", bg=SUR, fg=DIM,
                 font=("Segoe UI",9)).pack(side="left")
        self.v_dur = tk.IntVar(value=30)
        tk.Spinbox(pb, from_=1, to=180, textvariable=self.v_dur, width=4,
                   bg=SUR, fg=TXT, font=("Segoe UI",9),
                   relief="flat").pack(side="left", padx=(2,10))
        tk.Button(pb, text="  🚀 Publish Exam  ", command=self._publish,
                  bg=GRN, fg=BG, font=("Segoe UI",10,"bold"),
                  relief="flat", cursor="hand2", padx=10, pady=6
                  ).pack(side="right", padx=10, pady=6)

        self._refresh_qlist()

    def _add_q(self):
        text = self.v_qtext.get("1.0","end").strip()
        opts = [v.get().strip() for v in [self.v_oa,self.v_ob,self.v_oc,self.v_od]]
        if not text or any(o == "" for o in opts):
            messagebox.showerror("Error","Fill question text and all 4 options."); return
        self.qs.append({"id": int(time.time()*1000), "text": text,
                        "category": self.v_cat.get() or "General",
                        "options": opts, "correct": self.v_correct.get(),
                        "marks": self.v_marks.get()})
        jsave(QS_FILE, self.qs); self._refresh_qlist()
        self.v_qtext.delete("1.0","end")
        for v in [self.v_oa,self.v_ob,self.v_oc,self.v_od]: v.set("")

    def _del_q(self):
        s = self.q_lb.curselection()
        if not s: return
        if messagebox.askyesno("Remove", f"Remove Q{s[0]+1}?"):
            self.qs.pop(s[0]); jsave(QS_FILE, self.qs); self._refresh_qlist()

    def _refresh_qlist(self):
        self.q_lb.delete(0,"end")
        for i,q in enumerate(self.qs):
            self.q_lb.insert("end",
                f"  Q{i+1}. {q['text'][:52]}   ✓ {q['options'][q['correct']][:18]}")
        self.lbl_qcount.config(
            text=f"{len(self.qs)} question{'s' if len(self.qs)!=1 else ''}")

    def _publish(self):
        if not self.qs:
            messagebox.showerror("Error","Add questions first!"); return
        k = self.cfg.get("api_key","").strip()
        b = self.cfg.get("bin_id","").strip()
        if not k or not b:
            messagebox.showerror("Error","Set API key and Bin ID in Config tab first!"); return
        payload = {
            "exam": {"published": True, "duration": self.v_dur.get(),
                     "published_at": int(time.time()), "questions": self.qs},
            "results": {}, "students": {}
        }
        threading.Thread(target=self._do_publish, args=(k,b,payload), daemon=True).start()

    def _do_publish(self, k, b, payload):
        try:
            r = requests.put(f"{BASE}/b/{b}", json=payload, headers=hdrs(k), timeout=15)
            if r.ok:
                self.after(0, lambda: [
                    messagebox.showinfo("Published","✅ Exam published!\nStudents can now join."),
                    self._log("✅ Exam published to bin.")])
            else:
                self.after(0, lambda: [
                    messagebox.showerror("Error", f"HTTP {r.status_code}\n{r.text[:200]}"),
                    self._log(f"❌ Publish failed: {r.status_code} {r.text[:80]}")])
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    # ── TAB: RESULTS ─────────────────────────────────────────
    def _tab_results(self, nb):
        f = tk.Frame(nb, bg=BG); nb.add(f, text="  📊 Results  ")
        top = tk.Frame(f, bg=BG); top.pack(fill="x", padx=14, pady=10)
        tk.Label(top, text="Exam Results", font=("Segoe UI",12,"bold"),
                 bg=BG, fg=TXT).pack(side="left")
        tk.Button(top, text="🔄 Refresh", command=self._load_results,
                  bg=SUR, fg=ACC, font=("Segoe UI",9), relief="flat",
                  cursor="hand2", highlightbackground=BOR, highlightthickness=1,
                  padx=8, pady=4).pack(side="right")

        st = ttk.Style()
        st.configure("R.Treeview", background=SUR, foreground=TXT,
                     fieldbackground=SUR, rowheight=26, font=("Segoe UI",10))
        st.configure("R.Treeview.Heading", background=BOR, foreground=ACC,
                     font=("Segoe UI",10,"bold"))
        st.map("R.Treeview", background=[("selected",ACC)], foreground=[("selected",WHT)])
        cols = ("Student","Score","Percent","Submitted At")
        self.tree = ttk.Treeview(f, columns=cols, show="headings",
                                  style="R.Treeview", height=16)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=200, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=14, pady=8)

    def _load_results(self):
        k = self.cfg.get("api_key","").strip()
        b = self.cfg.get("bin_id","").strip()
        if not k or not b:
            messagebox.showerror("Error","Set API key and Bin ID in Config first!"); return
        threading.Thread(target=self._do_load_results, args=(k,b), daemon=True).start()

    def _do_load_results(self, k, b):
        try:
            r = requests.get(f"{BASE}/b/{b}/latest", headers=hdrs(k), timeout=10)
            if r.ok:
                res = r.json().get("record",{}).get("results",{})
                self.after(0, lambda: self._fill_results(res))
            else:
                self.after(0, lambda: messagebox.showerror("Error",f"HTTP {r.status_code}"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", str(e)))

    def _fill_results(self, res):
        for row in self.tree.get_children(): self.tree.delete(row)
        for name,d in res.items():
            pct = round(d["score"]/d["total"]*100) if d["total"] else 0
            t   = datetime.fromtimestamp(d["submitted_at"]).strftime("%H:%M:%S")
            self.tree.insert("","end",
                values=(name, f"{d['score']}/{d['total']}", f"{pct}%", t))

    # ── TAB: CONFIG ──────────────────────────────────────────
    def _tab_config(self, nb):
        f = tk.Frame(nb, bg=BG); nb.add(f, text="  ⚙ Config  ")
        box = tk.Frame(f, bg=SUR, highlightbackground=BOR, highlightthickness=1)
        box.pack(padx=20, pady=20, anchor="nw")

        info = (
            "HOW TO LINK PROCTOR ↔ STUDENT\n\n"
            "1.  Go to https://jsonbin.io  →  create a free account\n"
            "2.  Dashboard → API Keys → copy your  Master Key\n"
            "3.  Paste it below, click  ➕ Create Bin\n"
            "4.  A Bin ID appears — give BOTH the Master Key AND Bin ID to students\n"
            "5.  Students enter those same two values when they open student.py\n"
            "6.  Proctor: add questions → 🚀 Publish Exam\n"
            "7.  Students: questions load from network automatically\n"
        )
        tk.Label(box, text=info, bg=SUR, fg=DIM, font=("Segoe UI",9),
                 justify="left", wraplength=480
                 ).grid(row=0, column=0, columnspan=2,
                        padx=14, pady=(12,4), sticky="w")

        def cfrow(label, val, row):
            tk.Label(box, text=label, bg=SUR, fg=DIM,
                     font=("Segoe UI",9), width=16, anchor="w"
                     ).grid(row=row, column=0, sticky="w", padx=14, pady=4)
            v = tk.StringVar(value=val)
            tk.Entry(box, textvariable=v, width=48, bg=BG, fg=TXT,
                     insertbackground=TXT, font=("Courier New",9),
                     relief="flat", highlightbackground=BOR, highlightthickness=1
                     ).grid(row=row, column=1, sticky="ew", padx=(0,14), pady=4)
            return v

        self.v_api_key = cfrow("Master API Key:", self.cfg.get("api_key",""), 1)
        self.v_bin_id  = cfrow("Bin ID:",         self.cfg.get("bin_id",""),  2)

        btns = tk.Frame(box, bg=SUR); btns.grid(row=3,column=0,columnspan=2,padx=14,pady=8,sticky="w")
        for txt,cmd,col in [
            ("💾 Save",        self._save_cfg,   ACC),
            ("➕ Create Bin",  self._create_bin, "#4488ff"),
            ("🔌 Test",        self._test_conn,  "#555577"),
        ]:
            tk.Button(btns, text=f"  {txt}  ", command=cmd, bg=col, fg=WHT,
                      font=("Segoe UI",9), relief="flat", cursor="hand2",
                      padx=6, pady=5).pack(side="left", padx=(0,8))

        self.lbl_conn = tk.Label(box, text="", bg=SUR, fg=DIM, font=("Courier New",9))
        self.lbl_conn.grid(row=4, column=0, columnspan=2, padx=14, pady=2, sticky="w")

        share = tk.Frame(box, bg=BG, highlightbackground=BOR, highlightthickness=1)
        share.grid(row=5, column=0, columnspan=2, padx=14, pady=(4,14), sticky="ew")
        tk.Label(share, text="📤 Share Bin ID with students:",
                 bg=BG, fg=YEL, font=("Segoe UI",9)).pack(side="left", padx=10, pady=8)
        self.lbl_share = tk.Label(share, text=self.cfg.get("bin_id","—"),
                                  bg=BG, fg=ACC, font=("Courier New",10,"bold"))
        self.lbl_share.pack(side="left")

        # log
        tk.Label(f, text="Network Log:", bg=BG, fg=DIM, font=("Segoe UI",9)
                 ).pack(anchor="w", padx=20, pady=(10,2))
        self.logbox = scrolledtext.ScrolledText(
            f, height=7, bg=SUR, fg=GRN, font=("Courier New",9),
            insertbackground=TXT, relief="flat",
            highlightbackground=BOR, highlightthickness=1, state="disabled")
        self.logbox.pack(fill="x", padx=20, pady=(0,10))

    def _save_cfg(self):
        self.cfg["api_key"] = self.v_api_key.get().strip()
        self.cfg["bin_id"]  = self.v_bin_id.get().strip()
        jsave(CFG_FILE, self.cfg)
        self.lbl_share.config(text=self.cfg["bin_id"] or "—")
        self._log("Config saved.")

    def _create_bin(self):
        k = self.v_api_key.get().strip()
        if not k: messagebox.showerror("Error","Enter API key first!"); return
        self._log("Creating bin…")
        threading.Thread(target=self._do_create_bin, args=(k,), daemon=True).start()

    def _do_create_bin(self, k):
        try:
            r = requests.post(f"{BASE}/b",
                json={"exam":{"published":False,"questions":[]},"results":{},"students":{}},
                headers={**hdrs(k),"X-Bin-Name":"examshield","X-Bin-Private":"false"},
                timeout=15)
            d = r.json()
            if d.get("metadata",{}).get("id"):
                bid = d["metadata"]["id"]
                self.cfg["api_key"] = k; self.cfg["bin_id"] = bid
                jsave(CFG_FILE, self.cfg)
                self.after(0, lambda: [
                    self.v_bin_id.set(bid), self.lbl_share.config(text=bid),
                    self._log(f"✅ Bin created: {bid}"),
                    messagebox.showinfo("Bin Created",
                        f"Bin created!\n\nBin ID: {bid}\n\n"
                        "Give students:\n  • This Bin ID\n  • Your Master API Key")])
            else:
                self.after(0, lambda: self._log(f"❌ Failed: {d}"))
        except Exception as e:
            self.after(0, lambda: self._log(f"❌ {e}"))

    def _test_conn(self):
        k = self.v_api_key.get().strip(); b = self.v_bin_id.get().strip()
        if not k or not b: self._log("❌ Fill both API key and Bin ID first."); return
        self._log("Testing…"); threading.Thread(target=self._do_test,args=(k,b),daemon=True).start()

    def _do_test(self, k, b):
        try:
            r = requests.get(f"{BASE}/b/{b}/latest", headers=hdrs(k), timeout=10)
            if r.ok:
                rec = r.json().get("record",{})
                qs  = len(rec.get("exam",{}).get("questions",[]))
                pub = rec.get("exam",{}).get("published", False)
                ns  = len(rec.get("students",{}))
                nr  = len(rec.get("results",{}))
                msg = f"✅ Connected! questions={qs} published={pub} students={ns} results={nr}"
                self.after(0, lambda: [self.lbl_conn.config(text="✅ OK",fg=GRN), self._log(msg)])
            else:
                self.after(0, lambda: [
                    self.lbl_conn.config(text=f"❌ HTTP {r.status_code}",fg=RED),
                    self._log(f"❌ HTTP {r.status_code}: {r.text[:100]}")])
        except Exception as e:
            self.after(0, lambda: [self.lbl_conn.config(text=f"❌ Error",fg=RED),
                                    self._log(f"❌ {e}")])

    # ── POLL ─────────────────────────────────────────────────
    def _poll_loop(self):
        while self._running:
            k = self.cfg.get("api_key","").strip()
            b = self.cfg.get("bin_id","").strip()
            if k and b:
                try:
                    r = requests.get(f"{BASE}/b/{b}/latest", headers=hdrs(k), timeout=10)
                    if r.ok:
                        stus = r.json().get("record",{}).get("students",{})
                        self.after(0, lambda s=stus: self._update_waiting(s))
                except: pass
            time.sleep(6)

    def _update_waiting(self, stus):
        n = len(stus)
        self.lbl_online.config(
            text=f"{n} student{'s' if n!=1 else ''} online" if n else "No students online",
            fg=GRN if n else DIM)
        for w in self.wait_grid.winfo_children(): w.destroy()
        if stus:
            self.wait_empty.pack_forget()
            for name, info in stus.items():
                c = tk.Frame(self.wait_grid, bg=SUR,
                             highlightbackground=BOR, highlightthickness=1)
                c.pack(side="left", padx=6, pady=6, ipadx=10, ipady=8)
                tk.Label(c, text=f"● {name}", bg=SUR, fg=GRN,
                         font=("Segoe UI",10,"bold")).pack(anchor="w")
                joined = datetime.fromtimestamp(
                    info.get("joined_at", time.time())).strftime("%H:%M:%S")
                tk.Label(c, text=f"Joined {joined}", bg=SUR, fg=DIM,
                         font=("Courier New",9)).pack(anchor="w")
                status = info.get("status","waiting")
                tk.Label(c, text=status.upper(), bg=SUR,
                         fg=GRN if status=="waiting" else YEL,
                         font=("Courier New",9)).pack(anchor="w")
        else:
            self.wait_empty.pack(expand=True)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logbox.config(state="normal")
        self.logbox.insert("end", f"[{ts}] {msg}\n")
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def _quit(self):
        self._running = False
        self.destroy()


if __name__ == "__main__":
    Proctor().mainloop()