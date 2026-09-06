import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai
from admin_panel.models import SystemSetting, AuditLog
from .utils import _is_admin, _get_profile, _page_context, _audit


@login_required
def reports(request):
    is_admin = _is_admin(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'generate':
            try:
                report_type = request.POST.get('report_type', 'cheque_summary')
                title = request.POST.get('title', f"Report - {date.today()}")
                date_from = request.POST.get('date_from')
                date_to = request.POST.get('date_to')

                cheque_qs = Cheque.objects.select_related('payee', 'fund_cluster').all()
                if date_from:
                    cheque_qs = cheque_qs.filter(date__gte=date_from)
                if date_to:
                    cheque_qs = cheque_qs.filter(date__lte=date_to)

                snapshot = {
                    "cheques": list(cheque_qs.values(
                        'pk', 'cheque_number', 'payee_name', 'amount', 'date', 'status',
                        'fund_cluster__code', 'bank_name', 'purpose'
                    )),
                    "total_count": cheque_qs.count(),
                    "total_amount": str(cheque_qs.aggregate(total=Sum('amount'))['total'] or 0),
                }

                report = Report.objects.create(
                    title=title,
                    report_type=report_type,
                    generated_by=request.user,
                    date_from=date_from,
                    date_to=date_to,
                    data_snapshot=snapshot,
                )
                _audit(request.user, "Generated report", {"id": report.pk, "type": report_type})
                success = f"Report '{title}' generated."
            except Exception as e:
                error = str(e)

    reports_list = Report.objects.filter(generated_by=request.user).order_by('-generated_at')[:50]
    context = _page_context(request, is_admin, "Reports", extra={
        "reports": reports_list,
        "error": error,
        "success": success,
    })
    return render(request, "admin_panel/reports.html", context)


@login_required
def report_delete(request, pk):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    report = get_object_or_404(Report, pk=pk)
    report.delete()
    _audit(request.user, "Deleted report", {"id": pk})
    return JsonResponse({"ok": True})


@login_required
def report_export_preview(request, pk):
    report = get_object_or_404(Report, pk=pk)
    return JsonResponse({
        "ok": True,
        "report": {
            "id": report.pk,
            "title": report.title,
            "type": report.report_type,
            "data": report.data_snapshot,
        }
    })


@login_required
def export_report_pdf(request, pk):
    report = get_object_or_404(Report, pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{report.title}.pdf"'

    from io import BytesIO
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as pdf_canvas

    buf = BytesIO()
    c = pdf_canvas.Canvas(buf)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(72, 750, report.title)
    c.setFont('Helvetica', 10)
    c.drawString(72, 730, f"Type: {report.report_type}")
    c.drawString(72, 715, f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M')}")

    y = 680
    c.setFont('Helvetica-Bold', 10)
    c.drawString(72, y, "Cheque Number")
    c.drawString(200, y, "Payee")
    c.drawString(350, y, "Amount")
    c.drawString(450, y, "Status")
    y -= 20

    c.setFont('Helvetica', 9)
    for cheque in report.data_snapshot.get('cheques', []):
        if y < 72:
            c.showPage()
            y = 750
        c.drawString(72, y, str(cheque.get('cheque_number', '')))
        c.drawString(200, y, str(cheque.get('payee_name', ''))[:30])
        c.drawString(350, y, str(cheque.get('amount', '')))
        c.drawString(450, y, str(cheque.get('status', '')))
        y -= 15

    c.showPage()
    c.save()
    buf.seek(0)
    response.write(buf.getvalue())
    return response


@login_required
def report_detail(request, pk):
    report = get_object_or_404(Report, pk=pk)
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, f"Report: {report.title}", extra={
        "report": report,
    })
    return render(request, "admin_panel/report_detail.html", context)


@login_required
def report_print(request, pk):
    report = get_object_or_404(Report, pk=pk)
    return render(request, "admin_panel/report_print.html", {"report": report})


@login_required
def report_print_modal(request, pk):
    report = get_object_or_404(Report, pk=pk)
    return JsonResponse({
        "ok": True,
        "report": {
            "id": report.pk,
            "title": report.title,
            "type": report.report_type,
            "data": report.data_snapshot,
        }
    })
