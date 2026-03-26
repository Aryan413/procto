"""
ExamShield – main.py  (Complete Fixed Version)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIXES:
  1. AUDIO  – WebSocket (Socket.IO) replaces HTTP-polling.
              Works with ngrok HTTPS (ssl_verify=False).
              Jitter-buffer prevents crackling.
              Per-student audio rooms → proctors receive the right voice.

  2. CAMERA – Left panel canvas is filled completely (aspect-ratio aware).
              No more tiny-thumbnail in live-view.

  3. MULTI-STUDENT – Per-student server sessions keyed by student_id.
              Unlimited concurrent students; each has independent
              audio, video, violations, strikes counters.

Run as COMBINED (server + UI on same machine):
    python main.py

Run as SERVER ONLY (headless, e.g. on VPS / ngrok host):
    python main.py --server

Run CLIENT ONLY (student / proctor machine):
    python main.py --client
"""

import tkinter as tk
from tkinter import messagebox, ttk
import sqlite3, time, threading, queue, sys, json
import cv2
import numpy as np
from PIL import Image, ImageTk

# ─── Optional deps ────────────────────────────────────────────────────────────
try:
    import sounddevice as sd
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False
    print("[WARN] sounddevice not found – audio disabled. pip install sounddevice")

try:
    import socketio as sio_client
    HAS_SIO_CLIENT = True
except ImportError:
    HAS_SIO_CLIENT = False
    print("[WARN] python-socketio not found. pip install 'python-socketio[client]'")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ══════════════════════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════════════════════
DARK_BG   = "#0d1117"
PANEL_BG  = "#161b22"
ACCENT    = "#00e5ff"
RED_ALERT = "#ff4d4d"
GREEN_OK  = "#0be881"
TEXT_FG   = "#e6edf3"
SUBTLE    = "#8b949e"

# ══════════════════════════════════════════════════════════════════════════════
#  1.  AUDIO ENGINE  – Socket.IO WebSocket  (FIX #1)
# ══════════════════════════════════════════════════════════════════════════════
SAMPLE_RATE  = 16000
CHANNELS     = 1
CHUNK_MS     = 30
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)   # 480 samples


