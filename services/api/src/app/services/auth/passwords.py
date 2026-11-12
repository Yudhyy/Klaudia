"""Compatibility imports for the application password adapter."""

from klaudia_api.adapters.auth.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
