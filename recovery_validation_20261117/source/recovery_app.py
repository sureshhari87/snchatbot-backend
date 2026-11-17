"""Explicit maintenance entry point. Never selected by normal startup."""

from main import app, log_event, require_permission
from recovery_export_api import install

install(app, require_permission("config:manage"), audit=log_event)
