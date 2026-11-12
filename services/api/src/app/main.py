"""Retain the installed ASGI application import."""

from klaudia_api.interfaces.http.app_factory import lifespan as lifespan
from klaudia_api.main import app as app
