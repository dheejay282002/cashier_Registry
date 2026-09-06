import csv
import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Sum, Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report
from admin_panel.models import SystemSetting, AuditLog
from .utils import (
    _is_admin, _get_profile, _page_context, _audit,
    _parse_money, _fund_balance_error, _adjust_fund_balance,
    _cheque_is_released, _cheque_action_block_reason,
    _number_to_words, _amount_to_words_py
)


def _invalidate_dashboard_cache():
    cache.delete('admin_dashboard_chart_data')
    cache.delete('admin_chart_released_data')


@login_required
def cheque_pdf(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)
    buf = BytesIO()
    w = 8 * inch
    h = 3 * inch
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setFillColorRGB(0, 0, 0)
    left = 18
    right = w - 18
    top = h - 18

    c.setFont('Courier-Bold', 12)
    c.drawString(left, top - 6, (cheque.bank_name or 'Bank Name').upper())
    c.setFont('Courier', 8)
    c.drawString(left, top - 20, 'Account No.:')
    c.setFont('Courier', 8)
    c.drawRightString(right, top - 6, 'CHEQUE NO.')
    c.setFont('Courier-Bold', 12)
    c.drawRightString(right, top - 20, str(cheque.real_cheque_number or '—'))
    c.setFont('Courier', 8)
    c.drawRightString(right - 4*mm, top - 36, 'Date:')
    c.setFont('Courier', 9)
    date_str = cheque.date.strftime('%b %d, %Y') if cheque.date else ''
    c.drawRightString(right, top - 36, date_str)

    c.setFont('Courier', 9)
    payee_y = top - 60
    c.drawString(left, payee_y, 'PAY TO THE ORDER OF')
    c.line(left, payee_y - 6, right - 160, payee_y - 6)
    c.drawString(left + 120, payee_y, (cheque.payee_name or '').upper())

    box_w = 110
    box_h = 22
    box_x = right - box_w
    box_y = payee_y - 6 - box_h
    c.rect(box_x, box_y, box_w, box_h, stroke=1, fill=0)
    c.setFont('Courier-Bold', 10)
    amt_text = f'₱ {cheque.amount:.2f}' if cheque.amount is not None else '₱ 0.00'
    c.drawRightString(box_x + box_w - 6, box_y + box_h/2 - 4, amt_text)

    words = _amount_to_words_py(cheque.amount or Decimal('0.00'))
    c.setFont('Courier-Bold', 9)
    words_y = box_y - 18
    c.drawString(left, words_y, words)
    c.line(left, words_y - 4, right - 30, words_y - 4)

    sig_x = right - 180
    sig_y = words_y - 28
    c.line(sig_x, sig_y + 18, sig_x + 160, sig_y + 18)
    c.setFont('Courier-Bold', 8)
    c.drawString(sig_x + 10, sig_y + 6, 'Authorized personnel')
    c.setFont('Courier', 7)
    c.drawString(sig_x + 10, sig_y - 6, 'Authorize Signature over printed name')

    micr_y = 12
    c.setDash(3, 3)
    c.setLineWidth(0.8)
    c.line(left, micr_y + 6, right, micr_y + 6)
    c.setDash()
    c.setFont('Courier', 8)
    c.drawCentredString(w/2, micr_y - 2, f'⑆ {cheque.real_cheque_number or "0000000"} ⑆ 000000000 ⑆ 000000 ⑆')

    c.showPage()
    c.save()
    buf.seek(0)
    return HttpResponse(buf.getvalue(), content_type='application/pdf')


@login_required
def cheque_list(request):
    is_admin = _is_admin(request.user)
    cheques = Cheque.objects.select_related('payee', 'fund_cluster', 'created_by').order_by('-created_at')
    context = _page_context(request, is_admin, "Cheques", extra={
        "cheques": cheques,
    })
    return render(request, "admin_panel/cheques.html", context)


