"""Compatibility import for application database lifecycle and legacy methods."""

from app.adapters.persistence.application import ApplicationDatabase as AppDBClient

__all__ = ["AppDBClient"]
