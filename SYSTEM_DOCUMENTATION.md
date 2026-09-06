# Cashier and Payment (CAP) Registry System

A comprehensive financial management web application designed for government or institutional cashiering operations. It manages the end-to-end lifecycle of cheque processing, supplier payments, fund cluster balance tracking, RADAI record keeping, and financial reporting.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 6.0.4 (Python) |
| Database | MySQL (primary) / SQLite3 (fallback) |
| Frontend | HTML5, CSS3, JavaScript, Font Awesome, Inter + JetBrains Mono fonts |
| PDF Generation | ReportLab |
| Encryption | Fernet symmetric encryption |
| MFA | TOTP (pyotp), Email OTP |
| AI Chatbot | OpenAI-compatible API (13 providers) |
| Email | Django SMTP (dynamic database-configured backend) |

---

## System Features

### Cheque Management
- Create, edit, print, and void cheques
- PDF cheque generation with amount-to-words (Philippine peso format)
- Cheque status workflow: Draft → Pending → Released → Voided/Stale
- Sequential auto-numbering with gap prevention
- Tax computation (VAT 5%/3%, withholding tax 2%/1%, professional tax)
- DV/Payroll and ORS/BURS auto-numbering (YYYY-MM-NNNN format)
- Stale cheque tracking with configurable overdue alerts
- Check validator for release via lookup

### Supplier Management
- Full supplier CRUD with financial fields
- Account number encryption (Fernet)
- Auto-incrementing series, OR numbers, and code lines
- CSV import with column mapping and duplicate detection
- CSV export functionality
- Nature of collections auto-fill

### RADAI (Receipt, Audit, Disbursement, Accountability, Internal)
- RADAI record management with fund cluster linking
- ORS/BURS number tracking
- CSV import/export
- Configurable default settings

### Fund Cluster Management
- Fund cluster creation and balance tracking
- Bank name and account number (encrypted)
- Active/inactive status

### Reporting Engine
- Report types: Cheque Summary, RADAI Summary, Weekly/Monthly/Annual reports
- Date range and fund cluster filtering
- Data snapshots stored as JSON
- PDF export with ReportLab
- Print-ready HTML rendering
- Auto-numbered reports (YYYY-MM-NNNN)

---

## Authentication & Security

### Multi-Step Login Flow
1. Username/email + password authentication
2. Device block check
3. Concurrent session check (single-device enforcement)
4. MFA verification (if enabled)
5. Role-based redirect to dashboard

### Three User Roles

| Role | Access Level |
|---|---|
| **Admin** | Full access to all modules |
| **Cashier** | Operational access (cheques, suppliers, RADAI, reports) |
| **Guest** | Read-only (dashboard, reports, profile) |

### Multi-Factor Authentication (MFA)
- **TOTP**: Authenticator app with QR code generation
- **Email OTP**: 6-digit code, 10-minute expiry
- **Recovery codes**: 8 codes (XXXX-XXXX-XXXX format), one-time use
- 3 failed TOTP attempts auto-send recovery code to email

### Security Features
- Single-device session enforcement (only one active session per user)
- Lock screen with configurable idle timeout (10s–3600s)
- Auto-logout with configurable timeout (60s–86400s)
- Device blocking by IP, device name, or OS user
- Session monitoring via JavaScript heartbeat
- Password policy: 8+ chars, uppercase, lowercase, digit, special character
- Fernet encryption for sensitive fields (account numbers, emails)
- SHA-256 email hashing for fast lookups
- CSRF protection and X-Frame-Options middleware
- Full audit logging of all actions

### Biometric & RFID Authentication
- RFID card scanning and lookup
- Fingerprint template enrollment
- Face embedding enrollment (registration + verification)
- Voice sample enrollment

---

## AI Chatbot

- Supports 13 AI providers: OpenAI, Anthropic Claude, Google Gemini, Groq, DeepSeek, Mistral, xAI/Grok, OpenRouter, Together AI, Cohere, Hugging Face, Ollama (local), custom OpenAI-compatible
- Fetches live financial data (cheque counts, supplier counts, fund cluster counts) and injects into AI context
- Per-user chat history with configurable max context messages
- Admin-configurable system prompt, welcome message, model name, and API key

