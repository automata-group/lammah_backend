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


def use_lecture_quota(user_id: str, metadata: dict = None, cost: int = 15):
    """
    Deducts a specific number of 'Smart Minutes' from the user's quota.
    Defaults to 15 minutes for standard processing.
    """
    supabase = get_supabase()
    
    # 1. Check active subscription
    active_sub = supabase.table("user_subscriptions") \
        .select("*, subscription_plans(*)") \
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
        
        if used + cost <= quota:
            new_used = used + cost
            supabase.table("user_subscriptions") \
                .update({"lectures_used": new_used}) \
                .eq("id", sub["id"]) \
                .execute()
            # Also sync profiles.quota_remaining so auth_guard + frontend display correct value
            new_remaining = max(0, quota - new_used)
            supabase.table("profiles") \
                .update({"quota_remaining": new_remaining}) \
                .eq("id", user_id) \
                .execute()
            used_subscription = True
            logger.info(f"📉 خصم {cost} دقيقة من باقة الاشتراك: المستخدم {user_id} - (مستخدم: {new_used}/{quota} - متبقي: {new_remaining})")
            
    # 2. If no active subscription with quota, check free profile quota
    if not used_subscription:
        profile = supabase.table("profiles").select("quota_remaining").eq("id", user_id).execute()
        quota_remaining = profile.data[0].get("quota_remaining", 0) if profile.data else 0
        
        if quota_remaining < cost:
            logger.warning(f"🚫 رفض معالجة لمحاضرة: لا تكفي الدقائق لـ {user_id} (مطلوب {cost}، متبقي {quota_remaining})")
            return False
        
        new_quota = quota_remaining - cost
        supabase.table("profiles").update({"quota_remaining": new_quota}).eq("id", user_id).execute()
        logger.info(f"📉 خصم {cost} دقيقة من الحصة المجانية: المستخدم {user_id} - متبقي {new_quota}")

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
        return result.data[0]["id"] if (result.data and len(result.data) > 0) else None
    except Exception as e:
        logger.error(f"⚠️ فشل تسجيل سجل الاستخدام للمستخدم {user_id}: {str(e)}")
        try:
            # Fallback: Retry with lighter metadata (e.g. truncated transcript) in case payload exceeded PostgREST limit
            if metadata and "transcript" in metadata and len(str(metadata["transcript"])) > 15000:
                light_meta = dict(metadata)
                light_meta["transcript"] = str(metadata["transcript"])[:15000] + "\n...[الأرشيف الكامل محفوظ في ملف التحميل]"
                log_data["metadata"] = light_meta
                retry_res = supabase.table("usage_logs").insert(log_data).execute()
                if retry_res.data and len(retry_res.data) > 0:
                    return retry_res.data[0]["id"]
        except Exception as re_err:
            logger.error(f"⚠️ فشل التسجيل البديل: {re_err}")
        
    return None


def log_usage_failure(user_id: str, error_message: str):
    """Logs a failed attempt without deducting any quota."""
    try:
        supabase = get_supabase()
        log_data = {
            "user_id": user_id,
            "action_type": "lecture_process",
            "status": "failed",
            "metadata": {"error": error_message}
        }
        supabase.table("usage_logs").insert(log_data).execute()
        logger.info(f"[FAILURE_LOGGED] تم تسجيل فشل العملية للمستخدم {user_id} - لم يتم خصم رصيد.")
    except Exception as e:
        logger.error(f"[ERROR] فشل تسجيل سجل الخطأ للمستخدم {user_id}: {str(e)}")


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

def refund_lecture_quota(user_id: str, history_id: str, error_msg: str = None):
    """
    Refunds a lecture credit if processing failed.
    """
    try:
        supabase = get_supabase()
        
        # 1. Update the log to failed status
        supabase.table("usage_logs").update({
            "status": "failed",
            "metadata": {"error": error_msg or "Unknown error"}
        }).eq("id", history_id).execute()

        # 2. Check if we should refund profile quota or subscription quota
        # If the user has an active subscription, increment its 'available' quota by decrementing used
        active_sub = supabase.table("user_subscriptions") \
            .select("id, lectures_used") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
        
        if active_sub.data:
            sub_id = active_sub.data[0]["id"]
            used = active_data = active_sub.data[0].get("lectures_used", 0)
            if used > 0:
                supabase.table("user_subscriptions").update({"lectures_used": used - 1}).eq("id", sub_id).execute()
                logger.info(f"🔄 Refunded 1 lecture to subscription {sub_id} for user {user_id}")
                return
                
        # 3. Fallback: Refund to profile quota
        prof = supabase.table("profiles").select("quota_remaining").eq("id", user_id).execute()
        if prof.data:
            curr = prof.data[0].get("quota_remaining", 0)
            supabase.table("profiles").update({"quota_remaining": curr + 1}).eq("id", user_id).execute()
            logger.info(f"🔄 Refunded 1 lecture to profile for user {user_id}")
            
    except Exception as e:
        logger.error(f"[REFUND ERROR] Failed to refund for user {user_id}: {e}")


def get_active_quota(user_id: str) -> dict:
    """Quick check of remaining aggregate quota for a user."""
    info = get_user_subscription_info(user_id)
    active = info.get("active_subscription")
    plan_name = "مجاني"
    plan_price = 0.0
    sub_plan = {}
    if active and "subscription_plans" in active:
        sub_plan = active["subscription_plans"] or {}
        plan_name = sub_plan.get("name", "مجاني")
        try:
            plan_price = float(sub_plan.get("price_ils", 0) or 0)
        except Exception:
            plan_price = 0.0

    profile = info.get("profile") or {}
    username = profile.get("username") or ""
    if not username and profile.get("device_metadata"):
        try:
            import json
            meta = json.loads(profile["device_metadata"]) if isinstance(profile["device_metadata"], str) else profile["device_metadata"]
            username = meta.get("seller_username") or ""
        except Exception:
            pass

    # Strictly 100-ILS plan subscribers only can generate academic notebooks
    plan_name_lower = plan_name.lower()
    plan_en_lower = str(sub_plan.get("name_en", "")).lower()
    is_100_plan = bool(
        active and (
            plan_price >= 100.0 or
            "الذهبية" in plan_name or
            "النخبة" in plan_name or
            "elite" in plan_name_lower or
            "full term" in plan_en_lower
        )
    )

    return {
        "quota_remaining": info.get("quota_remaining", 0),
        "quota_reset_at": profile.get("quota_reset_at"),
        "plan_id": profile.get("current_plan_id"),
        "plan_name": plan_name,
        "plan_price": plan_price,
        "can_generate_notebook": is_100_plan,
        "full_name": profile.get("full_name"),
        "username": username
    }

