import threading
import uuid

from django.http import HttpRequest, HttpResponse

from .base import BaseMiddleware


class CommonMiddleware(BaseMiddleware):
    def __call__(self, request: HttpRequest) -> HttpResponse:
        threading.current_thread().name = uuid.uuid4()
        response: HttpResponse = self.get_response(request)
        return response
