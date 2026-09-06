# CAP System — Full System Flowchart

> Cashier and Payment (CAP) System — Django + MySQL/SQLite
> Complete flow from Login → Logout with database operations at every step

---

## Database Schema Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          DATABASE: new_db (MySQL)                                │
│                          (SQLite fallback: db.sqlite3 — 0.60 MB)                │
│                          20 tables total                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────┐    ┌─────────────────────┐    ┌────────────────────┐  │
│  │  AUTH TABLES         │    │  CORE TABLES         │    │  SYSTEM TABLES     │  │
│  │  (Django built-in)   │    │  (cashier app)       │    │  (admin_panel)     │  │
│  ├─────────────────────┤    ├─────────────────────┤    ├────────────────────┤  │
│  │ auth_user            │    │ cashier_profile      │    │ admin_panel_       │  │
│  │   id, username,      │    │   user_id [FK]       │    │   systemsetting    │  │
│  │   password, email,   │    │   role, status,      │    │   (1 row — config) │  │
│  │   is_superuser,      │    │   totp_secret,       │    │                    │  │
│  │   is_staff,          │    │   totp_enabled,      │    │ admin_panel_       │  │
│  │   is_active,         │    │   email_otp_enabled, │    │   auditlog         │  │
│  │   first_name,        │    │   mfa_recovery_codes,│    │   (41 rows)        │  │
│  │   last_name,         │    │   must_change_pw,    │    │                    │  │
│  │   date_joined,       │    │   active_session_key,│    │ admin_panel_       │  │
│  │   last_login         │    │   last_activity,     │    │   managementoption │  │
│  │                      │    │   auto_lock_seconds, │    │   (taxes, remarks, │  │
│  │ auth_permission      │    │   auto_logout_secs,  │    │    account titles) │  │
│  │   (60 rows)          │    │   login_attempt_     │    │                    │  │
│  │                      │    │   blocked,           │    │ admin_panel_       │  │
│  │ auth_group           │    │   blocked_login_time,│    │   accounttitlegroup│  │
│  │ auth_group_          │    │   blocked_login_ip,  │    │                    │  │
│  │   permissions        │    │   email_encrypted,   │    │ admin_panel_       │  │
│  │ auth_user_groups     │    │   email_hash,        │    │   chatbotconfig    │  │
│  │ auth_user_user_      │    │   profile_picture,   │    │                    │  │
│  │   permissions        │    │   rfid_uid,          │    │ admin_panel_       │  │
│  │                      │    │   fingerprint_       │    │   chatmessage      │  │
│  │ django_session       │    │   template,          │    │                    │  │
│  │   session_key [PK],  │    │   face_embedding,    │    │ admin_panel_       │  │
│  │   session_data,      │    │   voice_sample       │    │   roleconfig       │  │
│  │   expire_date        │    │                      │    │   (3 rows)         │  │
│  │                      │    │ cashier_cheque       │    └────────────────────┘  │
│  │ django_content_type  │    │   cheque_number,     │                           │
│  │   (15 rows)          │    │   payee_id [FK],     │    ┌────────────────────┐  │
│  │                      │    │   payee_name,        │    │  DJANGO TABLES      │  │
│  │ django_migrations    │    │   fund_cluster_id    │    ├────────────────────┤  │
│  │   (36 rows)          │    │   [FK], amount, date,│    │ django_session     │  │
│  │                      │    │   purpose, bank_name,│    │ django_migrations  │  │
│  │ django_admin_log     │    │   account_number,    │    │ django_content_    │  │
│  │   (0 rows)           │    │   dv_payroll_no,     │    │   type             │  │
│  │                      │    │   ors_burs_no,       │    │ django_admin_log   │  │
│  └─────────────────────┘    │   status, created_by, │    │ sqlite_sequence    │  │
│                              │   updated_by, printed,│    └────────────────────┘  │
│                              │   voided_at,          │                           │
│                              │   void_reason,        │    ┌────────────────────┐  │
│                              │   stale_after_days,   │    │  MEDIA FILES        │  │
│                              │   stale_at            │    ├────────────────────┤  │
│                              │                       │    │ profile_pictures/  │  │
│                              │ cashier_fundcluster   │    │ system_assets/     │  │
│                              │   code, name,         │    │   system_logo      │  │
│                              │   description,        │    │   login_background │  │
│                              │   balance, bank_name, │    │   lockscreen_      │  │
│                              │   account_number,     │    │     wallpaper      │  │
│                              │   is_active           │    │ lockscreen_        │  │
│                              │                       │    │   wallpapers/      │  │
│                              │ cashier_supplier      │    │ biometrics/        │  │
│                              │   (85 rows)           │    │   fingerprint/     │  │
│                              │   mr_or, mr, code_or, │    │   face/            │  │
│                              │   account_number,     │    │   voice/           │  │
│                              │   account_name,       │    │ media/             │  │
│                              │   amount, status,     │    │   cheques/         │  │
│                              │   tin, address,       │    │   reports/         │  │
│                              │   created_by [FK],    │    │   radai/           │  │
│                              │   raw_import          │    └────────────────────┘  │
│                              │                       │                           │
│                              │ cashier_report        │                           │
│                              │   title, report_type, │                           │
│                              │   period, generated_  │                           │
│                              │   by [FK], date_from, │                           │
│                              │   date_to, data_      │                           │
│                              │   snapshot (JSON),    │                           │
│                              │   notes               │                           │
│                              │                       │                           │
│                              │ cashier_transaction   │                           │
│                              │   (0 rows — unused)   │                           │
│                              │                       │                           │
│                              │ cashier_roleconfig    │                           │
│                              │   role, config (JSON) │                           │
│                              └─────────────────────┘                           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Master Flowchart — Login to Logout (with Database Operations)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FLOWCHART START                                     │
│                          User opens CAP System (browser)                         │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │
                                        ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  1. LOGIN PAGE — /                                                              ║
