# CAP System — Full System Flowchart

> Cashier and Payment (CAP) System — Django + SQLite
> Complete flow from Login → Logout with all branching logic

---

## Master Flowchart — Login to Logout

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              ●  FLOWCHART START  ●                               │
│                          User opens CAP System (browser)                         │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  1. LOGIN PAGE — /                                                               │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  login.html rendered                                                      │  │
│  │                                                                           │  │
│  │  • System logo & name (from SystemSetting)                                │  │
│  │  • Custom background image (login_background)                             │  │
│  │  • Glassmorphism card UI                                                  │  │
│  │                                                                           │  │
│  │  FORM:                                                                    │  │
│  │    username — text input (username OR email)                              │  │
│  │    password — password input (with show/hide toggle)                      │  │
│  │                                                                           │  │
│  │  ACTIONS:                                                                 │  │
│  │    [Sign In]  → POST to / (login_view)                                   │  │
│  │    [Forgot password?] → /forgot-password/                                │  │
│  │                                                                           │  │
│  │  IF ?kicked=1 → show "Session Terminated" banner                         │  │
│  │  IF error → show error message                                           │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │ POST {username, password}
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  2. VALIDATE CREDENTIALS                                                        │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │  views.py:658-699                                                         │  │
│  │                                                                           │  │
│  │  authenticate(request, username, password)                                │  │
│  │    └─► backends.py: EmailOrUsernameModelBackend.authenticate()            │  │
│  │                                                                           │  │
│  │  LOOKUP ORDER:                                                            │  │
│  │    1. User.objects.get(username__iexact=username)  ← fast                │  │
│  │    2. Profile email_hash lookup (SHA-256 of email)                       │  │
│  │    3. Decrypt Profile.email_encrypted and compare                        │  │
│  │    4. Decrypt User.email and compare (brute force)                       │  │
│  │                                                                           │  │
│  │  PASSWORD CHECK:                                                          │  │
│  │    user.check_password(password) via Django's hasher                      │  │
│  │                                                                           │  │
│  │  RESULT:                                                                  │  │
│  │    user object if valid, None if invalid                                  │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┬───────────────────┘
                            │                                 │
                            ▼ INVALID (user is None)          ▼ VALID (user exists)
                ┌───────────────────────┐       ┌──────────────────────────────────┐
                │  error = "Invalid     │       │  3. DEVICE BLOCK CHECK           │
                │  username or password"│       │  views.py:224-239                │
                │                       │       │                                  │
                │  Render login.html    │       │  _is_device_blocked()            │
                │  with error           │       │  → Get client IP, User-Agent     │
                │  → Return to Login    │       │  → Get device name + OS user     │
                └───────────────────────┘       │                                  │
                                                │  MATCH RULES:                    │
                                                │  1. IP + User-Agent match        │
                                                │  2. DeviceName + WinUser match   │
                                                │     (hardware signature)         │
                                                │  3. IP + DeviceName match        │
                                                └────────┬────────────┬────────────┘
                                                         │            │
                                                         ▼ BLOCKED    ▼ NOT BLOCKED
                                                ┌──────────────┐  ┌──────────────────┐
                                                │ error =      │  │ 4. CONCURRENT    │
                                                │ "Device      │  │ SESSION CHECK    │
                                                │ blocked by   │  │ views.py:242-275 │
                                                │ admin"       │  │                  │
                                                │              │  │ _is_session_     │
                                                │ Render with  │  │ active()         │
                                                │ error        │  │                  │
                                                └──────────────┘  │ CHECK:           │
                                                                  │ • Session key    │
                                                                  │   exists in DB?  │
                                                                  │ • Session not    │
                                                                  │   expired?       │
                                                                  │ • last_activity  │
                                                                  │   within auto_   │
                                                                  │   logout threshold│
                                                                  └───┬──────────┬───┘
                                                                      │          │
                                                                      ▼ ACTIVE   ▼ NO ACTIVE
                                                                      │ SESSION  │ SESSION
                                                                      │          │
                                                              ┌───────┴────┐     │
                                                              │ Set        │     │
                                                              │ login_     │     │
                                                              │ attempt_   │     │
                                                              │ blocked =  │     │
                                                              │ True       │     │
                                                              │            │     │
                                                              │ Notify     │     │
                                                              │ active     │     │
                                                              │ session    │     │
                                                              │            │     │
                                                              │ error =    │     │
                                                              │ "Account   │     │
                                                              │ already    │     │
                                                              │ logged in  │     │
                                                              │ on another │     │
                                                              │ device"    │     │
                                                              └────────────┘     │
                                                                                 ▼
                                                          ┌──────────────────────────────────┐
                                                          │  5. CHECK PROFILE FLAGS           │
                                                          │  views.py:671-697                 │
                                                          │                                   │
                                                          │  profile = Profile.get_or_create()│
                                                          │  Sync role for superuser          │
                                                          │                                   │
                                                          │  FLAG CHECKS (in order):          │
                                                          │                                   │
                                                          │  ┌─────────────────────────┐     │
                                                          │  │ must_change_password?   │     │
                                                          │  │ profile.must_change_pw  │     │
                                                          │  └────────────┬────────────┘     │
                                                          │               │                   │
                                                          │        YES ──┤── NO              │
                                                          │               │                   │
                                                          │               ▼                   │
                                                          │  ┌─────────────────────────┐     │
                                                          │  │ MFA enabled?            │     │
                                                          │  │ totp_enabled OR         │     │
                                                          │  │ email_otp_enabled       │     │
                                                          │  └────────────┬────────────┘     │
                                                          │               │                   │
                                                          │        YES ──┤── NO              │
                                                          │               │                   │
                                                          └───────────────┼──────────────────┘
                                                                          │
                                  ┌───────────────────────────────────────┼────────────────────┐
                                  │                                       │                    │
                                  ▼                                       ▼                    ▼
