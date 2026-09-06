import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Sum, Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai, RadaiSetting, DEFAULT_ROLE_PERMISSIONS
from admin_panel.models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup


# ─────────────────────────────────────────────
# PASSWORD VALIDATION
# ─────────────────────────────────────────────

def _validate_password(password):
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return "Password must contain at least one uppercase letter."
    if not re.search(r'[a-z]', password):
        return "Password must contain at least one lowercase letter."
    if not re.search(r'[0-9]', password):
        return "Password must contain at least one number."
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>\-_=+\[\]\\;\'`~]', password):
        return "Password must contain at least one special character (!@#$%^&* etc.)."
    return None


# ─────────────────────────────────────────────
# TAX CALCULATION
# ─────────────────────────────────────────────

def _get_tax_rates(rate_str):
    vat_rate = Decimal('0.00')
    nv_rate = Decimal('0.00')
    s = str(rate_str or '').strip().replace('%', '')
    if not s:
        return vat_rate, nv_rate
    if '/' in s:
        parts = s.split('/')
        if len(parts) == 2:
            try:
                vat_rate = Decimal(parts[0].strip()) / Decimal('100')
            except (InvalidOperation, ValueError):
                pass
            try:
                nv_rate = Decimal(parts[1].strip()) / Decimal('100')
            except (InvalidOperation, ValueError):
                pass
    else:
        try:
            val = Decimal(s) / Decimal('100')
            vat_rate = val
            nv_rate = val
        except (InvalidOperation, ValueError):
            pass
    return vat_rate, nv_rate