@login_required
def cheque_lookup(request):
    cheque_id = request.GET.get('id')
    if not cheque_id:
        return JsonResponse({"error": "No cheque ID provided"}, status=400)
    try:
        cheque = Cheque.objects.select_related('payee', 'fund_cluster').get(pk=cheque_id)
        return JsonResponse({
            "ok": True,
            "cheque": {
                "id": cheque.pk,
                "cheque_number": cheque.cheque_number,
                "payee_name": cheque.payee_name,
                "amount": str(cheque.amount),
                "date": cheque.date.isoformat() if cheque.date else "",
                "status": cheque.status,
                "fund_cluster": cheque.fund_cluster.code if cheque.fund_cluster else "",
                "bank_name": cheque.bank_name,
                "purpose": cheque.purpose,
            }
        })
    except Cheque.DoesNotExist:
        return JsonResponse({"error": "Cheque not found"}, status=404)


@login_required
def cheque_create(request):
    is_admin = _is_admin(request.user)

    suppliers_qs = [
        _enrich_supplier_for_display(s)
        for s in Supplier.objects.filter(status='active').order_by('account_name')
    ]
    funds_qs = FundCluster.objects.filter(is_active=True).order_by('code')
    error = request.GET.get('error') or None
    success = request.GET.get('success') or None
    cheque = None

    if request.method == "POST":
        amount = _parse_money(request.POST.get('amount', '0'))

        payee_id = request.POST.get('payee_id') or None
        fund_id = request.POST.get('fund_cluster_id') or None
        cheque_date = request.POST.get('date') or date.today().isoformat()
        status = request.POST.get('status', 'draft')
        fund_cluster = FundCluster.objects.filter(pk=fund_id, is_active=True).first() if fund_id else None

        if status == 'released':
            error = _fund_balance_error(fund_cluster, amount)

        manual_cheque_input = request.POST.get('cheque_number', '').strip()
        try:
            cheque_number_value = Cheque.normalize_cheque_number(manual_cheque_input) if manual_cheque_input else Cheque.next_cheque_number()
        except ValueError as exc:
            cheque_number_value = Cheque.next_cheque_number()
            error = str(exc)

        cheque = Cheque(
            cheque_number=cheque_number_value,
            payee_name=request.POST.get('payee_name', '').strip(),
            amount=amount,
            date=cheque_date,
            purpose=request.POST.get('purpose', '').strip(),
            bank_name=request.POST.get('bank_name', '').strip(),
            account_number=request.POST.get('account_number', '').strip(),
            status=status,
            created_by=request.user,
            dv_payroll_no=request.POST.get('dv_payroll_no', '').strip() or SystemSetting.next_dv_payroll_no(),
            ors_burs_no=request.POST.get('ors_burs_no', '').strip() or SystemSetting.next_ors_burs_no(),
            responsibility_center=request.POST.get('responsibility_center', '').strip(),
            uacs_object_code=request.POST.get('uacs_object_code', '').strip(),
            nature_of_payment=request.POST.get('nature_of_payment', '').strip(),
            professional_tax=_parse_money(request.POST.get('professional_tax', '')) or None,
            tax_5_3=_parse_money(request.POST.get('tax_5_3', '')) or None,
            tax_3_1=_parse_money(request.POST.get('tax_3_1', '')) or None,
        )
        if payee_id:
            cheque.payee_id = payee_id
        if fund_id:
            cheque.fund_cluster_id = fund_id

        stale_days = request.POST.get('stale_after_days')
        try:
            cheque.stale_after_days = int(stale_days) if stale_days not in (None, '', 'null') else None
        except Exception:
            cheque.stale_after_days = None

        if cheque.status == 'released' and cheque.stale_after_days:
            try:
                base_date = date.fromisoformat(cheque.date) if isinstance(cheque.date, str) else (cheque.date or date.today())
            except Exception:
                base_date = date.today()
            cheque.stale_at = base_date + timedelta(days=cheque.stale_after_days)

        if error:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({"ok": False, "error": error})
        else:
            cheque.save()

            # Sync the new/edited cheque number directly to the supplier's check serial (mr_or)
            try:
                supplier_obj = cheque.payee
                if not supplier_obj and cheque.payee_name:
                    supplier_obj = Supplier.objects.filter(account_name__iexact=cheque.payee_name.strip()).first()
                if supplier_obj and cheque.cheque_number:
                    supplier_obj.mr_or = cheque.cheque_number
                    supplier_obj.save(update_fields=['mr_or'])
            except Exception as sync_err:
                logger.warning(f"Failed to sync cheque serial to supplier: {sync_err}")

            if cheque.status == 'released' and cheque.fund_cluster_id:
                _adjust_fund_balance(cheque.fund_cluster, -cheque.amount)

            settings_obj = SystemSetting.get_settings()
            settings_obj.last_cheque_number = cheque.cheque_number
            settings_obj.save(update_fields=['last_cheque_number'])

            _audit(request.user, "Created cheque", {
                "id": cheque.pk,
                "number": cheque.cheque_number,
                "amount": str(amount)
            })
            _invalidate_dashboard_cache()

            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({
                    "ok": True,
                    "cheque_id": cheque.pk,
                    "print_url": reverse('cheque_print', args=[cheque.pk]),
                    "success_url": reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} saved and print initiated."
                })
            return redirect(reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} saved successfully.")

    context = _page_context(request, is_admin, "New Cheque", extra={
        "suppliers": suppliers_qs,
        "funds": funds_qs,
        "error": error,
        "success": success,
        "cheque_number_value": cheque.cheque_number if cheque else Cheque.next_cheque_number(),
        "cheque": cheque,
        "next_cheque": Cheque.next_cheque_number(),
        "today": date.today().isoformat(),
    })

    try:
        defaults = _compute_auto_supplier_defaults(request.user, global_scope=True)
        context.update(defaults)
    except Exception:
        pass

    return render(request, "admin_panel/cheque_editor.html", context)


