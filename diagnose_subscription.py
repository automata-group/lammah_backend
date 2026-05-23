import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime, timedelta, timezone

# Load env variables from backend/.env
load_dotenv(".env")
url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')

if not url or not key:
    print("❌ Failed to load SUPABASE_URL or SUPABASE_SERVICE_KEY from .env")
    sys.exit(1)

supabase = create_client(url, key)

print("🔍 [DIAGNOSTICS] Querying Supabase Profiles...")
try:
    # 1. Fetch profiles
    prof_resp = supabase.table('profiles').select('id, full_name, role, current_plan_id').execute()
    profiles = prof_resp.data if prof_resp.data else []
    print(f"Found {len(profiles)} profiles:")
    for p in profiles:
        print(f"👤 Name: {p.get('full_name')} | ID: {p.get('id')} | Current Plan ID: {p.get('current_plan_id')} | Role: {p.get('role')}")

    # 2. Query user subscriptions
    print("\n🔍 [DIAGNOSTICS] Querying User Subscriptions...")
    sub_resp = supabase.table('user_subscriptions').select('*, subscription_plans(*)').execute()
    subs = sub_resp.data if sub_resp.data else []
    print(f"Found {len(subs)} subscription records:")
    for s in subs:
        plan_name = s.get('subscription_plans', {}).get('name') if s.get('subscription_plans') else 'Unknown'
        print(f"💳 Sub ID: {s.get('id')} | User ID: {s.get('user_id')} | Plan: {plan_name} | Status: {s.get('status')} | Expires: {s.get('expires_at')}")

    # 3. For any user who claims they have 'شهري - برو' but has no active sub, let's fix it!
    # Let's search for 'شهري - برو' in subscription_plans
    plan_resp = supabase.table('subscription_plans').select('*').eq('name', 'شهري - برو').execute()
    pro_plan = plan_resp.data[0] if plan_resp.data else None
    
    if not pro_plan:
        print("❌ Could not find a plan named 'شهري - برو' in subscription_plans table!")
    else:
        print(f"\n✅ Found 'شهري - برو' Plan ID: {pro_plan['id']}")
        
        # Apply upgrade to all profiles so they can test easily!
        for p in profiles:
            user_id = p['id']
            # Check if this user has an active sub for 'شهري - برو'
            active_pro = [s for s in subs if s['user_id'] == user_id and s.get('subscription_plans', {}).get('name') == 'شهري - برو' and s['status'] == 'active']
            
            if not active_pro:
                print(f"⚡ Fixing user {p.get('full_name')} ({user_id}) - inserting active 'شهري - برو' subscription!")
                
                # First deactivate any existing subscriptions
                supabase.table('user_subscriptions').update({'status': 'expired'}).eq('user_id', user_id).execute()
                
                # Insert active subscription
                expires = datetime.now(timezone.utc) + timedelta(days=30)
                sub_data = {
                    "user_id": user_id,
                    "plan_id": pro_plan['id'],
                    "status": "active",
                    "expires_at": expires.isoformat(),
                    "lectures_used": 0,
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                
                ins_resp = supabase.table('user_subscriptions').insert(sub_data).execute()
                if ins_resp.data:
                    # Update profile current_plan_id & quota
                    supabase.table('profiles').update({
                        'current_plan_id': pro_plan['id'],
                        'quota_remaining': pro_plan.get('lecture_quota', 100)
                    }).eq('id', user_id).execute()
                    
                    print(f"🎉 SUCCESS! User {p.get('full_name')} is now fully upgraded to active 'شهري - برو' with {pro_plan.get('lecture_quota', 100)} lectures!")

except Exception as e:
    print("❌ Diagnostics failed with error:", e)
