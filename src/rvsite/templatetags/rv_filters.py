from django import template

register = template.Library()

@register.filter
def get_at_index(list, index):
    return list[index]
    
@register.filter
def get_first_at_index(list, index):
    try:
        return [list[index][0]]
    except:
        return None