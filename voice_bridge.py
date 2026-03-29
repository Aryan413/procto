"""
voice_bridge.py  —  Low-latency two-way audio for ExamShield
=============================================================
Place this file next to main.py.

Architecture
------------
  Proctor machine runs a WebSocket server on port 6001 (start_voice_bridge).
  Both proctor and student each run a VoiceClient that connects to that server.

  Client roles:
    "proctor"  — captures proctor mic  → sends to bridge  → bridge fans out to students
    "student"  — captures student mic  → sends to bridge  → bridge fans out to proctor

  The bridge simply re-broadcasts each incoming audio chunk to every OTHER
  connected client (i.e. proctor hears student, student hears proctor).

Install deps (if missing):
  pip install sounddevice websocket-client flask-sock
"""

import threading
import queue
import time
import struct

try:
    import numpy as np
    _NP_OK = True
except ImportError:
    _NP_OK = False

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE  = 16000   # Hz
CHANNELS     = 1       # mono
CHUNK_FRAMES = 320     # 20 ms per chunk — low latency (was 100ms = 1600 frames)
DTYPE        = "int16"

# ── Optional imports ───────────────────────────────────────────────────────────
try:
    import sounddevice as _sd
    _SD_OK = True
except ImportError:
    _SD_OK = False

try:
    import websocket as _ws_lib          # websocket-client
    _WS_CLIENT_OK = True
except ImportError:
    _WS_CLIENT_OK = False

try:
    from flask_sock import Sock as _FlaskSock
    _FLASK_SOCK_OK = True
except ImportError:
    _FLASK_SOCK_OK = False


# ══════════════════════════════════════════════════════════════════════════════
#  BRIDGE SERVER  (runs on proctor machine)
# ══════════════════════════════════════════════════════════════════════════════

_bridge_clients: list = []   # list of (role, ws_conn)
_bridge_lock = threading.Lock()


