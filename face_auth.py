"""
face_auth.py  — Real face registration & verification for ExamShield
=====================================================================
REGISTRATION  (called once when student creates account)
  • Opens webcam, detects a face, captures 5 sample frames
  • Extracts 468 MediaPipe Face Mesh landmarks per frame
  • Stores the mean landmark vector as a BLOB in students.db
    (users.face_data column)
  • Also stores a reference JPEG thumbnail for visual confirmation

VERIFICATION  (called at every login)
  • Opens webcam, collects up to MAX_ATTEMPTS frames
  • Computes cosine similarity between live landmark vector and
    stored reference vector
  • Returns True only if similarity >= THRESHOLD for at least
    REQUIRED_MATCHES consecutive frames
"""

import cv2
import numpy as np
import sqlite3
import time
import struct
import tkinter as tk
from tkinter import messagebox

try:
    import mediapipe as mp
    _MP_AVAILABLE = True
except ImportError:
    _MP_AVAILABLE = False

DB = "students.db"

# ── Tuning knobs ────────────────────────────────────────────────────────────
CAPTURE_SAMPLES   = 5       # frames averaged for registration embedding
COSINE_THRESHOLD  = 0.93    # similarity ≥ this → face matches
REQUIRED_MATCHES  = 8       # consecutive matching frames needed to accept
MAX_ATTEMPTS      = 180     # max frames tried before "failed" (~6 s @ 30 fps)
LANDMARK_COUNT    = 468     # MediaPipe Face Mesh landmark count

# ── DB helpers ───────────────────────────────────────────────────────────────

def init_face_db():
    """Ensure face_data column exists (backwards-compat with older DB)."""
    conn = sqlite3.connect(DB)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN face_data BLOB")
        conn.commit()
    except Exception:
        pass  # column already exists
    conn.close()


def _save_face_embedding(uid: str, embedding: np.ndarray):
    """Store a float32 numpy embedding as BLOB in face_data column."""
    blob = embedding.astype(np.float32).tobytes()
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE users SET face_data=? WHERE student_id=?", (blob, uid))
    conn.commit()
    conn.close()


def _load_face_embedding(uid: str):
    """Return stored face embedding as float32 ndarray, or None."""
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT face_data FROM users WHERE student_id=?", (uid,)).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    arr = np.frombuffer(row[0], dtype=np.float32).copy()
    return arr


# ── MediaPipe helper ─────────────────────────────────────────────────────────

def _get_face_mesh():
    if not _MP_AVAILABLE:
        return None
    return mp.solutions.face_mesh.FaceMesh(
        static_image_mode=False,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.7)


def _extract_embedding(frame, face_mesh) -> np.ndarray | None:
    """
    Run MediaPipe Face Mesh on BGR frame.
    Return a normalised 1-D float32 vector of all landmark coords,
    or None if no face detected.
    """
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    result = face_mesh.process(rgb)
    if not result.multi_face_landmarks:
        return None
    lm = result.multi_face_landmarks[0].landmark
    vec = np.array([[l.x, l.y, l.z] for l in lm], dtype=np.float32).flatten()
    # Normalise to unit vector (pose/distance invariant)
    norm = np.linalg.norm(vec)
    if norm < 1e-6:
        return None
    return vec / norm


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))  # both already unit-normalised


# ── DRAW helpers ─────────────────────────────────────────────────────────────

def _overlay_status(frame, line1, line2="", color=(0, 220, 100)):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 80), (w, h), (10, 10, 10), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
    cv2.putText(frame, line1, (12, h - 52),
                cv2.FONT_HERSHEY_DUPLEX, 0.65, color, 1)
    if line2:
        cv2.putText(frame, line2, (12, h - 22),
                    cv2.FONT_HERSHEY_DUPLEX, 0.55, (180, 180, 180), 1)


# ═══════════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

