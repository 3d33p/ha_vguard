"""VGuard Smart 2.0 Python client."""

from .client import DashboardSnapshot, VGuardClient, VGuardError
from .products import Product

__version__ = "0.2.1"

__all__ = [
    "DashboardSnapshot",
    "Product",
    "VGuardClient",
    "VGuardError",
    "__version__",
]
