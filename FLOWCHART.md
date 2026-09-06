# CAP System Flowcharts - Presentation Slides

---

## Slide 1: Title Slide

**Cashier and Payment (CAP) System**
Manual Process & System Flowchart

---

## Slide 2: Client Manual Process Flow

```
START
  │
  ▼
┌─────────────────────┐
│ Client submits       │
│ payment request      │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Cashier validates    │
│ documents            │
└──────────┬──────────┘
           ▼
┌─────────────────────┐     NO     ┌──────────────┐
│ Documents complete? │──────────▶│ Return for   │
└──────────┬──────────┘           │ correction   │
           │ YES                  └──────┬───────┘
           ▼                             │
┌─────────────────────┐                  │
│ Assign Fund Cluster │◀─────────────────┘
│ Check balance       │
└──────────┬──────────┘
           ▼
┌─────────────────────┐     NO     ┌──────────────┐
│ Sufficient balance? │──────────▶│ Request      │
└──────────┬──────────┘           │ fund alloc.  │
           │ YES                  └──────────────┘
           ▼
┌─────────────────────┐
│ Generate cheque #   │
│ Fill cheque form    │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Compute taxes       │
│ (VAT, Withholding)  │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Prepare ORS/BURS    │
│ DV/Payroll number   │
└──────────┬──────────┘
           ▼
┌─────────────────────┐     ┌──────────────┐
│ Submit for approval │◀───▶│ Supervisor   │
└──────────┬──────────┘     │ reviews      │
           ▼                └──────────────┘
┌─────────────────────┐
│ Print cheque        │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Record in logbook   │
└──────────┬──────────┘
           ▼
┌─────────────────────┐     YES    ┌──────────────┐
│ Cheque released?    │──────────▶│ Update ledger │
└──────────┬──────────┘           └──────────────┘
           │ NO
           ▼
┌─────────────────────┐
│ Void / stale cheque │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ Generate reports    │
└──────────┬──────────┘
           ▼
          END
```

---

## Slide 3: System Flowchart - Authentication

```
┌─────────────────────────────────────────────┐
│              AUTHENTICATION                  │
├─────────────────────────────────────────────┤
│                                             │
│   ┌─────────────────────────────────────┐   │
│   │  Login Page                         │   │
│   │  • Username + Password              │   │
│   └──────────────┬──────────────────────┘   │
│                  ▼                          │
│   ┌─────────────────────────────────────┐   │
│   │  MFA Verification                   │   │
│   │  • TOTP / Email OTP                 │   │
│   │  • RFID Card                        │   │
│   │  • Biometric (Face/Fingerprint)     │   │
│   └──────────────┬──────────────────────┘   │
│                  ▼                          │
│   ┌─────────────────────────────────────┐   │
│   │  Session Created                    │   │
│   │  • Device tracked                   │   │
│   │  • Single-device enforced           │   │
│   └─────────────────────────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
```

---

## Slide 4: System Flowchart - Role Routing

```
┌─────────────────────────────────────────────────────┐
│                 ROLE-BASED ROUTING                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│     │  ADMIN   │  │ CASHIER  │  │  GUEST   │      │
│     │ All      │  │ Cheques  │  │ Reports  │      │
│     │ modules  │  │ Suppliers│  │ only     │      │
│     │          │  │ RADAI    │  │          │      │
│     └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│          │              │              │            │
│          ▼              ▼              ▼            │
│     ┌─────────────────────────────────────────┐    │
│     │              DASHBOARD                  │    │
│     │  • Cheque counts by status              │    │
│     │  • Total amounts                        │    │
│     │  • Charts (released, suppliers)         │    │
│     └─────────────────────────────────────────┘    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Slide 5: System Flowchart - Cheque Workflow

```
┌─────────────────────────────────────────────────────┐
│            CHEQUE MANAGEMENT WORKFLOW               │
├─────────────────────────────────────────────────────┤
│                                                     │
│  CREATE:                                            │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐  │
│  │Select  │─▶│Auto #  │─▶│Fund    │─▶│Enter   │  │
│  │Payee   │  │Generate│  │Cluster │  │Amount  │  │
│  └────────┘  └────────┘  └────────┘  └────────┘  │
│       │                            │              │
│       ▼                            ▼              │
│  ┌─────────────────┐    ┌─────────────────────┐   │
│  │System computes  │    │Auto-generate:       │   │
│  │taxes            │    │• DV/Payroll No      │   │
│  │• VAT 5%/3%     │    │• ORS/BURS No        │   │
│  │• Withholding    │    └─────────────────────┘   │
│  │• Prof. Tax      │                              │
│  └─────────────────┘                              │
│                                                     │
│  STATUS TRANSITIONS:                                │
│  ┌────────┐     ┌────────┐     ┌────────┐         │
│  │ DRAFT  │────▶│PENDING │────▶│RELEASED│         │
│  └───┬────┘     └───┬────┘     └───┬────┘         │
│      │              │              │               │
│      ▼              ▼              ▼               │
│  ┌────────┐    ┌────────┐    ┌────────┐           │
│  │ VOIDED │    │ VOIDED │    │ STALE  │           │
│  └────────┘    └────────┘    └────────┘           │
│                                                     │
│  ACTIONS: Print │ PDF │ Void │ Advance Status      │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Slide 6: System Flowchart - Other Modules