┌────────────────────────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────┐
│  6. CHANGE PASSWORD FORM                   │  │  7. PICK MFA METHOD     │  │  DIRECT LOGIN        │
│  views.py:361-410                          │  │  views.py:412-444       │  │  views.py:692-697    │
│                                            │  │                         │  │                      │
│  Condition: profile.must_change_password   │  │  Condition: MFA enabled │  │  user.backend =      │
│                                            │  │                         │  │  'ModelBackend'      │
│  FORM:                                     │  │  SESSION STORED:        │  │                      │
│    new_password — password input           │  │    mfa_user_id = user.pk│  │  login(request, user) │
│    confirm_password — password input       │  │                         │  │                      │
│                                            │  │  FORM OPTIONS:          │  │  _register_session() │
│  VALIDATION:                               │  │    [Authenticator App]  │  │                      │
│    • Passwords match                       │  │      → pick_mfa = totp  │  │  _mfa_redirect(user) │
│    • 8+ characters                         │  │                         │  │  → Dashboard         │
│    • Uppercase letter                      │  │    [Email OTP]          │  │                      │
│    • Lowercase letter                      │  │      → pick_mfa = email │  └──────────────────────┘
│    • Number                                │  │      → Send OTP email   │
│    • Special character                     │  │      → email_otp_sent=  │
│                                            │  │        True             │
│  SUBMIT:                                   │  │                         │
│    user.set_password(new_password)         │  │  BACK: [Back to login]  │
│    user.save()                             │  │    → / (reset state)    │
│    must_change_password = False            │  └───────────┬─────────────┘
│    profile.save()                          │              │ POST {pick_mfa}
│                                            │              ▼
│  AFTER CHANGE:                             │  ┌──────────────────────────────────────────┐
│    IF MFA enabled → mfa_pick = True        │  │  8. SEND MFA CODE                        │
│    IF no MFA → direct login                │  │  views.py:423-444                        │
│                                            │  │                                          │
│  SESSION:                                  │  │  TOTP:                                   │
│    change_pw_user_id = user.pk             │  │    • Show 6-digit input                   │
│                                            │  │    • Wait for user to enter code          │
│  RENDER:                                   │  │                                          │
│    login.html with change_pw=True          │  │  EMAIL OTP:                              │
│    → Shows change password form            │  │    • generate_email_otp()                 │
│                                            │  │    • send_otp_email(user_email, otp)      │
│                                            │  │    • Store in session: email_otp_code     │
│                                            │  │    • OTP expires in 10 minutes            │
│                                            │  │    • Resend available via /mfa/resend-    │
│                                            │  │      email-otp/ endpoint                 │
│                                            │  └───────────────┬──────────────────────────┘
│                                            │                  │ POST {mfa_code}
│                                            │                  ▼
│                                            │  ┌──────────────────────────────────────────┐
│                                            │  │  9. VERIFY MFA CODE                      │
│                                            │  │  views.py:446-656                        │
│                                            │  │                                          │
│                                            │  │  THREE PATHS:                            │
│                                            │  │                                          │
│                                            │  │  A) RECOVERY CODE (use_recovery=1)      │
│                                            │  │     • Format: XXXX-XXXX-XXXX            │
│                                            │  │     • verify_recovery_code() against     │
│                                            │  │       profile.mfa_recovery_codes         │
│                                            │  │     • Remove used code from list         │
│                                            │  │     • Check concurrent session           │
│                                            │  │                                          │
│                                            │  │  B) TOTP CODE (method=totp)             │
│                                            │  │     • verify_totp(secret, code)          │
│                                            │  │     • If fails → check as recovery code  │
│                                            │  │     • Track failed attempts              │
│                                            │  │     • 3 failures → auto-send recovery    │
│                                            │  │       code to email                      │
│                                            │  │                                          │
│                                            │  │  C) EMAIL OTP (method=email)            │
│                                            │  │     • Compare with session email_otp_code│
│                                            │  │     • Check expiry (10 min)              │
│                                            │  │                                          │
│                                            │  │  ALL PATHS:                              │
│                                            │  │    • Check concurrent session first      │
│                                            │  │    • On success → login() + session      │
│                                            │  │    • On failure → show error             │
│                                            │  └───────────────┬──────────────────────────┘
│                                            │                  │
└────────────────────────────────────────────┘                  │
                                                                ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  10. CREATE SESSION & TRACK DEVICE                     │
                                │  views.py:305-348  (_register_session)                 │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ SESSION KEY MANAGEMENT:                          │  │
                                │  │   new_key = request.session.session_key          │  │
                                │  │   old_key = profile.active_session_key           │  │
                                │  │                                                  │  │
                                │  │   IF old_key != new_key:                        │  │
                                │  │     • Delete old Session from DB                 │  │
                                │  │     • Mark old UserDevice as terminated          │  │
                                │  │                                                  │  │
                                │  │   profile.active_session_key = new_key           │  │
                                │  │   profile.last_activity = now                    │  │
                                │  │   profile.status = 'active'                      │  │
                                │  │   profile.save()                                 │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ DEVICE TRACKING:                                 │  │
                                │  │   ip = _get_client_ip(request)                   │  │
                                │  │   dev_info = _get_client_device_and_os(request)  │  │
                                │  │     → device_name (computer hostname)             │  │
                                │  │     → windows_username (OS login)                │  │
                                │  │     → os_and_browser (Chrome/Windows, etc.)      │  │
                                │  │   loc = _get_location_from_ip(ip)                │  │
                                │  │     → via ip-api.com (city, country)             │  │
                                │  │                                                  │  │
                                │  │   UserDevice.objects.create(                     │  │
                                │  │     user, session_key, device_name,              │  │
                                │  │     windows_username, ip_address, location,      │  │
                                │  │     user_agent, last_activity                    │  │
                                │  │   )                                              │  │
                                │  └──────────────────────────────────────────────────┘  │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  11. ROLE-BASED ROUTING                                │
                                │  views.py:731-738  (_mfa_redirect)                     │
                                │                                                        │
                                │  role = user.profile.role                              │
                                │                                                        │
                                │  ┌──────────┐   ┌──────────┐   ┌──────────┐          │
                                │  │  ADMIN   │   │ CASHIER  │   │  GUEST   │          │
                                │  └────┬─────┘   └────┬─────┘   └────┬─────┘          │
                                │       │              │              │                  │
                                │       ▼              ▼              ▼                  │
                                │  admin_dashboard  cashier_dashboard  cashier_dashboard │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  12. DASHBOARD                                         │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ ADMIN DASHBOARD (views.py:2707)                  │  │
                                │  │                                                  │  │
                                │  │  Cheque Stats:                                   │  │
                                │  │    • Total / Draft / Pending / Released          │  │
                                │  │    • Voided / Stale                              │  │
                                │  │    • Total released amount (₱)                   │  │
                                │  │                                                  │  │
                                │  │  Today's Activity:                               │  │
                                │  │    • Created / Released / Amount                 │  │
                                │  │                                                  │  │
                                │  │  Analytics:                                      │  │
                                │  │    • RADAI stats (count + amount)                │  │
                                │  │    • Top 5 fund clusters by balance              │  │
                                │  │    • Avg processing speed (days)                 │  │
                                │  │    • 12-month trend sparkline                    │  │
                                │  │    • Cashier performance (top 5)                 │  │
                                │  │                                                  │  │
                                │  │  Alerts:                                         │  │
                                │  │    • Low fund balance (< ₱10,000)               │  │
                                │  │    • Stale cheques (> 7 days pending)            │  │
                                │  │                                                  │  │
                                │  │  API Endpoints:                                  │  │
                                │  │    • /api/admin-dashboard-data/                  │  │
                                │  │    • /api/admin-chart-released/                  │  │
                                │  │    • /api/admin-suppliers-chart/                 │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ CASHIER DASHBOARD (views.py:2933)                │  │
                                │  │                                                  │  │
                                │  │  My Cheques:                                     │  │
                                │  │    • Draft / Pending / Released / Voided / Stale │  │
                                │  │    • My released amount (₱)                      │  │
                                │  │    • Unreleased = draft + pending                │  │
                                │  │                                                  │  │
                                │  │  My Activity:                                    │  │
                                │  │    • Today created / released / amount           │  │
                                │  │                                                  │  │
                                │  │  My Data:                                        │  │
                                │  │    • Suppliers count                             │  │
                                │  │    • RADAI count + amount                        │  │
                                │  │    • Avg processing days                         │  │
                                │  │                                                  │  │
                                │  │  Alerts:                                         │  │
                                │  │    • Cheques pending > 7 days                    │  │
                                │  │    • Already stale cheques                       │  │
                                │  │                                                  │  │
                                │  │  Charts:                                         │  │
                                │  │    • 6-month trend (counts + amounts)            │  │
                                │  │    • Top suppliers chart                         │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ GUEST DASHBOARD                                  │  │
                                │  │    • Reports view only                           │  │
                                │  │    • Generate reports                            │  │
                                │  │    • No CRUD operations                          │  │
                                │  └──────────────────────────────────────────────────┘  │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  13. MODULE ACCESS & TASKS                             │
                                │                                                        │
                                │  Enforced by RolePermissionsMiddleware (middleware.py)  │
                                │  + _enforce_permission() in views                     │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ ADMIN MODULES (full access):                     │  │
                                │  │   • User Management — CRUD users, assign roles   │  │
                                │  │   • System Settings — name, logo, email, timeout │  │
                                │  │   • Account Titles — UACS codes, groups          │  │
                                │  │   • Taxes — VAT/withholding config               │  │
                                │  │   • Fund Clusters — balance, bank info           │  │
                                │  │   • Suppliers — CRUD, import CSV, export         │  │
                                │  │   • RADAI — CRUD, import CSV, export             │  │
                                │  │   • Cheques — CRUD, print, PDF, void, advance    │  │
                                │  │   • Reports — generate, print, export PDF        │  │
                                │  │   • Audit Log — view all actions                 │  │
                                │  │   • Manage Devices — block, terminate, delete    │  │
                                │  │   • Import Data — CSV import with preview        │  │
                                │  │   • Export — cheques, suppliers CSV              │  │
                                │  │   • Backup & Restore — DB backup/restore         │  │
                                │  │   • Chatbot Setup — AI assistant config          │  │
                                │  │   • Email Settings — SMTP config, test           │  │
                                │  │   • Role Permissions — configure per-role        │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ CASHIER MODULES (limited):                       │  │
                                │  │   • Dashboard                                    │  │
                                │  │   • Cheques — view, create, edit, print, void   │  │
                                │  │   • Fund Clusters — view only                    │  │
                                │  │   • Suppliers — view, create, edit, delete       │  │
                                │  │   • RADAI — view, create, edit, delete, export   │  │
                                │  │   • Reports — view, generate                     │  │
                                │  │   • Registry                                     │  │
                                │  │   • Profile Settings                             │  │
                                │  │   • System Settings (limited)                    │  │
                                │  │   • Account Titles                               │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ GUEST MODULES (reports only):                    │  │
                                │  │   • Dashboard                                    │  │
                                │  │   • Reports — view, generate                     │  │
                                │  │   • Profile Settings                             │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  PERMISSION CHECK:                                     │
                                │    URL → resolve url_name                             │
                                │    → URL_PERMISSION_MAP[url_name]                     │
                                │    → (nav_key, action_module, action_name)             │
                                │    → Check DEFAULT_ROLE_PERMISSIONS[user_role]         │
                                │    → PermissionDenied if not authorized               │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  14. SESSION MONITORING (Background Middleware)         │
                                │                                                        │
                                │  Three middleware layers run on EVERY request:        │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ A) SingleDeviceLoginMiddleware (middleware.py:215)│  │
                                │  │                                                  │  │
                                │  │  CHECKS:                                         │  │
                                │  │    • profile.active_session_key vs session_key   │  │
                                │  │    • UserDevice.is_terminated flag               │  │
                                │  │    • UserDevice.is_blocked flag                  │  │
                                │  │    • last_activity timestamp (10s rate limit)    │  │
                                │  │                                                  │  │
                                │  │  ON MISMATCH:                                    │  │
                                │  │    • Flush session                               │  │
                                │  │    • logout(request)                             │  │
                                │  │    • AJAX → JSON {kicked: true} (401)            │  │
                                │  │    • Normal → redirect /?kicked=1                │  │
                                │  │                                                  │  │
                                │  │  ON BLOCKED:                                     │  │
                                │  │    • AJAX → JSON {blocked: true} (403)           │  │
                                │  │    • Normal → device_blocked.html                │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ B) LockScreenMiddleware (middleware.py:22)        │  │
                                │  │                                                  │  │
                                │  │  TRIGGER:                                        │  │
                                │  │    session['screen_locked'] = True               │  │
                                │  │                                                  │  │
                                │  │  BEHAVIOR:                                       │  │
                                │  │    • AJAX → JSON {locked: true} (423)            │  │
                                │  │    • Normal → redirect /lockscreen/?next=<path>  │  │
                                │  │                                                  │  │
                                │  │  SKIP PATHS: /, /logout/, /lockscreen/,         │  │
                                │  │    /verify-password/, /lock/, /static/, /media/  │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ C) RolePermissionsMiddleware (middleware.py:59)   │  │
                                │  │                                                  │  │
                                │  │  CHECKS:                                         │  │
                                │  │    • Resolve URL name from path                  │  │
                                │  │    • Map to (nav_key, module, action)            │  │
                                │  │    • Superuser/admin: bypass                     │  │
                                │  │    • Check nav permission in role config         │  │
                                │  │    • Check action permission in role config      │  │
                                │  │                                                  │  │
                                │  │  ON DENIAL:                                      │  │
                                │  │    • Raise PermissionDenied                      │  │
                                │  └──────────────────────────────────────────────────┘  │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │ D) JS Heartbeat (base.html)                      │  │
                                │  │                                                  │  │
                                │  │  • Polls /session-check/ every N seconds         │  │
                                │  │  • Returns {ok: true} or {kicked: true}          │  │
                                │  │  • On kicked → redirect /?kicked=1               │  │
                                │  │  • On blocked → device_blocked page              │  │
                                │  └──────────────────────────────────────────────────┘  │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  15. LOCK SCREEN (Optional)                            │
                                │                                                        │
                                │  TRIGGERS:                                             │
                                │    • Idle timeout (auto_lock_seconds)                  │
                                │    • Manual lock via /lock/ endpoint                  │
                                │    • Admin terminates session                          │
                                │                                                        │
                                │  /lockscreen/ (views.py:9420)                         │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │                                                  │  │
                                │  │  SOFT LOCK:                                      │  │
                                │  │    • Just click/press key to dismiss             │  │
                                │  │    • No password required                        │  │
                                │  │                                                  │  │
                                │  │  HARD LOCK:                                      │  │
                                │  │    • Must enter password to unlock               │  │
                                │  │    • Verifies via /verify-password/             │  │
                                │  │    • On success → redirect to previous page      │  │
                                │  │    • On failure → "Wrong password!"              │  │
                                │  │                                                  │  │
                                │  │  CONTEXT:                                        │  │
                                │  │    • User profile picture                        │  │
                                │  │    • Lockscreen wallpaper (per-user)             │  │
                                │  │    • Auto-lock countdown timer                   │  │
                                │  └──────────────────────────────────────────────────┘  │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │  16. LOGOUT — /logout/                                 │
                                │  views.py:278-302                                      │
                                │                                                        │
                                │  ┌──────────────────────────────────────────────────┐  │
                                │  │  logout_view(request):                           │  │
                                │  │                                                  │  │
                                │  │  1. profile.status = 'resting'                   │  │
                                │  │     profile.save(update_fields=['status'])        │  │
                                │  │                                                  │  │
                                │  │  2. Delete user's ChatMessage records            │  │
                                │  │     ChatMessage.objects.filter(user=...).delete()│  │
                                │  │                                                  │  │
                                │  │  3. Mark UserDevice as terminated                │  │
                                │  │     UserDevice.objects.filter(                   │  │
                                │  │       session_key=...                            │  │
                                │  │     ).update(is_terminated=True)                 │  │
                                │  │                                                  │  │
                                │  │  4. Clear active session key                     │  │
                                │  │     profile.active_session_key = ''              │  │
                                │  │     profile.save(update_fields=[...])            │  │
                                │  │                                                  │  │
                                │  │  5. logout(request)                              │  │
                                │  │     → Django clears auth session                 │  │
                                │  │                                                  │  │
                                │  │  6. render(logout_loading.html)                  │  │
                                │  │     → Redirect animation page                    │  │
                                │  │     → Auto-redirect to / (login)                 │  │
                                │  └──────────────────────────────────────────────────┘  │
                                └────────────────────────┬───────────────────────────────┘
                                                         │
                                                         ▼
                                ┌────────────────────────────────────────────────────────┐
                                │                             ●  FLOWCHART END  ●         │
                                │                     User back at Login Page             │
                                └────────────────────────────────────────────────────────┘
