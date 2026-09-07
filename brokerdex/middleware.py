from uuid import UUID, uuid4

from .observability import reset_request_id, set_request_id


def _is_valid_request_id(value):
    if not value:
        return False
    try:
        return str(UUID(value)) == value.lower()
    except (AttributeError, ValueError):
        return False


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied_request_id = request.headers.get("X-Request-ID")
        request_id = (
            supplied_request_id
            if _is_valid_request_id(supplied_request_id)
            else str(uuid4())
        )
        request.request_id = request_id
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)
