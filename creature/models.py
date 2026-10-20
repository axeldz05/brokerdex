from django.db import models, transaction
import os
import uuid
import datetime
from decimal import Decimal

from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.core.exceptions import ValidationError
from account.models import Account

def creature_image_upload_path(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('creatures', filename)

class PrimaryType(models.TextChoices):
    FIRE = 'fire', 'Fire'
    WATER = 'water', 'Water'
    GRASS = 'grass', 'Grass'
    ELECTRIC = 'electric', 'Electric'
    PSYCHIC = 'psychic', 'Psychic'
    FIGHTING = 'fighting', 'Fighting'
    DARK = 'dark', 'Dark'
    STEEL = 'steel', 'Steel'
    DRAGON = 'dragon', 'Dragon'
    FAIRY = 'fairy', 'Fairy'
    NORMAL = 'normal', 'Normal'
    FLYING = 'flying', 'Flying'
    GROUND = 'ground', 'Ground'
    ROCK = 'rock', 'Rock'
    BUG = 'bug', 'Bug'
    GHOST = 'ghost', 'Ghost'
    ICE = 'ice', 'Ice'
    POISON = 'poison', 'Poison'

class Creature(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField()
    type = models.CharField(max_length=15, choices=PrimaryType.choices)
    secondary_type = models.CharField(
        max_length=15, 
        choices=PrimaryType.choices, 
        null=True, 
        blank=True
    )
    current_price = models.DecimalField(max_digits=12, decimal_places=2, db_index=True)
    pricing_reference_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        editable=False,
        help_text="Stable reference used by the dynamic pricing engine",
    )
    previous_close = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Base Stats
    hp = models.PositiveIntegerField(default=20)
    attack = models.PositiveIntegerField(default=10)
    defense = models.PositiveIntegerField(default=55)
    special_attack = models.PositiveIntegerField(default=15)
    special_defense = models.PositiveIntegerField(default=20)
    speed = models.PositiveIntegerField(default=80)
    
    abilities = models.ManyToManyField('Ability', blank=True, related_name='creatures')

    evolves_from = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='evolves_to'
    )
    evolution_level = models.PositiveIntegerField(null=True, blank=True)
    
    is_legendary = models.BooleanField(default=False)
    is_mythical = models.BooleanField(default=False)
    circuit_breaker_expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="If set, trading is frozen until this time (volatility halt)"
    )
    
    # battle related fields
    battle_cooldown = models.DurationField(default=datetime.timedelta(days=0,hours=3))
    cooldown_expires_at = models.DateTimeField(default=timezone.now)
    currently_in_battle = models.BooleanField(default=False)
    elo_rating = models.IntegerField(default=1000, help_text="Internal ELO rating for battle valuation — never exposed to users")
    wins = models.IntegerField(default=0)
    losses = models.IntegerField(default=0)
    small_icon = models.ImageField(
        upload_to=creature_image_upload_path,
        blank=True,
        null=True,
        verbose_name="Creature Small Icon"
    )
    large_icon = models.ImageField(
        upload_to=creature_image_upload_path,
        blank=True,
        null=True,
        verbose_name="Creature Large Icon"
    )
    
    class Meta:
        verbose_name = "creature"
        verbose_name_plural = "creatures"
        indexes = [
            models.Index(fields=['current_price']),
            models.Index(fields=['type']),
        ]
        ordering = ['name']

    def get_abilities_queryset(self):
        """Get the actual Ability objects associated with this creature"""
        return self.abilities.all()
    
    def add_ability(self, ability):
        if not self.pk:
            raise ValidationError("You must save the creature before adding abilities.")
        if self.abilities.count() >= 4:
            raise ValidationError("Cannot add more than 4 abilities")
        ability_id = getattr(ability, 'pk', ability)
        if self.abilities.filter(pk=ability_id).exists():
            raise ValidationError("Ability already exists on this creature")
        self.abilities.add(ability)    

    def remove_ability(self, ability):
        ability_id = getattr(ability, 'pk', ability)
        if self.abilities.filter(pk=ability_id).exists():
            self.abilities.remove(ability)

    def __str__(self):
        types = f"{self.type}"
        if self.secondary_type:
            types += f"/{self.secondary_type}"
        return f"{self.name} ({types})"    

    def save(self, *args, **kwargs):
        if self.pricing_reference_price is None:
            rarity = Decimal('1.0')
            if self.is_mythical:
                rarity = Decimal('2.0')
            elif self.is_legendary:
                rarity = Decimal('1.5')
            from account.money import quantize_money
            self.pricing_reference_price = quantize_money(self.current_price / rarity)
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {
                    'pricing_reference_price',
                }
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('trading:creature_detail', args=[str(self.id)])

    def price_change(self):
        """Returns (change_abs, change_pct) from previous_close to current_price."""
        prev = self.previous_close
        if prev and prev != 0:
            change = self.current_price - prev
            pct = (change / prev * 100).quantize(Decimal('0.01'))
            return change.quantize(Decimal('0.01')), pct
        return Decimal('0'), Decimal('0')

    @property
    def small_icon_url(self):
        if self.small_icon and hasattr(self.small_icon, 'url'):
            return self.small_icon.url
        return None

    @property
    def large_icon_url(self):
        if self.large_icon and hasattr(self.large_icon, 'url'):
            return self.large_icon.url
        return None
    def is_available_for_battle(self):
        from django.utils import timezone
        if self.currently_in_battle:
            return False
        if self.cooldown_expires_at > timezone.now():
            return False
        return True

    @property
    def is_trading_halted(self):
        if self.circuit_breaker_expires_at is None:
            return False
        from django.utils import timezone
        return timezone.now() < self.circuit_breaker_expires_at

    def end_battle(self):
        from django.utils import timezone
        self.currently_in_battle = False
        self.cooldown_expires_at = timezone.now() + self.battle_cooldown
        self.save()

