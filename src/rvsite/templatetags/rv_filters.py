from datetime import timezone

from django import template
from email.utils import formatdate

register = template.Library()


@register.filter
def get_at_index(list, index):
    return list[index]


@register.filter
def get_first_at_index(list, index):
    try:
        return [list[index][0]]
    except Exception:
        return None


@register.filter(name='feed_datetime')
def feed_datetime(value):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return formatdate(value.timestamp(), usegmt=True)
