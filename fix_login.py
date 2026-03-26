# fix_login.py - run this: python fix_login.py
import re

content = open('login.py', encoding='utf-8').read()

# 1. Add imports
if 'face_auth' not in content:
    content = content.replace(
        'from proctor import start_proctoring',
        'from proctor import start_proctoring\nfrom face_auth import init_face_db, capture_face_registration, verify_face'
    )
    print("OK: import added")
else:
    print("SKIP: import already there")

# 2. Add init_face_db
if 'init_face_db' not in content:
    content = content.replace(
        'init_db()\n    app = LoginApp()',
        'init_db()\n    init_face_db()\n    app = LoginApp()'
    )
    print("OK: init_face_db added")
else:
    print("SKIP: init_face_db already there")

# 3. Patch register - find and replace the success message + except block
if 'capture_face_registration' not in content:
    # Find the register method's try/except ending
    pattern = r"(cursor\.execute\(\"INSERT INTO users.*?conn\.close\(\))\s*messagebox\.showinfo\([^)]*registered[^)]*\)\s*except sqlite3\.IntegrityError:\s*messagebox\.showerror\([^)]*already registered[^)]*\)"
    replacement = r"""\1
        except sqlite3.IntegrityError:
            messagebox.showerror("Error", "This Student ID is already registered.")
            return
        self.root.withdraw()
        ok = capture_face_registration(student_id)
        self.root.deiconify()
        if ok:
            messagebox.showinfo("Success", "Registered! Face saved. You can now log in.")
        else:
            messagebox.showwarning("Warning", "Account created but face not captured.")"""
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    if new_content != content:
        content = new_content
        print("OK: register patched")
    else:
        print("FAIL: register pattern not matched")
else:
    print("SKIP: register already patched")

# 4. Patch login - replace the if user: block
if 'verify_face' not in content:
    pattern2 = r"if user:\s*messagebox\.showinfo\([^)]*exam will now begin[^)]*\)\s*self\.animating = False\s*self\.root\.destroy\(\)\s*start_proctoring\(student_id\)\s*else:\s*messagebox\.showerror\([^)]*Login Failed[^)]*\)"
    replacement2 = """if not user:
            messagebox.showerror("Login Failed", "Invalid Student ID or Password.")
            return
        self.root.withdraw()
        verified = verify_face(student_id)
        self.root.deiconify()
        if not verified:
            messagebox.showerror("Access Denied", "Face verification failed! Access denied.")
            return
        messagebox.showinfo("Verified", f"Identity confirmed! Welcome, {student_id}.")
        self.animating = False
        self.root.destroy()
        start_proctoring(student_id)"""
    new_content2 = re.sub(pattern2, replacement2, content, flags=re.DOTALL)
    if new_content2 != content:
        content = new_content2
        print("OK: login patched")
    else:
        print("FAIL: login pattern not matched")
else:
    print("SKIP: login already patched")

open('login.py', 'w', encoding='utf-8').write(content)

# Final check
c = open('login.py', encoding='utf-8').read()
print("\n--- FINAL CHECK ---")
print("face_auth import :", 'face_auth' in c)
print("verify_face      :", 'verify_face' in c)
print("capture_face     :", 'capture_face_registration' in c)
print("\nAll done! Run: python login.py")