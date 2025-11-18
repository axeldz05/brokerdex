from rest_framework import serializers
from .models import Creature

class CreatureSerializer(serializers.ModelSerializer):
    small_icon_url = serializers.SerializerMethodField()
    large_icon_url = serializers.SerializerMethodField()
    original_image_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Creature
        fields = [
            'id', 'name', 'description', 
            'small_icon_url', 'large_icon_url',
            'current_price', 'previous_close', 'type', 'symbol'
        ]
    
    def get_small_icon_url(self, obj):
        request = self.context.get('request')
        url = obj.small_icon_url
        if url and request:
            return request.build_absolute_uri(url)
        return url
    
    def get_large_icon_url(self, obj):
        request = self.context.get('request')
        url = obj.large_icon_url
        if url and request:
            return request.build_absolute_uri(url)
        return url
    
    def get_original_image_url(self, obj):
        request = self.context.get('request')
        url = obj.original_image_url
        if url and request:
            return request.build_absolute_uri(url)
        return url
