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
                "user_id": s.get("user_id"),
                "user_name": profile.get("full_name") or profile.get("phone_number") or "مجهول",
                "phone": profile.get("phone_number"),
                "plan": plan.get("name"),
                "used": used,
                "remaining": max(0, quota - used),
                "quota": quota,
                "fingerprint": profile.get("device_fingerprint"),
                "created_at": s.get("created_at")
            })

        # 4b. Find users with manual quota (not covered by active subscriptions)
        # We look for quota_remaining > 15 because 15 is the default free trial
        subscriber_ids = [s.get("user_id") for s in subs_res.data if s.get("user_id")]
        manual_res = supabase.table("profiles") \
            .select("id, full_name, phone_number, device_fingerprint, quota_remaining, created_at") \
            .gt("quota_remaining", 15) \
            .execute()
            
        for p in (manual_res.data or []):
            if p["id"] not in subscriber_ids:
                detailed_subs.append({
                    "user_id": p.get("id"),
                    "user_name": p.get("full_name") or p.get("phone_number") or "مجهول (رصيد يدوي)",
                    "phone": p.get("phone_number"),
                    "plan": "⭐ رصيد يدوي / تجريبي",
                    "used": 0,
                    "remaining": p.get("quota_remaining", 0),
                    "quota": p.get("quota_remaining", 0),
                    "fingerprint": p.get("device_fingerprint"),
                    "created_at": p.get("created_at")
                })

        # 5. Recent Errors (Last 100 failed attempts)
        errors_res = supabase.table("usage_logs") \
            .select("*, profiles(full_name, phone_number)") \
            .eq("status", "failed") \
            .order("created_at", desc=True) \
            .limit(100) \
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

@router.post("/users/{user_id}/cancel-plan")
async def cancel_user_plan(user_id: str, admin=Depends(verify_admin)):
    """Revoke a user's plan and reset them to free trial."""
    try:
        supabase = get_supabase()
        
        # 1. Cancel any active subscriptions
        supabase.table("user_subscriptions") \
            .update({"status": "cancelled"}) \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .execute()
            
        # 2. Reset profile (Quota to 15, clear plan)
        supabase.table("profiles") \
            .update({
                "quota_remaining": 15,
                "current_plan_id": None
            }) \
            .eq("id", user_id) \
            .execute()
            
        logger.info(f"✅ Admin {admin['id']} revoked plan for user {user_id}")
        return {"status": "success", "message": "تم إلغاء الخطة بنجاح وإعادة المستخدم للتجربة المجانية."}
        
    except Exception as e:
        logger.error(f"❌ Error revoking plan: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل إلغاء الخطة")

@router.get("/users")
async def get_all_users(admin=Depends(verify_admin)):
    """Fetch all users for the admin dashboard."""
    try:
        supabase = get_supabase()
        res = supabase.table("profiles").select("id, full_name, phone_number, is_blocked, role, created_at, quota_remaining, current_plan_id, subscription_plans(name)").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"❌ Error fetching users: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل جلب المستخدمين")

@router.post("/users/{user_id}/ban")
async def ban_user(user_id: str, admin=Depends(verify_admin)):
    """Block a user from using the platform."""
    try:
        supabase = get_supabase()
        supabase.table("profiles").update({"is_blocked": True}).eq("id", user_id).execute()
        return {"status": "success", "message": "تم حظر المستخدم بنجاح."}
    except Exception as e:
        logger.error(f"❌ Error banning user: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل حظر المستخدم")

@router.post("/users/{user_id}/unban")
async def unban_user(user_id: str, admin=Depends(verify_admin)):
    """Unblock a user."""
    try:
        supabase = get_supabase()
        supabase.table("profiles").update({"is_blocked": False}).eq("id", user_id).execute()
        return {"status": "success", "message": "تم فك الحظر عن المستخدم بنجاح."}
    except Exception as e:
        logger.error(f"❌ Error unbanning user: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل فك الحظر عن المستخدم")

@router.get("/analytics/financial")
async def get_financial_analytics(admin=Depends(verify_admin)):
    """Fetch financial analytics (MRR, Total Revenue)."""
    try:
        supabase = get_supabase()
        res = supabase.table("transactions").select("amount, created_at, status").eq("status", "approved").execute()
        
        total_revenue = sum(float(tx.get("amount", 0)) for tx in (res.data or []))
        
        # Simple MRR calculation based on last 30 days
        from datetime import datetime, timedelta
        import pytz
        thirty_days_ago = datetime.now(pytz.UTC) - timedelta(days=30)
        mrr = sum(float(tx.get("amount", 0)) for tx in (res.data or []) if datetime.fromisoformat(tx.get("created_at").replace('Z', '+00:00')) >= thirty_days_ago)

        return {
            "total_revenue": total_revenue,
            "mrr": mrr,
            "total_approved_transactions": len(res.data or [])
        }
    except Exception as e:
        logger.error(f"❌ Error fetching financial analytics: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل جلب التحليلات المالية")

import json
import os

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "system_settings.json")

@router.get("/announcements")
async def get_announcement():
    """Fetch global announcement (publicly accessible)."""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("global_announcement", {"message": "", "active": False})
        return {"message": "", "active": False}
    except Exception:
        return {"message": "", "active": False}

from pydantic import BaseModel
class AnnouncementModel(BaseModel):
    message: str
    active: bool

@router.post("/announcements")
async def update_announcement(data: AnnouncementModel, admin=Depends(verify_admin)):
    """Update global announcement."""
    try:
        settings = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                
        settings["global_announcement"] = {"message": data.message, "active": data.active}
        
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
            
        return {"status": "success", "message": "تم تحديث الإشعار العام بنجاح"}
    except Exception as e:
        logger.error(f"❌ Error updating announcement: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل تحديث الإشعار")

class BulkCancelPlanModel(BaseModel):
    user_ids: list

@router.post("/users/bulk-cancel-plan")
async def bulk_cancel_user_plans(data: BulkCancelPlanModel, admin=Depends(verify_admin)):
    """Revoke plans for multiple users at once and reset them to free trial."""
    if not data.user_ids:
        raise HTTPException(status_code=400, detail="يرجى تحديد معرفات المستخدمين")
    try:
        supabase = get_supabase()

        # 1. Cancel active subscriptions for all specified users
        supabase.table("user_subscriptions") \
            .update({"status": "cancelled"}) \
            .in_("user_id", data.user_ids) \
            .eq("status", "active") \
            .execute()

        # 2. Reset profile quota and plan for all specified users
        supabase.table("profiles") \
            .update({"quota_remaining": 15, "current_plan_id": None}) \
            .in_("id", data.user_ids) \
            .execute()

        logger.info(f"✅ Admin {admin['id']} bulk revoked plans for users: {data.user_ids}")
        return {"status": "success", "message": f"تم إلغاء خطط {len(data.user_ids)} مستخدم بنجاح وإعادتهم للتجربة المجانية."}

    except Exception as e:
        logger.error(f"❌ Error bulk revoking plans: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل إلغاء الخطط للمستخدمين المحددين")

