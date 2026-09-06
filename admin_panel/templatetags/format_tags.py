import json
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def json_format(value):
    """Format a dict/JSON value as readable JSON string."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    try:
        formatted = json.dumps(value, indent=2, ensure_ascii=False)
        return mark_safe(f'<pre style="margin:0; font-family:inherit; font-size:inherit; white-space:pre-wrap;">{formatted}</pre>')
    except (TypeError, ValueError):
        return str(value)


@register.filter
def json_inline(value):
    """Format a dict/JSON value as compact inline JSON."""
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
