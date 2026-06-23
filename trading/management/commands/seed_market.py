import random
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from account.models import Account
from creature.models import Creature, Ability, PrimaryType


# Pokémon data with balanced stats for trading simulation
STARTER_CREATURES = [
    {
        'name': 'Charizard', 'type': 'fire', 'secondary_type': 'flying',
        'hp': 78, 'attack': 84, 'defense': 78,
        'special_attack': 109, 'special_defense': 85, 'speed': 100,
        'current_price': Decimal('245.00'), 'description': 'A fire-breathing dragon. Its flames grow hotter in battle.',
    },
    {
        'name': 'Blastoise', 'type': 'water',
        'hp': 79, 'attack': 83, 'defense': 100,
        'special_attack': 85, 'special_defense': 105, 'speed': 78,
        'current_price': Decimal('220.00'), 'description': 'A massive tortoise with water cannons on its shell.',
    },
    {
        'name': 'Venusaur', 'type': 'grass', 'secondary_type': 'poison',
        'hp': 80, 'attack': 82, 'defense': 83,
        'special_attack': 100, 'special_defense': 100, 'speed': 80,
        'current_price': Decimal('210.00'), 'description': 'The flower on its back absorbs sunlight to fuel devastating attacks.',
    },
    {
        'name': 'Pikachu', 'type': 'electric',
        'hp': 35, 'attack': 55, 'defense': 40,
        'special_attack': 50, 'special_defense': 50, 'speed': 90,
        'current_price': Decimal('180.00'), 'description': 'An electric mouse that stores electricity in its cheeks.',
    },
    {
        'name': 'Gengar', 'type': 'ghost', 'secondary_type': 'poison',
        'hp': 60, 'attack': 65, 'defense': 60,
        'special_attack': 130, 'special_defense': 75, 'speed': 110,
        'current_price': Decimal('310.00'), 'description': 'A shadow lurker that drains life force from its opponents.',
    },
    {
        'name': 'Dragonite', 'type': 'dragon', 'secondary_type': 'flying',
        'hp': 91, 'attack': 134, 'defense': 95,
        'special_attack': 100, 'special_defense': 100, 'speed': 80,
        'current_price': Decimal('450.00'), 'description': 'A gentle giant of the skies. Incredibly powerful in battle.',
        'is_legendary': False,
    },
    {
        'name': 'Mewtwo', 'type': 'psychic',
        'hp': 106, 'attack': 110, 'defense': 90,
        'special_attack': 154, 'special_defense': 90, 'speed': 130,
        'current_price': Decimal('890.00'), 'description': 'An artificial Pokémon created through genetic engineering.',
        'is_legendary': True,
    },
    {
        'name': 'Lucario', 'type': 'fighting', 'secondary_type': 'steel',
        'hp': 70, 'attack': 110, 'defense': 70,
        'special_attack': 115, 'special_defense': 70, 'speed': 90,
        'current_price': Decimal('275.00'), 'description': 'Senses the aura of all things. Fights with honor and precision.',
    },
    {
        'name': 'Garchomp', 'type': 'dragon', 'secondary_type': 'ground',
        'hp': 108, 'attack': 130, 'defense': 95,
        'special_attack': 80, 'special_defense': 85, 'speed': 102,
        'current_price': Decimal('420.00'), 'description': 'A land shark that flies at jet speed. Apex predator.',
    },
    {
        'name': 'Tyranitar', 'type': 'rock', 'secondary_type': 'dark',
        'hp': 100, 'attack': 134, 'defense': 110,
        'special_attack': 95, 'special_defense': 100, 'speed': 61,
        'current_price': Decimal('380.00'), 'description': 'Its body cannot be harmed by any attack. Extremely destructive.',
    },
    {
        'name': 'Gyarados', 'type': 'water', 'secondary_type': 'flying',
        'hp': 95, 'attack': 125, 'defense': 79,
        'special_attack': 60, 'special_defense': 100, 'speed': 81,
        'current_price': Decimal('290.00'), 'description': 'Once a weak fish, it evolved into a raging sea serpent.',
    },
    {
        'name': 'Sylveon', 'type': 'fairy',
        'hp': 95, 'attack': 65, 'defense': 65,
        'special_attack': 110, 'special_defense': 130, 'speed': 60,
        'current_price': Decimal('195.00'), 'description': 'Wraps its ribbon-like feelers around its trainer to sense emotions.',
    },
    {
        'name': 'Scizor', 'type': 'bug', 'secondary_type': 'steel',
        'hp': 70, 'attack': 130, 'defense': 100,
        'special_attack': 55, 'special_defense': 80, 'speed': 65,
        'current_price': Decimal('260.00'), 'description': 'Its pincers are made of steel. A precision striker.',
    },
    {
        'name': 'Alakazam', 'type': 'psychic',
        'hp': 55, 'attack': 50, 'defense': 45,
        'special_attack': 135, 'special_defense': 95, 'speed': 120,
        'current_price': Decimal('305.00'), 'description': 'Its brain continuously grows, making it smarter over time.',
    },
    {
        'name': 'Rayquaza', 'type': 'dragon', 'secondary_type': 'flying',
        'hp': 105, 'attack': 150, 'defense': 90,
        'special_attack': 150, 'special_defense': 90, 'speed': 95,
        'current_price': Decimal('1200.00'), 'description': 'Lives in the ozone layer. The mediator between Kyogre and Groudon.',
        'is_legendary': True,
    },
    {
        'name': 'Mew', 'type': 'psychic',
        'hp': 100, 'attack': 100, 'defense': 100,
        'special_attack': 100, 'special_defense': 100, 'speed': 100,
        'current_price': Decimal('950.00'), 'description': 'Contains the DNA of every Pokémon. Extremely rare.',
        'is_mythical': True,
    },
]