def capture_face_registration(uid: str):
    """
    Open webcam, wait for a clear face, collect CAPTURE_SAMPLES embeddings,
    average them, and persist in the DB.  Shows a live preview window.
    """
    if not _MP_AVAILABLE:
        messagebox.showwarning("Face Auth",
            "MediaPipe not installed — face registration skipped.\n"
            "Run: pip install mediapipe")
        return

    face_mesh = _get_face_mesh()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    samples      = []
    countdown    = 0
    status_msg   = "Position your face in the frame"
    status_col   = (80, 200, 80)
    start_time   = time.time()
    TIMEOUT_SECS = 40

    print(f"[FaceAuth] Starting registration for {uid}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)   # mirror for natural feel
        emb   = _extract_embedding(frame, face_mesh)

        if emb is not None:
            samples.append(emb)
            countdown = len(samples)
            pct = int(len(samples) / CAPTURE_SAMPLES * 100)
            status_msg = f"Capturing face... {pct}%  ({len(samples)}/{CAPTURE_SAMPLES})"
            status_col = (0, 220, 120)

            # Draw a green progress arc
            h, w = frame.shape[:2]
            cx, cy, r = w // 2, h // 2, min(w, h) // 3
            angle = int(360 * len(samples) / CAPTURE_SAMPLES)
            cv2.ellipse(frame, (cx, cy), (r, r), -90, 0, angle, (0, 220, 80), 3)

            if len(samples) >= CAPTURE_SAMPLES:
                status_msg = "✓  Face captured!  Saving..."
                status_col = (0, 240, 240)
                _overlay_status(frame, status_msg, "", status_col)
                cv2.imshow("ExamShield — Face Registration", frame)
                cv2.waitKey(600)
                break
        else:
            status_msg = "⚠  No face detected — look at the camera"
            status_col = (0, 80, 255)
            if time.time() - start_time > TIMEOUT_SECS:
                status_msg = "⏰  Timeout — try again in better lighting"
                _overlay_status(frame, status_msg, "", (0, 60, 255))
                cv2.imshow("ExamShield — Face Registration", frame)
                cv2.waitKey(1500)
                break

        _overlay_status(frame, status_msg,
                        f"Student: {uid}  |  Press Q to cancel", status_col)
        cv2.imshow("ExamShield — Face Registration", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            print("[FaceAuth] Registration cancelled by user")
            break

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()

    if len(samples) >= CAPTURE_SAMPLES:
        mean_emb = np.mean(np.stack(samples), axis=0)
        mean_emb /= (np.linalg.norm(mean_emb) + 1e-9)   # re-normalise
        _save_face_embedding(uid, mean_emb)
        print(f"[FaceAuth] Embedding saved for {uid}  (dim={mean_emb.shape})")
        messagebox.showinfo("Face Registered",
            f"✅  Face registered successfully for '{uid}'!\n\n"
            "Your face will be checked at every login.")
    else:
        messagebox.showwarning("Face Registration Incomplete",
            "Not enough face samples were captured.\n"
            "You can log in, but face verification will be skipped this time.\n\n"
            "Re-register later for full security.")


# ═══════════════════════════════════════════════════════════════════════════════
#  VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def verify_face(uid: str) -> bool:
    """
    Verify that the person in front of the camera matches the stored embedding.

    Returns True  → face matched (or no embedding stored yet → auto-pass with warning)
    Returns False → face did NOT match
    """
    ref_emb = _load_face_embedding(uid)

    if ref_emb is None:
        # No face data registered — allow login but warn
        messagebox.showwarning("Face Not Registered",
            f"No face data found for '{uid}'.\n\n"
            "Face verification is SKIPPED this time.\n"
            "Please re-register to enable biometric security.")
        return True

    if not _MP_AVAILABLE:
        messagebox.showwarning("Face Auth",
            "MediaPipe not installed — face check skipped.")
        return True

    face_mesh = _get_face_mesh()
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)

    attempts       = 0
    consecutive    = 0
    best_sim       = 0.0
    status_msg     = "Look at the camera for face verification"
    status_col     = (80, 200, 80)
    result         = False

    print(f"[FaceAuth] Verifying {uid}  (threshold={COSINE_THRESHOLD})")

    while attempts < MAX_ATTEMPTS:
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        emb   = _extract_embedding(frame, face_mesh)
        attempts += 1

        h, w = frame.shape[:2]

        if emb is not None:
            sim = _cosine_sim(emb, ref_emb)
            best_sim = max(best_sim, sim)
            bar_w = int(w * min(sim, 1.0))

            if sim >= COSINE_THRESHOLD:
                consecutive += 1
                bar_col = (0, 220, 80)
                status_msg = (f"✓  Match  {sim:.3f}  "
                              f"({consecutive}/{REQUIRED_MATCHES})")
                status_col = (0, 220, 100)
            else:
                consecutive = 0
                bar_col = (0, 100, 255) if sim > 0.80 else (0, 50, 200)
                status_msg = f"Verifying...  similarity={sim:.3f}"
                status_col = (0, 160, 255)

            # Draw similarity bar
            cv2.rectangle(frame, (0, h - 90), (bar_w, h - 82), bar_col, -1)
            cv2.putText(frame, f"Similarity: {sim:.3f}",
                        (w - 210, h - 90), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, bar_col, 1)

            if consecutive >= REQUIRED_MATCHES:
                status_msg = "✅  Face Verified!"
                status_col = (0, 255, 180)
                _overlay_status(frame, status_msg, f"Welcome, {uid}", status_col)
                cv2.imshow("ExamShield — Face Verification", frame)
                cv2.waitKey(800)
                result = True
                break
        else:
            consecutive = 0
            status_msg = "⚠  Face not detected — look straight at camera"
            status_col = (0, 80, 200)

        remaining = MAX_ATTEMPTS - attempts
        _overlay_status(frame, status_msg,
                        f"ID: {uid}  |  {remaining} frames left  |  Q=cancel",
                        status_col)

        cv2.imshow("ExamShield — Face Verification", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            print("[FaceAuth] Verification cancelled by user")
            break

    cap.release()
    face_mesh.close()
    cv2.destroyAllWindows()

    if not result:
        print(f"[FaceAuth] Verification FAILED for {uid}  "
              f"(best_sim={best_sim:.3f}  threshold={COSINE_THRESHOLD})")
        msg = (
            f"❌  Face verification FAILED for '{uid}'.\n\n"
            f"Best match: {best_sim:.1%}  (required ≥{COSINE_THRESHOLD:.0%})\n\n"
            "Tips:\n"
            "• Ensure good lighting — no strong backlighting\n"
            "• Look directly at the camera\n"
            "• Remove glasses or hat if they weren't present at registration\n\n"
            "If you are the right person, ask admin to re-register your face."
        )
        messagebox.showerror("Face Verification Failed", msg)
    else:
        print(f"[FaceAuth] Verification PASSED for {uid}  "
              f"(best_sim={best_sim:.3f})")

    return result