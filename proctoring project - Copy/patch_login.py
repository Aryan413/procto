"""
Run this once: python patch_login.py
It patches login.py to add face verification.
"""
import re, os

path = 'login.py'
content = open(path).read()

# ── 1. Add import ──────────────────────────────────────────────────────────
if 'face_auth' not in content:
    content = content.replace(
        'from proctor import start_proctoring',
        'from proctor import start_proctoring\nfrom face_auth import init_face_db, capture_face_registration, verify_face'
    )
    print("✓ Added face_auth import")
else:
    print("- face_auth import already present")

# ── 2. Add init_face_db() in __main__ ─────────────────────────────────────
if 'init_face_db' not in content:
    content = content.replace(
        'init_db()\n    app = LoginApp()',
        'init_db()\n    init_face_db()\n    app = LoginApp()'
    )
    print("✓ Added init_face_db() call")
else:
    print("- init_face_db already present")

# ── 3. Patch register_student ─────────────────────────────────────────────
if 'capture_face_registration' not in content:
    # Find the register method and replace it entirely using regex
    old_pat = r'(    def register_student\(self\):.*?except sqlite3\.IntegrityError:.*?messagebox\.showerror\("Error", "This Student ID is already registered\."\))'
    new_reg = '''    def register_student(self):
        student_id = self.entry_username.get().strip()
        password = self.entry_password.get().strip()
        if not student_id or not password:
            messagebox.showerror("Error", "Please fill in both fields to register.")
            return
        try:
            conn = sqlite3.connect('students.db')
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (student_id, password) VALUES (?, ?)",
                           (student_id, password))
            conn.commit()
            conn.close()
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "This Student ID is already registered.")
            return
        messagebox.showinfo("Face Registration",
            f"Account created for '{student_id}'!\\n\\nNow register your face.\\nLook at the camera and press OK.")
        self.root.withdraw()
        ok = capture_face_registration(student_id)
        self.root.deiconify()
        if ok:
            messagebox.showinfo("Success", "Face registered successfully! You can now log in.")
        else:
            messagebox.showwarning("Face Not Captured",
                "Face registration failed or timed out.\\nYou can still log in but face check will be skipped.")'''
    result = re.sub(old_pat, new_reg, content, flags=re.DOTALL)
    if result != content:
        content = result
        print("✓ Patched register_student")
    else:
        print("✗ Could not patch register_student - pattern not found")
else:
    print("- register_student already patched")

# ── 4. Patch attempt_login ─────────────────────────────────────────────────
if 'verify_face' not in content:
    old_pat2 = r'(    def attempt_login\(self\):.*?)(        if user:.*?start_proctoring\(student_id\).*?else:.*?messagebox\.showerror\("Login Failed".*?\))'
    new_login_body = '''        if not user:
            messagebox.showerror("Login Failed",
                "Invalid Student ID or Password. Please register if you are a new user.")
            return
        # ── Face verification ──────────────────────────────────────────
        messagebox.showinfo("Face Verification",
            f"Password accepted!\\n\\nNow verifying your identity.\\nLook at the camera and press OK.")
        self.root.withdraw()
        verified = verify_face(student_id)
        self.root.deiconify()
        if not verified:
            messagebox.showerror("Access Denied",
                "Face verification failed!\\nYour face does not match the registered student.\\nAccess denied.")
            return
        messagebox.showinfo("Verified", f"Identity confirmed!\\nWelcome, {student_id}.\\nThe exam will now begin.")
        self.animating = False
        self.root.destroy()
        start_proctoring(student_id)'''

    result2 = re.sub(old_pat2, lambda m: m.group(1) + new_login_body, content, flags=re.DOTALL)
    if result2 != content:
        content = result2
        print("✓ Patched attempt_login")
    else:
        # Simpler direct replacement fallback
        old_simple = '''        if user:
            messagebox.showinfo("Success", f"Welcome, {student_id}. The exam will now begin.")
            self.animating = False
            self.root.destroy()
            start_proctoring(student_id)
        else:
            messagebox.showerror("Login Failed",
                "Invalid Student ID or Password. Please register if you are a new user.")'''
        new_simple = '''        if not user:
            messagebox.showerror("Login Failed",
                "Invalid Student ID or Password. Please register if you are a new user.")
            return
        messagebox.showinfo("Face Verification",
            f"Password accepted!\\n\\nNow verifying your identity.\\nLook at the camera and press OK.")
        self.root.withdraw()
        verified = verify_face(student_id)
        self.root.deiconify()
        if not verified:
            messagebox.showerror("Access Denied",
                "Face verification failed! Access denied.")
            return
        messagebox.showinfo("Verified", f"Identity confirmed! Welcome, {student_id}.")
        self.animating = False
        self.root.destroy()
        start_proctoring(student_id)'''
        if old_simple in content:
            content = content.replace(old_simple, new_simple)
            print("✓ Patched attempt_login (fallback)")
        else:
            print("✗ Could not patch attempt_login")
else:
    print("- attempt_login already patched")

# ── Save ───────────────────────────────────────────────────────────────────
open(path, 'w').write(content)
print("\nDone! Run: python login.py")