STARTER_ABILITIES = [
    {'name': 'Flamethrower', 'ability_type': 'fire', 'damage_class': 'special', 'power': 90, 'accuracy': 100, 'pp': 15},
    {'name': 'Hydro Pump', 'ability_type': 'water', 'damage_class': 'special', 'power': 110, 'accuracy': 80, 'pp': 5},
    {'name': 'Solar Beam', 'ability_type': 'grass', 'damage_class': 'special', 'power': 120, 'accuracy': 100, 'pp': 10},
    {'name': 'Thunderbolt', 'ability_type': 'electric', 'damage_class': 'special', 'power': 90, 'accuracy': 100, 'pp': 15},
    {'name': 'Shadow Ball', 'ability_type': 'ghost', 'damage_class': 'special', 'power': 80, 'accuracy': 100, 'pp': 15},
    {'name': 'Dragon Claw', 'ability_type': 'dragon', 'damage_class': 'physical', 'power': 80, 'accuracy': 100, 'pp': 15},
    {'name': 'Psychic', 'ability_type': 'psychic', 'damage_class': 'special', 'power': 90, 'accuracy': 100, 'pp': 10},
    {'name': 'Aura Sphere', 'ability_type': 'fighting', 'damage_class': 'special', 'power': 80, 'accuracy': 100, 'pp': 20},
    {'name': 'Iron Head', 'ability_type': 'steel', 'damage_class': 'physical', 'power': 80, 'accuracy': 100, 'pp': 15},
    {'name': 'Earthquake', 'ability_type': 'ground', 'damage_class': 'physical', 'power': 100, 'accuracy': 100, 'pp': 10},
    {'name': 'Stone Edge', 'ability_type': 'rock', 'damage_class': 'physical', 'power': 100, 'accuracy': 80, 'pp': 5},
    {'name': 'Moonblast', 'ability_type': 'fairy', 'damage_class': 'special', 'power': 95, 'accuracy': 100, 'pp': 15},
    {'name': 'X-Scissor', 'ability_type': 'bug', 'damage_class': 'physical', 'power': 80, 'accuracy': 100, 'pp': 15},
    {'name': 'Ice Beam', 'ability_type': 'ice', 'damage_class': 'special', 'power': 90, 'accuracy': 100, 'pp': 10},
    {'name': 'Sludge Bomb', 'ability_type': 'poison', 'damage_class': 'special', 'power': 90, 'accuracy': 100, 'pp': 10},
    {'name': 'Dark Pulse', 'ability_type': 'dark', 'damage_class': 'special', 'power': 80, 'accuracy': 100, 'pp': 15},
    {'name': 'Air Slash', 'ability_type': 'flying', 'damage_class': 'special', 'power': 75, 'accuracy': 95, 'pp': 15},
    {'name': 'Tackle', 'ability_type': 'normal', 'damage_class': 'physical', 'power': 40, 'accuracy': 100, 'pp': 35},
    {'name': 'Quick Attack', 'ability_type': 'normal', 'damage_class': 'physical', 'power': 40, 'accuracy': 100, 'pp': 30, 'priority': 1},
    {'name': 'Hyper Beam', 'ability_type': 'normal', 'damage_class': 'special', 'power': 150, 'accuracy': 90, 'pp': 5},
]

