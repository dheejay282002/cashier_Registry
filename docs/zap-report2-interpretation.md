# ZAP Report 2 — Interpretation & Discussion

**Scanner:** OWASP ZAP 2.17.0 (passive scan only)
**Target:** `http://127.0.0.1:8000` (Django 6.0.4 development server)
**Project:** `registry` (supplier/cashier collection system)
**Scan context:** unauthenticated, no ZAP context/session, no active tests

---

## 1. Report Profile

Report 2 is a **passive re-scan** run after the round-1 remediation work. It observed only
traffic that already existed (browser requests relayed through the ZAP proxy); ZAP did not
inject payloads, log in, or crawl with authentication. Consequently the report reflects
**header/cookie/policy posture and code hotspots**, not authenticated functional security.

- **10 alerts total: 0 High, 3 Medium, 3 Low, 4 Informational**
- **7 alerts** on `127.0.0.1:8000` (the application)
- **3 alerts** on `edge-consumer-static.azureedge.net` (not the application)

---

## 2. Quantitative Summary

### 2.1 Severity distribution

| Severity | Count |
|----------|-------|
| High     | 0     |
| Medium   | 3     |
| Low      | 3     |
| Info     | 4     |
| **Total**| **10**|

### 2.2 Host split

| Host | Alerts | In scope? |
|------|--------|-----------|
| `http://127.0.0.1:8000` | 7 | Yes |
| `https://edge-consumer-static.azureedge.net` | 3 | No |

### 2.3 Alert inventory and dispositions

The 7 in-scope alerts have known severities (2 Medium CSP, 2 Low static-header, 3 Info).
The 3 out-of-scope CDN alerts therefore carry the remaining Medium/Low/Info slots
(one each) — exact per-alert severities are as printed in the ZAP report.