║  ┌───────────────────────────────────────────────────────────────────────────┐  ║
║  │  DATABASE READ:                                                           │  ║
║  │    SELECT * FROM admin_panel_systemsetting WHERE id=1                    │  ║
║  │    → Reads: system_name, system_logo, login_background                   │  ║
║  │                                                                           │  ║
║  │  login.html rendered with:                                               │  ║
║  │    • System logo & name (from SystemSetting)                             │  ║
║  │    • Custom background image (login_background)                          │  ║
║  │    • Glassmorphism card UI                                               │  ║
║  │                                                                           │  ║
║  │  FORM:                                                                    │  ║
║  │    username — text input (username OR email)                             │  ║
║  │    password — password input (with show/hide toggle)                     │  ║
║  │                                                                           │  ║
║  │  ACTIONS:                                                                 │  ║
║  │    [Sign In]  → POST to / (login_view)                                  │  ║
║  │    [Forgot password?] → /forgot-password/                               │  ║
║  │                                                                           │  ║
║  │  IF ?kicked=1 → show "Session Terminated" banner                        │  ║
║  │  IF error → show error message                                          │  ║
║  └───────────────────────────────────────────────────────────────────────────┘  ║
╚═════════════════════╤═══════════════════════════════════════════════════════════╝
                      │ POST {username, password}
                      ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  2. VALIDATE CREDENTIALS                                                        ║
║  ┌───────────────────────────────────────────────────────────────────────────┐  ║
║  │  views.py:658-699                                                         │  ║
║  │                                                                           │  ║
║  │  authenticate(request, username, password)                               │  ║
║  │    └─► backends.py: EmailOrUsernameModelBackend.authenticate()           │  ║
║  │                                                                           │  ║
║  │  DATABASE READ (4-step lookup):                                           │  ║
║  │    1. SELECT * FROM auth_user WHERE username = ? (case-insensitive)      │  ║
║  │       → Table: auth_user                                                 │  ║
║  │                                                                           │  ║
║  │    2. SELECT * FROM cashier_profile                                      │  ║
║  │       WHERE email_hash = SHA256(lower(email))                            │  ║
║  │       → Table: cashier_profile (email_hash column)                       │  ║
║  │       → Joins: profile.user_id → auth_user.id                           │  ║
║  │                                                                           │  ║
║  │    3. SELECT * FROM cashier_profile                                      │  ║
║  │       WHERE email_encrypted IS NOT NULL                                  │  ║
║  │       → Decrypt each email_encrypted (Fernet)                           │  ║
║  │       → Compare with input email                                         │  ║
║  │       → Table: cashier_profile (email_encrypted column)                  │  ║
║  │                                                                           │  ║
║  │    4. SELECT * FROM auth_user                                            │  ║
║  │       → Decrypt each user.email (Fernet)                                │  ║
║  │       → Compare with input email                                         │  ║
║  │       → Table: auth_user (email column)                                 │  ║
║  │                                                                           │  ║
║  │  PASSWORD CHECK:                                                          │  ║
║  │    user.check_password(password)                                         │  ║
║  │    → Compares against auth_user.password (PBKDF2 hash)                  │  ║
║  │                                                                           │  ║
║  │  RESULT:                                                                  │  ║
║  │    user object if valid, None if invalid                                │  ║
║  └───────────────────────────────────────────────────────────────────────────┘  ║
╚═══════════════╤═════════════════════════════════════╤═══════════════════════════╝
                │                                     │
                ▼ INVALID                             ▼ VALID
    ┌───────────────────────┐     ┌──────────────────────────────────────────┐
    │  DATABASE READ:       │     │  3. DEVICE BLOCK CHECK                   │
    │  (none — just error)  │     │  views.py:224-239                        │
    │                       │     │                                          │
    │  error = "Invalid     │     │  DATABASE READ:                          │
    │  username or password"│     │    SELECT * FROM cashier_userdevice      │
    │                       │     │    WHERE is_blocked = True               │
    │  → Return to Login    │     │    → Table: cashier_userdevice           │
    └───────────────────────┘     │    (this table is created at login)      │
                                  │    → First login: no blocked devices     │
                                  │    → Returns empty set → NOT BLOCKED     │
                                  └────────┬──────────────┬──────────────────┘
                                           │              │
                                           ▼ BLOCKED      ▼ NOT BLOCKED
                                  ┌──────────────┐  ┌──────────────────────┐
                                  │ DATABASE     │  │ 4. CONCURRENT        │
                                  │ READ:        │  │ SESSION CHECK        │
                                  │ (none)       │  │ views.py:242-275     │
                                  │              │  │                      │
                                  │ error =      │  │ DATABASE READ:       │
                                  │ "Device      │  │   SELECT * FROM      │
                                  │ blocked"     │  │   cashier_profile    │
                                  │              │  │   WHERE user_id = ?  │
                                  │ → Login page │  │   → Reads:           │
                                  └──────────────┘  │     active_session_  │
                                                    │     key, last_       │
                                                    │     activity,        │
                                                    │     auto_logout_     │
                                                    │     seconds          │
                                                    │                      │
                                                    │   SELECT * FROM      │
                                                    │   django_session     │
                                                    │   WHERE session_key  │
                                                    │   = active_session_  │
                                                    │   key                │
                                                    │   → Checks if old    │
                                                    │   session exists     │
                                                    │   and not expired    │
                                                    │                      │
                                                    │   Calculates:        │
                                                    │   elapsed = now -    │
                                                    │     last_activity    │
                                                    │   threshold = auto_  │
                                                    │     logout_seconds   │
                                                    │   active = elapsed <= │
                                                    │     threshold        │
                                                    └───┬──────────┬───────┘
                                                        │          │
                                                        ▼ ACTIVE   ▼ NO ACTIVE
                                                        │ SESSION  │ SESSION
                                                        │          │
                                                ┌───────┴────┐     │
                                                │ DATABASE   │     │
                                                │ WRITE:     │     │
                                                │ UPDATE     │     │
                                                │ cashier_   │     │
                                                │ profile    │     │
                                                │ SET        │     │
                                                │ login_     │     │
                                                │ attempt_   │     │
                                                │ blocked =  │     │
                                                │ True,      │     │
                                                │ blocked_   │     │
                                                │ login_time,│     │
                                                │ blocked_   │     │
                                                │ login_ip   │     │
                                                └────────────┘     │
                                                                   ▼
                                    ┌──────────────────────────────────────────┐
                                    │  5. CHECK PROFILE FLAGS                   │
                                    │  views.py:671-697                         │
                                    │                                          │
                                    │  DATABASE READ:                           │
                                    │    SELECT * FROM cashier_profile          │
                                    │    WHERE user_id = ?                     │
                                    │    → Table: cashier_profile               │
                                    │    → Reads: role, must_change_password,   │
                                    │      totp_enabled, email_otp_enabled      │
                                    │                                          │
                                    │  IF role != user role:                    │
                                    │    DATABASE WRITE:                        │
                                    │      UPDATE cashier_profile               │
                                    │      SET role = 'admin'                  │
                                    │      WHERE user_id = ?                   │
                                    │      (sync superuser → admin)            │
                                    │                                          │
                                    │  FLAG CHECKS (in order):                 │
                                    │    must_change_password?                  │
                                    │    → YES: go to Step 6                   │
                                    │    → NO: check MFA                       │
                                    │                                          │
                                    │    MFA enabled? (totp OR email)           │
                                    │    → YES: go to Step 7                   │
                                    │    → NO: go to Step 10 (direct login)    │
                                    └──────────┬──────────┬──────────┬─────────┘
                                               │          │          │
                              ┌─────────────────┘          │          └─────────────────┐
                              ▼                            ▼                          ▼