class Ability(models.Model):
    class DamageClass(models.TextChoices):
        PHYSICAL = 'physical', 'Physical'
        SPECIAL = 'special', 'Special'
        STATUS = 'status', 'Status'

    class TargetType(models.TextChoices):
        SELF = 'self', 'Self'
        SINGLE_OPPONENT = 'single_opponent', 'Single Opponent'
        SINGLE_ALLY = 'single_ally', 'Single Ally'
        ALL_OPPONENTS = 'all_opponents', 'All Opponents'
        ALL_ALLIES = 'all_allies', 'All Allies'
        ALL = 'all', 'All'
        RANDOM_OPPONENT = 'random_opponent', 'Random Opponent'
        FIELD = 'field', 'Field'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    description = models.TextField()
    ability_type = models.CharField(max_length=15, choices=PrimaryType.choices)
    
    power = models.PositiveIntegerField(null=True, blank=True, help_text="Base power of the move")
    accuracy = models.PositiveIntegerField(
        default=100, 
        help_text="Accuracy percentage (0-100)"
    )
    pp = models.PositiveIntegerField(
        default=20, 
        help_text="Power Points - how many times the move can be used"
    )
    priority = models.IntegerField(
        default=0, 
        help_text="Move priority (higher goes first)"
    )
    
    damage_class = models.CharField(
        max_length=10, 
        choices=DamageClass.choices,
        help_text="Physical, Special, or Status move"
    )
    target = models.CharField(
        max_length=20,
        choices=TargetType.choices,
        default=TargetType.SINGLE_OPPONENT
    )
    
    makes_contact = models.BooleanField(
        default=False,
        help_text="Whether the move makes physical contact"
    )
    is_healing_move = models.BooleanField(default=False)
    healing_percentage = models.PositiveIntegerField(
        default=0,
        help_text="Percentage of HP healed (if healing move)"
    )
    
    chance_to_inflict_status = models.PositiveIntegerField(
        default=0,
        help_text="Chance to inflict status condition (0-100)"
    )
    
    generation_introduced = models.PositiveIntegerField(default=1)
    is_signature_move = models.BooleanField(
        default=False,
        help_text="Whether this is a signature move for specific creatures"
    )

    def clean(self):
        """Validate the ability data"""
        if self.accuracy and self.accuracy > 100:
            raise ValidationError("Accuracy cannot exceed 100%")
        
        if self.power and self.damage_class == self.DamageClass.STATUS:
            raise ValidationError("Status moves cannot have power")
            
        if self.is_healing_move and self.power:
            raise ValidationError("Healing moves cannot have power")
    
    def __str__(self):
        return f"{self.name} ({self.get_ability_type_display()})"

    class Meta:
        verbose_name_plural = "Abilities"
        ordering = ['name']

class BattleManager(models.Manager):
    def start_battle(self, creature_1, creature_2):
        if creature_1 == creature_2:
            raise ValidationError("A creature cannot fight with itself")

        with transaction.atomic():
            creature_ids = [creature_1.pk, creature_2.pk]
            locked_creatures = list(
                Creature.objects.select_for_update()
                .filter(pk__in=creature_ids)
                .order_by('pk')
            )
            creatures_by_id = {
                creature.pk: creature for creature in locked_creatures
            }
            creature_1 = creatures_by_id[creature_1.pk]
            creature_2 = creatures_by_id[creature_2.pk]

            for creature in (creature_1, creature_2):
                if not creature.is_available_for_battle():
                    raise ValidationError(
                        f"{creature.name} is not available to fight."
                    )

            Creature.objects.filter(pk__in=creature_ids).update(
                currently_in_battle=True
            )
            battle = self.create(status='active', current_turn=1)
            BattleParticipant.objects.create(
                battle=battle,
                creature=creature_1,
                current_hp=creature_1.hp
            )
            BattleParticipant.objects.create(
                battle=battle,
                creature=creature_2,
                current_hp=creature_2.hp
            )
            return battle