| # | Alert | Severity | Host | Disposition |
|---|-------|----------|------|-------------|
| 1 | Content-Security-Policy: `script-src` includes `'unsafe-inline'` | Medium | 127.0.0.1 | **Accepted & documented** |
| 2 | Content-Security-Policy: `style-src` includes `'unsafe-inline'` | Medium | 127.0.0.1 | **Accepted & documented** |
| 3 | Server banner leaked in response header | Low | 127.0.0.1 `/static/*` | **Fixed after scan** |
| 4 | `X-Content-Type-Options: nosniff` missing | Low | 127.0.0.1 `/static/*` | **Fixed after scan** |
| 5 | Permissive CORS (`Access-Control-Allow-Origin: *`) | (1× Medium/Low/Info) | edge-consumer-static.azureedge.net | **Out of scope** |
| 6 | HSTS header not set | (see #5 split) | edge-consumer-static.azureedge.net | **Out of scope** |
| 7 | Cache-Control missing on static assets | (see #5 split) | edge-consumer-static.azureedge.net | **Out of scope** |
| 8 | Modern Web App detected | Info | 127.0.0.1 | **No action** (fingerprinting note only) |
| 9 | Session Management mechanism identified | Info | 127.0.0.1 | **No action** (informational) |
| 10 | Possible XSS hotspot (`step=verify_code`) | Info | 127.0.0.1 | **False positive** (see §3.4) |

---

## 3. Alert-by-Alert Analysis

### 3.1 CSP `'unsafe-inline'` for script-src and style-src — Medium — ACCEPTED

**Finding.** The CSP served by `SecurityHeadersMiddleware`
(`admin_panel/middleware.py`, `CONTENT_SECURITY_POLICY`) allows `'unsafe-inline'` in both
`script-src` and `style-src`, which weakens XSS containment.

**Why accepted.** Removing it requires refactoring a large legacy code base:

- **413 inline event handlers** across **35 templates** must be converted to
  `addEventListener` bindings;
- **1,731 `style=` attributes** and **59 `<style>` blocks** are covered by the
  `style-src` token.

**Risk statement (for documentation).** `'unsafe-inline'` in `script-src` degrades CSP
from an XSS backstop to a transport-hardening header. The residual risk is mitigated by
Django's auto-escaping of reflected/template values (verified for the one genuinely
reflected parameter, `identifier`) plus the existing cookie/transport hardening
(`HttpOnly`, `SameSite=Lax`, nosniff, no Server banner). The tokens are scoped so that
future hardening can drop `script-src 'unsafe-inline'` template-by-template and finally
delete it in one controlled change.

**Decision:** accepted for this release; revisit when the inline-handler refactor is scheduled.

### 3.2 Server banner + nosniff on `/static/*` — Low — FIXED

**Finding (at scan time).** Django's static-files `runserver` handler emitted
`Server: Django/6.0.4.dev...` and no `X-Content-Type-Options` on static responses. The
application-level middleware could not intercept these because static files bypassed it.

**Fix.** Custom command `admin_panel/management/commands/runserver.py`:

- subclasses `staticfiles`' `runserver`, and
- wraps the WSGI app in `StaticSecurityHeaders`, which rewrites `start_response` to force
  `Server: ""`, add `X-Content-Type-Options: nosniff`, and add the CSP to static responses;
- `'admin_panel'` was moved **before** `'django.contrib.staticfiles'` in `INSTALLED_APPS`
  (`registry/settings.py`) so Django's command registry resolves `runserver` to the custom
  command (`get_commands()` iterates apps in reverse order; first definition wins).

**Verification.** Raw TCP probes against ports 8011 and 8000 confirmed `/` and
`/static/fonts/inter-500.css` both return an empty `Server`, `nosniff`, and the CSP;
`manage.py check` clean; the 31-assertion regression suite passes.

> Note: these two alerts are artifacts of being scanned on the **development** server; a
> production WSGI/ASGI server would need the equivalent headers configured anyway.

### 3.3 `edge-consumer-static.azureedge.net` alerts — OUT OF SCOPE

All three alerts (CORS `*`, HSTS, Cache-Control) originate from an **Edge browser
mouse-gesture extension** fetching resources from `edge-consumer-static.azureedge.net`
through ZAP's proxy. They are not application assets and were never requested by the
app.

**Action:** for future scans, exclude the host from the ZAP context (or scan with a
context scoped to `http://127.0.0.1:8000` only) so CDN/extension noise does not appear
in the report.

### 3.4 XSS hotspot on `step=verify_code` — Info — FALSE POSITIVE

**Trigger.** ZAP POSTed `step=verify_code&identifier=&reset_code=ZAP` against the
forgot-password endpoint; the passive scanner flagged the value `verify_code` inside the
rendered page.

**Evidence.** The flagged occurrence is the **hardcoded** template input
`<input type="hidden" name="step" value="verify_code">` in
`templates/forgot_password.html` (~line 336) — a literal, not user input. The only
genuinely reflected value, `identifier` (`admin_panel/views.py` `forgot_password`,
step `'1'`), is emitted through Django's auto-escaping. Reported XSS payloads do not
execute; no active scan was run to confirm beyond static inspection.

**Decision:** no code change; retain evidence in this document.

### 3.5 Informational alerts — NO ACTION

- **Modern Web App detected** — fingerprinting observation (SPA-style behavior), not a
  vulnerability.
- **Session Management identified** — ZAP noting that session cookies exist; expected
  (`csrftoken`, session cookie with `HttpOnly; SameSite=Lax`).

---

## 4. Report 1 → Report 2 Comparison

Round-1 findings were remediated and re-verified before this scan. Wire evidence from
`GET /` in Report 2's traffic confirms every round-1 fix is live:

| Control | Round 1 | Round 2 evidence |
|---|---|---|
| Content-Security-Policy | absent | present (served by middleware) |
| `Server` header | `Django/…` / `BaseHTTP` | empty |
| `X-Content-Type-Options` | absent | `nosniff` |
| `csrftoken` cookie | readable by JS | `HttpOnly; SameSite=Lax` |
| Referrer-Policy | absent | present |
| Cross-Origin-Opener-Policy | absent | present |

Round-1 template work (11 templates refactored to a meta-tag-based `getCsrfToken()`
helper so JS no longer reads the `csrftoken` cookie directly) remains intact — the
rendered-JS checks pass.

**Net result:** no round-1 alert class reappeared; Report 2 contains only items that are
either post-scan fixed (static headers), risk-accepted (CSP tokens), out of scope (CDN),
or informational/false-positive.

---

## 5. ZAP Insights Interpretation

| Insight | Reading |
|---|---|
| ~92% of requests "slow" | **wsgiref single-threaded dev server** under a proxy — dev-tooling artifact, not application latency. Measure against the real deployment server. |
| Numerous `403` responses | ZAP's **CSRF-negative passive tests** (requests deliberately stripped/modified) rejected by Django's CSRF middleware — evidence the protection works. |
| 47 warnings / 4 errors in `zap.log` | Proxy/connection noise (browser extension requests, interrupted streams) — **no scanner failures**; the crawl completed normally. |

---

## 6. Scan Limitations

1. **Passive only** — no payload injection, no fuzzing; injection/authz/IDOR classes are
   untested by this report.
2. **Unauthenticated** — only **12 endpoints** were observed; all role-gated screens
   (admin panel, cashier, system settings, backups) were never crawled.
3. **No ZAP context** — no logged-in session, no authenticated scanning rules.
4. **Development target** — Django dev server + `DEBUG = True`
   (`registry/settings.py:34`). Static-response headers are now covered by the custom
   `runserver` command, but `DEBUG` must be switched off (with an explicit ask) and a
   production server used before any deployment-facing assessment.
5. **Environment noise** — the browser's Edge extension contributed 3 alerts (§3.3).

---

## 7. Conclusion

- **Zero High-severity findings.**
- Every **actionable, in-scope** finding from Report 2 is either **fixed** (2 Lows:
  Server banner and nosniff on static responses) or **explicitly risk-accepted with
  documented rationale** (2 Mediums: CSP `'unsafe-inline'` tokens pending a large
  frontend refactor).
- Remaining alerts are **out of scope** (3 CDN/extension), **informational** (2), or a
  **demonstrated false positive** (1 XSS hotspot).
- The report's value is primarily as **regression evidence** for the round-1 hardening and
  as a baseline for the recommended authenticated, context-scoped re-scan.

---

## 8. Recommended Follow-ups

1. **Authenticated, context-scoped ZAP re-scan** — log in as admin and cashier, define a
   ZAP context for `http://127.0.0.1:8000` only, exclude
   `edge-consumer-static.azureedge.net`, and enable active scanning on a test account.
2. **CSP hardening backlog** — convert inline handlers to `addEventListener`, replace
   `style=` attributes with classes, then drop `'unsafe-inline'` from `script-src`
   (nonces or hashes) followed by `style-src`.
3. **Production posture** — `DEBUG = False`, real WSGI/ASGI server, and
   `Secure`/`HSTS` settings before any internet-facing deployment.
4. **Regression harness** — keep
   `C:\Users\acer\AppData\Local\Temp\opencode\test_zap_remediation.py` (31 assertions)
   or promote it into `admin_panel/tests.py` so header/cookie posture stays verified in CI.

---

*Reconstructed from the Report 2 analysis session (28–29 Oct 2026). Severities for the
three out-of-scope CDN alerts are reported collectively; consult the raw ZAP report for
their individual severity labels.*
