import cv2
import mediapipe as mp
import numpy as np
from ultralytics import YOLO
import time
import csv
from collections import deque

# ── Gaze tracking ──────────────────────────────────────────────────────────
from gaze_tracking import GazeTracking


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
WARNING_DURATION = 4.0   # seconds of continuous violation before a strike
MAX_WARNINGS     = 5     # exam terminated after this many strikes

# How many consecutive "looking away" frames before we start the strike timer
GAZE_AWAY_FRAME_THRESHOLD = 15   # ~0.5s at 30fps

# Gaze directions considered suspicious
GAZE_SUSPICIOUS = {"left", "right", "up", "down"}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def draw_hud(frame, student_id, face_count, warning_count, gaze_dir,
             gaze_away_streak, calibrated):
    """Semi-transparent HUD bar at the top of the frame."""
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 70), (15, 15, 15), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    # Student ID
    cv2.putText(frame, f"ID: {student_id}", (10, 22),
                cv2.FONT_HERSHEY_DUPLEX, 0.6, (180, 180, 180), 1)

    # Face count
    face_col = (80, 220, 80) if face_count == 1 else (0, 80, 255)
    cv2.putText(frame, f"Faces: {face_count}", (10, 52),
                cv2.FONT_HERSHEY_DUPLEX, 0.6, face_col, 1)

    # Gaze direction
    gaze_col  = (80, 220, 80) if gaze_dir == "center" else (0, 180, 255)
    calib_tag = "" if calibrated else " (calibrating...)"
    cx = w // 2 - 120
    cv2.putText(frame, f"Gaze: {gaze_dir}{calib_tag}", (cx, 22),
                cv2.FONT_HERSHEY_DUPLEX, 0.6, gaze_col, 1)

    # Gaze-away progress bar (centre strip)
    if gaze_away_streak > 0:
        ratio   = min(gaze_away_streak / GAZE_AWAY_FRAME_THRESHOLD, 1.0)
        r_int   = int(220 * ratio)
        g_int   = int(220 * (1 - ratio))
        bar_col = (0, g_int, r_int + 35)
        bx, by, bw, bh = w // 2 - 80, 38, 160, 8
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (50, 50, 50), -1)
        cv2.rectangle(frame, (bx, by), (bx + int(bw * ratio), by + bh), bar_col, -1)

    # Strike counter top-right
    strike_col = (0, 220, 80) if warning_count == 0 else (
        (0, 160, 255) if warning_count < MAX_WARNINGS - 1 else (0, 50, 255))
    cv2.putText(frame, f"Strikes: {warning_count}/{MAX_WARNINGS}",
                (w - 200, 22), cv2.FONT_HERSHEY_DUPLEX, 0.6, strike_col, 1)


