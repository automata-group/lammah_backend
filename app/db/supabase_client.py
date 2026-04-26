from supabase import create_client, Client
from app.core.config import settings

# Client for AUTH operations (uses anon key - for verifying user tokens)
_auth_client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Client for DB operations (uses service_role key - bypasses RLS)
_db_key = settings.SUPABASE_SERVICE_KEY or settings.SUPABASE_KEY
_db_client: Client = create_client(settings.SUPABASE_URL, _db_key)


def get_auth_client() -> Client:
    """For auth operations like get_user(token)."""
    return _auth_client


def get_supabase() -> Client:
    """For DB operations - uses service_role to bypass RLS."""
    return _db_client
