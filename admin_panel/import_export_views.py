import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai
from admin_panel.models import SystemSetting, AuditLog
from .utils import (
    _is_admin, _get_profile, _page_context, _audit,
    _parse_money, _db_path, _media_root, _count_backup_stats,
    _supplier_import_row_values, _supplier_import_raw_import,
    _parse_supplier_date_value, _enrich_supplier_for_display,
    SUPPLIER_IMPORT_COLUMNS, RADAI_IMPORT_COLUMNS
)


def _write_sqlite_snapshot(source_db_path, dest_db_path):
    source_conn = sqlite3.connect(str(source_db_path))
    try:
        dest_conn = sqlite3.connect(str(dest_db_path))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()


def _pack_backup_archive(created_by):
    timestamp = timezone.now().strftime("%Y_%m_%d_%H%M%S")
    backup_name = f"system_backup_{timestamp}_FULL.bak"
    temp_dir = Path(tempfile.mkdtemp(prefix="finalproject_backup_"))
    snapshot_db = temp_dir / "db.sqlite3"

    db_path = _db_path()
    if db_path and db_path.exists():
        _write_sqlite_snapshot(db_path, snapshot_db)

    media_src = _media_root()
    media_dst = temp_dir / "media"
    if media_src.exists():
        shutil.copytree(str(media_src), str(media_dst), dirs_exist_ok=True)

    archive_path = _get_backup_storage_dir() / backup_name
    with zipfile.ZipFile(str(archive_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in temp_dir.rglob('*'):
            if f.is_file():
                arcname = str(f.relative_to(temp_dir))
                zf.write(str(f), arcname)

    shutil.rmtree(str(temp_dir), ignore_errors=True)
    return archive_path, backup_name


def _get_backup_storage_dir():
    storage = Path(settings.BASE_DIR) / "backups"
    storage.mkdir(exist_ok=True)
    return storage


def _store_backup(archive_path, backup_name, created_by):
    pass


def _enforce_backup_retention():
    storage = _get_backup_storage_dir()
    backups = sorted(storage.glob("system_backup_*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[10:]:
        old.unlink(missing_ok=True)


def _inspect_backup_archive(archive_path):
    info = {"tables": [], "media_files": []}
    with zipfile.ZipFile(str(archive_path), 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.sqlite3'):
                info["tables"].append(name)
            elif name.startswith('media/'):
                info["media_files"].append(name)
    return info


def _restore_media_tree(archive_path):
    media_src = _media_root()
    with zipfile.ZipFile(str(archive_path), 'r') as zf:
        for name in zf.namelist():
            if name.startswith('media/'):
                target = media_src / name[6:]
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(str(target), 'wb') as dst:
                    dst.write(src.read())


def _apply_restore_archive(archive_path):
    db_path = _db_path()
    with zipfile.ZipFile(str(archive_path), 'r') as zf:
        for name in zf.namelist():
            if name.endswith('.sqlite3'):
                with zf.open(name) as src:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(str(db_path), 'wb') as dst:
                        dst.write(src.read())
    _restore_media_tree(archive_path)


@login_required
def import_data(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    error = None
    success = None
    preview_data = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'preview':
            file = request.FILES.get('file')
            import_type = request.POST.get('import_type', 'supplier')
            if file:
                try:
                    rows = _supplier_import_raw_import(file, request.user)
                    preview_data = {
                        "import_type": import_type,
                        "total_rows": len(rows),
                        "headers": rows[0].get('_headers', []) if rows else [],
                        "sample": [_supplier_import_row_values(r) for r in rows[:5]],
                    }
                except Exception as e:
                    error = str(e)

    context = _page_context(request, is_admin, "Import Data", extra={
        "error": error,
        "success": success,
        "preview_data": preview_data,
    })
    return render(request, "admin_panel/import_data.html", context)


def _do_import(file_obj, user, import_type='supplier'):
    rows = _supplier_import_raw_import(file_obj, user)
    imported = 0
    errors = []

    for i, row in enumerate(rows, 1):
        try:
            values = _supplier_import_row_values(row)
            if import_type == 'supplier':
                supplier = Supplier(
                    account_name=values.get('account_name', ''),
                    date=_parse_supplier_date_value(values.get('date')),
                    mr_or=values.get('mr_or', ''),
                    or_number=values.get('or_number', ''),
                    series_day=values.get('series_day', ''),
                    dv_payroll_no=values.get('dv_payroll', ''),
                    ors_burs_no=values.get('ors_burs', ''),
                    responsibility_center=values.get('responsibility_center', ''),
                    uacs_object_code=values.get('uacs', ''),
                    nature_of_collections=values.get('nature_of_collections', ''),
                    gross_amount=_parse_money(values.get('gross_amount', '0')),
                    professional_tax=_parse_money(values.get('professional_tax', '0')),
                    tax_5_3=_parse_money(values.get('tax_5', '0')),
                    tax_3_1=_parse_money(values.get('tax_2', '0')),
                    amount=_parse_money(values.get('amount', '0')),
                    fund_cluster=values.get('fund_cluster', ''),
                    remarks=values.get('remarks', ''),
                    other_deductions=values.get('other_deductions', ''),
                    created_by=user,
                )
                supplier.save()
                imported += 1
        except Exception as e:
            errors.append(f"Row {i}: {str(e)}")

    return {"imported": imported, "errors": errors}


@login_required
def backup_restore(request):
    is_admin = _is_admin(request.user)
    if not is_admin:
        return render(request, "access_denied.html")

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'backup':
            try:
                archive_path, backup_name = _pack_backup_archive(request.user)
                _enforce_backup_retention()
                _audit(request.user, "Created backup", {"file": backup_name})
                success = f"Backup created: {backup_name}"
            except Exception as e:
                error = str(e)
        elif action == 'restore':
            backup_file = request.FILES.get('backup_file')
            if backup_file:
                try:
                    temp_path = Path(tempfile.mkdtemp()) / backup_file.name
                    with open(str(temp_path), 'wb') as f:
                        for chunk in backup_file.chunks():
                            f.write(chunk)
                    _apply_restore_archive(temp_path)
                    _audit(request.user, "Restored from backup")
                    success = "System restored successfully."
                except Exception as e:
                    error = str(e)

    backups = sorted(_get_backup_storage_dir().glob("system_backup_*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    stats = _count_backup_stats()
    context = _page_context(request, is_admin, "Backup & Restore", extra={
        "backups": backups,
        "stats": stats,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/backup_restore.html", context)


@login_required
def export_suppliers(request):
    import csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="suppliers_export.csv"'
    writer = csv.writer(response)
    writer.writerow([label for _, label in SUPPLIER_IMPORT_COLUMNS])
    for s in Supplier.objects.filter(status='active').order_by('account_name'):
        writer.writerow([
            s.mr_or, s.date, s.or_number, s.series_day,
            s.dv_payroll_no, s.ors_burs_no, s.responsibility_center,
            s.uacs_object_code, s.account_name, s.nature_of_collections,
            str(s.gross_amount), str(s.professional_tax), str(s.tax_5_3),
            str(s.tax_3_1), str(s.amount), s.fund_cluster, s.remarks,
            s.other_deductions,
        ])
    return response


@login_required
def download_import_template(request):
    import csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="supplier_import_template.csv"'
    writer = csv.writer(response)
    writer.writerow([label for _, label in SUPPLIER_IMPORT_COLUMNS])
    return response