class Battle(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('active', 'Active'),
        ('finished', 'Finished'),
    )
    
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    current_turn = models.PositiveIntegerField(default=1)
    winner = models.ForeignKey('BattleParticipant', null=True, blank=True, on_delete=models.SET_NULL, related_name='won_battles')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    creature_1_elo_before = models.IntegerField(default=1000, help_text="ELO snapshot — backend only, never exposed")
    creature_2_elo_before = models.IntegerField(default=1000, help_text="ELO snapshot — backend only, never exposed")
    creature_1_elo_after = models.IntegerField(null=True, blank=True)
    creature_2_elo_after = models.IntegerField(null=True, blank=True)
    creature_1_potential_change = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Pre-computed potential price change % if creature_1 wins"
    )
    creature_2_potential_change = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        help_text="Pre-computed potential price change % if creature_2 wins"
    )
    next_turn_at = models.DateTimeField(default=timezone.now, help_text="When the next turn will be processed")
    result_processed_at = models.DateTimeField(null=True, blank=True)

    objects = BattleManager()
    
    def record_action(self, actor_participant, target_participant, ability, damage):
        """Registers an action, calculates damage, status, and determinates if the battle has finished"""
        with transaction.atomic():
            battle = Battle.objects.select_for_update().get(pk=self.pk)
            if battle.status != 'active':
                raise ValidationError(
                    "Cannot record an action in an inactive battle"
                )

            participants = {
                participant.pk: participant
                for participant in BattleParticipant.objects.select_for_update()
                .filter(
                    pk__in=[
                        actor_participant.pk,
                        target_participant.pk,
                    ]
                )
                .order_by('pk')
            }
            actor_participant = participants[actor_participant.pk]
            target_participant = participants[target_participant.pk]

            existing_action = BattleAction.objects.filter(
                battle=battle,
                turn_number=battle.current_turn,
            ).first()
            if existing_action:
                return existing_action

            target_participant.current_hp -= damage
            if target_participant.current_hp <= 0:
                target_participant.current_hp = 0
            target_participant.save(update_fields=['current_hp'])
            
            action = BattleAction.objects.create(
                battle=battle,
                turn_number=battle.current_turn,
                actor=actor_participant,
                target=target_participant,
                ability=ability,
                damage_dealt=damage,
            )
            
            if target_participant.current_hp == 0:
                battle.status = 'finished'
                battle.winner = actor_participant
                battle.save(update_fields=['status', 'winner'])

                locked_creatures = list(
                    Creature.objects.select_for_update()
                    .filter(
                        pk__in=[
                            actor_participant.creature_id,
                            target_participant.creature_id,
                        ]
                    )
                    .order_by('pk')
                )
                now = timezone.now()
                for creature in locked_creatures:
                    creature.currently_in_battle = False
                    creature.cooldown_expires_at = (
                        now + creature.battle_cooldown
                    )
                    creature.save(update_fields=[
                        'currently_in_battle',
                        'cooldown_expires_at',
                    ])
            else:
                battle.current_turn += 1
                battle.save(update_fields=['current_turn'])

            self.status = battle.status
            self.winner = battle.winner
            self.current_turn = battle.current_turn
            return action

class BattleParticipant(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='participants')
    creature = models.ForeignKey('Creature', on_delete=models.CASCADE)
    current_hp = models.IntegerField(default=0)
    
    attack_stage = models.IntegerField(default=0)
    defense_stage = models.IntegerField(default=0)
    status_ailment = models.CharField(max_length=20, null=True, blank=True)

    # Price snapshots for battle valuation display (3NF: depend on composite key)
    price_before = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                       help_text="Creature price when battle started")
    price_after = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                      help_text="Creature price after battle finished")

    class Meta:
        unique_together = ['battle', 'creature']
        ordering = ['id']  # Consistent participant order: p1 first, p2 second

class BattleAction(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='actions')
    turn_number = models.PositiveIntegerField()
    actor = models.ForeignKey(BattleParticipant, on_delete=models.CASCADE, related_name='actions_performed')
    target = models.ForeignKey(BattleParticipant, on_delete=models.CASCADE, related_name='actions_received', null=True)
    
    ability = models.ForeignKey('Ability', on_delete=models.SET_NULL, null=True)
    is_item = models.BooleanField(default=False)
    
    damage_dealt = models.IntegerField(default=0)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['turn_number', 'timestamp']
        constraints = [
            models.UniqueConstraint(
                fields=['battle', 'turn_number'],
                name='unique_battle_action_per_turn',
            ),
        ]