class AudioEngine:
    """
    Two-way audio over Socket.IO WebSocket.

    Student  →  server room "student_<sid>"  →  proctor
    Proctor  →  server room "proctor_<sid>"  →  targeted student

    ssl_verify=False  allows ngrok HTTPS tunnels to work.
    """

    def __init__(self, role: str, server_url: str, user_id: str):
        self.role    = role
        self.url     = server_url.rstrip("/")
        self.uid     = user_id
        self.running = False
        self._play_q: queue.Queue = queue.Queue(maxsize=30)
        self._sio    = sio_client.Client(ssl_verify=False, logger=False,
                                         engineio_logger=False) if HAS_SIO_CLIENT else None
        self._connected = False
        self._current_listen: str = ""
        self._setup_events()

    # ── Socket.IO callbacks ──────────────────────────────────────────────────
    def _setup_events(self):
        if not self._sio:
            return

        @self._sio.on("connect")
        def _on_connect():
            self._connected = True
            self._sio.emit("register", {"role": self.role, "uid": self.uid})
            print(f"[Audio] connected as {self.role}/{self.uid}")

        @self._sio.on("disconnect")
        def _on_disconnect():
            self._connected = False
            print("[Audio] disconnected")

        @self._sio.on("audio_chunk")
        def _on_chunk(data):
            try:
                pcm = data.get("pcm") if isinstance(data, dict) else data
                if isinstance(pcm, (bytes, bytearray)):
                    arr = np.frombuffer(pcm, dtype="int16")
                    if not self._play_q.full():
                        self._play_q.put_nowait(arr)
            except Exception as e:
                print(f"[Audio] rx error: {e}")

    # ── Public API ───────────────────────────────────────────────────────────
    def start(self):
        self.running = True
        threading.Thread(target=self._connect_loop, daemon=True).start()
        if HAS_AUDIO:
            threading.Thread(target=self._tx_loop,   daemon=True).start()
            threading.Thread(target=self._play_loop, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            if self._sio and self._connected:
                self._sio.disconnect()
        except:
            pass

    def select_student(self, student_id: str):
        """Proctor: switch which student's audio to receive."""
        if not self._sio or not self._connected:
            return
        self._current_listen = student_id
        self._sio.emit("proctor_listen", {"target": student_id})
        # flush stale audio
        while not self._play_q.empty():
            try:
                self._play_q.get_nowait()
            except:
                break

    # ── Internal threads ─────────────────────────────────────────────────────
    def _connect_loop(self):
        if not self._sio:
            return
        while self.running:
            try:
                self._sio.connect(
                    self.url,
                    transports=["websocket"],
                    wait_timeout=8
                )
                self._sio.wait()      # blocks until server closes or error
            except Exception as e:
                print(f"[Audio] connect failed: {e}. Retry in 4s…")
                time.sleep(4)

    def _tx_loop(self):
        """Mic → Socket.IO"""
        # Wait for connection
        for _ in range(60):
            if self._connected:
                break
            time.sleep(0.5)
        if not self._connected:
            print("[Audio] TX: never connected, mic disabled")
            return
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                dtype="int16", blocksize=CHUNK_FRAMES) as stream:
                while self.running:
                    data, _ = stream.read(CHUNK_FRAMES)
                    if self._sio and self._connected:
                        self._sio.emit("audio_chunk", {
                            "from": self.uid,
                            "role": self.role,
                            "pcm":  data.tobytes()
                        })
        except Exception as e:
            print(f"[Mic] Error: {e}")

    def _play_loop(self):
        """Jitter-buffer → Speakers — waits for MIN_BUFFER chunks before playing"""
        MIN_BUFFER = 4
        try:
            with sd.OutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                  dtype="int16", blocksize=CHUNK_FRAMES) as stream:
                while self.running:
                    if self._play_q.qsize() >= MIN_BUFFER:
                        try:
                            chunk = self._play_q.get_nowait()
                            stream.write(chunk.reshape(-1, CHANNELS))
                        except queue.Empty:
                            pass
                    else:
                        time.sleep(0.005)
        except Exception as e:
            print(f"[Speaker] Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  2.  VIDEO CLIENT / RECEIVER
# ══════════════════════════════════════════════════════════════════════════════
class VideoClient:
    """Student – captures webcam and POSTs JPEG frames to server."""

    def __init__(self, server_url: str, student_id: str, cam_index: int = 0):
        self.url      = server_url.rstrip("/")
        self.sid      = student_id
        self.cam_idx  = cam_index
        self.running  = False
        self.latest_frame: np.ndarray | None = None
        self._lock    = threading.Lock()

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        cap = cv2.VideoCapture(self.cam_idx)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 15)
        while self.running:
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.05)
                continue
            with self._lock:
                self.latest_frame = frame.copy()
            _, buf = cv2.imencode(".jpg", frame,
                                  [cv2.IMWRITE_JPEG_QUALITY, 60])
            if HAS_REQUESTS:
                try:
                    requests.post(
                        f"{self.url}/push_frame/{self.sid}",
                        data=buf.tobytes(),
                        headers={"Content-Type": "image/jpeg"},
                        timeout=0.3,
                        verify=False
                    )
                except:
                    pass
            time.sleep(1 / 12)
        cap.release()

    def get_local_frame(self):
        with self._lock:
            return self.latest_frame


class VideoReceiver:
    """Proctor – polls server for students' latest JPEG frames."""

    def __init__(self, server_url: str):
        self.url     = server_url.rstrip("/")
        self.running = False
        self._sids   : set         = set()
        self._frames : dict        = {}    # sid → np.ndarray BGR
        self._lock   = threading.Lock()

    def watch(self, sid: str):
        self._sids.add(sid)

    def unwatch(self, sid: str):
        self._sids.discard(sid)

    def get_frame(self, sid: str):
        with self._lock:
            return self._frames.get(sid)

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            for sid in list(self._sids):
                if not HAS_REQUESTS:
                    break
                try:
                    r = requests.get(
                        f"{self.url}/pull_frame/{sid}",
                        timeout=0.5,
                        verify=False
                    )
                    if r.status_code == 200 and r.content:
                        arr = np.frombuffer(r.content, dtype=np.uint8)
                        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        if frame is not None:
                            with self._lock:
                                self._frames[sid] = frame
                except:
                    pass
            time.sleep(0.05)   # ~20 fps polling