╔══════════════════════════════════════════╗  ╔══════════════════════════╗  ╔═════════════════╗
║  6. CHANGE PASSWORD FORM                 ║  ║  7. PICK MFA METHOD     ║  ║  DIRECT LOGIN   ║
║  views.py:361-410                        ║  ║  views.py:412-444       ║  ║  views.py:692   ║
║                                          ║  ║                          ║  ║                  ║
║  Condition:                              ║  ║  SESSION STORED:         ║  ║  DATABASE READ:  ║
║  profile.must_change_password = True     ║  ║  mfa_user_id = user.pk  ║  ║  (none)          ║
║                                          ║  ║                          ║  ║                  ║
║  DATABASE WRITE:                         ║  ║  FORM OPTIONS:           ║  ║  user.backend =  ║
║    (deferred — on submit)                ║  ║    [Authenticator App]   ║  ║  'ModelBackend'  ║
║                                          ║  ║      → pick_mfa = totp  ║  ║                  ║
║  SUBMIT:                                 ║  ║                          ║  ║  DATABASE WRITE:  ║
║    DATABASE WRITE:                       ║  ║    [Email OTP]           ║  ║  (none — Django   ║
║      UPDATE auth_user                    ║  ║      → pick_mfa = email ║  ║  login() handles  ║
║      SET password = hash(new_pw)         ║  ║      → Send OTP email   ║  ║  session internally)║
║      WHERE id = ?                       ║  ║                          ║  ║                  ║
║      → Table: auth_user                 ║  ║  DATABASE READ:          ║  ║  go to Step 10   ║
║                                          ║  ║    SELECT * FROM        ║  ╚═════════════════╝
║    UPDATE cashier_profile               ║  ║    cashier_profile       ║
║    SET must_change_password = False      ║  ║    WHERE user_id = ?    ║
║    WHERE user_id = ?                    ║  ║    → Reads: totp_enabled,║
║    → Table: cashier_profile             ║  ║      email_otp_enabled   ║
║                                          ║  ║                          ║
║  IF MFA enabled → go to Step 8          ║  ║  go to Step 8            ║
║  IF no MFA → go to Step 10              ║  ║                          ║
╚══════════════════════════════════════════╝  ╚══════════════╤═══════════╝
                                                             │ POST {pick_mfa}
                                                             ▼
                                    ╔═══════════════════════════════════════════╗
                                    ║  8. SEND MFA CODE                         ║
                                    ║  views.py:423-444                         ║
                                    ║                                           ║
                                    ║  IF pick_mfa = "email":                   ║
                                    ║    DATABASE READ:                          ║
                                    ║      SELECT * FROM auth_user              ║
                                    ║      WHERE id = mfa_user_id              ║
                                    ║      → Table: auth_user                   ║
                                    ║      → Reads: email (Fernet encrypted)   ║
                                    ║                                           ║
                                    ║      SELECT * FROM admin_panel_           ║
                                    ║      systemsetting WHERE id=1            ║
                                    ║      → Reads: email_host, email_port,    ║
                                    ║        email_host_user, email_host_       ║
                                    ║        password, email_use_tls           ║
                                    ║                                           ║
                                    ║    SESSION STORED:                        ║
                                    ║      email_otp_code = generated OTP      ║
                                    ║      email_otp_created = timestamp       ║
                                    ║                                           ║
                                    ║    SMTP SEND:                             ║
                                    ║      → Send 6-digit OTP to user's email  ║
                                    ║      → OTP expires in 10 minutes         ║
                                    ║                                           ║
                                    ║  IF pick_mfa = "totp":                    ║
                                    ║    DATABASE READ:                          ║
                                    ║      SELECT * FROM cashier_profile        ║
                                    ║      WHERE user_id = ?                   ║
                                    ║      → Table: cashier_profile             ║
                                    ║      → Reads: totp_secret, totp_enabled  ║
                                    ║                                           ║
                                    ║    → Show 6-digit code input             ║
                                    ║    → Wait for user to enter code         ║
                                    ╚═══════════════════╤═══════════════════════╝
                                                        │ POST {mfa_code}
                                                        ▼
                                    ╔═══════════════════════════════════════════╗
                                    ║  9. VERIFY MFA CODE                       ║
                                    ║  views.py:446-656                         ║
                                    ║                                           ║
                                    ║  DATABASE READ:                            ║
                                    ║    SELECT * FROM cashier_profile           ║
                                    ║    WHERE user_id = mfa_user_id           ║
                                    ║    → Table: cashier_profile               ║
                                    ║    → Reads: totp_secret, totp_enabled,   ║
                                    ║      email_otp_enabled, mfa_recovery_codes║
                                    ║                                           ║
                                    ║  THREE VERIFICATION PATHS:                ║
                                    ║                                           ║
                                    ║  A) RECOVERY CODE (use_recovery=1):      ║
                                    ║     Format: XXXX-XXXX-XXXX               ║
                                    ║     → verify_recovery_code() against     ║
                                    ║       profile.mfa_recovery_codes (JSON)  ║
                                    ║     → Check concurrent session           ║
                                    ║                                           ║
                                    ║  B) TOTP CODE (method=totp):            ║
                                    ║     → verify_totp(secret, code)          ║
                                    ║     → If fails → check as recovery code  ║
                                    ║     → Track failed attempts (session)    ║
                                    ║     → 3 failures → auto-send recovery    ║
                                    ║       code to email                      ║
                                    ║                                           ║
                                    ║  C) EMAIL OTP (method=email):           ║
                                    ║     → Compare with session email_otp_code║
                                    ║     → Check expiry (10 min)              ║
                                    ║                                           ║
                                    ║  ON SUCCESS:                              ║
                                    ║    → Check concurrent session            ║
                                    ║    → go to Step 10                        ║
                                    ║                                           ║
                                    ║  ON RECOVERY CODE USED:                   ║
                                    ║    DATABASE WRITE:                         ║
                                    ║      UPDATE cashier_profile               ║
                                    ║      SET mfa_recovery_codes = [           ║
                                    ║        ...remaining_codes                 ║
                                    ║      ]                                    ║
                                    ║      WHERE user_id = ?                   ║
                                    ║      → Removes used recovery code        ║
                                    ╚═══════════════════╤═══════════════════════╝
                                                        │
                                                        ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  10. CREATE SESSION & TRACK DEVICE                                              ║
