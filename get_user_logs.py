import json
import os
import sys

# Add current directory to path so it can find 'app'
sys.path.append(os.getcwd())

from app.db.supabase_client import get_supabase

def get_logs():
    supabase = get_supabase()
    user_id = '8bcfddf9-088d-43af-a2ba-bd0ea33593bb'
    
    res = supabase.table('usage_logs').select('*').eq('user_id', user_id).order('created_at', desc=True).execute()
    
    with open('user_logs_report.json', 'w', encoding='utf-8') as f:
        json.dump(res.data, f, ensure_ascii=False, indent=2)
    
    print(f"Successfully wrote {len(res.data)} logs to user_logs_report.json")

if __name__ == "__main__":
    get_logs()
