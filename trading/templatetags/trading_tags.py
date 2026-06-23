from django import template

register = template.Library()


TYPE_COLORS = {
    'fire': '#F08030',
    'water': '#6890F0',
    'grass': '#78C850',
    'electric': '#F8D030',
    'psychic': '#F85888',
    'fighting': '#C03028',
    'dark': '#705848',
    'steel': '#B8B8D0',
    'dragon': '#7038F8',
    'fairy': '#EE99AC',
    'normal': '#A8A878',
    'flying': '#A890F0',
    'ground': '#E0C068',
    'rock': '#B8A038',
    'bug': '#A8B820',
    'ghost': '#705898',
    'ice': '#98D8D8',
    'poison': '#A040A0',
}

TYPE_BG_COLORS = {
    'fire': 'rgba(240, 128, 48, 0.15)',
    'water': 'rgba(104, 144, 240, 0.15)',
    'grass': 'rgba(120, 200, 80, 0.15)',
    'electric': 'rgba(248, 208, 48, 0.15)',
    'psychic': 'rgba(248, 88, 136, 0.15)',
    'fighting': 'rgba(192, 48, 40, 0.15)',
    'dark': 'rgba(112, 88, 72, 0.15)',
    'steel': 'rgba(184, 184, 208, 0.15)',
    'dragon': 'rgba(112, 56, 248, 0.15)',
    'fairy': 'rgba(238, 153, 172, 0.15)',
    'normal': 'rgba(168, 168, 120, 0.15)',
    'flying': 'rgba(168, 144, 240, 0.15)',
    'ground': 'rgba(224, 192, 104, 0.15)',
    'rock': 'rgba(184, 160, 56, 0.15)',
    'bug': 'rgba(168, 184, 32, 0.15)',
    'ghost': 'rgba(112, 88, 152, 0.15)',
    'ice': 'rgba(152, 216, 216, 0.15)',
    'poison': 'rgba(160, 64, 160, 0.15)',
}


@register.filter
def type_color(type_name):
    """Returns the color hex for a Pokémon type."""
    return TYPE_COLORS.get(type_name.lower(), '#A8A878')


@register.filter
def type_bg(type_name):
    """Returns a semi-transparent background color for a type badge."""
    return TYPE_BG_COLORS.get(type_name.lower(), 'rgba(168, 168, 120, 0.15)')
