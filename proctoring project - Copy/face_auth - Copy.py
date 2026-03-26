"""
face_auth.py - Face registration & verification for ExamShield
Uses face_recognition library for accurate matching.
MediaPipe loads lazily so login UI opens instantly with no lag.
"""
import cv2
import numpy as np
import sqlite3
import time

# ── Lazy imports - loaded only when camera is opened ──────────────────────
_fr = None
_mp_detector = None

def _get_fr():
    global _fr
    if _fr is None:
        try:
            import face_recognition as _face_rec
            _fr = _face_rec
        except ImportError:
            _fr = False
    return _fr if _fr is not False else None

def _get_detector():
    global _mp_detector
    if _mp_detector is None:
        import mediapipe as mp
        _mp_detector = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.6)
    return _mp_detector


# ── DB ─────────────────────────────────────────────────────────────────────

def init_face_db():
    conn = sqlite3.connect('students.db')
    try:
        conn.execute("ALTER TABLE users ADD COLUMN face_data BLOB")
        conn.commit()
    except:
        pass
    conn.close()

def _save_face(student_id, enc):
    conn = sqlite3.connect('students.db')
    conn.execute("UPDATE users SET face_data=? WHERE student_id=?",
                 (enc.tobytes(), student_id))
    conn.commit()
    conn.close()

def _load_face(student_id):
    conn = sqlite3.connect('students.db')
    row = conn.execute(
        "SELECT face_data FROM users WHERE student_id=?", (student_id,)
    ).fetchone()
    conn.close()
    if row and row[0]:
        arr = np.frombuffer(row[0], dtype=np.float64)
        if arr.shape == (128,):   # valid face_recognition encoding
            return arr
    return None


# ── FALLBACK: mediapipe pixel vector (if face_recognition not installed) ───

def _mp_face_crop(frame):
    det = _get_detector()
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    res = det.process(rgb)
    if not res.detections:
        return None
    d = res.detections[0].location_data.relative_bounding_box
    h, w = frame.shape[:2]
    x1 = max(int(d.xmin * w) - 20, 0)
    y1 = max(int(d.ymin * h) - 20, 0)
    x2 = min(int((d.xmin + d.width)  * w) + 20, w)
    y2 = min(int((d.ymin + d.height) * h) + 20, h)
    crop = frame[y1:y2, x1:x2]
    return cv2.resize(crop, (128, 128)) if crop.size > 0 else None

def _pixel_vec(crop):
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float64).flatten() / 255.0

def _cosine_sim(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


# ── REGISTRATION ───────────────────────────────────────────────────────────

def capture_face_registration(student_id):
    fr = _get_fr()
    cap = cv2.VideoCapture(0)
    samples = []
    start = time.time()
    NEEDED = 8
    TIMEOUT = 30

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        if fr:
            # Use face_recognition (accurate 128-d encoding)
            locs = fr.face_locations(rgb, model="hog")
            if locs:
                encs = fr.face_encodings(rgb, locs)
                if encs:
                    samples.append(encs[0])
                top, right, bottom, left = locs[0]
                col = (0, 255, 80) if len(samples) > 0 else (0, 200, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), col, 2)
        else:
            # Fallback: mediapipe pixel crop
            crop = _mp_face_crop(frame)
            if crop is not None:
                samples.append(_pixel_vec(crop))

        # HUD
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 75), (20, 20, 20), -1)
        cv2.putText(frame, "FACE REGISTRATION — ExamShield",
                    (10, 28), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 220, 255), 1)
        cv2.putText(frame, f"Samples: {len(samples)}/{NEEDED}  |  Look straight at camera",
                    (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)
        prog = int(frame.shape[1] * min(len(samples), NEEDED) / NEEDED)
        cv2.rectangle(frame, (0, 73), (prog, 77), (0, 220, 100), -1)

        if len(samples) >= NEEDED:
            cv2.putText(frame, "REGISTERED!", (frame.shape[1]//2 - 100, frame.shape[0]//2),
                        cv2.FONT_HERSHEY_DUPLEX, 1.3, (0, 255, 80), 2)
            cv2.imshow("ExamShield — Face Registration", frame)
            cv2.waitKey(900)
            break

        if time.time() - start > TIMEOUT:
            break

        cv2.imshow("ExamShield — Face Registration", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cap.release()
    cv2.destroyAllWindows()

    if len(samples) >= NEEDED:
        avg = np.mean(samples, axis=0)
        _save_face(student_id, avg)
        print(f"[FaceAuth] Registered {student_id} ({len(samples)} samples, fr={'yes' if fr else 'fallback'})")
        return True
    return False


# ── VERIFICATION ───────────────────────────────────────────────────────────

# Thresholds
FR_TOLERANCE   = 0.42   # face_recognition distance - lower = stricter (0.42 very strict)
PIXEL_SIM_THRESH = 0.94  # cosine similarity for fallback mode

def verify_face(student_id):
    stored = _load_face(student_id)
    if stored is None:
        print(f"[FaceAuth] No face stored for {student_id} — skipping check")
        return True   # no face registered, allow login

    fr = _get_fr()
    cap = cv2.VideoCapture(0)
    results = []
    start = time.time()
    NEEDED = 6
    TIMEOUT = 20
    distance = 1.0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        match = None

        if fr and stored.shape == (128,):
            # face_recognition mode
            locs = fr.face_locations(rgb, model="hog")
            if locs:
                encs = fr.face_encodings(rgb, locs)
                if encs:
                    distance = float(fr.face_distance([stored], encs[0])[0])
                    match = distance <= FR_TOLERANCE
                    results.append(match)
                top, right, bottom, left = locs[0]
                col = (0, 255, 80) if match else (0, 60, 255)
                cv2.rectangle(frame, (left, top), (right, bottom), col, 2)
                label = f"{'MATCH' if match else 'NO MATCH'} {distance:.2f}"
                cv2.putText(frame, label, (left, top - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        else:
            # Fallback pixel mode
            crop = _mp_face_crop(frame)
            if crop is not None:
                vec = _pixel_vec(crop)
                if stored.shape == vec.shape:
                    sim = _cosine_sim(stored, vec)
                    distance = 1.0 - sim
                    match = sim >= PIXEL_SIM_THRESH
                    results.append(match)

        # HUD
        verified = sum(results)
        total    = len(results)
        pct      = (verified / total * 100) if total > 0 else 0
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 75), (20, 20, 20), -1)
        cv2.putText(frame, "FACE VERIFICATION — ExamShield",
                    (10, 28), cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 220, 255), 1)
        cv2.putText(frame,
                    f"Scans: {total}/{NEEDED}  Matches: {verified}  Score: {pct:.0f}%  Dist: {distance:.3f}",
                    (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 200), 1)
        bar_col = (0, 220, 80) if pct >= 60 else (0, 60, 255)
        prog = int(frame.shape[1] * min(total, NEEDED) / NEEDED)
        cv2.rectangle(frame, (0, 73), (prog, 77), bar_col, -1)

        cv2.imshow("ExamShield — Face Verification", frame)

        if total >= NEEDED:
            cv2.waitKey(500)
            break
        if time.time() - start > TIMEOUT:
            break
        if cv2.waitKey(1) & 0xFF == 27:
            break

    cap.release()
    cv2.destroyAllWindows()

    if not results:
        print(f"[FaceAuth] No face detected for {student_id} — denying")
        return False

    verified = sum(results)
    final = (verified / len(results)) >= 0.60   # 60% of scans must match
    print(f"[FaceAuth] {student_id}: {verified}/{len(results)} -> {'PASS' if final else 'FAIL'}")
    return final