def start_voice_bridge(port: int = 6001):
    """
    Attach a WebSocket endpoint /ws/voice to a new tiny Flask app on `port`.
    Runs in a daemon thread — safe to call from main thread.
    """
    if not _FLASK_SOCK_OK:
        print("[VoiceBridge] flask-sock not installed — voice disabled")
        print("              Fix: pip install flask-sock")
        return
    if not _SD_OK:
        print("[VoiceBridge] sounddevice not installed — voice disabled")
        print("              Fix: pip install sounddevice")
        return

    try:
        from flask import Flask, request as _freq
        import logging
        app = Flask("VoiceBridgeServer")
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        sock = _FlaskSock(app)

        @sock.route("/ws/voice")
        def _ws_voice(ws):
            role = _freq.args.get("role", "unknown")
            with _bridge_lock:
                _bridge_clients.append((role, ws))
            print(f"[VoiceBridge] {role} connected  (total: {len(_bridge_clients)})")
            try:
                while True:
                    data = ws.receive()
                    if data is None:
                        break
                    # Re-broadcast to all OTHER clients
                    with _bridge_lock:
                        targets = [(r, c) for r, c in _bridge_clients if c is not ws]
                    for _, target_ws in targets:
                        try:
                            target_ws.send(data)
                        except Exception:
                            pass
            except Exception:
                pass
            finally:
                with _bridge_lock:
                    _bridge_clients[:] = [(r, c) for r, c in _bridge_clients if c is not ws]
                print(f"[VoiceBridge] {role} disconnected  (total: {len(_bridge_clients)})")

        def _run():
            app.run(host="0.0.0.0", port=port, threaded=True)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        print(f"[VoiceBridge] WebSocket server started on port {port}  (/ws/voice)")

    except Exception as e:
        print(f"[VoiceBridge] Failed to start: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  VOICE CLIENT  (runs on both proctor AND student machine)
# ══════════════════════════════════════════════════════════════════════════════

class VoiceClient:
    """
    Connects to the bridge WebSocket, streams microphone audio out, and
    plays received audio through the speaker — all in background threads.

    Audio pipeline (capture side):
      mic → delay-matched AEC → noise gate → VAD gate → mic gain → soft limiter → send

    Audio pipeline (playback side):
      receive → jitter buffer → volume → write speaker → store in AEC ring buffer

    Usage:
        vc = VoiceClient(role="student", bridge_url="ws://192.168.1.5:6000/ws/voice")
        vc.start()
        ...
        vc.stop()
    """

    RECONNECT_DELAY = 3.0

    # ── Mic gain ───────────────────────────────────────────────────────────────
    MIC_GAIN = 2.0

    # ── AEC — adaptive delay-matched echo cancellation ────────────────────────
    # At 20ms/chunk: 25 chunks = 500ms search window
    AEC_HISTORY  = 25
    AEC_STRENGTH = 0.90

    # ── Noise gate ─────────────────────────────────────────────────────────────
    # Lower threshold — 20ms chunks have naturally lower RMS than 100ms chunks.
    # Typical quiet room RMS at 20ms ≈ 50–150.  Voice ≈ 300+.
    NOISE_GATE_RMS = 120

    # ── Voice Activity Detection ───────────────────────────────────────────────
    VAD_RMS_MIN  = 150      # below this post-AEC = no voice
    VAD_HOLD_MS  = 300      # hold window after voice stops (ms)

    def __init__(self, role: str, bridge_url: str):
        self.role        = role
        self.bridge_url  = bridge_url
        self._running    = False
        self._connected  = False
        self._muted      = False
        self._volume     = 1.0
        self._ws         = None
        self._play_q: queue.Queue = queue.Queue(maxsize=3)  # 3×20ms = 60ms max buffer

        # AEC ring buffer: stores last AEC_HISTORY played chunks as float32 arrays
        self._aec_buf   = []          # list of np.ndarray, newest last
        self._aec_lock  = threading.Lock()

        self.on_status_change = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._connect_loop, daemon=True,
                         name=f"VoiceClient-{self.role}").start()
        threading.Thread(target=self._playback_loop, daemon=True,
                         name=f"VoicePlay-{self.role}").start()

    def stop(self):
        self._running   = False
        self._connected = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        return self._muted

    def set_volume(self, v: float):
        self._volume = max(0.0, float(v))

    # ── Internal — connection loop ─────────────────────────────────────────────

    def _connect_loop(self):
        while self._running:
            url = f"{self.bridge_url}?role={self.role}"
            self._connected = False
            try:
                self._notify_status(False, "Connecting…")
                ws = _ws_lib.WebSocketApp(
                    url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                self._notify_status(False, str(e))
            finally:
                self._connected = False
            if self._running:
                self._notify_status(False, f"Reconnecting in {self.RECONNECT_DELAY}s…")
                time.sleep(self.RECONNECT_DELAY)

    def _on_open(self, ws):
        self._connected = True
        self._notify_status(True, "Connected")
        threading.Thread(target=self._capture_loop, args=(ws,),
                         daemon=True, name=f"VoiceCap-{self.role}").start()

    def _on_message(self, ws, data):
        if not isinstance(data, (bytes, bytearray)):
            return
        chunk = bytes(data)
        if not chunk:
            return
        if self._play_q.full():
            try:
                self._play_q.get_nowait()
            except queue.Empty:
                pass
        try:
            self._play_q.put_nowait(chunk)
        except queue.Full:
            pass

    def _on_error(self, ws, error):
        self._connected = False
        self._notify_status(False, str(error))

    def _on_close(self, ws, *args):
        self._connected = False
        self._notify_status(False, "Disconnected")

    # ── Audio DSP helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _rms(samples_f32):
        """Root-mean-square energy of a float32 array."""
        if len(samples_f32) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples_f32 ** 2)))

    def _aec_cancel(self, mic_f32):
        """
        Delay-matched Acoustic Echo Cancellation.

        Searches the AEC ring buffer for the past speaker chunk that correlates
        most strongly with the current mic frame, then subtracts it at the
        computed optimal scale.  This handles the variable speaker→mic delay
        that made single-chunk subtraction unreliable.

        Returns the cleaned mic signal (float32, same length as mic_f32).
        """
        with self._aec_lock:
            history = list(self._aec_buf)   # snapshot, newest last

        if not history:
            return mic_f32

        n = len(mic_f32)
        best_corr  = -1.0
        best_chunk = None

        for ref in history:
            if len(ref) != n:
                continue
            # Normalised cross-correlation (dot product of unit vectors)
            mic_norm = mic_f32 / (np.linalg.norm(mic_f32) + 1e-9)
            ref_norm = ref     / (np.linalg.norm(ref)     + 1e-9)
            corr = float(np.dot(mic_norm, ref_norm))
            if corr > best_corr:
                best_corr  = corr
                best_chunk = ref

        if best_chunk is None or best_corr < 0.15:
            # Low correlation → no significant echo present, don't cancel
            return mic_f32

        # Optimal scale: how much of the reference is leaking into the mic
        scale = (np.dot(mic_f32, best_chunk) /
                 (np.dot(best_chunk, best_chunk) + 1e-9))
        scale = float(np.clip(scale, 0.0, 1.0))

        cleaned = mic_f32 - best_chunk * (scale * self.AEC_STRENGTH)
        return cleaned

    @staticmethod
    def _soft_limit(samples_f32, threshold=28000.0, ratio=8.0):
        """
        Soft-knee limiter: gentle compression above threshold, hard clip at 32767.
        Prevents digital distortion while preserving voice dynamics below threshold.
        """
        above = np.abs(samples_f32) > threshold
        if np.any(above):
            sign = np.sign(samples_f32)
            excess = np.abs(samples_f32) - threshold
            compressed = threshold + excess / ratio
            samples_f32 = np.where(above, sign * compressed, samples_f32)
        return np.clip(samples_f32, -32767.0, 32767.0)

    # ── Internal — microphone capture ─────────────────────────────────────────

    def _capture_loop(self, ws):
        """
        Full capture pipeline:
          raw mic → AEC → noise gate → VAD → mic gain → soft limiter → send

        CRITICAL: ws.send(data, opcode=OPCODE_BINARY) must be used.
        WebSocketApp.send() defaults to TEXT frame which corrupts binary audio.
        """
        if not _SD_OK:
            print(f"[VoiceClient/{self.role}] sounddevice missing — capture disabled")
            return

        import numpy as np
        try:
            OPCODE_BINARY = _ws_lib.ABNF.OPCODE_BINARY
        except AttributeError:
            OPCODE_BINARY = 0x2

        vad_hold_chunks = max(1, int(self.VAD_HOLD_MS / 100))  # 100ms per chunk
        vad_hold_count  = 0   # countdown: keep sending for this many more chunks

        print(f"[VoiceClient/{self.role}] Capture started")
        try:
            with _sd.InputStream(
                samplerate = SAMPLE_RATE,
                channels   = CHANNELS,
                dtype      = DTYPE,
                blocksize  = CHUNK_FRAMES,
                latency    = "low",
            ) as stream:
                while self._running and self._connected:
                    try:
                        chunk, overflowed = stream.read(CHUNK_FRAMES)
                    except Exception as e:
                        print(f"[VoiceClient/{self.role}] stream.read error: {e}")
                        break
                    if overflowed:
                        continue
                    if self._muted:
                        continue

                    # Flatten to mono float32
                    samples = (chunk[:, 0] if chunk.ndim > 1 else chunk).astype("float32")

                    # ── Stage 1: Noise gate ────────────────────────────────────
                    raw_rms = self._rms(samples)
                    if raw_rms < self.NOISE_GATE_RMS:
                        # Pure noise / silence — zero out and skip (don't send)
                        vad_hold_count = 0
                        continue

                    # ── Stage 2: AEC ──────────────────────────────────────────
                    samples = self._aec_cancel(samples)

                    # ── Stage 3: VAD — check if voice remains after AEC ───────
                    post_aec_rms = self._rms(samples)
                    if post_aec_rms >= self.VAD_RMS_MIN:
                        vad_hold_count = vad_hold_chunks   # voice detected — reset hold
                    elif vad_hold_count > 0:
                        vad_hold_count -= 1                # in hold window — still send
                    else:
                        continue                           # no voice, hold expired — skip

                    # ── Stage 4: Mic gain ─────────────────────────────────────
                    samples *= self.MIC_GAIN

                    # ── Stage 5: Soft limiter ─────────────────────────────────
                    samples = self._soft_limit(samples)

                    raw = samples.astype(DTYPE).tobytes()
                    try:
                        ws.send(raw, opcode=OPCODE_BINARY)
                    except Exception as e:
                        print(f"[VoiceClient/{self.role}] send error: {e}")
                        break
        except Exception as e:
            print(f"[VoiceClient/{self.role}] Capture error: {e}")
        print(f"[VoiceClient/{self.role}] Capture stopped")

    # ── Internal — speaker playback ────────────────────────────────────────────

    def _playback_loop(self):
        """
        Playback pipeline:
          receive → volume → write speaker → push to AEC ring buffer
        """
        if not _SD_OK:
            return

        import numpy as np

        silence_f32 = np.zeros(CHUNK_FRAMES, dtype="float32")
        silence_i16 = np.zeros(CHUNK_FRAMES, dtype=DTYPE)

        print(f"[VoiceClient/{self.role}] Playback started")
        try:
            with _sd.OutputStream(
                samplerate = SAMPLE_RATE,
                channels   = CHANNELS,
                dtype      = DTYPE,
                blocksize  = CHUNK_FRAMES,
                latency    = "low",
            ) as stream:
                while self._running:
                    try:
                        data = self._play_q.get(timeout=0.15)
                    except queue.Empty:
                        # Silence → push zeros into AEC buffer so AEC stays in sync
                        with self._aec_lock:
                            self._aec_buf.append(silence_f32.copy())
                            if len(self._aec_buf) > self.AEC_HISTORY:
                                self._aec_buf.pop(0)
                        stream.write(silence_i16)
                        continue

                    samples_i16 = np.frombuffer(data, dtype=DTYPE).copy()
                    # Exact block size
                    if len(samples_i16) < CHUNK_FRAMES:
                        samples_i16 = np.pad(samples_i16, (0, CHUNK_FRAMES - len(samples_i16)))
                    elif len(samples_i16) > CHUNK_FRAMES:
                        samples_i16 = samples_i16[:CHUNK_FRAMES]

                    # Volume adjustment
                    if self._volume != 1.0:
                        samples_i16 = np.clip(
                            samples_i16.astype("float32") * self._volume,
                            -32767, 32767
                        ).astype(DTYPE)

                    # Push float32 copy into AEC ring buffer BEFORE writing to speaker
                    # so the capture thread can find it when the echo arrives in mic
                    ref_f32 = samples_i16.astype("float32")
                    with self._aec_lock:
                        self._aec_buf.append(ref_f32)
                        if len(self._aec_buf) > self.AEC_HISTORY:
                            self._aec_buf.pop(0)

                    stream.write(samples_i16)
        except Exception as e:
            print(f"[VoiceClient/{self.role}] Playback error: {e}")
        print(f"[VoiceClient/{self.role}] Playback stopped")

    # ── Status helper ──────────────────────────────────────────────────────────

    def _notify_status(self, connected: bool, info: str):
        print(f"[VoiceClient/{self.role}] {'✅' if connected else '⚠'} {info}")
        if callable(self.on_status_change):
            try:
                self.on_status_change(connected, info)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════════════════════════════════════

