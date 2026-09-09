from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/prices/$", consumers.PriceTickerConsumer.as_asgi()),
    re_path(
        r"^ws/prices/(?P<creature_id>[0-9a-f-]+)/$",
        consumers.PriceTickerConsumer.as_asgi(),
    ),
]
