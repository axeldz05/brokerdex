from django import template

register = template.Library()

@register.filter
def get_type_color(type_name):
    color_map = {
        'water': '#1e90ff',
        'fire': '#ff4500',
        'earth': '#8b4513',
        'air': '#87ceeb',
        'electric': '#ffd700',
        'ice': '#add8e6',
        'grass': '#32cd32',
    }
    return color_map.get(type_name.lower(), '#000000')
