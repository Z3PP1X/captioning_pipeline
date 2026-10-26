"""Database package exposing DatabaseManager, get_db, and seed_database."""
from backend.app.db.seed import seed_database
from backend.app.db.session import DatabaseManager, get_db, mask_api_key, reset_default_db, slugify_text

__all__ = [
    "DatabaseManager",
    "get_db",
    "mask_api_key",
    "reset_default_db",
    "seed_database",
    "slugify_text",
]