```
┌─────────────────────────────────────────────────────┐
│              SUPPORTING MODULES                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌────────────────┐  ┌────────────────┐            │
│  │   SUPPLIERS    │  │     RADAI      │            │
│  │ • CRUD         │  │ • CRUD         │            │
│  │ • Import CSV   │  │ • Import CSV   │            │
│  │ • Export       │  │ • Export       │            │
│  │ • Track TIN    │  │ • ORS/BURS #   │            │
│  └────────────────┘  └────────────────┘            │
│                                                     │
│  ┌────────────────┐  ┌────────────────┐            │
│  │ FUND CLUSTER   │  │    REPORTS     │            │
│  │ • Balance      │  │ • Cheque Summary│           │
│  │ • Bank info    │  │ • RADAI Summary │           │
│  │ • Auto-deduct  │  │ • Weekly/Monthly│           │
│  └────────────────┘  │ • Annual        │           │
│                       │ • PDF export    │           │
│                       └────────────────┘            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Slide 7: Security Features

```
┌─────────────────────────────────────────────────────┐
│              SECURITY FEATURES                      │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                │
│  │     MFA      │  │   SESSION    │                │
│  │ • TOTP       │  │ • Auto-lock  │                │
│  │ • Email OTP  │  │ • Auto-logout│                │
│  │ • RFID       │  │ • Single     │                │
│  │ • Biometric  │  │   device     │                │
│  └──────────────┘  └──────────────┘                │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                │
│  │   ENCRYPT    │  │  AUDIT LOG   │                │
│  │ • Account #  │  │ • All actions│                │
│  │ • Sensitive  │  │ • Timestamps │                │
│  │   data       │  │ • User       │                │
│  └──────────────┘  └──────────────┘                │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐                │
│  │  PASSWORD    │  │   DEVICE     │                │
│  │ • 8+ chars   │  │ • Block      │                │
│  │ • Upper/lower│  │ • Terminate  │                │
│  │ • Numbers    │  │ • Monitor    │                │
│  │ • Special    │  │              │                │
│  └──────────────┘  └──────────────┘                │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Slide 8: System Administration

```
┌─────────────────────────────────────────────────────┐
│           SYSTEM ADMINISTRATION                     │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌────────────────────────────────────────────┐    │
│  │  USER MANAGEMENT                           │    │
│  │  • Create/Edit/Delete users                │    │
│  │  • Assign roles: Admin / Cashier / Guest   │    │
│  │  • Configure permissions per role          │    │
│  └────────────────────────────────────────────┘    │
│                                                     │
│  ┌────────────────────────────────────────────┐    │
│  │  SYSTEM SETTINGS                           │    │
│  │  • System name, logo, address              │    │
│  │  • Email/SMTP configuration                │    │
│  │  • Screen timeout settings                 │    │
│  │  • Lockscreen wallpaper                    │    │
│  └────────────────────────────────────────────┘    │
│                                                     │
│  ┌────────────────────────────────────────────┐    │
│  │  DATA MANAGEMENT                           │    │
│  │  • Import/Export (CSV)                     │    │
│  │  • Backup & Restore database               │    │
│  │  • Account titles management               │    │
│  │  • AI Chatbot setup                        │    │
│  └────────────────────────────────────────────┘    │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Slide 9: Data Flow Diagram

```
┌─────────────────────────────────────────────────────┐
│                 DATA FLOW DIAGRAM                   │
├─────────────────────────────────────────────────────┤
│                                                     │
│   ┌────────┐    ┌──────────┐    ┌──────────┐      │
│   │ CLIENT │───▶│   AUTH   │───▶│ DATABASE │      │
│   │(Browser)│   │(Login+MFA)│   │ (SQLite) │      │
│   └────────┘    └──────────┘    └─────┬────┘      │
│                                       │            │
│                                       ▼            │
│                               ┌──────────────┐    │
│                               │   CHEQUE     │    │
│                               │  MANAGEMENT  │    │
│                               └──────┬───────┘    │
│                                      │             │
│                    ┌─────────────────┼───────┐    │
│                    ▼                 ▼       ▼    │
│            ┌──────────┐  ┌──────────┐ ┌──────────┐
│            │SUPPLIERS │  │  FUND    │ │  RADAI   │
│            │          │  │ CLUSTER  │ │          │
│            └────┬─────┘  └────┬─────┘ └────┬─────┘
│                 │              │             │     │
│                 └──────────────┼─────────────┘     │
│                                ▼                   │
│                        ┌──────────────┐            │
│                        │   REPORTS    │            │
│                        │  Generate    │            │
│                        │  Export/PDF  │            │
│                        └──────────────┘            │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

*Cashier and Payment (CAP) System - Django + SQLite*
