import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from cashier.models import Profile, Cheque, Supplier, Radai, FundCluster
from admin_panel.models import SystemSetting, AuditLog, ChatMessage, ChatbotConfig
from .utils import _is_admin, _get_profile, _page_context, _audit


FINANCIAL_SYSTEM_PROMPT = """You are a financial assistant for the Automated Cashier Registry Check System. You help users with:
- Check status inquiries
- Supplier information
- Fund cluster balances
- Report generation
- System navigation

Always be helpful and professional. If you don't know something, say so."""


def _fetch_live_financial_data():
    return {
        "total_cheques": Cheque.objects.count(),
        "released_cheques": Cheque.objects.filter(status='released').count(),
        "pending_cheques": Cheque.objects.filter(status='pending').count(),
        "total_suppliers": Supplier.objects.filter(status='active').count(),
        "total_fund_clusters": FundCluster.objects.filter(is_active=True).count(),
    }


def _call_ai_provider(prompt, system_prompt=""):
    try:
        config = ChatbotConfig.objects.first()
        if not config or not config.api_key:
            return "AI service not configured."

        import urllib.request
        data = json.dumps({
            "model": config.model_name or "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": system_prompt or FINANCIAL_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 500,
        }).encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())
            return result['choices'][0]['message']['content']
    except Exception as e:
        return f"Error: {str(e)}"


@login_required
def chatbot_setup(request):
    is_admin = _is_admin(request.user)
    config = ChatbotConfig.objects.first()
    context = _page_context(request, is_admin, "Chatbot Setup", extra={
        "config": config,
    })
    return render(request, "admin_panel/chatbot_setup.html", context)


@login_required
def chatbot_config_api(request):
    if request.method == 'GET':
        config = ChatbotConfig.objects.first()
        return JsonResponse({
            "ok": True,
            "config": {
                "provider": config.provider if config else "",
                "api_key_set": bool(config.api_key) if config else False,
                "model_name": config.model_name if config else "",
            }
        })
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            config, _ = ChatbotConfig.objects.get_or_create(pk=1)
            config.provider = data.get('provider', '')
            if data.get('api_key'):
                config.api_key = data['api_key']
            config.model_name = data.get('model_name', '')
            config.save()
            return JsonResponse({"ok": True})
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@login_required
def chatbot_message_api(request):
    if request.method != 'POST':
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        if not user_message:
            return JsonResponse({"error": "Empty message"}, status=400)

        ChatMessage.objects.create(user=request.user, role='user', content=user_message)

        financial_data = _fetch_live_financial_data()
        context_prompt = f"Current system data: {json.dumps(financial_data)}\n\nUser question: {user_message}"
        ai_response = _call_ai_provider(context_prompt)

        ChatMessage.objects.create(user=request.user, role='assistant', content=ai_response)
        return JsonResponse({"ok": True, "response": ai_response})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def chatbot_clear_history(request):
    ChatMessage.objects.filter(user=request.user).delete()
    return JsonResponse({"ok": True})


@login_required
def chatbot_history_api(request):
    messages = ChatMessage.objects.filter(user=request.user).order_by('created_at')[:50]
    return JsonResponse({
        "ok": True,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in messages
        ]
    })
