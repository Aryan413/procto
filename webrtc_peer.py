"""
webrtc_peer.py  —  ExamShield Student-Side WebRTC Peer
=======================================================
Runs on the STUDENT machine alongside main.py.

This module:
  1. Captures the student camera via OpenCV
  2. Creates an aiortc RTCPeerConnection
  3. Connects to the signaling server via Socket.IO
  4. Handles SDP offer/answer exchange
  5. Streams video directly to the proctor's browser via WebRTC (UDP)
  6. Optionally sends JPEG frames to server for YOLO analysis

Usage (called from main.py):
    from webrtc_peer import WebRTCPeer
    peer = WebRTCPeer(server_url="https://your-ngrok-url", token="abc123", hub=camera_hub)
    peer.start()

Or standalone test:
    python webrtc_peer.py --server https://xxx.ngrok.io --token abc123
"""

import asyncio
import base64
import cv2
import fractions
import logging
import threading
import time
import numpy as np

# ── Optional deps ──────────────────────────────────────────────────────────
_SOCKETIO_AVAILABLE = False
try:
    import socketio as _sio_lib
    _SOCKETIO_AVAILABLE = True
except ImportError:
    pass

_AIORTC_AVAILABLE = False
try:
    from aiortc import (
        RTCPeerConnection, RTCSessionDescription, RTCIceCandidate,
        VideoStreamTrack, MediaStreamTrack,
    )
    from av import VideoFrame
    _AIORTC_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger("webrtc_peer")


# ══════════════════════════════════════════════════════════════════════════
#  CUSTOM VIDEO TRACK  — reads frames from CameraHub
# ══════════════════════════════════════════════════════════════════════════

