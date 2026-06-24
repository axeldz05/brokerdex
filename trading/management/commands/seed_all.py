import json
import os
import random
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction
from django.conf import settings

from account.models import Account
from creature.models import Creature, Ability, EggTemplate, Battle, BattleParticipant, BattleAction



DATA_DIR = os.path.join(settings.BASE_DIR, 'seed_data')


def load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        raise CommandError(f"Data file not found: {path}")
    with open(path) as f:
        return json.load(f)


def get_or_none(model_class, **kwargs):
    try:
        return model_class.objects.get(**kwargs)
    except model_class.DoesNotExist:
        return None


class Command(BaseCommand):
    help = 'Seed the database with raw Pokémon data from JSON files.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing Creatures, Abilities, and EggTemplates before seeding.',
        )
        parser.add_argument(
            '--demo',
            action='store_true',
            help='Also create demo data: users, orders, battles, and incubations.',
        )

    def handle(self, *args, **options):
        clear = options.get('clear')
        demo = options.get('demo')

        if clear:
            self._clear_data()

        self._seed_abilities()
        self._seed_creatures()
        self._seed_egg_templates()

        if demo:
            self._seed_demo_users()
            self._create_demo_orders()
            self._create_demo_battles()
            self._create_demo_incubations()
            self._seed_market_maker()

        self.stdout.write(self.style.SUCCESS('Seeding complete!'))

    def _clear_data(self):
        self.stdout.write('Clearing existing data...')
        EggTemplate.objects.all().delete()
        Creature.objects.all().delete()
        Ability.objects.all().delete()

    def _seed_abilities(self):
        abilities_data = load_json('abilities.json')
        created = 0
        for data in abilities_data:
            name = data.pop('name')
            clean_data = {k: v for k, v in data.items() if v is not None}
            _, was_created = Ability.objects.get_or_create(
                name=name,
                defaults=clean_data,
            )
            if was_created:
                created += 1
        self.stdout.write(f"  Abilities: {created} created / {len(abilities_data)} total")

    def _seed_creatures(self):
        creatures_data = load_json('creatures.json')
        created = 0
        for data in creatures_data:
            ability_names = data.pop('abilities', [])
            base_stats = data.pop('base_stats', {})

            # Build valid model fields from raw data
            creature_defaults = {
                'description': data.get('description', ''),
                'type': data['type'],
                'secondary_type': data.get('secondary_type'),
                'hp': base_stats.get('hp', 50),
                'attack': base_stats.get('attack', 50),
                'defense': base_stats.get('defense', 50),
                'special_attack': base_stats.get('special_attack', 50),
                'special_defense': base_stats.get('special_defense', 50),
                'speed': base_stats.get('speed', 50),
                'is_legendary': data.get('is_legendary', False),
                'is_mythical': data.get('is_mythical', False),
                'current_price': Decimal(str(data['initial_price'])),
                'previous_close': Decimal(str(data['initial_price'])),
                'battle_cooldown': timedelta(hours=1),
                'cooldown_expires_at': timezone.now(),
            }

            name = data['name']
            creature, was_created = Creature.objects.get_or_create(
                name=name,
                defaults=creature_defaults,
            )
            if was_created:
                created += 1

            for ab_name in ability_names:
                ability = get_or_none(Ability, name=ab_name)
                if ability:
                    try:
                        creature.abilities.add(ability)
                    except Exception:
                        pass
                else:
                    self.stdout.write(self.style.WARNING(
                        f"    Warning: Ability '{ab_name}' not found for {name}"
                    ))

        self.stdout.write(f"  Creatures: {created} created / {len(creatures_data)} total")

    def _seed_egg_templates(self):
        eggs_data = load_json('egg_templates.json')
        created = 0
        for data in eggs_data:
            pool_names = data.pop('creature_pool', [])
            hatch_hours = data.pop('hatch_duration_hours', 1)
            name = data['name']

            egg, was_created = EggTemplate.objects.get_or_create(
                name=name,
                defaults={
                    **data,
                    'hatch_duration': timedelta(hours=hatch_hours),
                }
            )
            if was_created:
                created += 1

            for c_name in pool_names:
                creature = get_or_none(Creature, name=c_name)
                if creature:
                    egg.creature_pool.add(creature)
                else:
                    self.stdout.write(self.style.WARNING(
                        f"    Warning: Creature '{c_name}' not found for egg '{name}'"
                    ))

        self.stdout.write(f"  Egg Templates: {created} created / {len(eggs_data)} total")

    def _seed_demo_users(self):
        users_data = load_json('demo_users.json')
        created = 0
        for data in users_data:
            balance = int(Decimal(str(data['balance'])) * 100)
            user, was_created = Account.objects.get_or_create(
                username=data['username'],
                defaults={
                    'email': data['email'],
                    'balance_cents': balance,
                },
            )
            if was_created:
                user.set_password(data['password'])
                user.save()
                created += 1
            elif user.balance_cents == 0:
                user.balance_cents = balance
                user.save()

        self.stdout.write(f"  Demo users: {created} created / {len(users_data)} total")

    def _seed_market_maker(self):
        _, created = Account.objects.get_or_create(
            username='market_maker',
            defaults={
                'email': 'market_maker@brokerdex.io',
                'balance_cents': 100_000_000_00,
                'is_active': True,
            }
        )
        if created:
            mm = Account.objects.get(username='market_maker')
            mm.set_password('market_maker_internal_2026')
            mm.save()
            self.stdout.write('  Market Maker account created.')

    def _create_demo_orders(self):
        orders_data = load_json('demo_orders.json')
        from trading.services import TradingEngine
        created = 0
        for data in orders_data:
            username = data['username']
            creature_name = data['creature']
            order_type = data['order_type']
            execution_type = data['execution_type']
            quantity = Decimal(str(data['quantity']))

            user = get_or_none(Account, username=username)
            creature = get_or_none(Creature, name=creature_name)

            if not user:
                self.stdout.write(self.style.WARNING(f"    Order: user '{username}' not found"))
                continue
            if not creature:
                self.stdout.write(self.style.WARNING(f"    Order: creature '{creature_name}' not found"))
                continue

            try:
                if execution_type == 'MARKET':
                    if order_type == 'BUY':
                        TradingEngine.execute_market_buy(user, creature, quantity)
                    else:
                        TradingEngine.execute_market_sell(user, creature, quantity)
                else:
                    limit_price = Decimal(str(data['limit_price']))
                    TradingEngine.place_limit_order(
                        user, creature, order_type, quantity, limit_price
                    )
                created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f"    Order failed: {username} {order_type} {quantity}x{creature_name}: {e}"
                ))

        self.stdout.write(f"  Demo orders: {created} executed / {len(orders_data)} total")

    def _create_demo_battles(self):
        battles_data = load_json('demo_battles.json')
        from trading.services import PricingEngine
        created = 0
        for data in battles_data:
            c1 = get_or_none(Creature, name=data['creature_1'])
            c2 = get_or_none(Creature, name=data['creature_2'])

            if not c1 or not c2:
                self.stdout.write(self.style.WARNING(
                    f"    Battle: creature not found ({data['creature_1']} or {data['creature_2']})"
                ))
                continue

            try:
                battle = Battle.objects.start_battle(c1, c2)
                participants = list(battle.participants.select_related('creature').all())

                # Set high HP so all demo turns can execute
                for p in participants:
                    p.current_hp = 999
                    p.save(update_fields=['current_hp'])

                for turn_data in data['log']:
                    attacker_name = turn_data['attacker']
                    ability_name = turn_data['ability']
                    damage = turn_data['damage']

                    attacker_p = next(
                        (p for p in participants if p.creature.name == attacker_name), None
                    )
                    defender_p = next(
                        (p for p in participants if p.creature.name != attacker_name), None
                    )
                    ability = get_or_none(Ability, name=ability_name)

                    if attacker_p and defender_p:
                        battle.record_action(attacker_p, defender_p, ability, damage)

                # Sync price updates for seeded battles
                for p in participants:
                    PricingEngine.update_creature_price(p.creature)

                created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f"    Battle failed ({data['creature_1']} vs {data['creature_2']}): {e}"
                ))

        self.stdout.write(f"  Demo battles: {created} created / {len(battles_data)} total")

    def _create_demo_incubations(self):
        from creature.models import Incubation
        eggs = list(EggTemplate.objects.filter(is_active=True))
        users = list(Account.objects.exclude(username='market_maker'))
        created = 0

        if not eggs or not users:
            self.stdout.write(self.style.WARNING('    Incubations: no eggs or users available'))
            return

        for i, user in enumerate(users):
            egg = eggs[i % len(eggs)]
            try:
                with transaction.atomic():
                    locked_user = Account.objects.select_for_update().get(pk=user.pk)
                    cost_cents = int(egg.price * 100)
                    if locked_user.balance_cents >= cost_cents:
                        locked_user.balance_cents -= cost_cents
                        locked_user.save(update_fields=['balance_cents'])
                        Incubation.objects.create(
                            user=locked_user,
                            egg_template=egg,
                            hatches_at=timezone.now() + egg.hatch_duration,
                        )
                        created += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f"    Incubation failed for {user.username}: {e}"
                ))

        self.stdout.write(f"  Demo incubations: {created} started")