class EggTemplate(models.Model):
    """
    Defines a type of egg that users can purchase.
    Each egg has a price, hatch duration, and a pool of possible creatures.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    hatch_duration = models.DurationField(
        help_text="Real time until the egg hatches (e.g. 1 hour)"
    )
    creature_pool = models.ManyToManyField(
        Creature,
        related_name='egg_templates',
        help_text="Possible creatures that can hatch from this egg"
    )
    image = models.ImageField(
        upload_to=creature_image_upload_path,
        blank=True, null=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (${self.price})"


class DeferredSchedulingStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    SCHEDULED = 'SCHEDULED', 'Scheduled'
    FAILED = 'FAILED', 'Failed'


class Incubation(models.Model):
    """
    Tracks a user's egg purchase and hatching process.
    """
    class Status(models.TextChoices):
        INCUBATING = 'INCUBATING', 'Incubating'
        HATCHED = 'HATCHED', 'Hatched'
        FAILED = 'FAILED', 'Failed and refunded'

    SchedulingStatus = DeferredSchedulingStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='incubations'
    )
    egg_template = models.ForeignKey(
        EggTemplate,
        on_delete=models.CASCADE,
        related_name='incubations'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.INCUBATING,
    )
    purchased_at = models.DateTimeField(auto_now_add=True)
    hatches_at = models.DateTimeField()
    hatched_at = models.DateTimeField(null=True, blank=True)
    hatched_creature = models.ForeignKey(
        Creature,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hatched_from'
    )
    celery_task_id = models.CharField(
        max_length=255,
        null=True, blank=True,
        help_text="Celery task ID for the hatch timer"
    )
    scheduling_status = models.CharField(
        max_length=12,
        choices=SchedulingStatus.choices,
        default=SchedulingStatus.PENDING,
    )
    scheduling_attempts = models.PositiveIntegerField(default=0)
    scheduling_error = models.TextField(blank=True)
    last_scheduling_attempt_at = models.DateTimeField(null=True, blank=True)
    completion_notified_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-purchased_at']

    def __str__(self):
        return f"{self.user.username} — {self.egg_template.name} [{self.status}]"

    @property
    def progress_percent(self):
        duration = (self.hatches_at - self.purchased_at).total_seconds()
        if duration <= 0:
            return 100

        elapsed = (timezone.now() - self.purchased_at).total_seconds()
        progress = int((elapsed / duration) * 100)
        return max(0, min(progress, 100))

    @property
    def user_failure_reason(self):
        if self.status != self.Status.FAILED:
            return ''
        return (
            'This egg could not be hatched because of a processing problem.'
        )

    @property
    def time_remaining(self):
        if self.status != self.Status.INCUBATING:
            return 0
        remaining = self.hatches_at - timezone.now()
        return max(remaining.total_seconds(), 0)


class TrainingSession(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed and refunded'

    SchedulingStatus = DeferredSchedulingStatus

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name='training_sessions',
    )
    creature = models.ForeignKey(
        Creature,
        on_delete=models.PROTECT,
        related_name='training_sessions',
    )
    portfolio = models.ForeignKey(
        'trading.Portfolio',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='training_sessions',
    )
    cost_cents = models.BigIntegerField()
    execute_at = models.DateTimeField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    celery_task_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    scheduling_status = models.CharField(
        max_length=12,
        choices=SchedulingStatus.choices,
        default=SchedulingStatus.PENDING,
    )
    scheduling_attempts = models.PositiveIntegerField(default=0)
    scheduling_error = models.TextField(blank=True)
    last_scheduling_attempt_at = models.DateTimeField(null=True, blank=True)
    result_stat = models.CharField(max_length=32, blank=True)
    result_boost = models.PositiveIntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notified_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'execute_at']),
        ]


class BattleInvestment(models.Model):
    """
    Tracks how many people invested (bought Portfolio) in each creature
    during specific turns of a battle. Populated by Celery task during
    battle processing.
    """
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='investments')
    turn_number = models.PositiveIntegerField()
    creature = models.ForeignKey(Creature, on_delete=models.CASCADE)
    investor_count = models.PositiveIntegerField(default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=8, default=0)

    class Meta:
        unique_together = ['battle', 'turn_number', 'creature']
        ordering = ['turn_number']

    def __str__(self):
        return f"Battle {self.battle_id} Turn {self.turn_number}: {self.investor_count} invested in {self.creature.name}"


@receiver(m2m_changed, sender=Creature.abilities.through)
def limit_creature_abilities(sender, instance, action, **kwargs):
    if action == "pre_add":
        if instance.abilities.count() + len(kwargs.get('pk_set', [])) > 4:
            raise ValidationError("A creature cannot have more than 4 abilities.")    