if _AIORTC_AVAILABLE:
    class CameraHubTrack(VideoStreamTrack):
        """
        A VideoStreamTrack that pulls frames from the ExamShield CameraHub.
        If hub is None, generates a blank 640x360 black frame.
        """
        kind = "video"

        def __init__(self, hub=None, width=640, height=360, fps=15):
            super().__init__()
            self.hub    = hub
            self.width  = width
            self.height = height
            self.fps    = fps
            self._pts   = 0
            self._time_base = fractions.Fraction(1, 90000)

        async def recv(self):
            pts, time_base = await self.next_timestamp()

            # Get frame from hub or generate blank
            frame_bgr = None
            if self.hub is not None:
                frame_bgr = self.hub.get_frame()

            if frame_bgr is None:
                frame_bgr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
                # Draw "No feed" text on blank frame
                cv2.putText(frame_bgr, "Waiting for camera...",
                            (self.width // 2 - 100, self.height // 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)

            # Resize to target resolution
            h, w = frame_bgr.shape[:2]
            if (w, h) != (self.width, self.height):
                frame_bgr = cv2.resize(frame_bgr, (self.width, self.height))

            # BGR → RGB for av
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
            video_frame.pts      = pts
            video_frame.time_base = time_base
            return video_frame


# ══════════════════════════════════════════════════════════════════════════
#  WEBRTC PEER CLASS
# ══════════════════════════════════════════════════════════════════════════

class WebRTCPeer:
    """
    Manages the WebRTC connection from the student machine to the proctor.

    The signaling flow:
      1. Connect to server via Socket.IO
      2. Emit "student-join" with token
      3. Wait for "offer" from proctor
      4. Create RTCPeerConnection, set remote description (offer)
      5. Create answer, set local description
      6. Send answer via "answer" event
      7. Exchange ICE candidates bidirectionally
      8. WebRTC video stream flows peer-to-peer (UDP)
    """

    STUN_SERVERS = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]

    def __init__(self, server_url: str, token: str, hub=None,
                 student_id: str = "student", yolo_interval: int = 30):
        """
        Args:
            server_url:    Full URL of the signaling server (e.g. https://xxx.ngrok.io)
            token:         Per-student token from server_webrtc.register_student()
            hub:           CameraHub instance (from main.py) — provides get_frame()
            student_id:    Used in signaling messages
            yolo_interval: Send a JPEG for YOLO every N frames (0 = disabled)
        """
        self.server_url    = server_url.rstrip("/")
        self.token         = token
        self.hub           = hub
        self.student_id    = student_id
        self.yolo_interval = yolo_interval

        self._sio          = None
        self._pc           = None
        self._loop         = None
        self._thread       = None
        self._running      = False
        self._frame_count  = 0
        self._pending_ice  = []   # ICE candidates received before PC was ready

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self):
        """Start the WebRTC peer in a background thread."""
        if not _SOCKETIO_AVAILABLE:
            print("[WebRTCPeer] ERROR: pip install python-socketio[client] aiohttp")
            return
        if not _AIORTC_AVAILABLE:
            print("[WebRTCPeer] ERROR: pip install aiortc av")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[WebRTCPeer] Starting for student: {self.student_id}")

    def stop(self):
        """Stop the WebRTC peer gracefully."""
        self._running = False
        if self._loop and self._pc:
            asyncio.run_coroutine_threadsafe(self._pc.close(), self._loop)
        if self._sio:
            try:
                self._sio.disconnect()
            except Exception:
                pass
        print(f"[WebRTCPeer] Stopped for: {self.student_id}")

    def send_violation(self, violation_type: str, detail: str = ""):
        """Push a violation event to the proctor via Socket.IO."""
        if self._sio and self._sio.connected:
            self._sio.emit("violation", {
                "student_id": self.student_id,
                "type":       violation_type,
                "detail":     detail,
                "timestamp":  time.strftime("%H:%M:%S"),
            })

    def send_stats(self, stats: dict):
        """Push live stats (face count, gaze, strikes) to the proctor."""
        if self._sio and self._sio.connected:
            stats["student_id"] = self.student_id
            self._sio.emit("stats-update", stats)

    # ── Internal ─────────────────────────────────────────────────────────

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            print(f"[WebRTCPeer] Loop error: {e}")
        finally:
            self._loop.close()

    async def _main(self):
        # ── Socket.IO client ────────────────────────────────────────────
        self._sio = _sio_lib.AsyncClient(
            ssl_verify=False,
            reconnection=True,
            reconnection_attempts=10,
            logger=False,
        )

        @self._sio.event
        async def connect():
            print(f"[WebRTCPeer] Connected to signaling server")
            await self._sio.emit("student-join", {
                "token":      self.token,
                "student_id": self.student_id,
            })

        @self._sio.event
        async def disconnect():
            print("[WebRTCPeer] Disconnected from signaling server")

        @self._sio.on("joined")
        async def on_joined(data):
            print(f"[WebRTCPeer] Joined as student: {data.get('student_id')}")

        @self._sio.on("offer")
        async def on_offer(data):
            print("[WebRTCPeer] Received SDP offer from proctor")
            await self._handle_offer(data)

        @self._sio.on("ice-candidate")
        async def on_ice(data):
            await self._handle_ice_candidate(data)

        @self._sio.on("terminated")
        async def on_terminated(data):
            print("[WebRTCPeer] Exam terminated by proctor")
            if self.hub:
                self.hub.strike_count = getattr(self.hub, "MAX_STRIKES", 5)

        await self._sio.connect(
            self.server_url,
            transports=["websocket"],
            headers={"ngrok-skip-browser-warning": "true"},
        )

        # Keep alive + send periodic stats
        while self._running and self._sio.connected:
            if self.hub and self._sio.connected:
                self.send_stats({
                    "face_count":     getattr(self.hub, "face_count", 0),
                    "gaze_dir":       getattr(self.hub, "gaze_dir", ""),
                    "strike_count":   getattr(self.hub, "strike_count", 0),
                    "phone_detected": getattr(self.hub, "phone_detected", False),
                })
            await asyncio.sleep(2)

    async def _handle_offer(self, data: dict):
        """Create RTCPeerConnection, add video track, answer the offer."""
        config = RTCPeerConnection.Configuration(
            iceServers=[RTCPeerConnection.RTCIceServer(**s) for s in self.STUN_SERVERS]
        ) if hasattr(RTCPeerConnection, "Configuration") else None

        self._pc = RTCPeerConnection() if config is None else RTCPeerConnection(config)

        # Add camera video track
        video_track = CameraHubTrack(hub=self.hub)
        self._pc.addTrack(video_track)

        # ICE candidate handler
        @self._pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate and self._sio.connected:
                await self._sio.emit("ice-candidate", {
                    "student_id": self.student_id,
                    "from":       "student",
                    "candidate":  {
                        "candidate":     candidate.to_sdp(),
                        "sdpMid":        candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    }
                })

        @self._pc.on("connectionstatechange")
        async def on_state():
            state = self._pc.connectionState
            print(f"[WebRTCPeer] Connection state: {state}")
            if state == "connected":
                print("[WebRTCPeer] WebRTC stream LIVE")
                # Start YOLO frame-send loop
                if self.yolo_interval > 0:
                    asyncio.ensure_future(self._yolo_loop())

        # Set remote description (the offer)
        offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
        await self._pc.setRemoteDescription(offer)

        # Flush any pending ICE candidates
        for ice_data in self._pending_ice:
            await self._apply_ice(ice_data)
        self._pending_ice.clear()

        # Create and set local description (answer)
        answer = await self._pc.createAnswer()
        await self._pc.setLocalDescription(answer)

        # Send answer to proctor via signaling
        await self._sio.emit("answer", {
            "student_id": self.student_id,
            "sdp":        self._pc.localDescription.sdp,
            "type":       self._pc.localDescription.type,
        })
        print("[WebRTCPeer] SDP answer sent to proctor")

    async def _handle_ice_candidate(self, data: dict):
        if data.get("from") == "student":
            return  # ignore our own candidates echoed back
        if self._pc is None:
            self._pending_ice.append(data)
            return
        await self._apply_ice(data)

    async def _apply_ice(self, data: dict):
        try:
            c = data.get("candidate", {})
            candidate_str = c.get("candidate", "")
            if not candidate_str:
                return
            # Parse "candidate:..." SDP line
            candidate = RTCIceCandidate.from_sdp(
                sdpMid=c.get("sdpMid", "0"),
                sdpMLineIndex=c.get("sdpMLineIndex", 0),
                candidate=candidate_str,
            )
            await self._pc.addIceCandidate(candidate)
        except Exception as e:
            logger.debug(f"ICE candidate error: {e}")

    async def _yolo_loop(self):
        """Periodically send a JPEG frame to the server for YOLO detection."""
        frame_n = 0
        while self._running and self._pc and \
              self._pc.connectionState == "connected":
            frame_n += 1
            if frame_n % self.yolo_interval == 0 and self.hub:
                frame = self.hub.get_frame()
                if frame is not None:
                    try:
                        _, buf = cv2.imencode(".jpg", frame,
                                             [cv2.IMWRITE_JPEG_QUALITY, 60])
                        b64 = base64.b64encode(buf.tobytes()).decode()
                        await self._sio.emit("yolo-frame", {
                            "student_id": self.student_id,
                            "frame":      b64,
                        })
                    except Exception:
                        pass
            await asyncio.sleep(1 / 5)  # 5fps YOLO max


# ══════════════════════════════════════════════════════════════════════════
#  CLI  —  standalone test
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ExamShield WebRTC Peer (student side)")
    parser.add_argument("--server", required=True, help="Signaling server URL")
    parser.add_argument("--token",  required=True, help="Student token")
    parser.add_argument("--id",     default="test_student", help="Student ID")
    args = parser.parse_args()

    if not _AIORTC_AVAILABLE:
        print("ERROR: aiortc not installed. Run: pip install aiortc av")
        exit(1)
    if not _SOCKETIO_AVAILABLE:
        print("ERROR: python-socketio not installed. Run: pip install 'python-socketio[client]' aiohttp")
        exit(1)

    print(f"Starting WebRTC peer for {args.id} → {args.server}")
    peer = WebRTCPeer(
        server_url=args.server,
        token=args.token,
        student_id=args.id,
        hub=None,  # No hub in standalone test — sends blank frames
        yolo_interval=30,
    )
    peer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        peer.stop()
        print("Stopped.")