```

---

## Sub-Flow: Forgot Password

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
                │  FIND USER                   │
                │  • If '@' in identifier:     │
                │    → _find_user_by_email()   │
                │      (email hash lookup)     │
                │  • Else:                     │
                │    → User.objects.get(       │
                │        username__iexact)     │
                └──────────┬───────┬───────────┘
                           │       │
                    NOT FOUND   FOUND
                           │       │
                           ▼       ▼
                ┌────────────┐  ┌──────────────────────┐
                │ Error:     │  │ Generate OTP code    │
                │ "No accnt  │  │ Send to user's email │
                │  found"    │  │ Store in session     │
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
                └──────────┬──────────┬────────────────┘
                           │          │
                      INVALID      VALID
                           │          │
                           ▼          ▼
                ┌────────────┐  ┌──────────────────────┐
                │ Error:     │  │ STEP 3: New password │
                │ "Invalid   │  │ Validate:            │
                │  code"     │  │ • 8+ chars           │
                └────────────┘  │ • Upper + lower      │
                                │ • Number + special   │
                                └──────────┬───────────┘
                                           │ POST {new_password}
                                           ▼
                                ┌──────────────────────┐
                                │ user.set_password()  │
                                │ user.save()          │
                                │ Clear session flags  │
                                │ Redirect → / (login) │
                                └──────────────────────┘
```