║  views.py:305-348  (_register_session)                                          ║
║                                                                                 ║
║  DATABASE READ:                                                                 ║
║    SELECT * FROM cashier_profile                                                ║
║    WHERE user_id = ?                                                           ║
║    → Table: cashier_profile                                                     ║
║    → Reads: active_session_key, status                                         ║
║                                                                                 ║
║  IF old session exists and different key:                                       ║
║    DATABASE DELETE:                                                             ║
║      DELETE FROM django_session                                                 ║
║      WHERE session_key = old_active_session_key                                ║
║      → Removes old Django session                                              ║
║                                                                                 ║
║    DATABASE WRITE:                                                              ║
║      UPDATE cashier_userdevice                                                  ║
║      SET is_terminated = True                                                   ║
║      WHERE session_key = old_key                                               ║
║      → Table: cashier_userdevice                                               ║
║                                                                                 ║
║  DATABASE WRITE:                                                                ║
║    UPDATE cashier_profile                                                       ║
║    SET active_session_key = new_key,                                            ║
║        last_activity = NOW(),                                                   ║
║        status = 'active'                                                        ║
║    WHERE user_id = ?                                                           ║
║    → Table: cashier_profile                                                     ║
║                                                                                 ║
║  DATABASE DELETE + INSERT:                                                      ║
║    DELETE FROM cashier_userdevice                                               ║
║    WHERE session_key = new_key                                                 ║
║    → Clean up any stale record                                                 ║
║                                                                                 ║
║    INSERT INTO cashier_userdevice (                                             ║
║      user_id, session_key, device_name, windows_username,                       ║
║      ip_address, location, user_agent, last_activity, login_time               ║
║    ) VALUES (...)                                                               ║
║    → Table: cashier_userdevice                                                  ║
║    → Stores: device fingerprint, IP, geolocation, browser info                 ║
╚═════════════════════════════╤═══════════════════════════════════════════════════╝
                              │
                              ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  11. ROLE-BASED ROUTING                                                         ║
