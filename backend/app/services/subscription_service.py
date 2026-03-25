from app.db.supabase_client import get_supabase
from app.core.logger import logger
from datetime import datetime, timezone, timedelta


def get_all_plans():
    """Return all active subscription plans."""
    supabase = get_supabase()
    result = supabase.table("subscription_plans") \
        .select("*") \
        .eq("is_active", True) \
        .order("price_ils") \
        .execute()
    return result.data or []


def get_plan_by_id(plan_id: str):
    """Get a specific plan by ID."""
    supabase = get_supabase()
    result = supabase.table("subscription_plans") \
        .select("*") \
        .eq("id", plan_id) \
        .single() \
        .execute()
    return result.data


def create_subscription(user_id: str, plan_id: str):
    """Create a new subscription for a user."""
    supabase = get_supabase()
    plan = get_plan_by_id(plan_id)
    
    if not plan:
        return None, "الباقة غير موجودة."
    
    # Calculate expiry
    now = datetime.now(timezone.utc)
    if plan["period_type"] == "weekly":
        expires_at = now + timedelta(weeks=1)
    else:
        expires_at = now + timedelta(days=30)
    
    # Cancel any existing active subscription
    supabase.table("user_subscriptions") \
        .update({"status": "cancelled"}) \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .execute()
    
    # Create new subscription
    sub_data = {
        "user_id": user_id,
        "plan_id": plan_id,
        "status": "active",
        "starts_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "lectures_used": 0
    }
    sub_result = supabase.table("user_subscriptions").insert(sub_data).execute()
    
    # Update user profile quota
    supabase.table("profiles").update({
        "current_plan_id": plan_id,
        "quota_remaining": plan["lecture_quota"],
        "quota_reset_at": expires_at.isoformat()
    }).eq("id", user_id).execute()
    
    logger.info(f"✅ اشتراك جديد للمستخدم {user_id}: {plan['name']} ({plan['lecture_quota']} محاضرة)")
    
    return sub_result.data[0] if sub_result.data else None, None


def use_lecture_quota(user_id: str, metadata: dict = None):
    """
    Deduct a lecture from user's quota.
    1. Try active subscription first.
    2. Try free quota.
    3. Log usage if successful.
    """
    supabase = get_supabase()
    
    # 1. Check for active subscription first
    active_sub = supabase.table("user_subscriptions") \
        .select("id, lectures_used, subscription_plans(lecture_quota)") \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    
    used_subscription = False
    
    if active_sub.data:
        sub = active_sub.data[0]
        used = sub.get("lectures_used", 0) or 0
        quota = sub.get("subscription_plans", {}).get("lecture_quota", 0)
        
        if used < quota:
            # Increment subscription usage
            supabase.table("user_subscriptions") \
                .update({"lectures_used": used + 1}) \
                .eq("id", sub["id"]) \
                .execute()
            used_subscription = True
            logger.info(f"📉 استخدام من باقة الاشتراك: المستخدم {user_id} - ({used+1}/{quota})")
            
    # 2. If no active subscription with quota, check free profile quota
    if not used_subscription:
        profile = supabase.table("profiles").select("quota_remaining").eq("id", user_id).execute()
        quota_remaining = profile.data[0].get("quota_remaining", 0) if profile.data else 0
        
        if quota_remaining <= 0:
            logger.warning(f"🚫 رفض معالجة لمحاضرة: لا توجد حصة {user_id}")
            return False
        
        new_quota = quota_remaining - 1
        supabase.table("profiles").update({"quota_remaining": new_quota}).eq("id", user_id).execute()
        logger.info(f"📉 استخدام من الحصة المجانية: المستخدم {user_id} - متبقي {new_quota}")

    # 3. Log usage
    try:
        log_data = {
            "user_id": user_id,
            "action_type": "lecture_process",
            "status": "success"
        }
        if metadata:
            log_data["metadata"] = metadata
            
        result = supabase.table("usage_logs").insert(log_data).execute()
        return result.data[0]["id"] if result.data else True
    except Exception as e:
        logger.error(f"⚠️ فشل تسجيل سجل الاستخدام للمستخدم {user_id}: {str(e)}")
        
    return True


def get_user_subscription_info(user_id: str):
    """Get complete subscription info for a user."""
    supabase = get_supabase()
    
    # Get profile (safe - no .single())
    profile_result = supabase.table("profiles") \
        .select("*") \
        .eq("id", user_id) \
        .execute()
    profile_data = profile_result.data[0] if profile_result.data else None
    
    # Get active subscription with plan details
    active_sub = supabase.table("user_subscriptions") \
        .select("*, subscription_plans(*)") \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()
    
    true_quota = 0
    active_data = None
    if active_sub.data:
        active_data = active_sub.data[0]
        used = active_data.get("lectures_used", 0) or 0
        total = active_data.get("subscription_plans", {}).get("lecture_quota", 0)
        true_quota = max(0, total - used)
        
        # Fallback to free profile quota if subscription runs out but they still have free ones
        if true_quota == 0 and profile_data:
            true_quota += profile_data.get("quota_remaining", 0)
    elif profile_data:
        true_quota = profile_data.get("quota_remaining", 0)

    return {
        "profile": profile_data,
        "active_subscription": active_data,
        "quota_remaining": true_quota
    }


def get_usage_logs(user_id: str, limit: int = 50):
    """Get usage logs for a user."""
    supabase = get_supabase()
    
    result = supabase.table("usage_logs") \
        .select("*") \
        .eq("user_id", user_id) \
        .order("created_at", desc=True) \
        .limit(limit) \
        .execute()
    
    return result.data or []