---

## Sub-Flow: Admin Device Management

```
┌─────────────────────────────────────────────────────────────────────┐
│  /manage-devices/  (views.py)                                       │
│  Admin-only access                                                  │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────┐
                │  View all UserDevice records          │
                │  Show: user, device, IP, location,    │
                │        status, last activity          │
                └──────────────┬───────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ Toggle Block     │ │ Terminate        │ │ Delete Device    │
│                  │ │ Session          │ │                  │
│ /toggle-device-  │ │                  │ │ /delete-device/  │
│ block/<pk>/      │ │ /terminate-      │ │ <pk>/            │
│                  │ │ session/<pk>/    │ │                  │
│ Sets is_blocked  │ │                  │ │ Removes record   │
│ = True/False     │ │ Sets is_terminated│ │ from DB          │
│                  │ │ = True           │ │                  │
│ User sees:       │ │                  │ │                  │
│ "Device blocked" │ │ User kicked:     │ │                  │
│ page on next     │ │ redirected to /  │ │                  │
│ request          │ │ ?kicked=1        │ │                  │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## Sub-Flow: MFA Setup (Per-User)

```
┌─────────────────────────────────────────────────────────────────────┐
│  /mfa/setup/  (views.py:9472)                                       │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────┐
                │  Current MFA Status:                  │
                │  • TOTP: enabled/disabled             │
                │  • Email OTP: enabled/disabled        │
                │  • Recovery codes: count remaining    │
                └──────────────┬───────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│ Enable TOTP      │ │ Enable Email OTP │ │ Regenerate       │
