from supabase import create_client, Client
from app.core.config import settings
from app.core.logger import logger

_auth_client: Client | None = None
_db_client: Client | None = None


def _init_clients():
    global _auth_client, _db_client
    url = str(settings.SUPABASE_URL).strip("\"' ")
    key = str(settings.SUPABASE_KEY).strip("\"' ")
    service_key = str(settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY).strip("\"' ")
    if _auth_client is None:
        try:
            _auth_client = create_client(url, key)
        except Exception as e:
            logger.error(f"❌ Failed to create Supabase auth client: {e}")
            raise
    if _db_client is None:
        try:
            _db_client = create_client(url, service_key)
        except Exception as e:
            logger.error(f"❌ Failed to create Supabase DB client: {e}")
            raise


def get_auth_client() -> Client:
    """For auth operations like get_user(token)."""
    _init_clients()
    return _auth_client


def get_supabase() -> Client:
    """For DB operations - uses service_role to bypass RLS."""
    _init_clients()
    return _db_client