# Mapping: creature name → list of ability names
CREATURE_ABILITIES = {
    'Charizard': ['Flamethrower', 'Air Slash', 'Dragon Claw', 'Hyper Beam'],
    'Blastoise': ['Hydro Pump', 'Ice Beam', 'Tackle', 'Hyper Beam'],
    'Venusaur': ['Solar Beam', 'Sludge Bomb', 'Tackle', 'Hyper Beam'],
    'Pikachu': ['Thunderbolt', 'Quick Attack', 'Tackle', 'Iron Head'],
    'Gengar': ['Shadow Ball', 'Sludge Bomb', 'Psychic', 'Dark Pulse'],
    'Dragonite': ['Dragon Claw', 'Hyper Beam', 'Earthquake', 'Ice Beam'],
    'Mewtwo': ['Psychic', 'Shadow Ball', 'Aura Sphere', 'Hyper Beam'],
    'Lucario': ['Aura Sphere', 'Iron Head', 'Dark Pulse', 'Quick Attack'],
    'Garchomp': ['Dragon Claw', 'Earthquake', 'Stone Edge', 'Iron Head'],
    'Tyranitar': ['Stone Edge', 'Dark Pulse', 'Earthquake', 'Iron Head'],
    'Gyarados': ['Hydro Pump', 'Dragon Claw', 'Ice Beam', 'Hyper Beam'],
    'Sylveon': ['Moonblast', 'Psychic', 'Shadow Ball', 'Quick Attack'],
    'Scizor': ['X-Scissor', 'Iron Head', 'Quick Attack', 'Hyper Beam'],
    'Alakazam': ['Psychic', 'Shadow Ball', 'Thunderbolt', 'Hyper Beam'],
    'Rayquaza': ['Dragon Claw', 'Air Slash', 'Hyper Beam', 'Earthquake'],
    'Mew': ['Psychic', 'Aura Sphere', 'Flamethrower', 'Thunderbolt'],
}


class Command(BaseCommand):
    help = 'Seed the market with starter creatures, abilities, and a Market Maker account.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing creatures and abilities before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            self.stdout.write('Clearing existing data...')
            Creature.objects.all().delete()
            Ability.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared all creatures and abilities.'))

        # Create Market Maker account
        mm_account, mm_created = Account.objects.get_or_create(
            username='market_maker',
            defaults={
                'email': 'market_maker@brokerdex.io',
                'balance_cents': 100_000_000_00,  # $100M
                'is_active': True,
            }
        )
        if mm_created:
            mm_account.set_password('market_maker_internal_2026')
            mm_account.save()
            self.stdout.write(self.style.SUCCESS('✓ Created Market Maker account'))
        else:
            self.stdout.write('  Market Maker account already exists.')

        # Create abilities
        ability_map = {}
        for ab_data in STARTER_ABILITIES:
            ability, created = Ability.objects.get_or_create(
                name=ab_data['name'],
                defaults=ab_data
            )
            ability_map[ability.name] = ability
            status = '✓' if created else '—'
            self.stdout.write(f'  {status} Ability: {ability.name}')

        # Create creatures
        now = timezone.now()
        for c_data in STARTER_CREATURES:
            c_data_copy = c_data.copy()
            c_data_copy['previous_close'] = c_data_copy['current_price']
            c_data_copy['battle_cooldown'] = timedelta(hours=1)
            c_data_copy['cooldown_expires_at'] = now

            creature, created = Creature.objects.get_or_create(
                name=c_data_copy.pop('name'),
                defaults=c_data_copy,
            )

            if created:
                # Assign abilities
                ability_names = CREATURE_ABILITIES.get(creature.name, [])
                for ab_name in ability_names:
                    if ab_name in ability_map:
                        creature.abilities.add(ability_map[ab_name])

                self.stdout.write(self.style.SUCCESS(
                    f'✓ {creature.name} — ${creature.current_price} '
                    f'({creature.type}) [{len(ability_names)} abilities]'
                ))
            else:
                self.stdout.write(f'  — {creature.name} already exists.')

        total = Creature.objects.count()
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Seeding complete! {total} creatures in the market.'
        ))