│                  │ │                  │ │ Recovery Codes   │
│ /mfa/totp-qr/    │ │ Sends OTP to    │ │                  │
│ → QR code image  │ │ email for       │ │ /mfa/regenerate- │
│ → Scan with      │ │ verification    │ │ recovery-codes/  │
│   authenticator  │ │                  │ │ → New set of     │
│ → Enter code     │ │ Toggle on/off   │ │   XXXX-XXXX-XXXX │
│   to verify      │ │                  │ │   codes          │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## Sub-Flow: Audit Trail

```
┌─────────────────────────────────────────────────────────────────────┐
│  AuditLog model (admin_panel/models.py)                             │
│  Logged on every significant action                                 │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────┐
                │  RECORD:                              │
                │    • action (create/edit/delete/void/ │
                │      advance/print/export/etc.)       │
                │    • model_name (Cheque/Supplier/etc) │
                │    • object_id                        │
                │    • user (who performed)             │
                │    • timestamp                        │
                │    • ip_address                       │
                │    • details (JSON - old/new values)  │
                └──────────────────────────────────────┘
                              │
                              ▼
                ┌──────────────────────────────────────┐
                │  VIEW: /audit-log/                    │
                │  • Filter by user, action, date       │
                │  • Search by model/object             │
                │  • Pagination                         │
                │  • Export capability                  │
                └──────────────────────────────────────┘
```

