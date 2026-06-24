from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SECRET_KEY

if not SUPABASE_URL or not SUPABASE_SECRET_KEY:
    raise ValueError("Supabase URL and SECRET KEY must be set in environment variables.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