def compute_bucket_taxes(net_amount, pro_rate=Decimal('0')):
    net_amount = Decimal(str(net_amount or '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    pro_rate = Decimal(pro_rate or '0')

    try:
        active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
        t1 = active_taxes[0] if len(active_taxes) > 0 else None
        t2 = active_taxes[1] if len(active_taxes) > 1 else None
        t1_rate = t1.uacs if t1 else '5%/3%'
        t2_rate = t2.uacs if t2 else '2%/1%'
    except Exception:
        t1_rate = '5%/3%'
        t2_rate = '2%/1%'

    t1_vat, t1_nv = _get_tax_rates(t1_rate)
    t2_vat, t2_nv = _get_tax_rates(t2_rate)

    denom = Decimal('1.12') - (t1_vat + t2_vat + pro_rate)
    if denom <= 0:
        denom = Decimal('1.12')

    gross = (net_amount * Decimal('1.12') / denom).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    base = (gross / Decimal('1.12')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    tax_5 = (base * t1_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tax_2 = (base * t2_vat).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    professional_tax = (net_amount * pro_rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    return {
        'gross': gross,
        'tax_5': tax_5,
        'tax_2': tax_2,
        'professional_tax': professional_tax,
        'total_tax': (tax_5 + tax_2 + professional_tax).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
    }


# ─────────────────────────────────────────────
# CORE HELPERS
# ─────────────────────────────────────────────

def _is_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile, _ = Profile.objects.get_or_create(user=user, defaults={"role": "cashier"})
    return profile.role == "admin"


def _get_profile(user):
    profile, _ = Profile.objects.get_or_create(
        user=user,
        defaults={"role": "admin" if user.is_superuser else "cashier"},
    )
    return profile


def _get_certification_name(user, report=None):
    if report and not report.generated_by:
        if report.data_snapshot and isinstance(report.data_snapshot, dict):
            snap_cert = report.data_snapshot.get('certification_name')
            if snap_cert:
                return snap_cert
            snap_name = report.data_snapshot.get('generated_by_name')
            if snap_name:
                return snap_name
        return "DEE JAY CRISTOBAL"

    officer_profile = Profile.objects.filter(is_disbursing_officer=True).first()
    u = officer_profile.user if officer_profile else user

    if not u:
        return ""
    first = u.first_name.strip()
    last = u.last_name.strip()

    if u.username == 'admindeejay':
        if first.upper() == 'CRISTOBAL':
            first, last = last, first

    middle = ""
    if hasattr(u, 'profile') and u.profile and u.profile.middle_initial:
        mi = u.profile.middle_initial.strip()
        if mi:
            if not mi.endswith('.'):
                mi += '.'
            middle = mi

    parts = []
    if first:
        parts.append(first)
    if middle:
        parts.append(middle)
    if last:
        parts.append(last)

    return " ".join(parts) or u.get_full_name() or u.username


def _enforce_permission(request, nav_key=None, module=None, action=None):
    if not request.user.is_authenticated:
        raise PermissionDenied("You must be logged in.")
    if request.user.is_superuser:
        return
    profile = _get_profile(request.user)
    if profile.role == "admin":
        return

    role_config = DEFAULT_ROLE_PERMISSIONS.get(profile.role, {"navigation": [], "permissions": {}})

    if nav_key:
        allowed_nav = role_config.get("navigation", [])
        if nav_key not in allowed_nav:
            raise PermissionDenied("You do not have access to this page.")

    if module and action:
        allowed_actions = role_config.get("permissions", {}).get(module, [])
        if action not in allowed_actions:
            raise PermissionDenied("You do not have permission to perform this action.")


def _page_context(request, is_admin, page_title, extra=None):
    profile = _get_profile(request.user)
    role_permissions = {}
    if profile:
        role_permissions = DEFAULT_ROLE_PERMISSIONS.get(profile.role, {"navigation": [], "permissions": {}})
    context = {
        "is_admin": is_admin,
        "page_title": page_title,
        "current_profile": profile,
        "role_permissions": role_permissions,
    }
    if extra:
        context.update(extra)
    return context


def _full_name_or_username(user):
    full_name = f"{user.first_name} {user.last_name}".strip()
    return full_name or user.username


# ─────────────────────────────────────────────
# NUMBER TO WORDS
# ─────────────────────────────────────────────

def _number_to_words(n: int) -> str:
    ones = ['', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE',
            'TEN', 'ELEVEN', 'TWELVE', 'THIRTEEN', 'FOURTEEN', 'FIFTEEN', 'SIXTEEN', 'SEVENTEEN', 'EIGHTEEN', 'NINETEEN']
    tens = ['', '', 'TWENTY', 'THIRTY', 'FORTY', 'FIFTY', 'SIXTY', 'SEVENTY', 'EIGHTY', 'NINETY']

    def below1000(x):
        if x == 0:
            return ''
        if x < 20:
            return ones[x] + ' '
        if x < 100:
            return tens[x // 10] + ((' ' + ones[x % 10]) if x % 10 else '') + ' '
        return ones[x // 100] + ' HUNDRED ' + below1000(x % 100)

    if n == 0:
        return 'ZERO'
    parts = []
    if n >= 1000000000000:
        parts.append(below1000(n // 1000000000000) + 'TRILLION')
        n = n % 1000000000000

    if n >= 1000000000:
        parts.append(below1000(n // 1000000000) + 'BILLION')
        n = n % 1000000000

    if n >= 1000000:
        parts.append(below1000(n // 1000000) + 'MILLION')
        n = n % 1000000
    if n >= 1000:
        parts.append(below1000(n // 1000) + 'THOUSAND')
        n = n % 1000
    if n > 0:
        parts.append(below1000(n))
    return ' '.join(p.strip() for p in parts).replace('  ', ' ').strip()


def _amount_to_words_py(amount: Decimal) -> str:
    try:
        amt = Decimal(amount).quantize(Decimal('0.01'))
    except Exception:
        return ''
    pesos = int(amt // 1)
    centavos = int((amt - pesos) * 100)
    pesos_text = _number_to_words(pesos) + ' PESOS'
    return f"{pesos_text} & {centavos:02d}/100 ONLY".upper()


# ─────────────────────────────────────────────
# MONEY / CHEQUE HELPERS
# ─────────────────────────────────────────────

def _parse_money(value, fallback=Decimal('0')):
    try:
        clean_val = str(value or '0').replace(',', '').replace('₱', '').replace('\u20B1', '').strip()
        return Decimal(clean_val).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(fallback).quantize(Decimal('0.01'))


def _fund_balance_error(fund_cluster, amount):
    if not fund_cluster:
        return "Please select a fund cluster before releasing the cheque."
    if amount <= 0:
        return "Cheque amount must be greater than zero."
    return None


def _adjust_fund_balance(fund_cluster, delta):
    pass


def _cheque_is_released(cheque):
    return cheque and cheque.status == 'released'


def _cheque_action_block_reason(cheque):
    if not cheque:
        return None
    if not cheque.fund_cluster_id:
        return None
    return _fund_balance_error(cheque.fund_cluster, cheque.amount)


# ─────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────

def _audit(user, action, details=None):
    try:
        AuditLog.objects.create(admin=user, action=action, details=details or {})
    except Exception:
        pass


# ─────────────────────────────────────────────
# SUPPLIER IMPORT HELPERS
# ─────────────────────────────────────────────

SUPPLIER_IMPORT_COLUMNS = [
    ('mr_or', 'Check Serial'),
    ('date', 'Check Date'),
    ('or_number', 'OR Number'),
    ('series_day', 'Series/Day'),
    ('dv_payroll', 'DV/Payroll No.'),
    ('ors_burs', 'ORS/BURS No.'),
    ('responsibility_center', 'Responsibility Center'),
    ('uacs', 'UACS Object Code'),
    ('account_name', 'Payee'),
    ('nature_of_collections', 'Nature of Payments'),
    ('gross_amount', 'Gross Amount'),
    ('professional_tax', 'Professional Tax'),
    ('tax_5', 'Tax (5%/3%)'),
    ('tax_2', 'Tax (2%/1%)'),
    ('amount', 'Net Amount'),
    ('fund_cluster', 'Fund Cluster'),
    ('remarks', 'Remarks'),
    ('other_deductions', 'Other Deductions'),
]


RADAI_IMPORT_COLUMNS = [
    ('date', 'Date'),
    ('mr_or', 'Serial No.'),
    ('dv_payroll', 'DV/Payroll No.'),
    ('reference_code', 'ORS/BURS No.'),
    ('responsibility_center', 'Responsibility Center Code'),
    ('account_name', 'Payee'),
    ('uacs', 'UACS Object Code'),
    ('nature_of_collections', 'Nature of Payment'),
    ('amount', 'Amount'),
]


def _supplier_import_headers():
    return [label for _, label in SUPPLIER_IMPORT_COLUMNS]


def _radai_import_headers():
    return [label for _, label in RADAI_IMPORT_COLUMNS]


# ─────────────────────────────────────────────
# SUPPLIER AUTO DEFAULTS
# ─────────────────────────────────────────────

def _compute_auto_supplier_defaults(user, global_scope=False):
    today = timezone.localdate()

    def _series_value(text):
        txt = str(text or '').strip()
        if not txt or not txt.isdigit():
            return None
        try:
            return int(txt)
        except Exception:
            return None

    series_qs = Supplier.objects.all() if global_scope else Supplier.objects.filter(created_by=user)
    existing_series = [_series_value(v) for v in series_qs.exclude(series_month='').values_list('series_month', flat=True)]
    existing_numbers = [v for v in existing_series if v is not None]
    next_series_month = (max(existing_numbers) if existing_numbers else series_qs.count()) + 1
    existing_count = series_qs.filter(created_at__date=today).count()

    auto_supplier_series_month = str(next_series_month)
    auto_supplier_series_day = str(existing_count + 1)
    today_supplier_date = today.isoformat()

    def _split_noc_pair(code_value, nature_value=''):
        code_text = str(code_value or '').strip()
        nature_text = str(nature_value or '').strip()
        if code_text and '-' in code_text:
            prefix, suffix = code_text.split('-', 1)
            prefix = prefix.strip()
            suffix = suffix.strip()
            if prefix.isdigit():
                code_text = prefix
                if suffix and not nature_text:
                    nature_text = suffix
        return code_text, nature_text

    def _latest_non_empty(field_name):
        return series_qs.exclude(**{field_name: ''}).order_by('-created_at', '-pk').values_list(field_name, flat=True).first() or ''

    def _next_trailing_number(value):
        txt = str(value or '').strip()
        if not txt:
            return ''
        parts = txt.split('-')
        if len(parts) == 3:
            a, b, c = parts
            if b.strip().isdigit():
                try:
                    return f"{a}-{int(b.strip()) + 1}-{c}"
                except Exception:
                    pass
        if '-' in txt:
            prefix, suffix = txt.rsplit('-', 1)
            if suffix.isdigit():
                try:
                    return f"{prefix}-{int(suffix) + 1}"
                except Exception:
                    pass
        m = re.search(r"(.*?)(\d+)$", txt)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            return f"{prefix}{num + 1}"
        return txt + '-1'

    def _next_or_number_value():
        existing_or_numbers = [str(v).strip() for v in series_qs.exclude(or_number='').values_list('or_number', flat=True) if str(v).strip()]
        if not existing_or_numbers:
            return ''

        def _or_sort_key(value):
            text = str(value or '').strip()
            digits = re.findall(r'\d+', text)
            if digits:
                try:
                    return (0, int(digits[-1]), text)
                except Exception:
                    pass
            return (1, text.lower(), text)

        highest_or_number = max(existing_or_numbers, key=_or_sort_key)
        return _next_trailing_number(highest_or_number)

    latest_noc_supplier = series_qs.filter(code_noc__gt='').order_by('-created_at', '-pk').first()
    default_code_noc = ''
    default_nature_of_collections = ''
    if latest_noc_supplier:
        default_code_noc, default_nature_of_collections = _split_noc_pair(latest_noc_supplier.code_noc, latest_noc_supplier.nature_of_collections)
        if not default_nature_of_collections:
            default_nature_of_collections = latest_noc_supplier.nature_of_collections or ''
        default_code_noc = _next_trailing_number(default_code_noc)

    date_qs = Supplier.objects.filter(date=today) if global_scope else Supplier.objects.filter(created_by=user, date=today)
    day_count = date_qs.count()
    code_or2_default = f"{today.month}-{today.day}-{day_count + 1}"

    auto_supplier_defaults = {
        'mr_or': _next_trailing_number(_latest_non_empty('mr_or')) if series_qs.exists() else '',
        'mr': today.strftime('%Y-%b-%d'),
        'code_or': _next_trailing_number(_latest_non_empty('code_or')) if series_qs.exists() else '',
        'code_or2': code_or2_default,
        'or_number': _next_or_number_value(),
        'code_line': _latest_non_empty('code_line'),
        'line': _latest_non_empty('line'),
        'code_noc': default_code_noc,
        'nature_of_collections': default_nature_of_collections,
    }

    last_code_line = auto_supplier_defaults.get('code_line', '')
    next_code_line = _next_trailing_number(last_code_line) if last_code_line else ''
    auto_supplier_defaults['code_line'] = next_code_line or last_code_line

    last_line = auto_supplier_defaults.get('line', '')
    try:
        next_line = str(int(last_line) + 1) if str(last_line).strip().isdigit() else last_line
    except Exception:
        next_line = last_line
    auto_supplier_defaults['line'] = next_line

    ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, value='Tax (3%/1%)').update(value='Tax (2%/1%)', uacs='2%/1%')
    active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
    t1 = active_taxes[0] if len(active_taxes) > 0 else None
    t2 = active_taxes[1] if len(active_taxes) > 1 else None

    tax_1_rate = t1.uacs if t1 else '5%/3%'
    tax_2_rate = t2.uacs if t2 else '2%/1%'
    tax_1_label = t1.value if t1 else 'Tax (5%/3%)'
    tax_2_label = t2.value if t2 else 'Tax (2%/1%)'

    return {
        'auto_supplier_series_month': auto_supplier_series_month,
        'auto_supplier_series_day': auto_supplier_series_day,
        'today_supplier_date': today_supplier_date,
        'auto_supplier_defaults': auto_supplier_defaults,
        'tax_1_rate': tax_1_rate,
        'tax_2_rate': tax_2_rate,
        'tax_1_label': tax_1_label,
        'tax_2_label': tax_2_label,
    }


# ─────────────────────────────────────────────
# SUPPLIER ENRICHMENT
# ─────────────────────────────────────────────

def _sync_suppliers_by_uacs(supplier):
    if not supplier.uacs_object_code:
        return
    Supplier.objects.filter(uacs_object_code=supplier.uacs_object_code).exclude(pk=supplier.pk).update(
        nature_of_collections=supplier.nature_of_collections,
        responsibility_center=supplier.responsibility_center,
    )


def _enrich_supplier_for_display(supplier):
    return {
        'pk': supplier.pk,
        'account_name': supplier.account_name,
        'date': supplier.date,
        'mr_or': supplier.mr_or,
        'or_number': supplier.or_number,
        'series_day': supplier.series_day,
        'series_month': supplier.series_month,
        'dv_payroll_no': supplier.dv_payroll_no,
        'ors_burs_no': supplier.ors_burs_no,
        'responsibility_center': supplier.responsibility_center,
        'uacs_object_code': supplier.uacs_object_code,
        'nature_of_collections': supplier.nature_of_collections,
        'gross_amount': supplier.gross_amount,
        'professional_tax': supplier.professional_tax,
        'tax_5_3': supplier.tax_5_3,
        'tax_3_1': supplier.tax_3_1,
        'amount': supplier.amount,
        'fund_cluster': supplier.fund_cluster,
        'remarks': supplier.remarks,
        'other_deductions': supplier.other_deductions,
        'status': supplier.status,
        'created_by': supplier.created_by,
        'created_at': supplier.created_at,
        'tin_number': supplier.tin_number,
        'contact_info': supplier.contact_info,
        'bank_name': supplier.bank_name,
        'account_number': supplier.account_number,
        'code_noc': supplier.code_noc,
        'code_or': supplier.code_or,
        'code_or2': supplier.code_or2,
        'code_line': supplier.code_line,
        'line': supplier.line,
        'mr': supplier.mr,
    }


def _enrich_radai_for_display(radai):
    return {
        'pk': radai.pk,
        'date': radai.date,
        'mr_or': radai.mr_or,
        'dv_payroll_no': radai.dv_payroll_no,
        'ors_burs_no': radai.ors_burs_no,
        'reference_code': radai.reference_code,
        'responsibility_center': radai.responsibility_center,
        'account_name': radai.account_name,
        'uacs_object_code': radai.uacs_object_code,
        'nature_of_collections': radai.nature_of_collections,
        'amount': radai.amount,
        'remarks': radai.remarks,
        'status': radai.status,
        'created_by': radai.created_by,
        'created_at': radai.created_at,
    }


# ─────────────────────────────────────────────
# SUPPLIER IMPORT ROW PARSING
# ─────────────────────────────────────────────

def _supplier_import_row_values(row):
    def _norm(value):
        return str(value or '').strip()

    def _normalize_key(value):
        return ''.join(ch for ch in str(value or '').strip().lower() if ch.isalnum())

    def _get_value(data, *keys, default=''):
        normalized = {_normalize_key(key): value for key, value in data.items() if not str(key).startswith('_')}
        for key in keys:
            lookup = _normalize_key(key)
            if lookup in normalized and _norm(normalized[lookup]):
                return _norm(normalized[lookup])
        return _norm(default)

    raw_values = row.get('_values', []) if isinstance(row, dict) else []
    ordered_values = [_norm(value) for value in raw_values]
    headers = row.get('_headers', []) or []
    values = {}

    for index, (field_name, header_name) in enumerate(SUPPLIER_IMPORT_COLUMNS):
        if index < len(ordered_values):
            values[field_name] = ordered_values[index]
        else:
            values[field_name] = _get_value(row, header_name, field_name)

    return values


def _supplier_import_raw_import(file_obj, user):
    import csv
    import io

    content = file_obj.read()
    try:
        text = content.decode('utf-8-sig')
    except Exception:
        text = content.decode('utf-8')

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        rows.append({'_headers': list(row.keys()), '_values': list(row.values())})
    return rows


def _parse_supplier_date_value(value):
    from datetime import date, datetime
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    s = str(value or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────
# BACKUP HELPERS
# ─────────────────────────────────────────────

def _db_path():
    db_settings = settings.DATABASES["default"]
    if db_settings.get("ENGINE") != "django.db.backends.sqlite3":
        return None
    return Path(db_settings["NAME"]).resolve()


def _media_root():
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if media_root:
        return Path(media_root).resolve()
    return (Path(settings.BASE_DIR) / "media").resolve()


def _count_backup_stats():
    return {
        "users": User.objects.count(),
        "profiles": Profile.objects.count(),
        "transactions": Transaction.objects.count(),
        "cheques": Cheque.objects.count(),
        "fund_clusters": FundCluster.objects.count(),
        "suppliers": Supplier.objects.count(),
        "reports": Report.objects.count(),
        "audit_logs": AuditLog.objects.count(),
        "system_settings": SystemSetting.objects.count(),
    }


def _estimate_report_sheets(row_count):
    return max(1, (int(row_count or 0) + 24) // 25)


def _build_report_snapshot(report_type, date_from=None, date_to=None, fund_cluster_id=None, cheque_status=None, payee=None):
    snapshot = {"generated_at": timezone.now().isoformat(), "type": report_type}

    def apply_date_filter(qs, date_field='created_at'):
        if date_from:
            if date_field == 'date':
                qs = qs.filter(**{f"{date_field}__gte": date_from})
            else:
                qs = qs.filter(**{f"{date_field}__date__gte": date_from})
        if date_to:
            if date_field == 'date':
                qs = qs.filter(**{f"{date_field}__lte": date_to})
            else:
                qs = qs.filter(**{f"{date_field}__date__lte": date_to})
        return qs

    if report_type == 'cheque_summary':
        qs = apply_date_filter(Cheque.objects.select_related('payee', 'fund_cluster').all(), 'date')
        fund_cluster = None
        if fund_cluster_id:
            fund_cluster = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
            if fund_cluster:
                qs = qs.filter(fund_cluster=fund_cluster)
        if cheque_status and cheque_status != 'all':
            qs = qs.filter(status=cheque_status)
        by_status = {s: qs.filter(status=s).count() for s, _ in Cheque.STATUS_CHOICES}
        total_amount = qs.aggregate(t=Sum('amount'))['t'] or 0
        rows = []
        for c in qs.order_by('-date')[:500]:
            rows.append({
                "id": c.pk,
                "number": c.cheque_number,
                "payee": c.payee_name,
                "fund": c.fund_cluster.code if c.fund_cluster else '',
                "amount": str(c.amount),
                "date": str(c.date),
                "status": c.status,
                "dv_payroll": c.dv_payroll_no or '',
                "ors_burs": c.ors_burs_no or '',
                "responsibility_center": c.responsibility_center or '',
                "uacs": c.uacs_object_code or '',
                "nature": c.nature_of_payment or '',
                "professional_tax": str(c.professional_tax) if c.professional_tax is not None else '',
                "tax_5": str(c.tax_5_3) if c.tax_5_3 is not None else '',
                "tax_2": str(c.tax_3_1) if c.tax_3_1 is not None else '',
            })
        sheet_count = _estimate_report_sheets(len(rows))
        cheque_nums = [r.get('number') for r in rows if r.get('number')]
        first_cheque = ''
        last_cheque = ''
        try:
            numeric_pairs = []
            for s in cheque_nums:
                if isinstance(s, int):
                    numeric_pairs.append((int(s), str(s)))
                elif isinstance(s, str) and s.isdigit():
                    numeric_pairs.append((int(s), s))
            if numeric_pairs:
                numeric_pairs.sort(key=lambda x: x[0])
                first_cheque = numeric_pairs[0][1]
                last_cheque = numeric_pairs[-1][1]
            else:
                if cheque_nums:
                    last_cheque = cheque_nums[0]
                    first_cheque = cheque_nums[-1]
        except Exception:
            if cheque_nums:
                last_cheque = cheque_nums[0]
                first_cheque = cheque_nums[-1]

        snapshot.update({"by_status": by_status, "total_amount": str(total_amount), "rows": rows, "sheet_count": sheet_count, "check_first": first_cheque, "check_last": last_cheque, "cheque_count": len(rows)})
        if fund_cluster:
            snapshot["fund_cluster_id"] = fund_cluster.pk
            snapshot["fund_cluster"] = str(fund_cluster)
            snapshot["fund_cluster_code"] = fund_cluster.code
            snapshot["fund_cluster_name"] = fund_cluster.name
            snapshot["bank_name"] = fund_cluster.bank_name or ''
            snapshot["account_number"] = fund_cluster.account_number or ''
            snapshot["bank_account"] = (
                f"{fund_cluster.bank_name} / {fund_cluster.account_number}"
                if fund_cluster.bank_name and fund_cluster.account_number
                else fund_cluster.bank_name or fund_cluster.account_number or ''
            )

    elif report_type == 'fund_summary':
        qs = FundCluster.objects.all()
        rows = []
        for f in qs:
            cheques = apply_date_filter(f.cheque_set.all())
            released = cheques.filter(status='released').aggregate(t=Sum('amount'))['t'] or 0
            rows.append({
                "id": f.pk,
                "code": f.code, "name": f.name,
                "balance": str(f.balance),
                "released_amount": str(released),
                "cheque_count": cheques.count(),
                "is_active": f.is_active,
            })
        snapshot["rows"] = rows

    elif report_type == 'supplier_summary':
        rows = []
        for s in Supplier.objects.all().order_by('-date', '-pk'):
            cheques = apply_date_filter(s.cheque_set.all())
            total = cheques.aggregate(t=Sum('amount'))['t'] or 0
            rows.append({
                "id": s.pk,
                "mr_or": s.mr_or,
                "mr": s.mr,
                "code_or": s.code_or,
                "code_or2": s.code_or2,
                "series_month": s.series_month,
                "date": s.date.isoformat() if s.date else '',
                "or_number": s.or_number,
                "series_day": s.series_day,
                "account_number": s.account_number,
                "account_name": s.account_name,
                "code_line": s.code_line,
                "line": s.line,
                "code_noc": s.code_noc,
                "nature_of_collections": s.nature_of_collections,
                "amount": str(s.amount),
                "remarks": s.remarks,
                "status": s.status,
                "cheque_count": cheques.count(),
                "total_amount": str(total),
            })
        snapshot["rows"] = rows

    elif report_type == 'radai_summary':
        qs = Radai.objects.select_related('created_by', 'fund_cluster').all()
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        if fund_cluster_id:
            qs = qs.filter(fund_cluster_id=fund_cluster_id)
        if payee:
            qs = qs.filter(account_name=payee)
        rows = []
        fc_code = ''
        fc_name = ''
        bank_name = ''
        acct_no = ''
        if fund_cluster_id:
            fc = FundCluster.objects.filter(pk=fund_cluster_id, is_active=True).first()
            if fc:
                fc_code = fc.code
                fc_name = str(fc)
                bank_name = fc.bank_name or ''
                acct_no = fc.account_number or ''
        for r in qs.order_by('-date', '-pk'):
            extras = (r.raw_import or {}).get('radai_extras') or {}
            if not fc_code and r.fund_cluster:
                fc_code = r.fund_cluster.code
                fc_name = str(r.fund_cluster)
                bank_name = r.fund_cluster.bank_name or ''
                acct_no = r.fund_cluster.account_number or ''
            rows.append({
                "id": r.pk,
                "date": r.date.isoformat() if r.date else '',
                "mr_or": r.mr_or,
                "dv_payroll": str(extras.get('dv_payroll') or ''),
                "ors_burs_no": r.reference_code or '',
                "account_name": r.account_name,
                "uacs": str(extras.get('uacs') or ''),
                "nature_of_collections": r.nature_of_collections,
                "amount": str(r.amount),
            })
        snapshot["rows"] = rows
        snapshot["fund_cluster_code"] = fc_code
        snapshot["fund_cluster_name"] = fc_name
        snapshot["fund_cluster"] = fc_name
        snapshot["fund_cluster_id"] = fund_cluster_id
        snapshot["bank_name"] = bank_name
        snapshot["account_number"] = acct_no
        snapshot["bank_account"] = f"{bank_name} / {acct_no}" if bank_name and acct_no else (bank_name or acct_no or '')
        if payee:
            snapshot["payee"] = payee
        serial_numbers = [r["mr_or"] for r in rows if r.get("mr_or")]
        snapshot["ada_first"] = serial_numbers[-1] if serial_numbers else ''
        snapshot["ada_last"] = serial_numbers[0] if serial_numbers else ''

    elif report_type == 'audit_summary':
        qs = apply_date_filter(AuditLog.objects.select_related('admin'), 'timestamp')
        rows = [{"id": a.pk, "user": a.admin.username if a.admin else '', "action": a.action, "timestamp": a.timestamp.isoformat()} for a in qs.order_by('-timestamp')[:500]]
        snapshot["rows"] = rows

    return snapshot


def _do_import_compat(import_type, rows, user):
    """Backward-compatible _do_import for tests. Wraps per-row logic."""
    def _norm(value):
        return str(value or '').strip()

    def _get_value(row, *keys, default=''):
        normalized = {str(k).strip().lower().replace(' ', '').replace('_', ''): v for k, v in row.items() if not str(k).startswith('_')}
        for key in keys:
            lookup = str(key).strip().lower().replace(' ', '').replace('_', '')
            if lookup in normalized and _norm(normalized[lookup]):
                return _norm(normalized[lookup])
        return _norm(default)

    def _is_nonzero(val):
        try:
            return bool(_norm(val)) and float(_norm(val).replace(',', '')) != 0
        except (ValueError, AttributeError):
            return bool(_norm(val))

    from cashier.models import Radai

    count = 0
    warnings = []

    if import_type == 'suppliers':
        fund_cluster_map = {
            fc.code.strip().lower(): fc
            for fc in FundCluster.objects.filter(is_active=True)
        }
        seen_import_keys = set()
        for r in rows:
            raw_import = {
                'sheet': r.get('_sheet', '') if isinstance(r, dict) else '',
                'row_number': r.get('_row_number') if isinstance(r, dict) else None,
                'headers': r.get('_headers', []) if isinstance(r, dict) else [],
                'columns': r.get('_headers', []) if isinstance(r, dict) else [],
                'values': r.get('_values', []) if isinstance(r, dict) else [],
                'cells': r.get('_ordered_cells', []) if isinstance(r, dict) else [],
                'data': {k: v for k, v in r.items() if not str(k).startswith('_')} if isinstance(r, dict) else {},
            }
            parsed = _supplier_import_row_values(r)
            non_empty_values = [_norm(value) for value in raw_import.get('values', []) if _norm(value)]
            if isinstance(r, dict) and not non_empty_values:
                non_empty_values = [_norm(value) for key, value in r.items() if not str(key).startswith('_') and _norm(value)]

            name = parsed.get('account_name', '')
            if not name:
                row_number = r.get('_row_number') if isinstance(r, dict) else None
                name = f"Imported Row {row_number or count + 1}"

            account_number = parsed.get('or_number', '')
            try:
                amount = Decimal(parsed.get('amount', '') or '0').quantize(Decimal('0.01'))
            except InvalidOperation:
                amount = Decimal('0.00')
            parsed_date = _parse_supplier_date_value(parsed.get('date')) or _parse_supplier_date_value(parsed.get('mr')) or _parse_supplier_date_value(parsed.get('mr_or'))
            import_key = (
                _norm(name),
                _norm(account_number),
                _norm(parsed.get('mr_or', '')),
                parsed_date.isoformat() if parsed_date else '',
            )
            if import_key in seen_import_keys:
                continue
            seen_import_keys.add(import_key)

            raw_fc_code = _norm(parsed.get('fund_cluster', ''))
            fund_cluster_obj = None
            if raw_fc_code:
                def _find_fund_cluster(code_str):
                    fc = fund_cluster_map.get(code_str.strip().lower())
                    if fc:
                        return fc
                    try:
                        int_str = str(int(float(code_str.strip())))
                        fc = fund_cluster_map.get(int_str.lower())
                        if fc:
                            return fc
                    except (ValueError, OverflowError):
                        pass
                    import re as _re
                    prefix_match = _re.match(r'^(\S+)', code_str.strip())
                    if prefix_match:
                        prefix = prefix_match.group(1).lower()
                        fc = fund_cluster_map.get(prefix)
                        if fc:
                            return fc
                    lower_val = code_str.strip().lower()
                    for db_code, fc_obj in fund_cluster_map.items():
                        if db_code in lower_val or lower_val in db_code:
                            return fc_obj
                    return None

                fund_cluster_obj = _find_fund_cluster(raw_fc_code)
                if fund_cluster_obj is None:
                    warnings.append(
                        f"Row skipped (Payee: '{name}'): Fund Cluster '{raw_fc_code}' is not registered in the system."
                    )
                    continue

            duplicate_exists = Supplier.objects.filter(
                account_name=name,
                account_number=account_number,
                mr_or=parsed.get('mr_or', ''),
                date=parsed_date,
            ).exists()
            if duplicate_exists:
                continue

            prof_tax_val = parsed.get('professional_tax', '')
            tax_5_val = parsed.get('tax_5', '')
            tax_2_val = parsed.get('tax_2', '')
            auto_is_vat = _is_nonzero(tax_5_val) or _is_nonzero(tax_2_val)

            other_deductions_raw = str(parsed.get('other_deductions', '') or '').strip().replace(',', '').replace('₱', '').replace('\u20B1', '')
            try:
                other_deductions_val = str(Decimal(other_deductions_raw or '0').quantize(Decimal('0.01'))) if other_deductions_raw else ''
            except Exception:
                other_deductions_val = ''

            def _normalize_uacs(val):
                s = str(val or '').strip()
                if s.endswith('.0'):
                    s = s[:-2]
                return s

            uacs_code = _normalize_uacs(parsed.get('uacs', ''))
            account_title_val = ''
            if uacs_code:
                opt = ManagementOption.objects.filter(
                    category=ManagementOption.CATEGORY_ACCOUNT_TITLE,
                    uacs=uacs_code,
                    is_active=True
                ).first()
                if opt:
                    account_title_val = opt.value
                else:
                    group = AccountTitleGroup.objects.filter(uacs=uacs_code).first()
                    if group:
                        account_title_val = group.name

            try:
                active_taxes = list(ManagementOption.objects.filter(category=ManagementOption.CATEGORY_TAX, is_active=True).exclude(value__iexact='Professional Tax').order_by('pk'))
                t1 = active_taxes[0] if len(active_taxes) > 0 else None
                t2 = active_taxes[1] if len(active_taxes) > 1 else None
                t1_rate = t1.uacs if t1 else '5%/3%'
                t2_rate = t2.uacs if t2 else '2%/1%'
            except Exception:
                t1_rate = '5%/3%'
                t2_rate = '2%/1%'

            t1_vat, t1_nv = _get_tax_rates(t1_rate)
            t2_vat, t2_nv = _get_tax_rates(t2_rate)

            tax_5_rate_vat_val = str((t1_vat * 100).quantize(Decimal('0.01')))
            tax_5_rate_non_vat_val = str((t1_nv * 100).quantize(Decimal('0.01')))
            tax_2_rate_vat_val = str((t2_vat * 100).quantize(Decimal('0.01')))
            tax_2_rate_non_vat_val = str((t2_nv * 100).quantize(Decimal('0.01')))

            if auto_is_vat:
                try:
                    gross_val_raw = parsed.get('gross_amount', '')
                    if _is_nonzero(gross_val_raw):
                        gross_dec = Decimal(str(gross_val_raw).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                    else:
                        tax_5_dec = Decimal(str(tax_5_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        tax_2_dec = Decimal(str(tax_2_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        prof_dec = Decimal(str(prof_tax_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        other_ded_dec = Decimal(str(other_deductions_val or 0).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                        gross_dec = amount + tax_5_dec + tax_2_dec + prof_dec + other_ded_dec

                    base_dec = (gross_dec / Decimal('1.12')).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                    if base_dec > 0:
                        if _is_nonzero(tax_5_val):
                            tax_5_dec = Decimal(str(tax_5_val).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                            tax_5_rate_vat_val = str(((tax_5_dec / base_dec) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                        else:
                            tax_5_rate_vat_val = '0.00'

                        if _is_nonzero(tax_2_val):
                            tax_2_dec = Decimal(str(tax_2_val).replace(',', '').replace('₱', '').replace('\u20B1', '').strip())
                            tax_2_rate_vat_val = str(((tax_2_dec / base_dec) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
                        else:
                            tax_2_rate_vat_val = '0.00'
                except Exception:
                    pass

            if not isinstance(raw_import, dict):
                raw_import = {}
            raw_import['supplier_extras'] = {
                'account_title': account_title_val,
                'dv_payroll': parsed.get('dv_payroll', ''),
                'ors_burs': parsed.get('ors_burs', ''),
                'responsibility_center': parsed.get('responsibility_center', ''),
                'uacs': uacs_code,
                'professional_tax': prof_tax_val,
                'tax_5': tax_5_val,
                'tax_2': tax_2_val,
                'other_deductions': other_deductions_val,
                'gross_amount': parsed.get('gross_amount', ''),
                'fund_cluster': fund_cluster_obj.code if fund_cluster_obj else raw_fc_code,
                'fund_cluster_id': str(fund_cluster_obj.pk) if fund_cluster_obj else '',
                'is_vat': 'true' if auto_is_vat else 'false',
                'tax_5_rate_vat': tax_5_rate_vat_val,
                'tax_5_rate_non_vat': tax_5_rate_non_vat_val,
                'tax_2_rate_vat': tax_2_rate_vat_val,
                'tax_2_rate_non_vat': tax_2_rate_non_vat_val,
            }

            Supplier.objects.create(
                account_name=name,
                account_number=account_number,
                mr_or=parsed.get('mr_or', ''),
                mr=parsed.get('mr', ''),
                code_or=parsed.get('code_or', ''),
                code_or2=parsed.get('code_or2', ''),
                series_month=parsed.get('series_month', ''),
                date=parsed_date,
                or_number=account_number,
                series_day=parsed.get('series_day', ''),
                code_line=parsed.get('code_line', ''),
                line=parsed.get('line', ''),
                code_noc=parsed.get('code_noc', ''),
                nature_of_collections=parsed.get('nature_of_collections', ''),
                amount=amount,
                remarks=parsed.get('remarks', ''),
                address=parsed.get('address', ''),
                tin=parsed.get('tin', ''),
                status='active',
                created_by=user,
                raw_import=raw_import,
            )
            count += 1
    elif import_type == 'radai':
        fund_cluster_map = {
            fc.code.strip().lower(): fc
            for fc in FundCluster.objects.filter(is_active=True)
        }
        existing_serials = set(
            Radai.objects.exclude(mr_or='').values_list('mr_or', flat=True)
        )
        for r in rows:
            try:
                amount = Decimal(_get_value(r, 'amount', default='0').replace(',', ''))
            except InvalidOperation:
                amount = Decimal('0')
            raw_date_str = _get_value(r, 'date')
            parsed_date = _parse_supplier_date_value(raw_date_str) if raw_date_str else None
            account_name = _get_value(r, 'account_name', 'payee')
            if not account_name:
                warnings.append(f"Row {r.get('_row_number', '?')}: Skipped - missing Payee")
                continue
            mr_or = _get_value(r, 'mr_or', 'serial no.', 'serial', 'check serial')
            if mr_or and mr_or in existing_serials:
                warnings.append(f"Row {r.get('_row_number', '?')}: Skipped - duplicate Serial No. '{mr_or}'")
                continue
            reference_code = _get_value(r, 'reference_code', 'ors/burs no.', 'ors/burs no. & responsibility center', 'reference code')
            responsibility_center = _get_value(r, 'responsibility_center', 'responsibility center code', 'responsibility center')
            nature = _get_value(r, 'nature_of_collections', 'nature of payment', 'nature')
            dv_payroll = _get_value(r, 'dv_payroll', 'dv/payroll no.')
            uacs = _get_value(r, 'uacs', 'uacs object code')
            remarks = _get_value(r, 'remarks')
            fc_code = _get_value(r, 'fund_cluster', 'fund cluster', 'cluster')

            if responsibility_center and reference_code:
                reference_code = f"{reference_code} | {responsibility_center}"
            elif responsibility_center:
                reference_code = responsibility_center
            fund_cluster = None
            if fc_code:
                fc_code_clean = fc_code.strip().lower()
                if fc_code_clean in fund_cluster_map:
                    fund_cluster = fund_cluster_map[fc_code_clean]
                else:
                    try:
                        int_code = str(int(float(fc_code_clean)))
                        fund_cluster = fund_cluster_map.get(int_code)
                    except (ValueError, OverflowError):
                        pass
                if not fund_cluster:
                    fund_cluster = FundCluster.objects.create(
                        code=fc_code.strip(),
                        name=fc_code.strip(),
                        is_active=True
                    )
                    fund_cluster_map[fc_code_clean] = fund_cluster
                    warnings.append(f"Row {r.get('_row_number', '?')}: Auto-created fund cluster '{fc_code}'")
            Radai.objects.create(
                account_name=account_name,
                amount=amount,
                date=parsed_date,
                mr_or=mr_or,
                reference_code=reference_code,
                nature_of_collections=nature,
                remarks=remarks,
                fund_cluster=fund_cluster,
                created_by=user,
                raw_import={
                    'dv_payroll': dv_payroll,
                    'uacs': uacs,
                },
            )
            existing_serials.add(mr_or)
            count += 1

    return count, warnings