def draw_progress_bar(frame, label, elapsed, duration, y_pos, color=(0, 0, 255)):
    """Labelled progress bar counting down to a strike."""
    remaining = max(0.0, duration - elapsed)
    ratio     = min(elapsed / duration, 1.0)
    bx, bw, bh = 50, 300, 14
    cv2.rectangle(frame, (bx, y_pos), (bx + bw, y_pos + bh), (40, 40, 40), -1)
    cv2.rectangle(frame, (bx, y_pos), (bx + int(bw * ratio), y_pos + bh), color, -1)
    cv2.putText(frame, f"{label} — strike in {remaining:.1f}s",
                (bx, y_pos - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)


def draw_calibration_overlay(frame):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - 50), (w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
    cv2.putText(frame, "Gaze calibrating — please look directly at the camera",
                (20, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 1)


def termination_screen(frame, reason="Maximum strikes reached."):
    frame[:] = (0, 0, 200)
    h, w = frame.shape[:2]
    cv2.putText(frame, "EXAM TERMINATED",
                (w // 2 - 220, h // 2 - 30),
                cv2.FONT_HERSHEY_DUPLEX, 1.5, (255, 255, 255), 3)
    cv2.putText(frame, reason,
                (w // 2 - int(len(reason) * 5), h // 2 + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 220, 220), 2)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def start_proctoring(student_id):
    print("[ProctorAI] Loading models...")
    yolo_model   = YOLO("yolov8n.pt")
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh    = mp_face_mesh.FaceMesh(max_num_faces=4,
                                         min_detection_confidence=0.92,
                                         min_tracking_confidence=0.92)
    gaze = GazeTracking()

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # Create resizable window matching login page default size
    cv2.namedWindow("ProctorAI", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("ProctorAI", 520, 600)
    print("[ProctorAI] Camera ready.")

    # CSV log
    log_file = f"{student_id}_exam_log.csv"
    with open(log_file, 'w', newline='') as f:
        csv.writer(f).writerow(["Timestamp", "Event", "Detail"])

    def log_event(event, detail=""):
        with open(log_file, 'a', newline='') as f:
            csv.writer(f).writerow([time.strftime("%H:%M:%S"), event, detail])

    # Strike timers
    warning_count         = 0
    phone_start_time      = None
    multi_face_start_time = None

    # Gaze state
    gaze_away_streak  = 0
    gaze_strike_timer = None

    # YOLO throttle
    YOLO_EVERY_N    = 3
    frame_counter   = 0
    last_yolo_boxes = []

    log_event("EXAM_START", student_id)
    print(f"[✅ EXAM START] Student: {student_id} | Max strikes: {MAX_WARNINGS} | Gaze timer: {WARNING_DURATION}s")
    print("-" * 60)

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_counter += 1

        # ── MediaPipe face count ──────────────────────────────────────────
        rgb          = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mesh_results = face_mesh.process(rgb)
        # Filter out small detections (posters/photos/reflections)
        # Only count faces whose bounding box is at least 8% of frame width
        face_count = 0
        h_fr, w_fr = frame.shape[:2]
        MIN_FACE_RATIO = 0.14   # face must be at least 14% of frame width
        if mesh_results.multi_face_landmarks:
            for fl in mesh_results.multi_face_landmarks:
                xs = [lm.x for lm in fl.landmark]
                face_w_ratio = max(xs) - min(xs)
                if face_w_ratio >= MIN_FACE_RATIO:
                    face_count += 1

        # ── Gaze tracking ─────────────────────────────────────────────────
        gaze.refresh(frame)
        gaze_dir   = gaze.direction()
        calibrated = gaze.calibration.is_complete()

        # Draw pupil crosshairs + live debug numbers
        if gaze.pupils_located:
            for coords in [gaze.pupil_left_coords(), gaze.pupil_right_coords()]:
                if coords:
                    cx, cy = coords
                    cv2.line(frame,   (cx - 6, cy),   (cx + 6, cy),   (0, 255, 120), 1)
                    cv2.line(frame,   (cx, cy - 6),   (cx, cy + 6),   (0, 255, 120), 1)
                    cv2.circle(frame, (cx, cy), 3, (0, 255, 120), -1)
            # show yaw value so you can see when it triggers
            yaw = float(np.mean(gaze._yaw_buf)) if gaze._yaw_buf else 0
            dw, dh = frame.shape[1], frame.shape[0]
            cv2.putText(frame, f"Yaw:{yaw:.3f}  (trigger at +/-{gaze.HEAD_YAW_THRESH:.2f})",
                        (dw - 350, dh - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (180, 180, 0), 1)

        # ── Gaze-away strike logic ────────────────────────────────────────
        if calibrated and gaze_dir in GAZE_SUSPICIOUS:
            gaze_away_streak += 1

            if gaze_away_streak >= GAZE_AWAY_FRAME_THRESHOLD:
                if gaze_strike_timer is None:
                    gaze_strike_timer = time.time()
                    log_event("GAZE_WARNING", f"Looking {gaze_dir} persistently")
                    print(f"[⚠ GAZE WARNING] Looking {gaze_dir} — strike timer started")

                elapsed = time.time() - gaze_strike_timer
                cv2.putText(frame, f"GAZE AWAY ({gaze_dir.upper()})!",
                            (50, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 80, 255), 2)
                draw_progress_bar(frame, "Gaze away", elapsed, WARNING_DURATION,
                                  145, color=(0, 80, 255))

                if elapsed >= WARNING_DURATION:
                    warning_count += 1
                    log_event(f"STRIKE {warning_count}",
                              f"Gaze away ({gaze_dir}) for {WARNING_DURATION}s")
                    gaze_strike_timer = time.time()
                    print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] GAZE AWAY — looked {gaze_dir} for {WARNING_DURATION}s")
        else:
            if gaze_away_streak >= GAZE_AWAY_FRAME_THRESHOLD and gaze_strike_timer:
                log_event("GAZE_RETURNED", "Student looked back at camera")
            gaze_away_streak  = 0
            gaze_strike_timer = None

        # ── Multiple-face strike logic ────────────────────────────────────
        MULTI_FACE_GRACE = 1.5   # seconds before strike (avoids flicker)
        if face_count > 1:
            cv2.putText(frame, f"MULTIPLE PEOPLE DETECTED ({face_count})",
                        (50, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 2)
            if multi_face_start_time is None:
                multi_face_start_time = time.time()
            elif time.time() - multi_face_start_time >= MULTI_FACE_GRACE:
                warning_count += 1
                log_event(f"STRIKE {warning_count}", f"Multiple faces ({face_count}) for {MULTI_FACE_GRACE}s")
                multi_face_start_time = time.time()
                print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] MULTIPLE FACES DETECTED ({face_count} faces in frame)")
        else:
            multi_face_start_time = None

        # ── Phone detection (YOLO, throttled) ─────────────────────────────
        if frame_counter % YOLO_EVERY_N == 0:
            detections = yolo_model(frame, verbose=False)[0]
            last_yolo_boxes = []
            for box in detections.boxes:
                cls   = int(box.cls[0])
                label = yolo_model.names[cls]
                conf  = float(box.conf[0])
                if label == "cell phone" and conf > 0.45:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    last_yolo_boxes.append((x1, y1, x2, y2, conf))

        phone_detected_this_frame = len(last_yolo_boxes) > 0
        for (x1, y1, x2, y2, conf) in last_yolo_boxes:
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 140, 255), 2)
            cv2.putText(frame, f"Phone {conf:.0%}",
                        (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 140, 255), 2)

        if phone_detected_this_frame:
            # INSTANT STRIKE - no timer needed for phone
            cv2.putText(frame, "PHONE DETECTED — INSTANT STRIKE!",
                        (50, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 255), 2)
            if phone_start_time is None:
                phone_start_time = time.time()
                warning_count += 1
                log_event(f"STRIKE {warning_count}", "Phone detected — instant strike")
                print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] PHONE DETECTED — instant strike")
        else:
            phone_start_time = None

        # ── Overlays ──────────────────────────────────────────────────────
        if not calibrated:
            draw_calibration_overlay(frame)

        draw_hud(frame, student_id, face_count, warning_count,
                 gaze_dir, gaze_away_streak, calibrated)

        # ── Termination ───────────────────────────────────────────────────
        if warning_count >= MAX_WARNINGS:
            log_event("EXAM_TERMINATED", "Max strikes reached")
            termination_screen(frame)
            cv2.imshow("ProctorAI", frame)
            cv2.waitKey(3500)
            break

        cv2.imshow("ProctorAI", frame)
        if cv2.waitKey(1) & 0xFF == 27:
            log_event("EXAM_EXITED", "ESC pressed")
            print(f"[👋 EXAM EXITED] {student_id} pressed ESC | Total strikes: {warning_count}")
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"[ProctorAI] Session ended. Log: {log_file}")


# ══════════════════════════════════════════════════════════════════════════
#  HIDDEN PROCTORING (background thread — no window shown to student)
# ══════════════════════════════════════════════════════════════════════════
def start_proctoring_hidden(student_id):
    """
    Runs proctoring completely silently in background.
    No cv2 window — captures frames, logs violations to CSV only.
    Proctor can view live feed separately via start_proctoring().
    """
    print(f"[ProctorAI-Hidden] Started for {student_id}")
    yolo_model   = YOLO("yolov8n.pt")
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh    = mp_face_mesh.FaceMesh(max_num_faces=4,
                                          min_detection_confidence=0.92,
                                          min_tracking_confidence=0.92)
    gaze = GazeTracking()
    cap  = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    log_file = f"{student_id}_exam_log.csv"
    with open(log_file, 'w', newline='') as f:
        csv.writer(f).writerow(["Timestamp","Event","Detail"])

    def log_event(event, detail=""):
        with open(log_file, 'a', newline='') as f:
            csv.writer(f).writerow([time.strftime("%H:%M:%S"), event, detail])
        print(f"[ProctorAI-Hidden] {event}: {detail}")

    warning_count         = 0
    phone_start_time      = None
    multi_face_start_time = None
    gaze_away_streak      = 0
    gaze_strike_timer     = None
    frame_counter         = 0
    last_yolo_boxes       = []
    YOLO_EVERY_N          = 5   # less frequent in hidden mode
    MULTI_FACE_GRACE      = 1.5

    log_event("EXAM_START", student_id)
    print(f"[✅ EXAM START] Student: {student_id} | Hidden proctoring active")
    print("-" * 60)

    while True:
        ret, frame = cap.read()
        if not ret: break
        frame_counter += 1

        # Face count
        rgb          = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mesh_results = face_mesh.process(rgb)
        face_count   = 0
        if mesh_results.multi_face_landmarks:
            h_fr, w_fr = frame.shape[:2]
            for fl in mesh_results.multi_face_landmarks:
                xs = [lm.x for lm in fl.landmark]
                if max(xs) - min(xs) >= 0.14:
                    face_count += 1

        # Gaze
        gaze.refresh(frame)
        gaze_dir   = gaze.direction()
        calibrated = gaze.calibration.is_complete()

        # Gaze strike
        if calibrated and gaze_dir in GAZE_SUSPICIOUS:
            gaze_away_streak += 1
            if gaze_away_streak >= GAZE_AWAY_FRAME_THRESHOLD:
                if gaze_strike_timer is None:
                    gaze_strike_timer = time.time()
                    log_event("GAZE_WARNING", f"Looking {gaze_dir}")
                    print(f"[⚠ GAZE WARNING] Looking {gaze_dir}")
                if time.time() - gaze_strike_timer >= WARNING_DURATION:
                    warning_count += 1
                    log_event(f"STRIKE {warning_count}", f"Gaze away ({gaze_dir})")
                    print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] GAZE AWAY — {gaze_dir}")
                    gaze_strike_timer = time.time()
        else:
            gaze_away_streak  = 0
            gaze_strike_timer = None

        # Multi-face strike
        if face_count > 1:
            if multi_face_start_time is None:
                multi_face_start_time = time.time()
            elif time.time() - multi_face_start_time >= MULTI_FACE_GRACE:
                warning_count += 1
                log_event(f"STRIKE {warning_count}", f"Multiple faces ({face_count})")
                print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] MULTIPLE FACES ({face_count})")
                multi_face_start_time = time.time()
        else:
            multi_face_start_time = None

        # Phone strike
        if frame_counter % YOLO_EVERY_N == 0:
            detections = yolo_model(frame, verbose=False)[0]
            last_yolo_boxes = [box for box in detections.boxes
                               if yolo_model.names[int(box.cls[0])] == "cell phone"
                               and float(box.conf[0]) > 0.45]

        if last_yolo_boxes:
            if phone_start_time is None:
                phone_start_time = time.time()
                warning_count += 1
                log_event(f"STRIKE {warning_count}", "Phone detected")
                print(f"[🔴 STRIKE {warning_count}/{MAX_WARNINGS}] PHONE DETECTED — instant strike")
        else:
            phone_start_time = None

        # Termination
        if warning_count >= MAX_WARNINGS:
            log_event("EXAM_TERMINATED", "Max strikes reached")
            print(f"[🚫 EXAM TERMINATED] {student_id} reached {MAX_WARNINGS} strikes")
            print("-" * 60)
            break

    cap.release()
    face_mesh.close()
    print(f"[ProctorAI-Hidden] Session ended for {student_id}")