---

## Security Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  SECURITY LAYERS                                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  LAYER 1: Authentication                                            │
│    • EmailOrUsernameModelBackend                                    │
│    • SHA-256 email hash for fast lookup                             │
│    • Fernet encryption for sensitive fields                         │
│                                                                     │
│  LAYER 2: Device Blocking                                           │
│    • Admin can block by IP, device signature                        │
│    • Blocked devices cannot log in                                  │
│                                                                     │
│  LAYER 3: Single Device Enforcement                                 │
│    • Only one active session per user                               │
│    • New login terminates old session                               │
│                                                                     │
│  LAYER 4: MFA / 2FA                                                 │
│    • TOTP (Google Authenticator, etc.)                              │
│    • Email OTP (6-digit, 10-min expiry)                             │
│    • Recovery codes (XXXX-XXXX-XXXX)                                │
│    • Auto-recovery after 3 failed MFA attempts                      │
│                                                                     │
│  LAYER 5: Password Policy                                           │
│    • Min 8 characters                                               │
│    • Uppercase + lowercase + digit + special char                   │
│    • Force change on first login (must_change_password)             │
│                                                                     │
│  LAYER 6: Session Management                                        │
│    • Auto-lock on idle (configurable seconds)                       │
│    • Auto-logout on idle (configurable seconds)                     │
│    • Hard lock (password required) vs soft lock                     │
│                                                                     │
│  LAYER 7: Role-Based Access Control                                 │
│    • Admin: full access                                             │
│    • Cashier: limited modules + actions                             │
│    • Guest: reports only                                            │
│    • Enforced via middleware + view decorators                       │
│                                                                     │
│  LAYER 8: Data Encryption                                           │
│    • Account numbers (Fernet)                                       │
│    • Email addresses (Fernet)                                       │
│    • SHA-256 hashes for lookups                                     │
│                                                                     │
│  LAYER 9: Audit Trail                                               │
│    • All actions logged                                             │
│    • Timestamps + IP addresses                                      │
│    • Old/new value tracking                                         │
│                                                                     │
│  LAYER 10: Browser Security                                         │
│    • Disabled: right-click, F-keys, Ctrl+* shortcuts               │
│    • Disabled: copy, cut, paste, drag, drop                         │
│    • Disabled: text selection (except inputs)                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Code Reference Index

