from django import template
import email

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

    return email.utils.formatdate(float(value.strftime('%s')))