---

## System Administration

### User Management
- User CRUD with role assignment
- Profile pictures and department/position fields
- Status management (active, pending, resting, disabled, suspended)
- Disbursing officer designation

### Device Management
- View all active device sessions
- Block/unblock devices
- Terminate/restore active sessions remotely
- Screen capture storage (base64)
- Current URL tracking per device
- IP geolocation via ip-api.com

### System Settings
- System name, entity name, and address
- System logo (normal + hover state)
- Login background customization
- Lockscreen wallpaper (per-user or system-wide)
- Auto-lock and auto-lockout timeout configuration

### Email Settings
- Dynamic SMTP backend (reads config from database)
- Configurable host, port, TLS, credentials
- Test email sending functionality

### Backup & Restore
- Full backup as ZIP archive (database + media)
- Emergency backup before restore (rollback)
- Configurable retention policy (max age, max count, auto-delete)
- Cross-database restore (SQLite ↔ MySQL)

### Audit Logging
- All significant actions logged (login, logout, CRUD, config changes)
- JSON details with old/new values
- Filterable by user, action, date
- Paginated audit log viewer

### Role Permissions
- URL-to-permission mapping (116 URL patterns)
- Configurable navigation and action permissions per role
- Superuser/admin bypass

---

## Additional Features

### Dark Mode
- Full dark mode with CSS custom properties
- Persisted in localStorage
- System preference detection via `prefers-color-scheme`

### Peer-to-Peer Messaging
- User-to-user messaging system
- Real-time polling for new messages
- Conversation history with read/unread tracking

### Import/Export
- Supplier and RADAI CSV import with preview
- Account title import with group management
- Cheque, supplier, and RADAI CSV export

### Auto-Numbering Systems
| Sequence | Format | Scope |
|---|---|---|
| Cheque Numbers | 6-digit sequential | Global |
| DV/Payroll Numbers | YYYY-MM-NNNN | Monthly reset |
| ORS/BURS Numbers | 02-206441-YYYY-MM-NNNNN | Monthly reset |
| Report Numbers | YYYY-MM-NNNN | Monthly reset |

---

## Project Structure

```
cap/
├── registry/                  # Django project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py / asgi.py
├── admin_panel/               # Primary app (auth, admin, all views)
│   ├── models.py              # SystemSetting, AuditLog, ManagementOption, etc.
│   ├── views.py               # Main views (10,459 lines)
│   ├── cheque_views.py        # Cheque CRUD, PDF generation
│   ├── supplier_views.py      # Supplier and RADAI CRUD
│   ├── report_views.py        # Report generation and export
│   ├── import_export_views.py # CSV import, backup/restore
│   ├── settings_views.py      # System settings, email config
│   ├── dashboard_views.py     # Dashboards with chart data
│   ├── chatbot_views.py       # AI chatbot
│   ├── mfa_views.py           # MFA setup/disable
│   ├── auth_views.py          # RFID, biometric enrollment
│   ├── middleware.py           # Lock screen, role permissions, single device
│   ├── backends.py            # Email or username auth backend
│   ├── fields.py              # Encrypted model fields
│   ├── mfa_utils.py           # TOTP, recovery codes, email OTP
│   ├── email_backend.py       # Dynamic SMTP backend
│   ├── utils.py               # Tax calc, password validation, audit
│   ├── urls.py                # 116 URL patterns
│   ├── templatetags/          # Custom template tags
│   ├── db_backends/           # MySQL compatibility backend
│   └── management/commands/   # Custom management commands
├── cashier/                   # Core business models
│   ├── models.py              # Transaction, FundCluster, Supplier, Cheque, etc.
│   └── views.py
├── templates/                 # 50 HTML templates
│   ├── base.html              # Master layout (dark mode, sidebar, navbar)
│   ├── login.html             # Glassmorphism login page
│   ├── lockscreen.html        # Idle timeout lock screen
│   ├── admin_panel/           # 35 admin templates
│   └── cashier/               # 3 cashier templates
├── static/                    # CSS, fonts, webfonts
├── media/                     # User uploads (profile pictures, assets)
├── manage.py
└── requirements.txt
```

