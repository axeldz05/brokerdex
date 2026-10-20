import json
import os
import random
import urllib.request
from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction
from django.conf import settings
from django.core.files.base import ContentFile

from account.money import quantize_money
from account.models import Account
from creature.models import (
    Creature,
    Ability,
    EggTemplate,
    Battle,
    BattleParticipant,
    BattleAction,
    BattleInvestment,
    Incubation,
)
from creature.services import BattleService
from trading.models import PriceHistory, Trade, Portfolio



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


POKEAPI_SPRITE_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
POKEAPI_ARTWORK_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork"

POKEAPI_ID_MAP = {
    "Charizard": 6, "Blastoise": 9, "Venusaur": 3, "Pikachu": 25,
    "Raichu": 26, "Gengar": 94, "Dragonite": 149, "Mewtwo": 150,
    "Lucario": 448, "Garchomp": 445, "Tyranitar": 248, "Gyarados": 130,
    "Sylveon": 700, "Scizor": 212, "Alakazam": 65, "Rayquaza": 384,
    "Mew": 151, "Greninja": 658, "Gardevoir": 282, "Metagross": 376,
}


def fetch_sprite(url, filename):
    """Download a sprite from PokeAPI and return a ContentFile, or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Brokerdex/1.0"})
        data = urllib.request.urlopen(req, timeout=10).read()
        return ContentFile(data, name=filename)
    except Exception:
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
        parser.add_argument(
            '--simulation',
            action='store_true',
            help='Create large-scale simulation population: ~100 users, 30 days of OHLCV history, distributed portfolios, battles, and incubations.',
        )

    def handle(self, *args, **options):
        clear = options.get('clear')
        demo = options.get('demo')
        simulation = options.get('simulation')

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

        if simulation:
            self._seed_simulation_population()

        self.stdout.write(self.style.SUCCESS('Seeding complete!'))

    def _clear_data(self):
        self.stdout.write('Clearing existing data...')
        BattleInvestment.objects.all().delete()
        BattleAction.objects.all().delete()
        BattleParticipant.objects.all().delete()
        Battle.objects.all().delete()
        EggTemplate.objects.all().delete()
        Creature.objects.all().delete()
        Ability.objects.all().delete()
        PriceHistory.objects.all().delete()
        Trade.objects.all().delete()
        Portfolio.objects.all().delete()
        Account.objects.filter(username__startswith='sim_').delete()

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

            initial_price = Decimal(str(data['initial_price']))
            is_mythical = data.get('is_mythical', False)
            is_legendary = data.get('is_legendary', False)
            rarity = Decimal('1.0')
            if is_mythical:
                rarity = Decimal('2.0')
            elif is_legendary:
                rarity = Decimal('1.5')
            ref_price = quantize_money(initial_price / rarity)

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
                'is_legendary': is_legendary,
                'is_mythical': is_mythical,
                'current_price': initial_price,
                'previous_close': initial_price,
                'pricing_reference_price': ref_price,
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

        # Fetch sprites for creatures that don't have icons yet
        fetched = 0
        for creature in Creature.objects.all():
            pokedex_id = POKEAPI_ID_MAP.get(creature.name)
            if not pokedex_id:
                continue
            if not creature.small_icon:
                small_file = fetch_sprite(
                    f"{POKEAPI_SPRITE_URL}/{pokedex_id}.png",
                    f"small_{creature.name.lower()}.png"
                )
                if small_file:
                    creature.small_icon.save(f"small_{creature.name.lower()}.png", small_file, save=False)
            if not creature.large_icon:
                large_file = fetch_sprite(
                    f"{POKEAPI_ARTWORK_URL}/{pokedex_id}.png",
                    f"large_{creature.name.lower()}.png"
                )
                if large_file:
                    creature.large_icon.save(f"large_{creature.name.lower()}.png", large_file, save=False)
            if creature.small_icon or creature.large_icon:
                creature.save(update_fields=['small_icon', 'large_icon'])
                fetched += 1
        if fetched:
            self.stdout.write(f"  Creature sprites: {fetched} updated")

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
                created += 1
            # Always reset balance to the configured amount
            user.balance_cents = balance
            user.save(update_fields=['balance_cents'])

        self.stdout.write(f"  Demo users: {created} new, {len(users_data) - created} existing (balances reset) / {len(users_data)} total")

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
        created_finished = 0

        for data in battles_data:
            c1 = get_or_none(Creature, name=data['creature_1'])
            c2 = get_or_none(Creature, name=data['creature_2'])

            if not c1 or not c2:
                self.stdout.write(self.style.WARNING(
                    f"    Battle: creature not found ({data['creature_1']} or {data['creature_2']})"
                ))
                continue

            try:
                battle = BattleService.start_battle(c1, c2)
                participants = list(battle.participants.select_related('creature').all())

                # Track damage per creature to set realistic final HP
                damage_taken = {c1.name: 0, c2.name: 0}

                # Give enough buffer HP so scripted turns don't kill prematurely
                for p in participants:
                    p.current_hp = 500
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
                    victim_name = c2.name if attacker_name == c1.name else c1.name
                    damage_taken[victim_name] += damage

                    if attacker_p and defender_p:
                        battle.record_action(attacker_p, defender_p, ability, damage)

                battle.refresh_from_db()
                # Set final HP to realistic values (actual max HP minus damage taken)
                for p in participants:
                    dmg = damage_taken.get(p.creature.name, 0)
                    remaining = max(p.creature.hp - dmg, 0)
                    p.current_hp = remaining
                    p.save(update_fields=['current_hp'])

                # Force-finish battle since buffer HP prevented natural death via record_action
                if battle.status == 'active':
                    winner_p = participants[0]  # creature_1 always wins per demo data
                    c1.end_battle()
                    c2.end_battle()
                    battle.status = 'finished'
                    battle.winner = winner_p
                    battle.save(update_fields=['status', 'winner'])

                # Process ELO, wins/losses, price updates
                BattleService.process_battle_result(battle)

                created_finished += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f"    Battle failed ({data['creature_1']} vs {data['creature_2']}): {e}"
                ))

        # Create active battles: pick 2 pairs that are not on cooldown
        created_active = 0
        available = list(Creature.objects.filter(
            currently_in_battle=False,
            cooldown_expires_at__lte=timezone.now(),
        ).order_by('?')[:4])

        if len(available) >= 2:
            try:
                battle1 = BattleService.start_battle(available[0], available[1])
                created_active += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"    Active battle 1 failed: {e}"))

        if len(available) >= 4:
            try:
                battle2 = BattleService.start_battle(available[2], available[3])
                created_active += 1
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"    Active battle 2 failed: {e}"))

        self.stdout.write(f"  Demo battles: {created_finished} finished, {created_active} active / {len(battles_data)} scripted")

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
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"    {user.username} cannot afford {egg.name} (${egg.price}, "
                            f"balance: ${locked_user.balance_dollars})"
                        ))
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f"    Incubation failed for {user.username}: {e}"
                ))

        self.stdout.write(f"  Demo incubations: {created} started")

    def _seed_simulation_population(self):
        from django.contrib.auth.hashers import make_password
        self.stdout.write('Seeding massive simulation population (~100 users, 30d history, battles, portfolios)...')
        self._seed_market_maker()

        creatures = list(Creature.objects.all())
        eggs = list(EggTemplate.objects.filter(is_active=True))

        if not creatures:
            self.stdout.write(self.style.ERROR('No creatures found to seed simulation.'))
            return

        # 1. Create 100 simulation users with pre-hashed passwords for maximum speed
        shared_password_hash = make_password('trainer_pass_2026')
        trainer_names = [
            "red", "blue", "green", "cynthia", "steven", "lance", "erika", "sabrina", "giovanni",
            "koga", "blaine", "surge", "whitney", "jasmine", "pryce", "clair", "falkner", "bugsy",
            "morty", "chuck", "roark", "gardenia", "maylene", "crasher_wake", "fantina", "byron",
            "candice", "volkner", "alder", "iris", "drasna", "siebold", "wikstrom", "malva",
            "diantha", "leon", "raihan", "nessa", "bea", "allister", "kabu", "piers", "marnie",
            "bede", "milo", "gordie", "melony", "opal", "nemona", "arven", "penny", "geeta",
            "larry", "ryme", "tulip", "grusha", "katy", "brassius", "iono", "kofu"
        ]
        while len(trainer_names) < 100:
            trainer_names.append(f"trainer_{len(trainer_names) + 1:03d}")

        users = []
        created_count = 0
        for name in trainer_names:
            username = f"sim_{name}"
            email = f"{username}@brokerdex.io"
            user = get_or_none(Account, username=username)
            if not user:
                balance_cents = random.randint(1_000_000, 8_000_000)
                user = Account.objects.create_user(
                    username=username,
                    email=email,
                    password=None,
                    balance_cents=balance_cents,
                )
                user.password = shared_password_hash
                user.save(update_fields=['password'])
                created_count += 1
            users.append(user)

        self.stdout.write(f"  Simulation Users: {created_count} created, {len(users)} ready")

        # 2. Distribute Portfolios across creatures
        portfolio_count = 0
        for user in users:
            owned_creatures = random.sample(creatures, k=random.randint(2, min(6, len(creatures))))
            for creature in owned_creatures:
                qty = quantize_money(Decimal(str(random.uniform(1.0, 10.0))))
                avg_cost = quantize_money(creature.current_price * Decimal(str(random.uniform(0.9, 1.1))))
                _, was_created = Portfolio.objects.get_or_create(
                    owner=user,
                    creature=creature,
                    defaults={'quantity': qty, 'average_cost': avg_cost}
                )
                if was_created:
                    portfolio_count += 1

        self.stdout.write(f"  Portfolios: {portfolio_count} creature holdings distributed")

        # 3. Generate 30 days of Historical OHLCV Candles and Trades
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        daily_candles = []
        hourly_candles = []
        weekly_candles = []
        trades = []

        for creature in creatures:
            base_price = creature.pricing_reference_price or creature.current_price
            price_walk = base_price

            # Hourly candles (last 720 hours = 30 days)
            for h in range(720, 0, -1):
                ts = now - timedelta(hours=h)
                drift = Decimal(str(random.uniform(-0.02, 0.02)))
                open_p = quantize_money(max(Decimal('5.00'), price_walk))
                price_walk = quantize_money(max(Decimal('5.00'), price_walk * (1 + drift)))
                close_p = price_walk
                high_p = quantize_money(max(open_p, close_p) * Decimal(str(random.uniform(1.002, 1.015))))
                low_p = quantize_money(min(open_p, close_p) * Decimal(str(random.uniform(0.985, 0.998))))
                vol = quantize_money(Decimal(str(random.uniform(2.0, 35.0))))

                hourly_candles.append(PriceHistory(
                    creature=creature,
                    interval=PriceHistory.Interval.ONE_HOUR,
                    open_price=open_p,
                    high_price=high_p,
                    low_price=low_p,
                    close_price=close_p,
                    volume=vol,
                    timestamp=ts,
                ))

            # Daily candles (last 30 days)
            price_walk = base_price
            for d in range(30, 0, -1):
                ts = (now - timedelta(days=d)).replace(hour=0)
                drift = Decimal(str(random.uniform(-0.04, 0.04)))
                open_p = quantize_money(max(Decimal('5.00'), price_walk))
                price_walk = quantize_money(max(Decimal('5.00'), price_walk * (1 + drift)))
                close_p = price_walk
                high_p = quantize_money(max(open_p, close_p) * Decimal(str(random.uniform(1.01, 1.05))))
                low_p = quantize_money(min(open_p, close_p) * Decimal(str(random.uniform(0.95, 0.99))))
                vol = quantize_money(Decimal(str(random.uniform(40.0, 450.0))))

                daily_candles.append(PriceHistory(
                    creature=creature,
                    interval=PriceHistory.Interval.ONE_DAY,
                    open_price=open_p,
                    high_price=high_p,
                    low_price=low_p,
                    close_price=close_p,
                    volume=vol,
                    timestamp=ts,
                ))

            # Weekly candles (last 4 weeks)
            for w in range(4, 0, -1):
                ts = (now - timedelta(weeks=w)).replace(hour=0)
                open_p = quantize_money(base_price * Decimal(str(random.uniform(0.95, 1.05))))
                close_p = quantize_money(base_price * Decimal(str(random.uniform(0.95, 1.05))))
                high_p = quantize_money(max(open_p, close_p) * Decimal('1.06'))
                low_p = quantize_money(min(open_p, close_p) * Decimal('0.94'))
                vol = quantize_money(Decimal(str(random.uniform(200.0, 1500.0))))

                weekly_candles.append(PriceHistory(
                    creature=creature,
                    interval=PriceHistory.Interval.ONE_WEEK,
                    open_price=open_p,
                    high_price=high_p,
                    low_price=low_p,
                    close_price=close_p,
                    volume=vol,
                    timestamp=ts,
                ))

            # Generate historical trades
            for _ in range(15):
                buyer, seller = random.sample(users, 2)
                t_price = quantize_money(base_price * Decimal(str(random.uniform(0.92, 1.08))))
                t_qty = quantize_money(Decimal(str(random.uniform(0.5, 4.0))))
                t_subtotal = t_price * t_qty
                t_comm = quantize_money(t_subtotal * Trade.COMMISSION_RATE)
                t_date = now - timedelta(hours=random.randint(1, 700))

                trade = Trade(
                    buyer=buyer,
                    seller=seller,
                    creature=creature,
                    price_per_unit=t_price,
                    quantity=t_qty,
                    commission=t_comm,
                    realized_cost_basis=quantize_money(t_subtotal * Decimal('0.95')),
                    executed_at=t_date,
                )
                trades.append(trade)

        PriceHistory.objects.bulk_create(daily_candles, ignore_conflicts=True)
        PriceHistory.objects.bulk_create(hourly_candles, ignore_conflicts=True)
        PriceHistory.objects.bulk_create(weekly_candles, ignore_conflicts=True)
        Trade.objects.bulk_create(trades, ignore_conflicts=True)

        self.stdout.write(
            f"  History: {len(daily_candles) + len(hourly_candles) + len(weekly_candles)} candles (1H/1D/1W) "
            f"and {len(trades)} historical trades generated"
        )

        # 4. Simulate Historical Battles
        battle_count = 0
        for _ in range(18):
            c1, c2 = random.sample(creatures, 2)
            if c1 == c2:
                continue
            try:
                battle = BattleService.start_battle(c1, c2)
                participants = list(battle.participants.select_related('creature').all())
                for p in participants:
                    p.current_hp = 500
                    p.save(update_fields=['current_hp'])

                for t in range(random.randint(2, 4)):
                    attacker = participants[t % 2]
                    defender = participants[(t + 1) % 2]
                    ability = attacker.creature.abilities.first()
                    battle.record_action(attacker, defender, ability, random.randint(25, 45))

                winner = random.choice(participants)
                c1.end_battle()
                c2.end_battle()
                battle.status = 'finished'
                battle.winner = winner
                battle.save(update_fields=['status', 'winner'])
                BattleService.process_battle_result(battle)
                battle_count += 1
            except Exception:
                continue

        # Leave 3 active battles in progress
        active_battles = 0
        available_creatures = list(Creature.objects.filter(
            currently_in_battle=False,
            cooldown_expires_at__lte=timezone.now(),
        ).order_by('?')[:6])

        for k in range(0, len(available_creatures) - 1, 2):
            try:
                BattleService.start_battle(available_creatures[k], available_creatures[k + 1])
                active_battles += 1
            except Exception:
                pass

        self.stdout.write(f"  Battles: {battle_count} finished with ELO shocks, {active_battles} active")

        # 5. Create Incubations
        incubation_count = 0
        if eggs:
            for user in random.sample(users, min(20, len(users))):
                egg = random.choice(eggs)
                duration = timedelta(minutes=random.randint(15, 120))
                Incubation.objects.create(
                    user=user,
                    egg_template=egg,
                    hatches_at=timezone.now() + duration,
                )
                incubation_count += 1

        self.stdout.write(f"  Incubations: {incubation_count} active incubations created")