@login_required
def cheque_edit(request, pk):
    is_admin = _is_admin(request.user)
    cheque = get_object_or_404(Cheque, pk=pk)
    suppliers_qs = [
        _enrich_supplier_for_display(s)
        for s in Supplier.objects.filter(status='active').order_by('account_name')
    ]
    funds_qs = FundCluster.objects.filter(is_active=True).order_by('code')
    error = request.GET.get('error') or None
    success = request.GET.get('success') or None

    if request.method == "POST":
        amount = _parse_money(request.POST.get('amount', '0'))
        payee_id = request.POST.get('payee_id') or None
        fund_id = request.POST.get('fund_cluster_id') or None
        cheque_date = request.POST.get('date') or date.today().isoformat()
        status = request.POST.get('status', cheque.status)
        fund_cluster = FundCluster.objects.filter(pk=fund_id, is_active=True).first() if fund_id else None

        if status == 'released':
            error = _fund_balance_error(fund_cluster, amount)

        if not error:
            cheque.payee_name = request.POST.get('payee_name', '').strip()
            cheque.amount = amount
            cheque.date = cheque_date
            cheque.purpose = request.POST.get('purpose', '').strip()
            cheque.bank_name = request.POST.get('bank_name', '').strip()
            cheque.account_number = request.POST.get('account_number', '').strip()
            cheque.status = status
            cheque.updated_by = request.user
            cheque.dv_payroll_no = request.POST.get('dv_payroll_no', '').strip()
            cheque.ors_burs_no = request.POST.get('ors_burs_no', '').strip()
            cheque.responsibility_center = request.POST.get('responsibility_center', '').strip()
            cheque.uacs_object_code = request.POST.get('uacs_object_code', '').strip()
            cheque.nature_of_payment = request.POST.get('nature_of_payment', '').strip()
            cheque.professional_tax = _parse_money(request.POST.get('professional_tax', '')) or None
            cheque.tax_5_3 = _parse_money(request.POST.get('tax_5_3', '')) or None
            cheque.tax_3_1 = _parse_money(request.POST.get('tax_3_1', '')) or None

            if payee_id:
                cheque.payee_id = payee_id
            if fund_id:
                cheque.fund_cluster_id = fund_id

            cheque.save()

            # Sync the new/edited cheque number directly to the supplier's check serial (mr_or)
            try:
                supplier_obj = cheque.payee
                if not supplier_obj and cheque.payee_name:
                    supplier_obj = Supplier.objects.filter(account_name__iexact=cheque.payee_name.strip()).first()
                if supplier_obj and cheque.cheque_number:
                    supplier_obj.mr_or = cheque.cheque_number
                    supplier_obj.save(update_fields=['mr_or'])
            except Exception as sync_err:
                logger.warning(f"Failed to sync cheque serial to supplier: {sync_err}")

            _audit(request.user, "Edited cheque", {"id": cheque.pk, "number": cheque.cheque_number})
            _invalidate_dashboard_cache()
            return redirect(reverse('cheque_create') + f"?success=Cheque %23{cheque.cheque_number} updated successfully.")

    context = _page_context(request, is_admin, "Edit Cheque", extra={
        "suppliers": suppliers_qs,
        "funds": funds_qs,
        "error": error,
        "success": success,
        "cheque": cheque,
        "editing": True,
    })
    return render(request, "admin_panel/cheque_editor.html", context)