║  views.py:731-738  (_mfa_redirect)                                              ║
║                                                                                 ║
║  DATABASE READ:                                                                 ║
║    SELECT * FROM cashier_profile                                                ║
║    WHERE user_id = ?                                                           ║
║    → Table: cashier_profile                                                     ║
║    → Reads: role ('admin'/'cashier'/'guest')                                   ║
║                                                                                 ║
║  ROUTING:                                                                       ║
║    role = 'admin'  → redirect('admin_dashboard')                               ║
║    role = 'cashier' → redirect('cashier_dashboard')                            ║
║    role = 'guest'  → redirect('cashier_dashboard')                            ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  12. DASHBOARD                                                                  ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  ADMIN DASHBOARD (views.py:2707)                                       │    ║
║  │                                                                         │    ║
║  │  DATABASE READS:                                                        │    ║
║  │    SELECT COUNT(*), SUM(amount), status                                │    ║
║  │    FROM cashier_cheque                                                  │    ║
║  │    GROUP BY status                                                      │    ║
║  │    → Table: cashier_cheque                                              │    ║
║  │    → Counts: draft, pending, released, voided, stale                   │    ║
║  │    → Total released amount                                              │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*), SUM(amount)                                         │    ║
║  │    FROM cashier_cheque                                                  │    ║
║  │    WHERE created_at >= today_start AND created_at < today_end          │    ║
║  │    → Today's created/released stats                                    │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*), SUM(amount)                                         │    ║
║  │    FROM cashier_radai                                                   │    ║
║  │    → Table: cashier_radai                                               │    ║
║  │    → RADAI total count and amount                                      │    ║
║  │                                                                         │    ║
║  │    SELECT * FROM cashier_fundcluster                                    │    ║
║  │    WHERE is_active = True AND balance > 0                               │    ║
║  │    ORDER BY balance DESC LIMIT 5                                        │    ║
║  │    → Table: cashier_fundcluster                                         │    ║
║  │    → Top 5 fund clusters by balance                                    │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*) FROM auth_user                                       │    ║
║  │    → Table: auth_user                                                   │    ║
║  │    → Total users                                                        │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*) FROM cashier_supplier                                │    ║
║  │    WHERE status = 'active'                                             │    ║
║  │    → Table: cashier_supplier                                            │    ║
║  │    → Active suppliers count                                             │    ║
║  │                                                                         │    ║
║  │    SELECT AVG(processing_time) FROM cashier_cheque                      │    ║
║  │    WHERE status = 'released'                                           │    ║
║  │    → Average processing speed (days)                                   │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*) FROM cashier_cheque                                  │    ║
║  │    WHERE status IN ('pending','draft')                                  │    ║
║  │    AND created_at < NOW() - INTERVAL 7 DAY                             │    ║
║  │    → Stale cheques (> 7 days)                                          │    ║
║  │                                                                         │    ║
║  │    SELECT user_id, COUNT(*) as cheque_count                             │    ║
║  │    FROM cashier_cheque                                                  │    ║
║  │    WHERE created_at >= month_start                                      │    ║
║  │    GROUP BY user_id ORDER BY cheque_count DESC LIMIT 5                 │    ║
║  │    → Cashier performance (top 5)                                       │    ║
║  │                                                                         │    ║
║  │    SELECT code, balance FROM cashier_fundcluster                        │    ║
║  │    WHERE is_active = True AND balance < 10000                           │    ║
║  │    → Low balance alerts                                                │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*) FROM cashier_cheque                                  │    ║
║  │    WHERE status IN ('pending','draft')                                  │    ║
║  │    AND created_at < NOW() - INTERVAL 7 DAY                             │    ║
║  │    → Stale cheque alerts                                               │    ║
║  │                                                                         │    ║
║  │    SELECT month, COUNT(*) FROM cashier_cheque                           │    ║
║  │    WHERE created_at >= 12 months ago                                   │    ║
║  │    GROUP BY month                                                      │    ║
║  │    → 12-month trend sparkline data                                     │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  CASHIER DASHBOARD (views.py:2933)                                     │    ║
║  │                                                                         │    ║
║  │  DATABASE READS:                                                        │    ║
║  │    SELECT COUNT(*), SUM(amount), status                                 │    ║
║  │    FROM cashier_cheque                                                  │    ║
║  │    WHERE created_by_id = current_user_id                               │    ║
║  │    → Table: cashier_cheque (user-scoped)                               │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*) FROM cashier_supplier                                │    ║
║  │    WHERE created_by_id = current_user_id                               │    ║
║  │    → My suppliers count                                                 │    ║
║  │                                                                         │    ║
║  │    SELECT COUNT(*), SUM(amount) FROM cashier_radai                      │    ║
║  │    WHERE created_by_id = current_user_id                               │    ║
║  │    → My RADAI count and amount                                          │    ║
║  │                                                                         │    ║
║  │    SELECT * FROM cashier_cheque                                         │    ║
║  │    WHERE status IN ('pending','draft')                                  │    ║
║  │    AND created_at < NOW() - INTERVAL 7 DAY                             │    ║
║  │    → My stale cheques                                                   │    ║
║  │                                                                         │    ║
║  │    SELECT stale_at FROM cashier_cheque                                  │    ║
║  │    WHERE status = 'released' AND stale_at IS NOT NULL                   │    ║
║  │    → Expiring soon / already stale alerts                              │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  GUEST DASHBOARD                                                       │    ║
║  │                                                                         │    ║
║  │  DATABASE READS:                                                        │    ║
║  │    SELECT COUNT(*), SUM(amount), status                                 │    ║
║  │    FROM cashier_cheque                                                  │    ║
║  │    → All cheques (read-only)                                            │    ║
║  │                                                                         │    ║
║  │    SELECT * FROM cashier_report                                          │    ║
║  │    → Available reports                                                   │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  13. MODULE ACCESS & TASKS — Database Operations Per Module                     ║
║                                                                                 ║
║  Enforced by RolePermissionsMiddleware (middleware.py:59)                       ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  CHEQUE MODULE                                                         │    ║
║  │                                                                         │    ║
║  │  LIST (/cheques/):                                                     │    ║
║  │    DATABASE READ:                                                       │    ║
║  │      SELECT c.*, p.account_name, fc.code, fc.name                      │    ║
║  │      FROM cashier_cheque c                                              │    ║
║  │      LEFT JOIN cashier_supplier p ON c.payee_id = p.id                 │    ║
║  │      LEFT JOIN cashier_fundcluster fc ON c.fund_cluster_id = fc.id     │    ║
║  │      ORDER BY c.created_at DESC                                        │    ║
║  │      → Paginated results                                               │    ║
║  │                                                                         │    ║
║  │  CREATE (/cheques/new/):                                               │    ║
║  │    DATABASE READ:                                                       │    ║
║  │      SELECT * FROM cashier_supplier ORDER BY account_name              │    ║
║  │      SELECT * FROM cashier_fundcluster WHERE is_active = True          │    ║
║  │      SELECT * FROM admin_panel_managementoption                        │    ║
║  │      WHERE category = 'tax' AND is_active = True                      │    ║
║  │      SELECT * FROM admin_panel_accounttitlegroup                       │    ║
║  │      SELECT * FROM admin_panel_managementoption                        │    ║
║  │      WHERE category = 'account_title'                                 │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      INSERT INTO cashier_cheque (                                       │    ║
║  │        cheque_number, payee_id, payee_name, fund_cluster_id,          │    ║
║  │        amount, date, purpose, bank_name, account_number,               │    ║
║  │        dv_payroll_no, ors_burs_no, responsibility_center,              │    ║
║  │        uacs_object_code, nature_of_payment,                            │    ║
║  │        professional_tax, tax_5_3, tax_3_1,                             │    ║
║  │        status, created_by_id                                           │    ║
║  │      ) VALUES (...)                                                     │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE (auto-numbering):                                     │    ║
║  │      UPDATE admin_panel_systemsetting                                   │    ║
║  │      SET last_cheque_number = ?                                        │    ║
║  │      → Increments cheque sequence                                      │    ║
║  │                                                                         │    ║
║  │      UPDATE cashier_fundcluster                                         │    ║
║  │      SET balance = balance - cheque_amount                              │    ║
║  │      WHERE id = fund_cluster_id                                        │    ║
║  │      → Deducts from fund cluster balance                               │    ║
║  │                                                                         │    ║
║  │  EDIT (/cheques/<pk>/edit/):                                           │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE WRITE: UPDATE cashier_cheque SET ... WHERE id = ?          │    ║
║  │                                                                         │    ║
║  │  PRINT (/cheques/<pk>/print/):                                         │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE WRITE: UPDATE cashier_cheque                               │    ║
║  │      SET printed_at = NOW(), status = 'pending'                        │    ║
║  │      WHERE id = ? AND status = 'draft'                                 │    ║
║  │                                                                         │    ║
║  │  PDF (/cheques/<pk>/pdf/):                                             │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    → Generates PDF (no DB write)                                       │    ║
║  │                                                                         │    ║
║  │  VOID (/cheques/<pk>/void/):                                           │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE WRITE: UPDATE cashier_cheque                               │    ║
║  │      SET status = 'voided', voided_at = NOW(), void_reason = ?        │    ║
║  │      WHERE id = ?                                                      │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE (refund):                                            │    ║
║  │      UPDATE cashier_fundcluster                                         │    ║
║  │      SET balance = balance + cheque_amount                              │    ║
║  │      WHERE id = fund_cluster_id                                        │    ║
║  │      → Refunds amount to fund cluster                                  │    ║
║  │                                                                         │    ║
║  │  ADVANCE STATUS (/cheques/<pk>/advance/):                              │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE WRITE: UPDATE cashier_cheque                               │    ║
║  │      SET status = next_status, updated_at = NOW()                      │    ║
║  │      → draft → pending → released                                     │    ║
║  │                                                                         │    ║
║  │  DELETE (/cheques/<pk>/delete/):                                       │    ║
║  │    DATABASE READ: SELECT * FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE DELETE: DELETE FROM cashier_cheque WHERE id = ?            │    ║
║  │    DATABASE WRITE (refund):                                            │    ║
║  │      UPDATE cashier_fundcluster                                         │    ║
║  │      SET balance = balance + cheque_amount                              │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  SUPPLIER MODULE                                                       │    ║
║  │                                                                         │    ║
║  │  LIST (/suppliers/):                                                   │    ║
║  │    DATABASE READ:                                                       │    ║
║  │      SELECT * FROM cashier_supplier ORDER BY account_name              │    ║
║  │      → Paginated results                                               │    ║
║  │                                                                         │    ║
║  │  CREATE (POST):                                                        │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      INSERT INTO cashier_supplier (account_number, account_name,       │    ║
║  │        address, tin, status, created_by_id) VALUES (...)               │    ║
║  │                                                                         │    ║
║  │  EDIT (POST):                                                          │    ║
║  │    DATABASE READ: SELECT * FROM cashier_supplier WHERE id = ?          │    ║
║  │    DATABASE WRITE: UPDATE cashier_supplier SET ... WHERE id = ?        │    ║
║  │                                                                         │    ║
║  │  IMPORT (CSV):                                                         │    ║
║  │    DATABASE WRITE (bulk):                                               │    ║
║  │      INSERT INTO cashier_supplier (...) VALUES (...), (...), ...       │    ║
║  │      → Bulk insert from CSV                                            │    ║
║  │                                                                         │    ║
║  │  EXPORT (CSV):                                                         │    ║
║  │    DATABASE READ: SELECT * FROM cashier_supplier                       │    ║
║  │    → Generate CSV download                                             │    ║
║  │                                                                         │    ║
║  │  UPDATE REMARK:                                                        │    ║
║  │    DATABASE WRITE: UPDATE cashier_supplier                             │    ║
║  │      SET remarks = ? WHERE id = ?                                      │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  RADAI MODULE                                                          │    ║
║  │                                                                         │    ║
║  │  Same CRUD pattern as Suppliers:                                       │    ║
║  │    LIST: SELECT * FROM cashier_radai                                    │    ║
║  │    CREATE: INSERT INTO cashier_radai (...)                             │    ║
║  │    EDIT: UPDATE cashier_radai SET ... WHERE id = ?                     │    ║
║  │    IMPORT: Bulk INSERT from CSV                                         │    ║
║  │    EXPORT: SELECT * → CSV download                                     │    ║
║  │                                                                         │    ║
║  │  SETTINGS:                                                             │    ║
║  │    READ/WRITE: SELECT/UPDATE cashier_radaiSetting                      │    ║
║  │      → ors_burs_no, responsibility_center defaults                    │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  FUND CLUSTER MODULE                                                   │    ║
║  │                                                                         │    ║
║  │  LIST: SELECT * FROM cashier_fundcluster WHERE is_active = True        │    ║
║  │  CREATE: INSERT INTO cashier_fundcluster (...)                         │    ║
║  │  EDIT: UPDATE cashier_fundcluster SET ... WHERE id = ?                 │    ║
║  │  DELETE: UPDATE cashier_fundcluster SET is_active = False WHERE id = ? │    ║
║  │                                                                         │    ║
║  │  BALANCE DEDUCTED on cheque creation:                                  │    ║
║  │    UPDATE cashier_fundcluster                                           │    ║
║  │    SET balance = balance - amount                                       │    ║
║  │    WHERE id = ?                                                        │    ║
║  │                                                                         │    ║
║  │  BALANCE REFUNDED on cheque void/delete:                               │    ║
║  │    UPDATE cashier_fundcluster                                           │    ║
║  │    SET balance = balance + amount                                       │    ║
║  │    WHERE id = ?                                                        │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  REPORTS MODULE                                                        │    ║
║  │                                                                         │    ║
║  │  GENERATE:                                                             │    ║
║  │    DATABASE READ (complex aggregations):                                │    ║
║  │      SELECT FROM cashier_cheque, cashier_supplier,                     │    ║
║  │        cashier_fundcluster, cashier_radai                               │    ║
║  │      → Aggregated by type, date range, period                          │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      INSERT INTO cashier_report (title, report_type, period,            │    ║
║  │        generated_by_id, date_from, date_to, data_snapshot, notes)      │    ║
║  │      VALUES (...)                                                       │    ║
║  │      → Saves report with JSON snapshot                                 │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      UPDATE admin_panel_systemsetting                                   │    ║
║  │      SET last_report_number = ?                                        │    ║
║  │                                                                         │    ║
║  │  LIST: SELECT * FROM cashier_report ORDER BY generated_at DESC         │    ║
║  │  DETAIL: SELECT * FROM cashier_report WHERE id = ?                     │    ║
║  │  DELETE: DELETE FROM cashier_report WHERE id = ?                       │    ║
║  │  PRINT: Read report data_snapshot → render HTML/PDF                    │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  USER MANAGEMENT (Admin only)                                          │    ║
║  │                                                                         │    ║
║  │  LIST: SELECT * FROM auth_user LEFT JOIN cashier_profile ON ...        │    ║
║  │                                                                         │    ║
║  │  CREATE:                                                                │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      INSERT INTO auth_user (username, password, email,                 │    ║
║  │        is_superuser, is_staff, is_active, first_name, last_name,       │    ║
║  │        date_joined) VALUES (...)                                       │    ║
║  │                                                                         │    ║
║  │    DATABASE WRITE (auto via signal):                                    │    ║
║  │      INSERT INTO cashier_profile (user_id, role, status)               │    ║
║  │      → Signal creates profile on User creation                         │    ║
║  │                                                                         │    ║
║  │  EDIT:                                                                  │    ║
║  │    DATABASE READ: SELECT * FROM auth_user WHERE id = ?                 │    ║
║  │    DATABASE READ: SELECT * FROM cashier_profile WHERE user_id = ?      │    ║
║  │    DATABASE WRITE: UPDATE auth_user SET ... WHERE id = ?               │    ║
║  │    DATABASE WRITE: UPDATE cashier_profile SET ... WHERE user_id = ?    │    ║
║  │                                                                         │    ║
║  │  DELETE:                                                                │    ║
║  │    DATABASE DELETE: DELETE FROM auth_user WHERE id = ?                 │    ║
║  │    → CASCADE deletes profile, devices, cheques, etc.                   │    ║
║  │                                                                         │    ║
║  │  ROLE PERMISSIONS:                                                     │    ║
║  │    READ/WRITE: cashier_roleconfig                                       │    ║
║  │      SELECT/UPDATE config JSON for role                                │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  MANAGE DEVICES (Admin only)                                           │    ║
║  │                                                                         │    ║
║  │  LIST: SELECT * FROM cashier_userdevice                                │    ║
║  │    JOIN auth_user ON userdevice.user_id = auth_user.id                 │    ║
║  │    → Shows all logged-in devices                                       │    ║
║  │                                                                         │    ║
║  │  BLOCK: UPDATE cashier_userdevice SET is_blocked = True WHERE id = ?  │    ║
║  │  UNBLOCK: UPDATE ... SET is_blocked = False WHERE id = ?              │    ║
║  │  TERMINATE: UPDATE ... SET is_terminated = True WHERE id = ?          │    ║
║  │  DELETE: DELETE FROM cashier_userdevice WHERE id = ?                   │    ║
║  │                                                                         │    ║
║  │  SCREEN CAPTURE:                                                       │    ║
║  │    UPDATE cashier_userdevice SET screen_capture = ? WHERE id = ?      │    ║
║  │    → Stores base64 screenshot image                                   │    ║
║  │                                                                         │    ║
║  │  CURRENT URL:                                                          │    ║
║  │    UPDATE cashier_userdevice SET current_url = ? WHERE id = ?         │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  AUDIT LOG                                                             │    ║
║  │                                                                         │    ║
║  │  LOG (on every action):                                                │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      INSERT INTO admin_panel_auditlog (admin_id, action, details,      │    ║
║  │        timestamp) VALUES (?, ?, ?, NOW())                              │    ║
║  │      → JSON details: {model, object_id, old_values, new_values}        │    ║
║  │                                                                         │    ║
║  │  VIEW (/audit-log/):                                                   │    ║
║  │    DATABASE READ:                                                       │    ║
║  │      SELECT a.*, u.username                                             │    ║
║  │      FROM admin_panel_auditlog a                                        │    ║
║  │      LEFT JOIN auth_user u ON a.admin_id = u.id                       │    ║
║  │      ORDER BY a.timestamp DESC                                         │    ║
║  │      → Paginated, filterable by user/action/date                       │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  14. SESSION MONITORING — Database Queries on Every Request                      ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  A) SingleDeviceLoginMiddleware (middleware.py:215)                     │    ║
║  │                                                                         │    ║
║  │  DATABASE READ (every request):                                        │    ║
║  │    SELECT * FROM cashier_profile WHERE user_id = ?                     │    ║
║  │    → Reads: active_session_key, last_activity                          │    ║
║  │                                                                         │    ║
║  │  DATABASE READ:                                                         │    ║
║  │    SELECT * FROM cashier_userdevice                                    │    ║
║  │    WHERE session_key = current_session_key                             │    ║
║  │    → Reads: is_terminated, is_blocked                                  │    ║
║  │                                                                         │    ║
║  │  DATABASE WRITE (every 10s):                                           │    ║
║  │    UPDATE cashier_profile SET last_activity = NOW() WHERE user_id = ? │    ║
║  │    UPDATE cashier_userdevice SET last_activity = NOW()                 │    ║
║  │      WHERE session_key = ?                                             │    ║
║  │                                                                         │    ║
║  │  ON KICK (different session):                                          │    ║
║  │    DATABASE WRITE:                                                      │    ║
║  │      DELETE FROM django_session WHERE session_key = old_key           │    ║
║  │      → Flush old session                                               │    ║
║  │                                                                         │    ║
║  │  ON BLOCKED DEVICE:                                                    │    ║
║  │    → Show device_blocked.html                                          │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  B) LockScreenMiddleware (middleware.py:22)                             │    ║
║  │                                                                         │    ║
║  │  DATABASE READ:                                                         │    ║
║  │    SELECT auto_lock_seconds FROM cashier_profile                       │    ║
║  │    WHERE user_id = ?                                                   │    ║
║  │    → Individual lock timeout (or system default)                       │    ║
║  │                                                                         │    ║
║  │  SESSION CHECK:                                                         │    ║
║  │    session['screen_locked'] — checked on every request                 │    ║
║  │    → If True: redirect to /lockscreen/                                 │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                 ║
║  ┌─────────────────────────────────────────────────────────────────────────┐    ║
║  │  C) JS Heartbeat (/session-check/)                                     │    ║
║  │                                                                         │    ║
║  │  DATABASE READ (polling):                                              │    ║
║  │    SELECT * FROM cashier_profile WHERE user_id = ?                     │    ║
║  │    → active_session_key, last_activity                                 │    ║
║  │                                                                         │    ║
║  │    SELECT * FROM cashier_userdevice                                    │    ║
║  │    WHERE session_key = ?                                               │    ║
║  │    → is_terminated, is_blocked                                         │    ║
║  │                                                                         │    ║
║  │  DATABASE READ:                                                         │    ║
║  │    SELECT * FROM admin_panel_systemsetting WHERE id=1                  │    ║
║  │    → auto_logout_seconds (threshold)                                   │    ║
║  │                                                                         │    ║
║  │  DATABASE WRITE (every 10s):                                           │    ║
║  │    UPDATE cashier_profile SET last_activity = NOW() WHERE user_id = ? │    ║
║  │                                                                         │    ║
║  │  ON KICK: → redirect /?kicked=1                                        │    ║
║  │  ON BLOCKED: → device_blocked page                                     │    ║
║  └─────────────────────────────────────────────────────────────────────────┘    ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  15. LOCK SCREEN (Optional)                                                     ║
║  /lockscreen/ (views.py:9420)                                                   ║
║                                                                                 ║
║  DATABASE READ:                                                                 ║
║    SELECT * FROM cashier_profile WHERE user_id = ?                             ║
║    → Reads: profile_picture, lockscreen_wallpaper, role                        ║
║                                                                                 ║
║  DATABASE READ:                                                                 ║
║    SELECT * FROM admin_panel_systemsetting WHERE id=1                          ║
║    → Reads: lockscreen_wallpaper (system default)                              ║
║                                                                                 ║
║  UNLOCK (POST password):                                                        ║
║    DATABASE READ:                                                               ║
║      SELECT password FROM auth_user WHERE id = ?                              │
║      → Verify password via check_password()                                   ║
║                                                                                 ║
║    SESSION WRITE:                                                               ║
║      session['screen_locked'] = False                                          ║
║      session['screen_lock_hard'] = False                                       ║
║                                                                                 ║
║  → Redirect to previous page or dashboard                                     ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
╔═════════════════════════════════════════════════════════════════════════════════╗
║  16. LOGOUT — /logout/                                                          ║
║  views.py:278-302                                                               ║
║                                                                                 ║
║  DATABASE READ:                                                                 ║
║    SELECT * FROM cashier_profile WHERE user_id = ?                             ║
║    → Reads: status, active_session_key                                         ║
║                                                                                 ║
║  DATABASE WRITE:                                                                ║
║    UPDATE cashier_profile                                                       ║
║    SET status = 'resting'                                                       ║
║    WHERE user_id = ?                                                           ║
║    → Table: cashier_profile                                                     ║
║                                                                                 ║
║  DATABASE DELETE:                                                               ║
║    DELETE FROM admin_panel_chatmessage                                          ║
║    WHERE user_id = ?                                                           ║
║    → Clears user's chat history                                                ║
║                                                                                 ║
║  DATABASE WRITE:                                                                ║
║    UPDATE cashier_userdevice                                                    ║
║    SET is_terminated = True                                                     ║
║    WHERE session_key = current_session_key                                     ║
║    → Table: cashier_userdevice                                                  ║
║                                                                                 ║
║  DATABASE WRITE:                                                                ║
║    UPDATE cashier_profile                                                       ║
║    SET active_session_key = ''                                                  ║
║    WHERE user_id = ?                                                           ║
║    → Clears active session reference                                           ║
║                                                                                 ║
║  SESSION FLUSH:                                                                 ║
║    Django logout(request)                                                       ║
║    → Deletes django_session record                                              ║
║    → Clears auth cookies                                                        ║
║                                                                                 ║
║  DATABASE WRITE (audit):                                                        ║
║    INSERT INTO admin_panel_auditlog (admin_id, action, details, timestamp)    ║
║    → Logs: "User logged out"                                                    ║
║                                                                                 ║
║  → render(logout_loading.html)                                                 ║
║  → Auto-redirect to / (login page)                                             ║
╚═══════════════════════════════╤═════════════════════════════════════════════════╝
                                │
                                ▼
                ┌────────────────────────────────────────┐
                │           FLOWCHART END                │
                │     User back at Login Page             │
                └────────────────────────────────────────┘