def make_ws_url(http_url: str, ws_port: int = 6001) -> str:
    """
    Convert an HTTP proctor URL into a WebSocket voice bridge URL.

    For Cloudflare tunnels (trycloudflare.com) the voice relay is served
    on the SAME port as the main Flask app (port 6000 / 443) at /ws/voice.
    Cloudflare does not allow custom ports — appending :6001 breaks the connection.

    For plain LAN/localhost URLs the original port is preserved (port 6000)
    since the relay is also on port 6000 now.

    Examples:
      "https://abc.trycloudflare.com"  →  "wss://abc.trycloudflare.com/ws/voice"
      "http://192.168.1.5:6000"        →  "ws://192.168.1.5:6000/ws/voice"
      "http://127.0.0.1:6000"          →  "ws://127.0.0.1:6000/ws/voice"
    """
    import re
    url = http_url.rstrip("/")
    # Cloudflare tunnel — drop any port, use standard wss (443)
    if "trycloudflare.com" in url or "cloudflare" in url:
        url = re.sub(r":\d+$", "", url)
        if url.startswith("https://"):
            base = "wss://" + url[len("https://"):]
        else:
            base = "wss://" + url[len("http://"):]
        return f"{base}/ws/voice"
    # LAN / localhost — keep existing port, swap scheme only
    if url.startswith("https://"):
        base = "wss://" + url[len("https://"):]
    else:
        base = "ws://" + url[len("http://"):]
    return f"{base}/ws/voice"