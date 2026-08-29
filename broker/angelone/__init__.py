"""Standalone, read-only Angel One market-data adapter."""
from .client import AngelOneClient
from .auth import AuthStatus
from .models import DataResponse

__all__ = ["AngelOneClient", "AuthStatus", "DataResponse"]