```

---

## Sub-Flow: Forgot Password (with Database)

```
┌─────────────────────────────────────────────────────────────────────┐
│  /forgot-password/  (views.py:741)                                 │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────┐
                │  STEP 1: Enter identifier    │
                │  (email or username)         │
                └──────────────┬───────────────┘
                               │ POST {identifier}
                               ▼
                ┌──────────────────────────────┐
                │  DATABASE READ:              │
                │    IF '@' in identifier:     │
                │      SELECT * FROM           │
                │      cashier_profile         │
                │      WHERE email_hash =      │
                │        SHA256(lower(email))  │
                │      → Joins to auth_user    │
                │    ELSE:                     │
                │      SELECT * FROM           │
                │      auth_user               │
                │      WHERE username = ?      │
                └──────────┬───────┬───────────┘
                           │       │
                    NOT FOUND   FOUND
                           │       │
                           ▼       ▼
                ┌────────────┐  ┌──────────────────────┐
                │ Error:     │  │ DATABASE WRITE:      │
                │ "No accnt  │  │   (session store)    │
                │  found"    │  │   otp_code = random  │
                │            │  │   otp_created = now  │
                │            │  │                      │
                │            │  │ SMTP SEND:           │
                │            │  │   → Send OTP to email│
                └────────────┘  └──────────┬───────────┘
                                           │
                                           ▼
                ┌──────────────────────────────────────┐
                │  STEP 2: Enter OTP code              │
                │  (6 digits, 10-min expiry)           │
                └──────────────────┬───────────────────┘
                                   │ POST {otp_code}
                                   ▼
                ┌──────────────────────────────────────┐
                │  Verify code against session         │
                │  (no DB query — session comparison)  │
                └──────────┬──────────┬────────────────┘
                           │          │
                      INVALID      VALID
                           │          │
                           ▼          ▼
                ┌────────────┐  ┌──────────────────────┐
                │ Error:     │  │ STEP 3: New password │
                │ "Invalid   │  │                      │
                │  code"     │  │ DATABASE WRITE:      │
                └────────────┘  │   UPDATE auth_user   │
                                │   SET password =     │
                                │     hash(new_pw)     │
                                │   WHERE id = user.id │
                                │                      │
                                │ → Clear session flags│
                                │ → Redirect → / (login)│
                                └──────────────────────┘
