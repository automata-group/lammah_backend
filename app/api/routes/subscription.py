from fastapi import APIRouter, Depends, HTTPException
from app.core.auth_guard import verify_token, get_user_profile
from app.core.logger import logger
from pydantic import BaseModel

router = APIRouter()


class SubscribeRequest(BaseModel):
    plan_id: str


@router.get("/plans")
async def list_plans():
    """List all available subscription plans (public endpoint)."""
    try:
        from app.services.subscription_service import get_all_plans
        plans = get_all_plans()
        
        weekly = [p for p in plans if p.get("period_type") == "weekly"]
        monthly = [p for p in plans if p.get("period_type") == "monthly"]
        
        return {"weekly": weekly, "monthly": monthly}
    except Exception as e:
        logger.warning(f"⚠️ لم يتم تحميل الباقات (الجدول ربما غير موجود): {str(e)}")
        return {"weekly": [], "monthly": []}


# Removed for security: Users must subscribe via checkout/transactions
# @router.post("/subscribe")
# async def subscribe(req: SubscribeRequest, profile=Depends(get_user_profile)):
#     ...


@router.get("/me")
async def get_my_info(profile=Depends(get_user_profile)):
    """Get current user profile and subscription info."""
    try:
        from app.services.subscription_service import get_user_subscription_info
        info = get_user_subscription_info(profile["id"])
        return info
    except Exception as e:
        logger.warning(f"⚠️ خطأ في جلب معلومات المستخدم: {str(e)}")
        return {
            "profile": profile,
            "active_subscription": None,
            "quota_remaining": profile.get("quota_remaining", 0)
        }


@router.get("/quota")
async def get_my_quota(profile=Depends(get_user_profile)):
    """Quick check of remaining aggregate quota."""
    from app.services.subscription_service import get_user_subscription_info
    info = get_user_subscription_info(profile["id"])
    active = info.get("active_subscription")
    plan_name = "مجاني"
    if active and "subscription_plans" in active:
        plan_name = active["subscription_plans"].get("name", "مجاني")

    return {
        "quota_remaining": info.get("quota_remaining", 0),
        "quota_reset_at": profile.get("quota_reset_at"),
        "plan_id": profile.get("current_plan_id"),
        "plan_name": plan_name,
        "full_name": profile.get("full_name")
    }


@router.get("/history")
async def get_history(profile=Depends(get_user_profile)):
    """Get usage history for the user."""
    try:
        from app.services.subscription_service import get_usage_logs
        logs = get_usage_logs(profile["id"])
        return {"logs": logs}
    except Exception as e:
        logger.warning(f"⚠️ خطأ في جلب السجل: {str(e)}")
        return {"logs": []}


@router.get("/history/{log_id}")
async def get_history_item(log_id: str, profile=Depends(get_user_profile)):
    """Get a specific usage history item including metadata annotations."""
    try:
        from app.db.supabase_client import get_supabase
        supabase = get_supabase()
        result = supabase.table("usage_logs").select("*").eq("id", log_id).eq("user_id", profile["id"]).single().execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        return result.data
    except Exception as e:
        logger.warning(f"⚠️ خطأ في جلب تفاصيل السجل: {str(e)}")
        raise HTTPException(status_code=404, detail="السجل غير موجود أو يخص شخصاً آخر")


class UpdateTitleRequest(BaseModel):
    title: str


@router.patch("/history/{log_id}/title")
async def update_lecture_title(log_id: str, body: UpdateTitleRequest, profile=Depends(get_user_profile)):
    """Update the title of a specific lecture history record."""
    try:
        from app.db.supabase_client import get_supabase
        supabase = get_supabase()
        # Fetch existing record
        existing = supabase.table("usage_logs").select("metadata").eq("id", log_id).eq("user_id", profile["id"]).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        # Merge title into existing metadata
        meta = existing.data.get("metadata") or {}
        meta["title"] = body.title.strip()
        supabase.table("usage_logs").update({"metadata": meta}).eq("id", log_id).execute()
        return {"success": True, "title": meta["title"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"⚠️ خطأ في تحديث العنوان: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل تحديث العنوان")
