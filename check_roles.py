import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
supabase = create_client(url, key)

res = supabase.table('profiles').select('id, full_name, phone_number, role').execute()
for r in res.data:
    print(f"ID: {r['id']} | Name: {r['full_name']} | Role: {r['role']} | Phone: {r.get('phone_number')}")
