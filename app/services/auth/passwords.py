"""Compatibility imports for the application password adapter."""

from app.adapters.auth.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
