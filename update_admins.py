import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
supabase = create_client(url, key)

ids_to_admin = [
    '63aa25dc-3bd8-4021-a659-b83d0b2d619c', # ra666hoo
    '8bcfddf9-088d-43af-a2ba-bd0ea33593bb', # lamahusam200
    'd683d88d-63cd-417e-ac21-ff941ce2ab13', # lamahusam0
]

for uid in ids_to_admin:
    supabase.table('profiles').update({'role': 'admin'}).eq('id', uid).execute()
    print(f"Updated {uid} to admin")