# ══════════════════════════════════════════════════════════════════════════════
#  3.  CAMERA FILL HELPER  (FIX #2)
# ══════════════════════════════════════════════════════════════════════════════
def frame_to_photo(frame: np.ndarray, target_w: int, target_h: int) -> ImageTk.PhotoImage:
    """
    Resize *frame* (BGR) to fill (target_w × target_h) while keeping
    aspect ratio, then centre-pad with black.  Returns PhotoImage.
    """
    fh, fw = frame.shape[:2]
    if fw == 0 or fh == 0:
        return None
    scale  = min(target_w / fw, target_h / fh)
    nw, nh = int(fw * scale), int(fh * scale)
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas  = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    x0 = (target_w - nw) // 2
    y0 = (target_h - nh) // 2
    canvas[y0:y0+nh, x0:x0+nw] = resized
    rgb   = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb))


# ══════════════════════════════════════════════════════════════════════════════
#  4.  STUDENT WINDOW
# ══════════════════════════════════════════════════════════════════════════════
class StudentWindow:
    def __init__(self, uid: str, audio: AudioEngine, video: VideoClient):
        self.uid   = uid
        self.audio = audio
        self.video = video
        self.win   = tk.Tk()
        self.win.title(f"ExamShield – Student: {uid}")
        self.win.configure(bg=DARK_BG)
        self.win.geometry("920x580")
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._tick()
        self.win.mainloop()

    def _build(self):
        # Header
        hdr = tk.Frame(self.win, bg=PANEL_BG, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"🛡 ExamShield  ·  {self.uid}",
                 font=("Consolas", 12, "bold"), fg=ACCENT, bg=PANEL_BG
                ).pack(side="left", padx=14, pady=12)
        tk.Label(hdr, text="● LIVE  🎙 AUDIO",
                 font=("Consolas", 9), fg=RED_ALERT, bg=PANEL_BG
                ).pack(side="right", padx=14)

        # Body
        body = tk.Frame(self.win, bg=DARK_BG)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        # Left: camera
        lf = tk.Frame(body, bg=PANEL_BG)
        lf.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(lf, text="📷 Camera", font=("Consolas", 9),
                 fg=SUBTLE, bg=PANEL_BG).pack(anchor="w", padx=8, pady=(6, 2))
        self.cam_cv = tk.Canvas(lf, bg="#000", highlightthickness=0)
        self.cam_cv.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Right: status
        rf = tk.Frame(body, bg=PANEL_BG, width=220)
        rf.pack(side="right", fill="y")
        rf.pack_propagate(False)
        tk.Label(rf, text="Status", font=("Consolas", 10, "bold"),
                 fg=ACCENT, bg=PANEL_BG).pack(anchor="w", padx=10, pady=(10, 6))
        self.status_lbl = tk.Label(rf, text="● Connected",
                                   font=("Consolas", 9), fg=GREEN_OK, bg=PANEL_BG)
        self.status_lbl.pack(anchor="w", padx=10)
        tk.Label(rf, text="\nKeep your face visible.\nDo not switch tabs.",
                 font=("Consolas", 8), fg=SUBTLE, bg=PANEL_BG,
                 justify="left").pack(anchor="w", padx=10)

    def _tick(self):
        frame = self.video.get_local_frame()
        if frame is not None:
            w = max(self.cam_cv.winfo_width(),  1)
            h = max(self.cam_cv.winfo_height(), 1)
            photo = frame_to_photo(frame, w, h)
            if photo:
                self.cam_cv.create_image(0, 0, anchor="nw", image=photo)
                self.cam_cv.image = photo
        self.win.after(66, self._tick)

    def _close(self):
        self.audio.stop()
        self.video.stop()
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  5.  PROCTOR LIVE-VIEW  (FIX #2 – camera fills left 50%)
# ══════════════════════════════════════════════════════════════════════════════
class LiveViewWindow:
    """
    Left 50%  →  student camera feed, fills the panel completely.
    Right 50% →  violations log.
    """

    def __init__(self, parent, uid: str, receiver: VideoReceiver,
                 audio: AudioEngine):
        self.uid      = uid
        self.receiver = receiver
        self.audio    = audio

        self.win = tk.Toplevel(parent)
        self.win.title(f"Student: {uid}")
        self.win.configure(bg=DARK_BG)
        self.win.geometry("1120x660")
        self.win.protocol("WM_DELETE_WINDOW", self._close)

        receiver.watch(uid)
        audio.select_student(uid)
        self._build()
        self._tick()

    def _build(self):
        # ── Header
        hdr = tk.Frame(self.win, bg=PANEL_BG, height=46)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=f"👤  {self.uid}  —  Live View",
                 font=("Consolas", 12, "bold"), fg=ACCENT, bg=PANEL_BG
                ).pack(side="left", padx=14, pady=12)
        tk.Button(hdr, text="✕ Close",
                  font=("Consolas", 9), fg=TEXT_FG, bg="#30363d",
                  activebackground=RED_ALERT, relief="flat", padx=10,
                  command=self._close
                 ).pack(side="right", padx=10, pady=8)

        # ── Paned split: left camera | right violations
        pane = tk.PanedWindow(self.win, orient="horizontal",
                              bg=DARK_BG, sashwidth=4,
                              sashrelief="flat", sashpad=0)
        pane.pack(fill="both", expand=True)

        # LEFT – camera canvas fills entire half
        left = tk.Frame(pane, bg="#000")
        pane.add(left, minsize=400, stretch="always")

        self.cam_cv = tk.Canvas(left, bg="#000", highlightthickness=0)
        self.cam_cv.pack(fill="both", expand=True)

        # RIGHT – violations
        right = tk.Frame(pane, bg=PANEL_BG)
        pane.add(right, minsize=300, stretch="always")

        tk.Label(right, text="⚠  Violations",
                 font=("Consolas", 11, "bold"), fg=RED_ALERT, bg=PANEL_BG
                ).pack(anchor="w", padx=14, pady=(14, 4))

        vf = tk.Frame(right, bg=PANEL_BG)
        vf.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        sb = tk.Scrollbar(vf, orient="vertical", bg=PANEL_BG)
        self.vbox = tk.Listbox(
            vf, yscrollcommand=sb.set,
            font=("Consolas", 9), fg=RED_ALERT,
            bg="#0d1117", selectbackground="#30363d",
            relief="flat", highlightthickness=0, bd=0
        )
        sb.config(command=self.vbox.yview)
        sb.pack(side="right", fill="y")
        self.vbox.pack(fill="both", expand=True)

        # Stats strip
        self.stats_var = tk.StringVar(value="Faces: —  |  Gaze: —  |  Strikes: 0")
        tk.Label(right, textvariable=self.stats_var,
                 font=("Consolas", 9), fg=SUBTLE, bg=PANEL_BG
                ).pack(anchor="w", padx=14, pady=(4, 10))

        # Set initial sash at 50%
        self.win.update_idletasks()
        self.win.after(100, lambda: pane.sash_place(0,
                        self.win.winfo_width() // 2, 0))

    def add_violation(self, text: str):
        ts = time.strftime("%H:%M:%S")
        self.vbox.insert("end", f"[{ts}]  {text}")
        self.vbox.see("end")

    def update_stats(self, faces: int, gaze: str, strikes: int):
        self.stats_var.set(
            f"Faces: {faces}  |  Gaze: {gaze}  |  Strikes: {strikes}"
        )

    def _tick(self):
        if not self.win.winfo_exists():
            return
        frame = self.receiver.get_frame(self.uid)
        if frame is not None:
            w = max(self.cam_cv.winfo_width(),  1)
            h = max(self.cam_cv.winfo_height(), 1)
            photo = frame_to_photo(frame, w, h)
            if photo:
                self.cam_cv.create_image(0, 0, anchor="nw", image=photo)
                self.cam_cv.image = photo
        self.win.after(50, self._tick)   # 20 fps

    def _close(self):
        self.receiver.unwatch(self.uid)
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  6.  PROCTOR DASHBOARD  (FIX #3 – unlimited students, grid cards)
# ══════════════════════════════════════════════════════════════════════════════
class ProctorDashboard:
    def __init__(self, uid: str, server_url: str,
                 audio: AudioEngine, receiver: VideoReceiver):
        self.uid      = uid
        self.url      = server_url.rstrip("/")
        self.audio    = audio
        self.receiver = receiver
        self.live_views: dict[str, LiveViewWindow] = {}
        self._cards   : dict[str, tk.Frame]        = {}

        self.win = tk.Tk()
        self.win.title("ExamShield – Proctor Dashboard")
        self.win.configure(bg=DARK_BG)
        self.win.geometry("1100x680")
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._poll()
        self.win.mainloop()

    def _build(self):
        # Header
        hdr = tk.Frame(self.win, bg=PANEL_BG, height=48)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🛡  ExamShield  ·  Proctor Dashboard",
                 font=("Consolas", 13, "bold"), fg=ACCENT, bg=PANEL_BG
                ).pack(side="left", padx=16, pady=12)
        self.count_var = tk.StringVar(value="Students online: 0")
        tk.Label(hdr, textvariable=self.count_var,
                 font=("Consolas", 9), fg=SUBTLE, bg=PANEL_BG
                ).pack(side="right", padx=16)

        # Scrollable student grid
        outer = tk.Frame(self.win, bg=DARK_BG)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        sb = tk.Scrollbar(outer, orient="vertical")
        self.grid_canvas = tk.Canvas(outer, bg=DARK_BG,
                                     yscrollcommand=sb.set,
                                     highlightthickness=0)
        sb.config(command=self.grid_canvas.yview)
        sb.pack(side="right", fill="y")
        self.grid_canvas.pack(fill="both", expand=True)

        self.grid_frame = tk.Frame(self.grid_canvas, bg=DARK_BG)
        self._grid_win  = self.grid_canvas.create_window(
            (0, 0), window=self.grid_frame, anchor="nw"
        )
        self.grid_frame.bind("<Configure>", lambda e: self.grid_canvas.configure(
            scrollregion=self.grid_canvas.bbox("all")
        ))
        self.grid_canvas.bind("<Configure>", lambda e:
            self.grid_canvas.itemconfig(self._grid_win, width=e.width)
        )

    # ── Student card ─────────────────────────────────────────────────────────
    def _make_card(self, sid: str) -> tk.Frame:
        idx = len(self._cards)
        row = idx // 5
        col = idx %  5

        card = tk.Frame(self.grid_frame, bg=PANEL_BG,
                        relief="flat", padx=4, pady=4)
        card.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")

        # Thumbnail (FIX #2 mini version)
        thumb = tk.Canvas(card, bg="#000", width=180, height=102,
                          highlightthickness=1,
                          highlightbackground="#30363d")
        thumb.grid(row=0, column=0, columnspan=3, padx=4, pady=(4, 2))
        card.thumb = thumb

        tk.Label(card, text=sid, font=("Consolas", 9, "bold"),
                 fg=ACCENT, bg=PANEL_BG
                ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6)

        card.faces_v   = tk.StringVar(value="Faces: 0")
        card.gaze_v    = tk.StringVar(value="Gaze: center")
        card.strikes_v = tk.StringVar(value="Strikes: 0")

        for c, vname in enumerate(["faces_v", "gaze_v", "strikes_v"]):
            clr = RED_ALERT if vname == "strikes_v" else SUBTLE
            tk.Label(card, textvariable=getattr(card, vname),
                     font=("Consolas", 8), fg=clr, bg=PANEL_BG
                    ).grid(row=2, column=c, padx=4, sticky="w")

        tk.Button(card, text="Select ›",
                  font=("Consolas", 8), fg=TEXT_FG, bg="#21262d",
                  activebackground=ACCENT, relief="flat", padx=8,
                  command=lambda s=sid: self._open_live(s)
                 ).grid(row=3, column=0, columnspan=3,
                         pady=(6, 4), padx=4, sticky="ew")

        self._cards[sid] = card
        self.receiver.watch(sid)
        return card

    def _open_live(self, sid: str):
        if sid in self.live_views:
            try:
                self.live_views[sid].win.lift()
                return
            except tk.TclError:
                pass
        self.live_views[sid] = LiveViewWindow(
            self.win, sid, self.receiver, self.audio
        )

    # ── Periodic poll ────────────────────────────────────────────────────────
    def _poll(self):
        if not HAS_REQUESTS:
            self.win.after(1000, self._poll)
            return
        try:
            r = requests.get(f"{self.url}/students",
                             timeout=1.5, verify=False)
            if r.status_code == 200:
                data: list[dict] = r.json()
                self.count_var.set(f"Students online: {len(data)}")
                for s in data:
                    sid   = s["id"]
                    card  = self._cards.get(sid) or self._make_card(sid)
                    faces = s.get("faces",   0)
                    gaze  = s.get("gaze",    "center")
                    stk   = s.get("strikes", 0)
                    card.faces_v.set(f"Faces: {faces}")
                    card.gaze_v.set(f"Gaze: {gaze}")
                    card.strikes_v.set(f"Strikes: {stk}")
                    # Red tint if violations
                    card.configure(bg="#2d1b1b" if stk > 0 else PANEL_BG)
                    # Push violations to open live view
                    for v in s.get("new_violations", []):
                        if sid in self.live_views:
                            try:
                                self.live_views[sid].add_violation(v)
                                self.live_views[sid].update_stats(
                                    faces, gaze, stk
                                )
                            except:
                                pass
                    # Update thumbnail
                    frame = self.receiver.get_frame(sid)
                    if frame is not None:
                        photo = frame_to_photo(frame, 180, 102)
                        if photo:
                            card.thumb.create_image(
                                0, 0, anchor="nw", image=photo
                            )
                            card.thumb.image = photo
        except:
            pass
        self.win.after(600, self._poll)

    def _close(self):
        self.audio.stop()
        self.receiver.stop()
        self.win.destroy()


# ══════════════════════════════════════════════════════════════════════════════
#  7.  DATABASE
# ══════════════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect("students.db")
    conn.execute("""CREATE TABLE IF NOT EXISTS users(
        id   TEXT PRIMARY KEY,
        pw   TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student'
    )""")
    conn.execute(
        "INSERT OR IGNORE INTO users VALUES('admin','admin123','proctor')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO users VALUES('student1','pass1','student')"
    )
    conn.commit()
    conn.close()


def check_credentials(uid: str, pw: str):
    conn = sqlite3.connect("students.db")
    row = conn.execute(
        "SELECT role FROM users WHERE id=? AND pw=?", (uid, pw)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ══════════════════════════════════════════════════════════════════════════════
#  8.  LOGIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════
class LoginWindow:
    def __init__(self):
        init_db()
        self.win = tk.Tk()
        self.win.title("ExamShield – Login")
        self.win.configure(bg=DARK_BG)
        self.win.geometry("430x510")
        self.win.resizable(False, False)
        self._build()
        self.win.mainloop()

    def _build(self):
        tk.Label(self.win, text="🛡",
                 font=("Segoe UI Emoji", 38),
                 fg=ACCENT, bg=DARK_BG).pack(pady=(28, 0))
        tk.Label(self.win, text="ExamShield",
                 font=("Consolas", 22, "bold"),
                 fg=ACCENT, bg=DARK_BG).pack()
        tk.Label(self.win, text="Secure Online Proctoring",
                 font=("Consolas", 9), fg=SUBTLE, bg=DARK_BG).pack(pady=(0, 22))

        f = tk.Frame(self.win, bg=PANEL_BG, padx=30, pady=26)
        f.pack(fill="x", padx=30)

        # Role selection
        row = tk.Frame(f, bg=PANEL_BG)
        row.pack(fill="x", pady=(0, 14))
        self.role_var = tk.StringVar(value="student")
        for val, lbl in [("student", "Student"), ("proctor", "Proctor")]:
            tk.Radiobutton(
                row, text=lbl, variable=self.role_var, value=val,
                font=("Consolas", 10), fg=TEXT_FG, bg=PANEL_BG,
                selectcolor=DARK_BG, activebackground=PANEL_BG,
                activeforeground=ACCENT
            ).pack(side="left", padx=12)

        # Fields
        fields = [
            ("User ID",     "uid_ent",  "",                        ""),
            ("Password",    "pw_ent",   "",                        "*"),
            ("Server URL",  "url_ent",  "http://127.0.0.1:6000",  ""),
        ]
        for (label, attr, default, show) in fields:
            tk.Label(f, text=label, font=("Consolas", 9),
                     fg=SUBTLE, bg=PANEL_BG).pack(anchor="w")
            ent = tk.Entry(f, font=("Consolas", 10),
                           bg="#0d1117", fg=TEXT_FG,
                           insertbackground=ACCENT,
                           relief="flat", bd=0, show=show)
            ent.insert(0, default)
            ent.pack(fill="x", pady=(2, 10), ipady=7, padx=2)
            setattr(self, attr, ent)

        self.err_var = tk.StringVar()
        tk.Label(f, textvariable=self.err_var,
                 fg=RED_ALERT, bg=PANEL_BG,
                 font=("Consolas", 9)).pack()

        tk.Button(f, text="Login  →",
                  font=("Consolas", 11, "bold"),
                  fg=DARK_BG, bg=ACCENT,
                  activebackground="#00b8d4",
                  relief="flat", padx=14, pady=9,
                  command=self._login
                 ).pack(fill="x", pady=(10, 0))

    def _login(self):
        uid  = self.uid_ent.get().strip()
        pw   = self.pw_ent.get().strip()
        url  = self.url_ent.get().strip().rstrip("/")
        role = check_credentials(uid, pw)

        if not uid or not url:
            self.err_var.set("User ID and Server URL are required.")
            return

        if role is None:
            # Allow any ID/pw in dev mode (or check against server)
            # Change this to strict mode if needed
            role = self.role_var.get()

        self.win.destroy()
        audio = AudioEngine(role, url, uid)
        audio.start()

        if role == "student":
            video = VideoClient(url, uid)
            video.start()
            StudentWindow(uid, audio, video)
        else:
            recv = VideoReceiver(url)
            recv.start()
            ProctorDashboard(uid, url, audio, recv)


# ══════════════════════════════════════════════════════════════════════════════
#  9.  FLASK + SOCKET.IO SERVER  (FIX #1 + #3 – WebSocket audio, per-student)
# ══════════════════════════════════════════════════════════════════════════════
def start_server():
    try:
        from flask import Flask, request as freq, Response, jsonify
        from flask_socketio import (SocketIO, emit, join_room,
                                     leave_room, rooms as get_rooms)
    except ImportError:
        print("pip install flask flask-socketio  is required for the server!")
        return

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "examshield_2025"
    sio = SocketIO(app,
                   async_mode="threading",
                   cors_allowed_origins="*",
                   logger=False,
                   engineio_logger=False,
                   ping_timeout=20,
                   ping_interval=10)

    # ── State (per-student, FIX #3) ──────────────────────────────────────────
    # sessions[uid] = {
    #   "frames": [bytes],   (latest JPEG, list of 1)
    #   "faces": int,  "gaze": str,  "strikes": int,
    #   "violations": [],  "new_violations": [],
    #   "last_seen": float
    # }
    sessions   : dict[str, dict] = {}
    sio_to_uid : dict[str, str]  = {}   # socketio sid → user id
    sio_to_role: dict[str, str]  = {}   # socketio sid → role
    proctor_target: dict[str, str] = {} # proctor uid → student uid they listen to

    def get_sess(uid: str) -> dict:
        if uid not in sessions:
            sessions[uid] = {
                "frames": [], "faces": 0, "gaze": "center",
                "strikes": 0, "violations": [],
                "new_violations": [], "last_seen": time.time()
            }
        sessions[uid]["last_seen"] = time.time()
        return sessions[uid]

    # ── Socket.IO ─────────────────────────────────────────────────────────────
    @sio.on("connect")
    def _connect():
        print(f"[WS] client connected: {freq.sid}")

    @sio.on("disconnect")
    def _disconnect():
        uid = sio_to_uid.pop(freq.sid, None)
        sio_to_role.pop(freq.sid, None)
        if uid and uid in sessions:
            del sessions[uid]
            print(f"[WS] {uid} disconnected & session removed")

    @sio.on("register")
    def _register(data):
        uid  = data.get("uid")
        role = data.get("role", "student")
        sio_to_uid[freq.sid]  = uid
        sio_to_role[freq.sid] = role
        if role == "student":
            join_room(f"stu_{uid}")
            get_sess(uid)
            print(f"[WS] student registered: {uid}")
        else:
            join_room("proctors")
            print(f"[WS] proctor registered: {uid}")

    @sio.on("audio_chunk")
    def _audio(data):
        sender_uid = data.get("from")
        role       = data.get("role")
        pcm        = data.get("pcm")
        if role == "student":
            # Send to every proctor that's listening to this student
            sio.emit("audio_chunk", {"pcm": pcm},
                     to=f"listening_{sender_uid}")
        else:
            # Proctor → targeted student
            p_uid  = sio_to_uid.get(freq.sid)
            target = proctor_target.get(p_uid)
            if target:
                sio.emit("audio_chunk", {"pcm": pcm},
                         to=f"stu_{target}")

    @sio.on("proctor_listen")
    def _proctor_listen(data):
        target     = data.get("target")
        p_uid      = sio_to_uid.get(freq.sid)
        # Leave old listening room
        old = proctor_target.get(p_uid)
        if old:
            leave_room(f"listening_{old}")
        # Join new room
        join_room(f"listening_{target}")
        proctor_target[p_uid] = target
        print(f"[WS] proctor {p_uid} now listening to {target}")

    # ── HTTP: video frames ────────────────────────────────────────────────────
    @app.route("/push_frame/<uid>", methods=["POST"])
    def push_frame(uid):
        sess = get_sess(uid)
        sess["frames"] = [freq.get_data()]   # keep only latest
        return "", 204

    @app.route("/pull_frame/<uid>")
    def pull_frame(uid):
        sess = sessions.get(uid)
        if sess and sess["frames"]:
            return Response(sess["frames"][0], mimetype="image/jpeg")
        return "", 204

    # ── HTTP: student report (face/gaze/violations from student app) ─────────
    @app.route("/report/<uid>", methods=["POST"])
    def report(uid):
        sess = get_sess(uid)
        body = freq.get_json(silent=True) or {}
        sess["faces"]          = body.get("faces",      0)
        sess["gaze"]           = body.get("gaze",       "center")
        sess["strikes"]        = body.get("strikes",    sess["strikes"])
        new_v                  = body.get("violations", [])
        sess["violations"].extend(new_v)
        sess["new_violations"] = new_v
        return "", 204

    # ── HTTP: proctor polls student list ─────────────────────────────────────
    @app.route("/students")
    def list_students():
        now   = time.time()
        alive = []
        dead  = []
        for uid, s in sessions.items():
            if now - s["last_seen"] < 20:      # 20 s timeout
                alive.append({
                    "id":             uid,
                    "faces":          s["faces"],
                    "gaze":           s["gaze"],
                    "strikes":        s["strikes"],
                    "new_violations": s.pop("new_violations", [])
                })
            else:
                dead.append(uid)
        for uid in dead:
            del sessions[uid]
        return jsonify(alive)

    @app.route("/ping")
    def ping():
        return "pong", 200

    print("[Server] ✅ ExamShield server starting → http://0.0.0.0:6000")
    sio.run(app, host="0.0.0.0", port=6000,
            debug=False, use_reloader=False,
            allow_unsafe_werkzeug=True)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--server" in sys.argv:
        # Headless server only (VPS / ngrok host)
        start_server()
    elif "--client" in sys.argv:
        # Client only (no embedded server)
        LoginWindow()
    else:
        # Combined: embedded server + client UI (default, single-machine dev)
        srv_thread = threading.Thread(target=start_server, daemon=True)
        srv_thread.start()
        time.sleep(1.2)    # give Flask time to bind
        LoginWindow()