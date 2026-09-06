import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models import Sum, Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from cashier.models import Profile, Transaction, FundCluster, Supplier, Cheque, Report, Radai, RadaiSetting
from admin_panel.models import SystemSetting, AuditLog, ManagementOption, AccountTitleGroup
from .utils import _is_admin, _get_profile, _page_context, _audit, _parse_money


@login_required
def admin_dashboard(request):
    is_admin = True
    settings_obj = SystemSetting.get_settings()
    total_cheques = Cheque.objects.count()
    total_released = Cheque.objects.filter(status='released').count()
    total_pending = Cheque.objects.filter(status='pending').count()
    total_voided = Cheque.objects.filter(status='voided').count()
    total_stale = Cheque.objects.filter(status='stale').count()
    total_suppliers = Supplier.objects.filter(status='active').count()
    total_fund_clusters = FundCluster.objects.filter(is_active=True).count()
    total_reports = Report.objects.count()
    total_users = User.objects.count()
    recent_cheques = Cheque.objects.select_related('payee', 'fund_cluster').order_by('-created_at')[:10]
    recent_suppliers = Supplier.objects.order_by('-created_at')[:5]
    context = _page_context(request, is_admin, "Admin Dashboard", extra={
        "settings": settings_obj,
        "total_cheques": total_cheques,
        "total_released": total_released,
        "total_pending": total_pending,
        "total_voided": total_voided,
        "total_stale": total_stale,
        "total_suppliers": total_suppliers,
        "total_fund_clusters": total_fund_clusters,
        "total_reports": total_reports,
        "total_users": total_users,
        "recent_cheques": recent_cheques,
        "recent_suppliers": recent_suppliers,
    })
    return render(request, "admin_panel/dashboard.html", context)


@login_required
def cashier_dashboard(request):
    is_admin = _is_admin(request.user)
    settings_obj = SystemSetting.get_settings()
    total_cheques = Cheque.objects.count()
    total_released = Cheque.objects.filter(status='released').count()
    total_pending = Cheque.objects.filter(status='pending').count()
    total_suppliers = Supplier.objects.filter(status='active').count()
    recent_cheques = Cheque.objects.select_related('payee', 'fund_cluster').order_by('-created_at')[:10]
    context = _page_context(request, is_admin, "Cashier Dashboard", extra={
        "settings": settings_obj,
        "total_cheques": total_cheques,
        "total_released": total_released,
        "total_pending": total_pending,
        "total_suppliers": total_suppliers,
        "recent_cheques": recent_cheques,
    })
    return render(request, "cashier/dashboard.html", context)


@login_required
def admin_dashboard_chart_data(request):
    cache_key = 'admin_dashboard_chart_data'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    months = []
    now = timezone.now()
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        months.append(d.strftime('%b %Y'))
    released_by_month = []
    pending_by_month = []
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        month_start = d.replace(day=1)
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1)
        released_count = Cheque.objects.filter(status='released', date__gte=month_start, date__lt=month_end).count()
        pending_count = Cheque.objects.filter(status='pending', date__gte=month_start, date__lt=month_end).count()
        released_by_month.append(released_count)
        pending_by_month.append(pending_count)
    result = {
        "labels": months,
        "released": released_by_month,
        "pending": pending_by_month,
    }
    cache.set(cache_key, result, 300)
    return JsonResponse(result)


@login_required
def admin_chart_released_data(request):
    cache_key = 'admin_chart_released_data'
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    months = []
    now = timezone.now()
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        months.append(d.strftime('%b %Y'))
    released_amounts = []
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        month_start = d.replace(day=1)
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1)
        total = Cheque.objects.filter(status='released', date__gte=month_start, date__lt=month_end).aggregate(total=Sum('amount'))['total'] or 0
        released_amounts.append(float(total))
    result = {
        "labels": months,
        "amounts": released_amounts,
    }
    cache.set(cache_key, result, 300)
    return JsonResponse(result)


@login_required
def admin_suppliers_chart_data(request):
    suppliers_by_month = []
    now = timezone.now()
    months = []
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        months.append(d.strftime('%b %Y'))
        month_start = d.replace(day=1)
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1)
        count = Supplier.objects.filter(created_at__date__gte=month_start, created_at__date__lt=month_end).count()
        suppliers_by_month.append(count)
    return JsonResponse({
        "labels": months,
        "data": suppliers_by_month,
    })


@login_required
def admin_fund_clusters_chart_data(request):
    from cashier.models import FundCluster
    clusters = FundCluster.objects.filter(is_active=True).values('code', 'name', 'balance')
    labels = [c['code'] for c in clusters]
    balances = [float(c['balance'] or 0) for c in clusters]
    return JsonResponse({"labels": labels, "balances": balances})


@login_required
def next_supplier_sequences(request):
    return JsonResponse({"ok": True})


@login_required
def cashier_dashboard_chart_data(request):
    months = []
    now = timezone.now()
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        months.append(d.strftime('%b %Y'))
    released_by_month = []
    pending_by_month = []
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        month_start = d.replace(day=1)
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1)
        released_count = Cheque.objects.filter(status='released', date__gte=month_start, date__lt=month_end).count()
        pending_count = Cheque.objects.filter(status='pending', date__gte=month_start, date__lt=month_end).count()
        released_by_month.append(released_count)
        pending_by_month.append(pending_count)
    return JsonResponse({
        "labels": months,
        "released": released_by_month,
        "pending": pending_by_month,
    })


@login_required
def cashier_suppliers_chart_data(request):
    suppliers_by_month = []
    now = timezone.now()
    months = []
    for i in range(5, -1, -1):
        d = (now - timedelta(days=30 * i)).date()
        months.append(d.strftime('%b %Y'))
        month_start = d.replace(day=1)
        if d.month == 12:
            month_end = d.replace(year=d.year + 1, month=1, day=1)
        else:
            month_end = d.replace(month=d.month + 1, day=1)
        count = Supplier.objects.filter(created_at__date__gte=month_start, created_at__date__lt=month_end).count()
        suppliers_by_month.append(count)
    return JsonResponse({
        "labels": months,
        "data": suppliers_by_month,
    })


@login_required
def admin_transactions(request):
    is_admin = True
    context = _page_context(request, is_admin, "Transactions")
    return render(request, "admin_panel/transactions.html", context)


@login_required
def cashier_transactions(request):
    is_admin = _is_admin(request.user)
    context = _page_context(request, is_admin, "Transactions")
    return render(request, "cashier/transactions.html", context)