| Stage | File | Line(s) | Function/View |
|-------|------|---------|---------------|
| Login | `admin_panel/views.py` | 351-728 | `login_view()` |
| Auth Backend | `admin_panel/backends.py` | 9-31 | `EmailOrUsernameModelBackend.authenticate()` |
| Email Lookup | `admin_panel/backends.py` | 33-86 | `_find_user_by_email()` |
| Device Block Check | `admin_panel/views.py` | 224-239 | `_is_device_blocked()` |
| Session Active Check | `admin_panel/views.py` | 242-275 | `_is_session_active()` |
| Register Session | `admin_panel/views.py` | 305-348 | `_register_session()` |
| Role Redirect | `admin_panel/views.py` | 731-738 | `_mfa_redirect()` |
| Admin Dashboard | `admin_panel/views.py` | 2707-2932 | `admin_dashboard()` |
| Cashier Dashboard | `admin_panel/views.py` | 2933-3104 | `cashier_dashboard()` |
| Lock Screen | `admin_panel/views.py` | 9420-9468 | `lockscreen_view()` |
| Verify Password | `admin_panel/views.py` | 9399-9416 | `verify_password()` |
| Session Check | `admin_panel/views.py` | 1509-1548 | `session_check()` |
| Logout | `admin_panel/views.py` | 278-302 | `logout_view()` |
| Forgot Password | `admin_panel/views.py` | 741-830 | `forgot_password()` |
| MFA Setup | `admin_panel/views.py` | 9472+ | `mfa_setup()` |
| MFA Utils | `admin_panel/mfa_utils.py` | — | `verify_totp()`, `generate_email_otp()` |
| Lock Middleware | `admin_panel/middleware.py` | 22-53 | `LockScreenMiddleware` |
| Role Middleware | `admin_panel/middleware.py` | 59-212 | `RolePermissionsMiddleware` |
| Single Device MW | `admin_panel/middleware.py` | 215-303 | `SingleDeviceLoginMiddleware` |
| Profile Model | `cashier/models.py` | 305-363 | `Profile` |
| UserDevice Model | `cashier/models.py` | 366-382 | `UserDevice` |
| Role Permissions | `cashier/models.py` | 196-248 | `DEFAULT_ROLE_PERMISSIONS` |
| URL Routes | `admin_panel/urls.py` | 4-109 | `urlpatterns` |

---

*CAP System — Full System Flowchart — Generated from codebase analysis*