```

---

## Database File Locations

```
┌─────────────────────────────────────────────────────────────────────┐
│  PRODUCTION:                                                        │
│    MySQL Database: new_db                                            │
│    Host: 127.0.0.1:3306 (or Railway cloud)                         │
│    User: admin_deejay                                               │
│    Engine: admin_panel.db_backends.mysql_compat (PyMySQL)          │
│                                                                     │
│  DEVELOPMENT / FALLBACK:                                            │
│    SQLite: db.sqlite3 (in project root)                            │
│    Size: 0.60 MB                                                    │
│                                                                     │
│  MEDIA FILES (not in DB — stored on filesystem):                    │
│    profile_pictures/     — User profile images                     │
│    system_assets/        — System logos, login backgrounds          │
│    lockscreen_wallpapers/— Per-user lockscreen images               │
│    biometrics/fingerprint/— Fingerprint templates                   │
│    biometrics/face/      — Face embeddings                          │
│    biometrics/voice/     — Voice samples                            │
│                                                                     │
│  SESSION STORAGE:                                                   │
│    django_session table                                             │
│    SESSION_COOKIE_AGE = 1800 (30 minutes)                          │
│    SESSION_EXPIRE_AT_BROWSER_CLOSE = True                           │
│    SESSION_SAVE_EVERY_REQUEST = True                                │
│                                                                     │
│  BACKUP:                                                            │
│    db.sqlite3.bak       — SQLite backup file                       │
│    /backup-restore/     — Admin backup/restore UI                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

*CAP System — Full System Flowchart with Database Layer*
