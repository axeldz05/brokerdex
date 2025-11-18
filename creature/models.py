from django.db import models
import os
import uuid

def creature_image_upload_path(instance, filename):
    """Generate upload path for creature images with type specification"""
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('creatures', filename)

class Creature(models.Model):
    CREATURE_TYPE = (
        ('PHYSICAL', 'Physical'),
        ('FIRE', 'Fire'),
        ('WATER', 'Water'),
        ('EARTH', 'Earth'),
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, default='') # personalized description of the creature
    # biography = models.OneToOne(Biography) should have a biography where it shows its history of live; exchanges, battles won, etc. 
    current_price = models.DecimalField(max_digits=12, decimal_places=2, db_index=True)
    previous_close = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    type = models.CharField(max_length=10, choices=CREATURE_TYPE)
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
    
    def __str__(self):
        return f"{self.name}"
    
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
