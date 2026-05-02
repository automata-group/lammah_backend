from fastapi import APIRouter, Depends, HTTPException
from app.core.auth_guard import get_user_profile
from app.db.supabase_client import get_supabase
from app.core.logger import logger

router = APIRouter()

async def verify_admin(profile=Depends(get_user_profile)):
    user_role = profile.get("role", "user")
    user_id = profile.get("id")
    logger.info(f"🔑 Admin Check: User {user_id} has role: {user_role}")
    
    # If role is not admin, but user is the owner (HP), allow for now
    if user_role != "admin":
        logger.warning(f"🚫 Unauthorized Admin access attempt from: {user_id}")
        raise HTTPException(status_code=403, detail=f"غير مسموح لك (دورك الحالي: {user_role})")
    return profile

@router.get("/stats")
async def get_admin_stats(admin=Depends(verify_admin)):
    """Advanced metrics for security and usage monitoring."""
    try:
        supabase = get_supabase()
        
        # 1. Total Users
        users_res = supabase.table("profiles").select("id", count="exact").execute()
        total_users = users_res.count if users_res.count is not None else len(users_res.data)
        
        # 2. Free Trial Users (No plan)
        free_users_res = supabase.table("profiles").select("id", count="exact").is_("current_plan_id", "null").execute()
        free_users_count = free_users_res.count if free_users_res.count is not None else len(free_users_res.data)
        
        # 3. Free Trial Usage (Total lectures processed by free users)
        # We'll get this from usage_logs where user is in free_users
        free_usage_res = supabase.table("usage_logs") \
            .select("id", count="exact") \
            .eq("action_type", "lecture_process") \
            .eq("status", "success") \
            .execute()
        # Note: This is simplified total. For precision we can filter by user_id list if needed.
        total_usage = free_usage_res.count if free_usage_res.count is not None else len(free_usage_res.data)
        
        # 4. Active Subscriptions Details (Monitoring for abuse)
        # We want: User Name, Plan, Used, Remaining, Fingerprint
        subs_res = supabase.table("user_subscriptions") \
            .select("*, profiles(full_name, phone_number, device_fingerprint), subscription_plans(name, lecture_quota)") \
            .eq("status", "active") \
            .order("created_at", desc=True) \
            .execute()
            
        detailed_subs = []
        for s in (subs_res.data or []):
            profile = s.get("profiles", {})
            plan = s.get("subscription_plans", {})
            used = s.get("lectures_used", 0)
            quota = plan.get("lecture_quota", 0)
            
            detailed_subs.append({
                "user_name": profile.get("full_name") or profile.get("phone_number") or "مجهول",
                "phone": profile.get("phone_number"),
                "plan": plan.get("name"),
                "used": used,
                "remaining": max(0, quota - used),
                "quota": quota,
                "fingerprint": profile.get("device_fingerprint"),
                "created_at": s.get("created_at")
            })

        # 5. Recent Errors (Last 20 failed attempts)
        errors_res = supabase.table("usage_logs") \
            .select("*, profiles(full_name, phone_number)") \
            .eq("status", "failed") \
            .order("created_at", desc=True) \
            .limit(20) \
            .execute()

        return {
            "total_users": total_users,
            "free_users_count": free_users_count,
            "total_lecture_usage": total_usage,
            "active_subscriptions": detailed_subs,
            "errors": errors_res.data or []
        }
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب إحصائيات الأدمن: {str(e)}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب البيانات")
