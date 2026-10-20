from django import template
from trading.templatetags.trading_tags import TYPE_COLORS

register = template.Library()


@register.filter
def get_type_color(type_name):
    if not type_name:
        return '#A8A878'
    return TYPE_COLORS.get(str(type_name).lower(), '#A8A878')
