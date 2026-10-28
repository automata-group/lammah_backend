from fastapi import APIRouter, Depends, HTTPException, Request, Query
from app.core.auth_guard import verify_token, get_user_profile
from app.core.logger import logger
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import json

router = APIRouter()


def _as_dict_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    return []


def _as_dict(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict):
        return dict(data)
    return {}


class SubscribeRequest(BaseModel):
    plan_id: str


from starlette.concurrency import run_in_threadpool

@router.get("/plans")
async def list_plans():
    """List all available subscription plans (non-blocking)."""
    try:
        from app.services.subscription_service import get_all_plans
        plans = await run_in_threadpool(get_all_plans)
        
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
async def get_quota(request: Request, profile=Depends(get_user_profile)):
    from app.services.subscription_service import get_active_quota
    from app.db.supabase_client import get_supabase
    from datetime import datetime, timezone
    
    quota_info = get_active_quota(profile["id"])
    
    fingerprint = profile.get("device_fingerprint")
    if not fingerprint:
        fingerprint = request.headers.get("X-Fingerprint")
        
    supabase = get_supabase()
    
    # Check if they have an active paid subscription
    active_sub = None
    try:
        sub_result = supabase.table("user_subscriptions") \
            .select("*, subscription_plans(*)") \
            .eq("user_id", profile["id"]) \
            .eq("status", "active") \
            .gte("expires_at", datetime.now(timezone.utc).isoformat()) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()
        active_sub = sub_result.data[0] if sub_result.data else None
    except Exception as e:
        logger.error(f"❌ Error checking subscription in quota route: {str(e)}")

    quota_info["device_abuse"] = False
    quota_info["device_abuse_message"] = None
    return quota_info


@router.get("/chat-usage")
async def get_chat_usage(profile=Depends(get_user_profile)):
    """Get total chat messages used by the user across all lectures."""
    try:
        from app.db.supabase_client import get_supabase
        supabase = get_supabase()
        total_chats = supabase.table("usage_logs").select("chat_count").eq("user_id", profile["id"]).execute()
        total_chats_list = _as_dict_list(total_chats.data)
        total_chat_count = sum(item.get("chat_count", 0) or 0 for item in total_chats_list)
        return {"total_chat_count": total_chat_count}
    except Exception as e:
        logger.warning(f"⚠️ Error fetching chat usage: {str(e)}")
        return {"total_chat_count": 0}


@router.get("/history")
async def get_history(
    limit: Optional[int] = Query(default=50, ge=1, le=100),
    full: bool = Query(default=False),
    profile=Depends(get_user_profile)
):
    """Get usage history for the user with high-performance lightweight payload."""
    try:
        from app.services.subscription_service import get_usage_logs
        logs = get_usage_logs(profile["id"], limit=limit or 50, include_full_metadata=full)
        return {"logs": logs}
    except Exception as e:
        logger.warning(f"⚠️ خطأ في جلب السجل: {str(e)}")
        return {"logs": []}


@router.get("/history/{log_id}")
async def get_history_item(log_id: str, profile=Depends(get_user_profile)):
    """Get a specific usage history item including metadata annotations and total chat count."""
    try:
        from app.db.supabase_client import get_supabase
        supabase = get_supabase()
        
        # Allow any user to retrieve the demo lecture
        if log_id == "4198557f-5b1b-48ac-91a8-d8129de8ca0b":
            result = supabase.table("usage_logs").select("*").eq("id", log_id).single().execute()
        else:
            result = supabase.table("usage_logs").select("*").eq("id", log_id).eq("user_id", profile["id"]).single().execute()
            
        data_dict = _as_dict(result.data)
        if not data_dict:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        
        # Calculate total chat count across all user's lectures
        total_chats = supabase.table("usage_logs").select("chat_count").eq("user_id", profile["id"]).execute()
        total_chats_list = _as_dict_list(total_chats.data)
        total_chat_count = sum(item.get("chat_count", 0) or 0 for item in total_chats_list)
        
        data_dict["total_chat_count"] = total_chat_count
        return data_dict
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
        
        # Block title update for the demo lecture unless it's the owner
        if log_id == "4198557f-5b1b-48ac-91a8-d8129de8ca0b" and profile["id"] != "8bcfddf9-088d-43af-a2ba-bd0ea33593bb":
            return {"success": True, "title": body.title.strip()}
            
        # Fetch existing record
        existing = supabase.table("usage_logs").select("metadata").eq("id", log_id).eq("user_id", profile["id"]).single().execute()
        existing_data = _as_dict(existing.data)
        if not existing_data:
            raise HTTPException(status_code=404, detail="السجل غير موجود")
        # Merge title into existing metadata
        meta = existing_data.get("metadata")
        meta_dict = dict(meta) if isinstance(meta, dict) else {}
        meta_dict["title"] = body.title.strip()
        supabase.table("usage_logs").update({"metadata": meta_dict}).eq("id", log_id).execute()
        return {"success": True, "title": meta_dict["title"]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"⚠️ خطأ في تحديث العنوان: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل تحديث العنوان")


class StudentProfileRequest(BaseModel):
    university: Optional[str] = None
    major: Optional[str] = None
    academic_year: Optional[str] = None
    interests: Optional[List[str]] = None
    study_preference: Optional[str] = None
    phone: Optional[str] = None


@router.get("/student-profile")
async def get_student_profile(profile=Depends(get_user_profile)):
    """Retrieve current student's academic profile from device_metadata."""
    raw_meta = profile.get("device_metadata") or "{}"
    try:
        meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta if isinstance(raw_meta, dict) else {})
    except Exception:
        meta = {}
    return {
        "academic_profile": meta.get("academic_profile", {}),
        "phone_number": profile.get("phone_number"),
        "full_name": profile.get("full_name"),
        "bonus_awarded": bool(meta.get("academic_profile_bonus_awarded", False))
    }


@router.post("/student-profile")
async def update_student_profile(req: StudentProfileRequest, profile=Depends(get_user_profile)):
    """Save or update student academic profile (university, major, year, interests, study preferences)."""
    try:
        from app.db.supabase_client import get_supabase
        supabase = get_supabase()

        raw_meta = profile.get("device_metadata") or "{}"
        try:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta if isinstance(raw_meta, dict) else {})
        except Exception:
            meta = {}

        academic_profile = {
            "university": (req.university or "").strip(),
            "major": (req.major or "").strip(),
            "academic_year": (req.academic_year or "").strip(),
            "interests": req.interests or [],
            "study_preference": (req.study_preference or "").strip(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        meta["academic_profile"] = academic_profile

        update_fields: Dict[str, Any] = {}
        update_fields["device_metadata"] = json.dumps(meta, ensure_ascii=False)
        if req.phone and req.phone.strip():
            clean_phone = req.phone.strip()
            update_fields["phone_number"] = clean_phone

        supabase.table("profiles").update(update_fields).eq("id", profile["id"]).execute()
        logger.info(f"🎓 Updated student profile for user {profile['id']} ({academic_profile['university']} - {academic_profile['major']})")

        msg = "تم حفظ ملفك الأكاديمي واهتماماتك بنجاح 🎓"

        return {
            "status": "success",
            "message": msg,
            "academic_profile": academic_profile,
            "bonus_awarded": False,
            "bonus_minutes": 0,
            "new_quota": None
        }
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ الملف الأكاديمي: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل حفظ البيانات الأكاديمية")
