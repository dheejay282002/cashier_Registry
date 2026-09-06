import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai, RadaiSetting
from admin_panel.models import SystemSetting, AuditLog, ManagementOption
from .utils import (
    _is_admin, _get_profile, _page_context, _audit,
    _parse_money, _compute_auto_supplier_defaults,
    _enrich_supplier_for_display, _enrich_radai_for_display,
    _sync_suppliers_by_uacs, _supplier_import_row_values,
    _supplier_import_raw_import, _parse_supplier_date_value,
    _supplier_import_headers, _radai_import_headers,
    SUPPLIER_IMPORT_COLUMNS, RADAI_IMPORT_COLUMNS
)


@login_required
def suppliers(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add':
            try:
                supplier = Supplier(
                    account_name=request.POST.get('account_name', '').strip(),
                    date=request.POST.get('date') or date.today(),
                    mr_or=request.POST.get('mr_or', '').strip(),
                    or_number=request.POST.get('or_number', '').strip(),
                    series_day=request.POST.get('series_day', '').strip(),
                    series_month=request.POST.get('series_month', '').strip(),
                    dv_payroll_no=request.POST.get('dv_payroll_no', '').strip(),
                    ors_burs_no=request.POST.get('ors_burs_no', '').strip(),
                    responsibility_center=request.POST.get('responsibility_center', '').strip(),
                    uacs_object_code=request.POST.get('uacs_object_code', '').strip(),
                    nature_of_collections=request.POST.get('nature_of_collections', '').strip(),
                    gross_amount=_parse_money(request.POST.get('gross_amount', '0')),
                    professional_tax=_parse_money(request.POST.get('professional_tax', '0')),
                    tax_5_3=_parse_money(request.POST.get('tax_5_3', '0')),
                    tax_3_1=_parse_money(request.POST.get('tax_3_1', '0')),
                    amount=_parse_money(request.POST.get('amount', '0')),
                    fund_cluster=request.POST.get('fund_cluster', '').strip(),
                    remarks=request.POST.get('remarks', '').strip(),
                    other_deductions=request.POST.get('other_deductions', '').strip(),
                    tin_number=request.POST.get('tin_number', '').strip(),
                    contact_info=request.POST.get('contact_info', '').strip(),
                    bank_name=request.POST.get('bank_name', '').strip(),
                    account_number=request.POST.get('account_number', '').strip(),
                    code_noc=request.POST.get('code_noc', '').strip(),
                    code_or=request.POST.get('code_or', '').strip(),
                    code_or2=request.POST.get('code_or2', '').strip(),
                    code_line=request.POST.get('code_line', '').strip(),
                    line=request.POST.get('line', '').strip(),
                    mr=request.POST.get('mr', '').strip(),
                    created_by=request.user,
                )
                supplier.save()
                _sync_suppliers_by_uacs(supplier)
                _audit(request.user, "Created supplier", {"id": supplier.pk, "name": supplier.account_name})
                success = f"Supplier '{supplier.account_name}' added."
            except Exception as e:
                error = str(e)

    suppliers_list = Supplier.objects.filter(status='active').order_by('-created_at')
    context = _page_context(request, is_admin, "Suppliers", extra={
        "suppliers": suppliers_list,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/suppliers.html", context)


@login_required
def update_supplier_remark(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    supplier_id = request.POST.get('supplier_id')
    remark = request.POST.get('remark', '').strip()
    try:
        supplier = Supplier.objects.get(pk=supplier_id)
        supplier.remarks = remark
        supplier.save(update_fields=['remarks'])
        return JsonResponse({"ok": True})
    except Supplier.DoesNotExist:
        return JsonResponse({"error": "Supplier not found"}, status=404)


@login_required
def registry(request):
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, "Registry")
    return render(request, "admin_panel/registry.html", context)


@login_required
def radai(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add':
            try:
                radai_entry = Radai(
                    date=request.POST.get('date') or date.today(),
                    mr_or=request.POST.get('mr_or', '').strip(),
                    dv_payroll_no=request.POST.get('dv_payroll_no', '').strip(),
                    ors_burs_no=request.POST.get('ors_burs_no', '').strip(),
                    reference_code=request.POST.get('reference_code', '').strip(),
                    responsibility_center=request.POST.get('responsibility_center', '').strip(),
                    account_name=request.POST.get('account_name', '').strip(),
                    uacs_object_code=request.POST.get('uacs_object_code', '').strip(),
                    nature_of_collections=request.POST.get('nature_of_collections', '').strip(),
                    amount=_parse_money(request.POST.get('amount', '0')),
                    created_by=request.user,
                )
                radai_entry.save()
                _audit(request.user, "Created RADAI", {"id": radai_entry.pk})
                success = "RADAI entry added."
            except Exception as e:
                error = str(e)

    radai_list = Radai.objects.filter(status='active').order_by('-created_at')
    context = _page_context(request, is_admin, "RADAI", extra={
        "radai_list": radai_list,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/radai.html", context)


@login_required
def update_radai_remark(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    radai_id = request.POST.get('radai_id')
    remark = request.POST.get('remark', '').strip()
    try:
        radai_entry = Radai.objects.get(pk=radai_id)
        radai_entry.remarks = remark
        radai_entry.save(update_fields=['remarks'])
        return JsonResponse({"ok": True})
    except Radai.DoesNotExist:
        return JsonResponse({"error": "RADAI not found"}, status=404)


@login_required
def update_radai_settings(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
        settings_obj, _ = RadaiSetting.objects.get_or_create(pk=1)
        for key, value in data.items():
            if hasattr(settings_obj, key):
                setattr(settings_obj, key, value)
        settings_obj.save()
        return JsonResponse({"ok": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def export_radai(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="radai_export.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Serial No.', 'DV/Payroll No.', 'ORS/BURS No.', 'Payee', 'UACS', 'Nature', 'Amount'])
    for r in Radai.objects.filter(status='active').order_by('-created_at'):
        writer.writerow([
            r.date, r.mr_or, r.dv_payroll_no, r.ors_burs_no,
            r.account_name, r.uacs_object_code, r.nature_of_collections, str(r.amount)
        ])
    return response


@login_required
def radai_detail(request, pk):
    try:
        r = Radai.objects.get(pk=pk)
        return JsonResponse({
            "ok": True,
            "radai": {
                "id": r.pk,
                "date": r.date.isoformat() if r.date else "",
                "mr_or": r.mr_or,
                "dv_payroll_no": r.dv_payroll_no,
                "ors_burs_no": r.ors_burs_no,
                "reference_code": r.reference_code,
                "responsibility_center": r.responsibility_center,
                "account_name": r.account_name,
                "uacs_object_code": r.uacs_object_code,
                "nature_of_collections": r.nature_of_collections,
                "amount": str(r.amount),
                "remarks": r.remarks,
            }
        })
    except Radai.DoesNotExist:
        return JsonResponse({"error": "RADAI not found"}, status=404)


@login_required
def fund_clusters(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'add':
            code = request.POST.get('code', '').strip()
            name = request.POST.get('name', '').strip()
            desc = request.POST.get('description', '').strip()
            if not code or not name:
                error = "Code and name are required."
            elif FundCluster.objects.filter(code=code).exists():
                error = "Fund cluster code already exists."
            else:
                fc = FundCluster.objects.create(
                    code=code,
                    name=name,
                    description=desc,
                    balance=0,
                    bank_name=request.POST.get('bank_name', '').strip(),
                    account_number=request.POST.get('account_number', '').strip()
                )
                _audit(request.user, "Created fund cluster", {"id": fc.pk, "code": code})
                success = f"Fund cluster '{name}' created."

    clusters = FundCluster.objects.annotate(
        cheque_count=Count('cheque', distinct=True),
        released_count=Count('cheque', filter=Q(cheque__status='released'), distinct=True),
        total_released=Sum('cheque__amount', filter=Q(cheque__status='released')),
    ).order_by('code')
    context = _page_context(request, is_admin, "Fund Clusters", extra={
        "clusters": clusters,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/fund_clusters.html", context)


@login_required
def fund_cluster_info(request, pk):
    try:
        fc = FundCluster.objects.get(pk=pk)
        return JsonResponse({
            "ok": True,
            "fund_cluster": {
                "id": fc.pk,
                "code": fc.code,
                "name": fc.name,
                "description": fc.description,
                "balance": str(fc.balance),
                "bank_name": fc.bank_name,
                "account_number": fc.account_number,
            }
        })
    except FundCluster.DoesNotExist:
        return JsonResponse({"error": "Fund cluster not found"}, status=404)
