from django.db import models
import os
import uuid
import datetime

from django.forms.widgets import Select
from django_better_admin_arrayfield.models.fields import ArrayField
from django.core.exceptions import ValidationError

def creature_image_upload_path(instance, filename):
    """Generate upload path for creature images with type specification"""
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
    name = models.CharField(max_length=100)
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
    
    abilities = ArrayField(
        models.PositiveIntegerField(),
        size=4,
        default=list,
        blank=True,
    )

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

    battle_cooldown = models.DurationField(default=datetime.timedelta(days=0,hours=3))
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
        if isinstance(ability, Ability):
            ability_id = ability.id
        else:
            ability_id = ability
            
        if ability_id not in self.abilities and len(self.abilities) < 4:
            self.abilities.append(ability_id)
        else:
            raise ValidationError("Cannot add more than 4 abilities or ability already exists")
    
    def remove_ability(self, ability_id):
        if ability_id in self.abilities:
            self.abilities.remove(ability_id)
    
    def clean(self):
        """Validate the creature data"""
        if len(self.abilities) > 4:
            raise ValidationError("A creature cannot have more than 4 abilities")
        
        existing_ability_ids = set(Ability.objects.filter(
            id__in=self.abilities
        ).values_list('id', flat=True))
        
        for ability_id in self.abilities:
            if ability_id not in existing_ability_ids:
                raise ValidationError(f"Ability with ID {ability_id} does not exist")
    
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

class Battle(models.Model):
    creatures = models.ManyToManyField(
        Creature, 
        through='BattleParticipant',
        through_fields=('battle', 'creature'),
        related_name='battles'
    )
    outcome = models.TextField()
    battle_date = models.DateTimeField(auto_now_add=True)

class BattleParticipant(models.Model):
    battle = models.ForeignKey(Battle, on_delete=models.CASCADE)
    creature = models.ForeignKey(Creature, on_delete=models.CASCADE)
    survived = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['battle', 'creature']

