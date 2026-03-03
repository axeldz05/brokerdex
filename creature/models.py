from django.db import models, transaction
import os
import uuid
import datetime

from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.utils import timezone
from django.core.exceptions import ValidationError

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
    
    # battle related fields
    battle_cooldown = models.DurationField(default=datetime.timedelta(days=0,hours=3))
    cooldown_expires_at = models.DateTimeField(default=timezone.now)
    currently_in_battle = models.BooleanField(default=False)
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
       """Get the actual Ability objects from the stored IDs"""
       return Ability.objects.filter(id__in=self.abilities)
    
    def add_ability(self, ability):
        if not self.pk:
            raise ValidationError("You must save the creature before adding abilities.")
        if self.abilities.count() >= 4:
            raise ValidationError("Cannot add more than 4 abilities")
        if self.abilities.filter(id=ability.id).exists():
            raise ValidationError("Ability already exists on this creature")
        self.abilities.add(ability)    

    def remove_ability(self, ability_id):
        if ability_id in self.abilities:
            self.abilities.remove(ability_id)

    def __str__(self):
        types = f"{self.type}"
        if self.secondary_type:
            types += f"/{self.secondary_type}"
        return f"{self.name} ({types})"    

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('creature_detail', args=[str(self.id)])

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
        if not creature_1.is_available_for_battle():
            raise ValidationError(f"{creature_1.name} is not available to fight.")
        if not creature_2.is_available_for_battle():
            raise ValidationError(f"{creature_2.name} is not available to fight.")
        with transaction.atomic():
            creature_1.currently_in_battle = True
            creature_1.save(update_fields=['currently_in_battle'])
            creature_2.currently_in_battle = True
            creature_2.save(update_fields=['currently_in_battle'])
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
    
    battle_log = models.JSONField(default=list, blank=True) 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = BattleManager()
    
    def record_action(self, actor_participant, target_participant, ability, damage):
        """Registers an action, calculates damage, status, and determinates if the battle has finished"""
        if self.status != 'active':
            raise ValidationError("Cannot record an action in an inactive battle")

        with transaction.atomic():
            target_participant.current_hp -= damage
            if target_participant.current_hp <= 0:
                target_participant.current_hp = 0
            target_participant.save(update_fields=['current_hp'])
            
            description = f"{actor_participant.creature.name} used {ability.name}!"
            BattleAction.objects.create(
                battle=self,
                turn_number=self.current_turn,
                actor=actor_participant,
                target=target_participant,
                ability=ability,
                damage_dealt=damage,
                description=description
            )
            
            if target_participant.current_hp == 0:
                self.status = 'finished'
                self.winner = actor_participant 
                self.save(update_fields=['status', 'winner'])
        
                actor_participant.creature.end_battle()
                target_participant.creature.end_battle()
            else:
                self.current_turn += 1
                self.save(update_fields=['current_turn'])

class BattleParticipant(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='participants')
    creature = models.ForeignKey('Creature', on_delete=models.CASCADE)
    current_hp = models.IntegerField(default=0)
    
    attack_stage = models.IntegerField(default=0)
    defense_stage = models.IntegerField(default=0)
    status_ailment = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        unique_together = ['battle', 'creature']

class BattleAction(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE, related_name='actions')
    turn_number = models.PositiveIntegerField()
    actor = models.ForeignKey(BattleParticipant, on_delete=models.CASCADE, related_name='actions_performed')
    target = models.ForeignKey(BattleParticipant, on_delete=models.CASCADE, related_name='actions_received', null=True)
    
    ability = models.ForeignKey('Ability', on_delete=models.SET_NULL, null=True)
    is_item = models.BooleanField(default=False)
    
    damage_dealt = models.IntegerField(default=0)
    description = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['turn_number', 'timestamp']



@receiver(m2m_changed, sender=Creature.abilities.through)
def limit_creature_abilities(sender, instance, action, **kwargs):
    if action == "pre_add":
        if instance.abilities.count() + len(kwargs.get('pk_set', [])) > 4:
            raise ValidationError("A creature cannot have more than 4 abilities.")    
