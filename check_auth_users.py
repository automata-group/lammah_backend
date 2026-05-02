import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
supabase = create_client(url, key)

try:
    # Get all users from auth
    users = supabase.auth.admin.list_users()
    
    # Get profiles
    profiles_resp = supabase.table('profiles').select('id, full_name, phone_number, role').execute()
    profiles_map = {p['id']: p for p in profiles_resp.data}
    
    for u in users:
        p = profiles_map.get(u.id, {})
        print(f"ID: {u.id} | Email: {u.email} | Phone (Auth): {u.phone} | Phone (Prof): {p.get('phone_number')} | Role: {p.get('role')} | Name: {p.get('full_name')}")

except Exception as e:
    print("Error:", e)