---

## Database Models

### Core Business Models (cashier app)
- **FundCluster** — Fund/account groups with balance tracking
- **Supplier** — Payee records with encrypted account numbers
- **Cheque** — Central entity with full lifecycle management
- **Radai** — Receipt/disbursement accountability records
- **Report** — Generated report records with JSON snapshots
- **Profile** — Extended user profile (role, MFA, biometrics, security)
- **UserDevice** — Device/session tracking with fingerprinting
- **RoleConfig** — Role permission configuration (JSON)

### System Models (admin_panel app)
- **SystemSetting** — Global configuration singleton
- **AuditLog** — Activity audit trail
- **ManagementOption** — Configurable options (account titles, taxes, remarks)
- **AccountTitleGroup** — Account title grouping
- **ChatbotConfig** — AI chatbot configuration
- **ChatMessage** — Chat history
- **UserMessage** — Peer-to-peer messaging

### Model Relationships
```
User (1) ──── (1) Profile
User (1) ──── (*) UserDevice
User (1) ──── (*) Cheque (created_by / updated_by)
User (1) ──── (*) Supplier (created_by)
User (1) ──── (*) Radai (created_by)
User (1) ──── (*) Report (generated_by)
Supplier (1) ──── (*) Cheque (payee)
FundCluster (1) ──── (*) Cheque
FundCluster (1) ──── (*) Radai
AccountTitleGroup (1) ──── (*) ManagementOption
```

---

## Middleware Stack

1. **LockScreenMiddleware** — Intercepts requests when screen is locked; redirects to lock screen or returns 423 for AJAX
2. **RolePermissionsMiddleware** — Maps URLs to permissions; checks against role config; admin/superuser bypass
3. **SingleDeviceLoginMiddleware** — Enforces single active session; detects terminated/blocked devices; rate-limits activity updates

---

## URL Patterns (116 total)

| Category | Key URLs |
|---|---|
| Authentication | `/`, `/logout/`, `/forgot-password/`, `/lockscreen/`, `/lock/` |
| Dashboards | `/admin-dashboard/`, `/cashier-dashboard/` |
| Cheques | `/cheques/`, `/cheques/new/`, `/cheques/<pk>/edit/`, `/cheques/<pk>/print/`, `/cheques/<pk>/pdf/` |
| Suppliers | `/suppliers/`, `/cashier/suppliers/`, `/export/suppliers/` |
| RADAI | `/radai/`, `/cashier/radai/`, `/export/radai/` |
| Fund Clusters | `/fund-clusters/` |
| Reports | `/reports/`, `/reports/<pk>/`, `/reports/export/pdf/` |
| User Management | `/user-management/` |
| System Settings | `/system-settings/`, `/email-settings/`, `/screen-timeout/` |
| MFA | `/mfa/setup/`, `/mfa/totp-qr/`, `/mfa/resend-email-otp/` |
| Chatbot | `/chatbot-setup/`, `/chatbot/message/`, `/chatbot/history/` |
| Audit | `/audit-log/` |
| Devices | `/manage-devices/`, `/toggle-device-block/<pk>/`, `/terminate-session/<pk>/` |
| Import/Export | `/import/`, `/export/cheques/`, `/export/suppliers/`, `/export/radai/` |
| About | `/about/`, `/admin-user-manual/`, `/cashier-user-manual/` |

---

## Deployment

The system supports deployment via:
- **ngrok** — For local development tunneling
- **Railway** — Cloud deployment platform
- **MySQL** — Recommended production database
- **SQLite3** — Development/fallback database

### Environment Notes
- Philippines timezone (UTC+8)
- Philippine peso currency formatting
- Government-specific financial concepts (UACS codes, DV/Payroll, ORS/BURS)

---

## License

Private — All rights reserved.
