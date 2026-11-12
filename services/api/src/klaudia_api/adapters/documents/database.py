"""Compatibility import for application database lifecycle and legacy methods."""

from klaudia_api.adapters.persistence.application import (
    ApplicationDatabase as AppDBClient,
)

__all__ = ["AppDBClient"]