@login_required
def cheque_print(request, pk):
    cheque = get_object_or_404(Cheque, pk=pk)
    cheque.printed_at = timezone.now()
    cheque.save(update_fields=['printed_at'])
    _audit(request.user, "Printed cheque", {"id": cheque.pk, "number": cheque.cheque_number})
    return render(request, "admin_panel/cheque_print.html", {"cheque": cheque})


@login_required
def cheque_void(request, pk):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    cheque = get_object_or_404(Cheque, pk=pk)
    reason = request.POST.get('reason', '').strip()
    cheque.status = 'voided'
    cheque.voided_at = timezone.now()
    cheque.void_reason = reason
    cheque.save(update_fields=['status', 'voided_at', 'void_reason'])
    _audit(request.user, "Voided cheque", {"id": cheque.pk, "number": cheque.cheque_number, "reason": reason})
    _invalidate_dashboard_cache()
    return JsonResponse({"ok": True})


@login_required
def cheque_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    cheque = get_object_or_404(Cheque, pk=pk)
    cheque_number = cheque.cheque_number
    cheque.delete()
    _audit(request.user, "Deleted cheque", {"number": cheque_number})
    _invalidate_dashboard_cache()
    return JsonResponse({"ok": True})


@login_required
def cheque_advance_status(request, pk):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    cheque = get_object_or_404(Cheque, pk=pk)
    current = cheque.status
    if current == 'draft':
        cheque.status = 'pending'
    elif current == 'pending':
        cheque.status = 'released'
        if cheque.stale_after_days:
            base_date = cheque.date or date.today()
            cheque.stale_at = base_date + timedelta(days=cheque.stale_after_days)
    else:
        return JsonResponse({"error": f"Cannot advance from {current}"}, status=400)
    cheque.save(update_fields=['status', 'stale_at'])
    _audit(request.user, "Advanced cheque status", {"id": cheque.pk, "from": current, "to": cheque.status})
    return JsonResponse({"ok": True, "new_status": cheque.status})


@login_required
def export_cheques(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="cheques_export.csv"'
    writer = csv.writer(response)
    writer.writerow(['Cheque Number', 'Payee', 'Amount', 'Date', 'Status', 'Fund Cluster'])
    for cheque in Cheque.objects.select_related('payee', 'fund_cluster').all():
        writer.writerow([
            cheque.cheque_number,
            cheque.payee_name,
            str(cheque.amount),
            cheque.date,
            cheque.status,
            cheque.fund_cluster.code if cheque.fund_cluster else '',
        ])
    return response
