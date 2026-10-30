from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from postgrest.types import CountMethod
from app.core.auth_guard import get_user_profile
from app.db.supabase_client import get_supabase
from app.core.logger import logger
from app.core.config import settings
from io import BytesIO
from datetime import datetime, timedelta, timezone
import json
try:
    import openpyxl  # type: ignore
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side  # type: ignore
    from openpyxl.utils import get_column_letter  # type: ignore
except ImportError:
    openpyxl = None  # type: ignore
    Font = PatternFill = Alignment = Border = Side = get_column_letter = None  # type: ignore

router = APIRouter()

def _as_dict_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, dict)]
    return []

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
        users_res = supabase.table("profiles").select("id", count=CountMethod.exact).execute()
        total_users = users_res.count if users_res.count is not None else len(_as_dict_list(users_res.data))
        
        # 2. Users without active plan
        free_users_res = supabase.table("profiles").select("id", count=CountMethod.exact).is_("current_plan_id", "null").execute()
        free_users_count = free_users_res.count if free_users_res.count is not None else len(_as_dict_list(free_users_res.data))
        
        # 3. Total lecture usage across users
        free_usage_res = supabase.table("usage_logs") \
            .select("id", count=CountMethod.exact) \
            .eq("action_type", "lecture_process") \
            .eq("status", "success") \
            .execute()
        total_usage = free_usage_res.count if free_usage_res.count is not None else len(_as_dict_list(free_usage_res.data))
        
        # 4. Active Subscriptions Details (Monitoring for abuse)
        subs_res = supabase.table("user_subscriptions") \
            .select("*, profiles(full_name, phone_number, device_fingerprint), subscription_plans(name, lecture_quota)") \
            .eq("status", "active") \
            .order("created_at", desc=True) \
            .execute()

        # Load auth user emails map for clear customer identification
        email_map: Dict[str, str] = {}
        try:
            auth_users = supabase.auth.admin.list_users(page=1, per_page=1000)
            if auth_users:
                for u in auth_users:
                    uid = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                    uemail = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                    if uid and uemail:
                        email_map[uid] = uemail
        except Exception as ae:
            logger.warning(f"Could not load auth emails in overview: {ae}")
            
        detailed_subs = []
        subs_data = _as_dict_list(subs_res.data)
        for s in subs_data:
            prof_raw = s.get("profiles")
            profile: Dict[str, Any] = prof_raw if isinstance(prof_raw, dict) else {}
            plan_raw = s.get("subscription_plans")
            plan: Dict[str, Any] = plan_raw if isinstance(plan_raw, dict) else {}
            used = int(s.get("lectures_used") or 0)
            quota = int(plan.get("lecture_quota") or 0)
            u_id = str(s.get("user_id", ""))
            u_email = email_map.get(u_id, "")
            u_name = profile.get("full_name") or (u_email.split("@")[0] if u_email else (profile.get("phone_number") or "مستخدم بدون اسم"))
            
            detailed_subs.append({
                "user_id": u_id,
                "user_name": u_name,
                "email": u_email,
                "phone": profile.get("phone_number"),
                "plan": plan.get("name"),
                "used": used,
                "remaining": max(0, quota - used),
                "quota": quota,
                "fingerprint": profile.get("device_fingerprint"),
                "created_at": s.get("created_at")
            })

        # 4b. Find users with manual quota (not covered by active subscriptions)
        subscriber_ids = set(str(s.get("user_id")) for s in subs_data if s.get("user_id"))
        manual_res = supabase.table("profiles") \
            .select("id, full_name, phone_number, device_fingerprint, quota_remaining, created_at") \
            .gt("quota_remaining", 15) \
            .execute()
            
        for p in _as_dict_list(manual_res.data):
            p_id = str(p.get("id", ""))
            if p_id not in subscriber_ids:
                u_email = email_map.get(p_id, "")
                u_name = p.get("full_name") or (u_email.split("@")[0] if u_email else (p.get("phone_number") or "مستخدم بدون اسم"))
                detailed_subs.append({
                    "user_id": p_id,
                    "user_name": u_name,
                    "email": u_email,
                    "phone": p.get("phone_number"),
                    "plan": "⭐ رصيد يدوي مخصص",
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
            
        # 2. Reset profile (Quota to 0, clear plan)
        supabase.table("profiles") \
            .update({
                "quota_remaining": 0,
                "current_plan_id": None
            }) \
            .eq("id", user_id) \
            .execute()
            
        logger.info(f"✅ Admin {admin['id']} revoked plan for user {user_id}")
        return {"status": "success", "message": "تم إلغاء الخطة بنجاح."}
        
    except Exception as e:
        logger.error(f"❌ Error revoking plan: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل إلغاء الخطة")

@router.get("/users")
async def get_all_users(admin=Depends(verify_admin)):
    """Fetch all users for the admin dashboard with their plan details."""
    try:
        supabase = get_supabase()
        profiles_res = supabase.table("profiles")\
            .select("id, full_name, phone_number, is_blocked, role, created_at, quota_remaining, current_plan_id, device_fingerprint, device_metadata")\
            .order("created_at", desc=True)\
            .execute()
            
        profiles = _as_dict_list(profiles_res.data)
        
        # Fetch active plans map
        plans_map: Dict[str, Any] = {}
        try:
            plans_res = supabase.table("subscription_plans").select("id, name").execute()
            for p in _as_dict_list(plans_res.data):
                p_id = p.get("id")
                p_name = p.get("name")
                if p_id and p_name:
                    plans_map[p_id] = p_name
        except Exception:
            pass

        # Fetch active user subscriptions map
        subs_map: Dict[str, Any] = {}
        try:
            subs_res = supabase.table("user_subscriptions")\
                .select("user_id, plan_id, status")\
                .eq("status", "active")\
                .execute()
            for s in _as_dict_list(subs_res.data):
                p_id = s.get("plan_id")
                u_id = s.get("user_id")
                if p_id in plans_map and u_id:
                    subs_map[u_id] = plans_map[p_id]
        except Exception:
            pass

        # Fetch auth user emails map
        email_map: Dict[str, str] = {}
        try:
            page = 1
            while True:
                auth_users = supabase.auth.admin.list_users(page=page, per_page=1000)
                if not auth_users:
                    break
                for u in auth_users:
                    uid = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                    uemail = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                    if uid and uemail:
                        email_map[uid] = uemail
                if len(auth_users) < 1000:
                    break
                page += 1
        except Exception as ae:
            logger.warning(f"Could not load auth user emails: {ae}")

        # Attach subscription plan name, email, and academic profile to profile
        for prof in profiles:
            u_id = str(prof.get("id", ""))
            prof["email"] = email_map.get(u_id, "")
            c_plan_id = prof.get("current_plan_id")
            plan_name = subs_map.get(u_id) or (plans_map.get(c_plan_id) if c_plan_id else None)
            if plan_name:
                prof["subscription_plans"] = {"name": plan_name}
            else:
                prof["subscription_plans"] = None

            raw_meta = prof.get("device_metadata")
            academic = {}
            if raw_meta:
                try:
                    parsed_meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                    if isinstance(parsed_meta, dict):
                        academic = parsed_meta.get("academic_profile", {})
                except Exception:
                    academic = {}
            prof["academic_profile"] = academic

        return profiles
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

@router.post("/users/{user_id}/make-admin")
async def make_user_admin(user_id: str, admin=Depends(verify_admin)):
    """Promote a user to Admin."""
    try:
        supabase = get_supabase()
        supabase.table("profiles").update({"role": "admin"}).eq("id", user_id).execute()
        logger.info(f"👑 User {user_id} promoted to admin by {admin['id']}")
        return {"status": "success", "message": "تمت ترقية المستخدم إلى مسؤول (Admin) بنجاح!"}
    except Exception as e:
        logger.error(f"❌ Error promoting user to admin: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل ترقية المستخدم كمسؤول")

@router.post("/users/{user_id}/remove-admin")
async def remove_user_admin(user_id: str, admin=Depends(verify_admin)):
    """Demote an admin back to regular user."""
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="لا يمكنك سحب صلاحية الأدمن من حسابك الحالي.")
    try:
        supabase = get_supabase()
        supabase.table("profiles").update({"role": "user"}).eq("id", user_id).execute()
        logger.info(f"👤 User {user_id} demoted from admin by {admin['id']}")
        return {"status": "success", "message": "تم سحب صلاحيات الأدمن وإعادة الحساب كمستخدم عادي."}
    except Exception as e:
        logger.error(f"❌ Error demoting admin: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل سحب صلاحية الأدمن")

class GrantAdminQueryModel(BaseModel):
    query: str # phone or full name

@router.post("/users/grant-admin-by-query")
async def grant_admin_by_query(data: GrantAdminQueryModel, admin=Depends(verify_admin)):
    """Find a user by phone or name and promote to Admin."""
    q = data.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="يرجى إدخال رقم الهاتف أو اسم المستخدم للبحث.")
    try:
        supabase = get_supabase()
        target_user: Optional[Dict[str, Any]] = None

        # Check if searching by email
        if "@" in q:
            try:
                auth_users = supabase.auth.admin.list_users(per_page=1000)
                for u in (auth_users or []):
                    uemail = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                    if uemail and uemail.lower() == q.lower():
                        uid = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                        p_res = supabase.table("profiles").select("id, full_name, phone_number, role").eq("id", uid).execute()
                        p_users = _as_dict_list(p_res.data)
                        if p_users:
                            target_user = p_users[0]
                            target_user["email"] = uemail
                            break
            except Exception as ae:
                logger.warning(f"Error searching user by email: {ae}")

        if not target_user:
            # Search by phone number or full name
            res = supabase.table("profiles")\
                .select("id, full_name, phone_number, role")\
                .or_(f"phone_number.ilike.%{q}%,full_name.ilike.%{q}%")\
                .limit(5)\
                .execute()
            
            users = _as_dict_list(res.data)
            if not users:
                raise HTTPException(status_code=404, detail="لم يتم العثور على مستخدم مطابق للبحث.")
            target_user = users[0]

        target_id = str(target_user.get("id", ""))
        supabase.table("profiles").update({"role": "admin"}).eq("id", target_id).execute()
        
        display_label = target_user.get("full_name") or target_user.get("email") or target_user.get("phone_number")
        return {
            "status": "success",
            "message": f"تمت ترقية ({display_label}) إلى مسؤول (Admin) بنجاح!",
            "user": target_user
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error granting admin by query: {str(e)}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء البحث وترقية المستخدم.")

# =========================================================================
# 👑 DIRECT CUSTOMER SUBSCRIPTION & SMART MINUTES ASSIGNMENT
# =========================================================================

class AssignSubscriptionModel(BaseModel):
    email: str
    plan_id: Optional[str] = None
    custom_minutes: Optional[int] = 0
    add_to_existing: bool = False
    notes: Optional[str] = None

@router.get("/subscriptions/plans")
async def get_admin_subscription_plans(admin=Depends(verify_admin)):
    """Fetch all active plans for the admin subscription assigner modal."""
    try:
        supabase = get_supabase()
        res = supabase.table("subscription_plans").select("*").eq("is_active", True).order("price_ils").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Error fetching admin plans: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب باقات الاشتراك")

@router.get("/users/lookup-by-email")
async def lookup_user_by_email(email: str, admin=Depends(verify_admin)):
    """Look up user details, current quota, and current plan by Google email."""
    clean_email = email.strip().lower()
    if not clean_email or "@" not in clean_email:
        raise HTTPException(status_code=400, detail="يرجى إدخال بريد إلكتروني صالح")
        
    supabase = get_supabase()
    target_user_id = None
    target_full_name = clean_email.split("@")[0]

    try:
        page = 1
        while True:
            auth_users = supabase.auth.admin.list_users(page=page, per_page=500)
            if not auth_users:
                break
            for u in auth_users:
                u_email = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                if u_email and u_email.strip().lower() == clean_email:
                    target_user_id = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                    meta = getattr(u, "user_metadata", {}) or {}
                    if isinstance(meta, dict) and meta.get("full_name"):
                        target_full_name = meta["full_name"]
                    break
            if target_user_id or len(auth_users) < 500:
                break
            page += 1
    except Exception as ae:
        logger.warning(f"Error listing auth users in lookup: {ae}")

    if not target_user_id:
        return {
            "exists": False,
            "email": clean_email,
            "full_name": target_full_name,
            "quota_remaining": 0,
            "current_plan": "حساب جديد (سيتم إنشاؤه وتفعيل الرصيد له فوراً)",
            "message": "هذا الحساب لم يسجل بعد، سيقوم النظام بتهيئته وشحن الرصيد له ليجده جاهزاً فور تسجيل دخوله بجوجل 🚀"
        }

    # Fetch profile
    prof_res = supabase.table("profiles").select("*").eq("id", target_user_id).execute()
    prof = prof_res.data[0] if prof_res.data else {}

    plan_name = "بدون باقة (حساب مجاني)"
    c_plan_id = prof.get("current_plan_id")
    if c_plan_id:
        p_res = supabase.table("subscription_plans").select("name").eq("id", c_plan_id).execute()
        if p_res.data:
            plan_name = p_res.data[0]["name"]

    return {
        "exists": True,
        "id": target_user_id,
        "email": clean_email,
        "full_name": prof.get("full_name") or target_full_name,
        "quota_remaining": prof.get("quota_remaining", 0),
        "current_plan": plan_name,
        "current_plan_id": c_plan_id,
        "is_blocked": prof.get("is_blocked", False),
        "phone_number": prof.get("phone_number", "")
    }

@router.post("/subscriptions/assign-by-email")
async def assign_subscription_by_email(data: AssignSubscriptionModel, admin=Depends(verify_admin)):
    """
    Subscribe a customer to any plan and/or add smart minutes directly via their Google email.
    """
    clean_email = data.email.strip().lower()
    if not clean_email or "@" not in clean_email:
        raise HTTPException(status_code=400, detail="يرجى إدخال بريد إلكتروني صالح للعميل.")

    supabase = get_supabase()
    target_user_id = None
    target_full_name = clean_email.split("@")[0]

    # 1. Search for user in Auth
    try:
        page = 1
        while True:
            auth_users = supabase.auth.admin.list_users(page=page, per_page=500)
            if not auth_users:
                break
            for u in auth_users:
                u_email = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                if u_email and u_email.strip().lower() == clean_email:
                    target_user_id = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                    meta = getattr(u, "user_metadata", {}) or {}
                    if isinstance(meta, dict) and meta.get("full_name"):
                        target_full_name = meta["full_name"]
                    break
            if target_user_id or len(auth_users) < 500:
                break
            page += 1
    except Exception as e:
        logger.warning(f"Error listing users for assign: {e}")

    # 2. If user does not exist in auth, create them in advance
    if not target_user_id:
        try:
            created = supabase.auth.admin.create_user({
                "email": clean_email,
                "email_confirm": True,
                "user_metadata": {"full_name": target_full_name}
            })
            if hasattr(created, "user") and created.user:
                target_user_id = str(created.user.id)
            elif isinstance(created, dict) and "id" in created:
                target_user_id = str(created["id"])
            logger.info(f"✨ Auto-created user account for {clean_email} -> {target_user_id}")
        except Exception as ce:
            logger.warning(f"Initial create_user returned: {ce}. Searching auth users again...")
            # If user already registered, perform an exhaustive search across pages
            try:
                page = 1
                while True:
                    auth_users = supabase.auth.admin.list_users(page=page, per_page=500)
                    if not auth_users:
                        break
                    for u in auth_users:
                        u_email = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                        if u_email and u_email.strip().lower() == clean_email:
                            target_user_id = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                            meta = getattr(u, "user_metadata", {}) or {}
                            if isinstance(meta, dict) and meta.get("full_name"):
                                target_full_name = meta["full_name"]
                            break
                    if target_user_id or len(auth_users) < 500:
                        break
                    page += 1
            except Exception as se:
                logger.error(f"Exhaustive search failed: {se}")

            if not target_user_id:
                raise HTTPException(status_code=400, detail={"user_message": f"تعذر إنشاء أو ربط حساب العميل: {str(ce)}"})

    if not target_user_id:
        raise HTTPException(status_code=404, detail={"user_message": "تعذر العثور على معرّف الحساب."})

    # 3. Ensure profile in profiles table
    prof_res = supabase.table("profiles").select("*").eq("id", target_user_id).execute()
    profile = prof_res.data[0] if prof_res.data else None

    if not profile:
        new_prof = {
            "id": target_user_id,
            "full_name": target_full_name,
            "quota_remaining": 0,
            "role": "user"
        }
        ins_res = supabase.table("profiles").insert(new_prof).execute()
        profile = ins_res.data[0] if ins_res.data else new_prof

    current_quota = int(profile.get("quota_remaining") or 0)

    # 4. Handle plan assignment
    plan = None
    plan_quota = 0
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=30)

    if data.plan_id and data.plan_id != "custom_only":
        p_res = supabase.table("subscription_plans").select("*").eq("id", data.plan_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail={"user_message": "الباقة المختارة غير موجودة."})
        plan = p_res.data[0]
        plan_quota = int(plan.get("lecture_quota", 0) or 0)
        p_price = float(plan.get("price_ils", 0.0) or 0.0)

        # All plans have a validity duration of exactly 1 month (30 days) as requested
        expires_at = now + timedelta(days=30)

        # Cancel any previous active subscription
        supabase.table("user_subscriptions") \
            .update({"status": "cancelled"}) \
            .eq("user_id", target_user_id) \
            .eq("status", "active") \
            .execute()

        # Insert new active subscription
        sub_record = {
            "user_id": target_user_id,
            "plan_id": plan["id"],
            "status": "active",
            "starts_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "lectures_used": 0
        }
        supabase.table("user_subscriptions").insert(sub_record).execute()

        # Record completed transaction
        try:
            supabase.table("transactions").insert({
                "user_id": target_user_id,
                "plan_id": plan["id"],
                "amount": p_price,
                "payment_method": "admin_direct",
                "status": "completed",
                "short_order_id": f"ADM-{target_user_id[:6].upper()}"
            }).execute()
        except Exception as tx_err:
            logger.warning(f"Failed to record admin transaction: {tx_err}")

    # 5. Compute new quota
    custom_add = int(data.custom_minutes or 0)
    minutes_added = plan_quota + custom_add

    if data.add_to_existing:
        new_quota = current_quota + minutes_added
    else:
        new_quota = minutes_added if plan else (current_quota + custom_add)

    # 6. Update user profile
    update_dict: Dict[str, Any] = {
        "quota_remaining": new_quota
    }
    if plan:
        update_dict["current_plan_id"] = plan["id"]
        update_dict["quota_reset_at"] = expires_at.isoformat()

    supabase.table("profiles").update(update_dict).eq("id", target_user_id).execute()
    try:
        from app.core.auth_guard import invalidate_profile_cache
        invalidate_profile_cache(target_user_id)
    except Exception:
        pass

    # 7. Create notification for user
    plan_display = plan["name"] if plan else "رصيد دقائق إضافية"
    try:
        supabase.table("notifications").insert({
            "user_id": target_user_id,
            "title": "تم تفعيل اشتراكك وإضافة الدقائق ⚡",
            "message": f"تم تفعيل {plan_display} بنجاح. رصيدك الحالي الآن: {new_quota} دقيقة ذكية.",
            "type": "subscription"
        }).execute()
    except Exception:
        pass

    logger.info(f"👑 Admin assigned plan '{plan_display}' to {clean_email}. Quota: {new_quota} mins.")

    return {
        "status": "success",
        "message": f"تم بنجاح تفعيل {plan['name'] if plan else 'الرصيد'} للحساب ({clean_email}) وإضافة {minutes_added} دقيقة! الرصيد الإجمالي الحالي: {new_quota} دقيقة.",
        "user_id": target_user_id,
        "email": clean_email,
        "plan_name": plan["name"] if plan else "رصيد يدوي مخصص",
        "minutes_added": minutes_added,
        "total_quota": new_quota,
        "expires_at": expires_at.isoformat() if plan else None
    }


@router.get("/analytics/financial")
async def get_financial_analytics(admin=Depends(verify_admin)):
    """Fetch financial analytics (Student Subscriptions and OpenRouter AI costs)."""
    try:
        supabase = get_supabase()
        
        # 1. Student Subscription Transactions
        tx_res = supabase.table("transactions")\
            .select("id, short_order_id, user_id, amount, status, payment_method, created_at, subscription_plans(name, name_en, price_ils), profiles:user_id(full_name, phone_number)")\
            .in_("status", ["completed", "approved"])\
            .order("created_at", desc=True)\
            .execute()
        
        # Build user usage & token consumption map from usage_logs
        user_usage_map: Dict[str, Any] = {}
        try:
            logs_res = supabase.table("usage_logs")\
                .select("user_id, metadata, api_cost_usd")\
                .execute()
            for l in _as_dict_list(logs_res.data):
                uid = l.get("user_id")
                if not uid:
                    continue
                if uid not in user_usage_map:
                    user_usage_map[uid] = {
                        "tokens": 0,
                        "cost_usd": 0.0,
                        "cost_ils": 0.0,
                        "lectures_count": 0
                    }
                user_usage_map[uid]["lectures_count"] += 1
                meta = l.get("metadata") or {}
                transcript = meta.get("transcript") or ""
                annotations = meta.get("annotations") or {}
                inp_chars = len(transcript) if isinstance(transcript, str) else 0
                out_chars = len(str(annotations))
                inp_tokens = int(inp_chars / 3.5)
                out_tokens = int(out_chars / 3.5)
                total_tokens = inp_tokens + out_tokens
                cost = (inp_tokens * 0.000000075) + (out_tokens * 0.00000030)
                user_usage_map[uid]["tokens"] += total_tokens
                user_usage_map[uid]["cost_usd"] += cost

            for uid, info in user_usage_map.items():
                info["cost_usd"] = round(info["cost_usd"], 4)
                info["cost_ils"] = round(info["cost_usd"] * 3.75, 2)
        except Exception as ue:
            logger.warning(f"Failed to aggregate user usage logs: {ue}")

        student_txs_raw = _as_dict_list(tx_res.data)
        student_txs = []
        for tx in student_txs_raw:
            plan_obj = tx.get("subscription_plans") or {}
            prof_obj = tx.get("profiles") or {}
            u_id = tx.get("user_id")
            usage_info = user_usage_map.get(u_id, {
                "tokens": 0,
                "cost_usd": 0.0,
                "cost_ils": 0.0,
                "lectures_count": 0
            })
            tx["plan_name"] = plan_obj.get("name") or plan_obj.get("name_en") or "اشتراك لمّاح"
            tx["sender_name"] = prof_obj.get("full_name") or "طالب"
            tx["sender_phone"] = prof_obj.get("phone_number") or ""
            tx["tokens_used"] = usage_info["tokens"]
            tx["ai_cost_usd"] = usage_info["cost_usd"]
            tx["ai_cost_ils"] = usage_info["cost_ils"]
            tx["lectures_processed"] = usage_info["lectures_count"]
            student_txs.append(tx)
        student_revenue = sum(float(tx.get("amount", 0) or 0) for tx in student_txs)
        
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        student_mrr = 0.0
        for tx in student_txs:
            try:
                created = datetime.fromisoformat(str(tx.get("created_at", "")).replace('Z', '+00:00'))
                if created >= thirty_days_ago:
                    student_mrr += float(tx.get("amount", 0) or 0)
            except Exception:
                pass

        # 2. OpenRouter AI Costs & Credits Integration (Live API)
        openrouter_stats = {
            "configured": bool(settings.OPENROUTER_API_KEY),
            "total_credits_usd": 0.0,
            "total_usage_usd": 0.0,
            "remaining_balance_usd": 0.0,
            "total_usage_ils": 0.0,
            "exchange_rate": 3.75,
            "is_low_balance": False
        }

        if settings.OPENROUTER_API_KEY:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/credits",
                        headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"}
                    )
                    if resp.status_code == 200:
                        cred_data = resp.json().get("data", {})
                        total_credits = float(cred_data.get("total_credits", 0.0) or 0.0)
                        total_usage = float(cred_data.get("total_usage", 0.0) or 0.0)
                        remaining = max(0.0, total_credits - total_usage)
                        usd_ils_rate = 3.75
                        usage_ils = round(total_usage * usd_ils_rate, 2)
                        
                        openrouter_stats.update({
                            "total_credits_usd": round(total_credits, 2),
                            "total_usage_usd": round(total_usage, 2),
                            "remaining_balance_usd": round(remaining, 2),
                            "total_usage_ils": usage_ils,
                            "exchange_rate": usd_ils_rate,
                            "is_low_balance": remaining < 3.0
                        })
            except Exception as oe:
                logger.warning(f"Could not load OpenRouter credits in admin analytics: {oe}")

        # 3. Server & Operational Expenses (from usage_logs action_type='server_expense')
        server_expenses_list = []
        total_server_expenses_ils = 0.0
        try:
            exp_res = supabase.table("usage_logs")\
                .select("id, metadata, api_cost_usd, created_at")\
                .eq("action_type", "server_expense")\
                .order("created_at", desc=True)\
                .execute()
            for r in _as_dict_list(exp_res.data):
                meta = r.get("metadata") or {}
                amt_ils = float(meta.get("amount_ils") or (float(r.get("api_cost_usd") or 0.0) * 3.75))
                total_server_expenses_ils += amt_ils
                server_expenses_list.append({
                    "id": r.get("id"),
                    "title": meta.get("title") or "تكلفة سيرفر وتشغيل",
                    "amount_ils": round(amt_ils, 2),
                    "amount_usd": round(amt_ils / 3.75, 2),
                    "category": meta.get("category") or "server",
                    "notes": meta.get("notes") or "",
                    "created_at": r.get("created_at")
                })
        except Exception as ee:
            logger.warning(f"Could not load server expenses: {ee}")

        total_server_expenses_ils = round(total_server_expenses_ils, 2)
        true_net_profit = round(max(0.0, student_revenue - openrouter_stats["total_usage_ils"] - total_server_expenses_ils), 2)

        return {
            "net_platform_profit": student_revenue,
            "true_net_profit": true_net_profit,
            "openrouter": openrouter_stats,
            "total_gross_inflow": student_revenue,
            "total_server_expenses_ils": total_server_expenses_ils,
            "server_expenses": server_expenses_list,
            "total_teacher_liabilities": 0.0,
            "mrr": student_mrr,
            
            # Student Subscriptions
            "student_subscriptions": {
                "revenue": student_revenue,
                "mrr": student_mrr,
                "total_count": len(student_txs),
                "recent_transactions": student_txs
            },
            
            # Legacy compatibility
            "tutoring": {
                "platform_net_revenue": 0.0,
                "gross_booking_volume": 0.0,
                "tutor_earned_share": 0.0,
                "tutor_payouts_completed": 0.0,
                "tutor_payouts_pending": 0.0,
                "total_wallets_balance": 0.0,
                "total_bookings_count": 0,
                "recent_bookings": [],
                "pending_payouts": [],
                "completed_payouts": []
            },
            "total_platform_net_revenue": student_revenue,
            "total_revenue": student_revenue,
            "total_approved_transactions": len(student_txs)
        }
    except Exception as e:
        logger.error(f"❌ Error fetching financial analytics: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل جلب التحليلات المالية")


# =========================================================================
# 💰 TRANSACTIONS & EXPENSES MANAGEMENT (EDIT PRICING & SERVER EXPENSES)
# =========================================================================

class UpdateTransactionModel(BaseModel):
    amount: Optional[float] = None
    payment_method: Optional[str] = None
    status: Optional[str] = None

@router.put("/transactions/{tx_id}")
async def update_transaction(tx_id: str, data: UpdateTransactionModel, admin=Depends(verify_admin)):
    """Update subscription transaction amount, payment method, or status."""
    try:
        supabase = get_supabase()
        update_data = {}
        if data.amount is not None:
            update_data["amount"] = float(data.amount)
        if data.payment_method is not None:
            update_data["payment_method"] = data.payment_method
        if data.status is not None:
            update_data["status"] = data.status

        if not update_data:
            raise HTTPException(status_code=400, detail="لا توجد بيانات للتعديل.")

        res = supabase.table("transactions").update(update_data).eq("id", tx_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="العملية غير موجودة.")

        logger.info(f"✅ Admin {admin['id']} updated transaction {tx_id}: {update_data}")
        return {"status": "success", "message": "تم تحديث بيانات المعاملة بنجاح.", "transaction": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating transaction: {e}")
        raise HTTPException(status_code=500, detail="فشل تحديث المعاملة.")

@router.delete("/transactions/{tx_id}")
async def delete_transaction(tx_id: str, admin=Depends(verify_admin)):
    """Delete a transaction completely from revenue records."""
    try:
        supabase = get_supabase()
        res = supabase.table("transactions").delete().eq("id", tx_id).execute()
        logger.info(f"🗑️ Admin {admin['id']} deleted transaction {tx_id}")
        return {"status": "success", "message": "تم حذف المعاملة وإلغاء أرباحها بنجاح."}
    except Exception as e:
        logger.error(f"Error deleting transaction: {e}")
        raise HTTPException(status_code=500, detail="فشل حذف المعاملة.")


class ServerExpenseModel(BaseModel):
    title: str
    amount_ils: float
    category: Optional[str] = "server"
    notes: Optional[str] = ""

@router.post("/expenses")
async def add_server_expense(data: ServerExpenseModel, admin=Depends(verify_admin)):
    """Add a server or operational expense to deduct from net profits."""
    try:
        supabase = get_supabase()
        amt_ils = float(data.amount_ils)
        amt_usd = round(amt_ils / 3.75, 4)
        
        record = {
            "user_id": admin["id"],
            "action_type": "server_expense",
            "api_cost_usd": amt_usd,
            "status": "success",
            "metadata": {
                "title": data.title.strip(),
                "amount_ils": amt_ils,
                "amount_usd": amt_usd,
                "category": data.category or "server",
                "notes": data.notes or ""
            }
        }
        res = supabase.table("usage_logs").insert(record).execute()
        logger.info(f"💸 Admin {admin['id']} added expense: {data.title} (₪{amt_ils})")
        return {"status": "success", "message": "تمت إضافة التكلفة بنجاح وخصمها من صافي الأرباح.", "expense": res.data[0] if res.data else None}
    except Exception as e:
        logger.error(f"Error adding server expense: {e}")
        raise HTTPException(status_code=500, detail="فشل تسجيل تكلفة السيرفر.")

@router.delete("/expenses/{expense_id}")
async def delete_server_expense(expense_id: str, admin=Depends(verify_admin)):
    """Delete a server expense record."""
    try:
        supabase = get_supabase()
        res = supabase.table("usage_logs").delete().eq("id", expense_id).eq("action_type", "server_expense").execute()
        logger.info(f"🗑️ Admin {admin['id']} removed expense {expense_id}")
        return {"status": "success", "message": "تم حذف التكلفة وإعادة احتساب الأرباح."}
    except Exception as e:
        logger.error(f"Error deleting server expense: {e}")
        raise HTTPException(status_code=500, detail="فشل حذف التكلفة.")

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
from typing import Optional

class AnnouncementModel(BaseModel):
    message: str
    active: bool
    banner_type: Optional[str] = "custom"
    bg_color: Optional[str] = "linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #ec4899 100%)"
    text_color: Optional[str] = "#ffffff"
    btn_bg: Optional[str] = "#ffffff"
    btn_text: Optional[str] = "#000000"
    icon_name: Optional[str] = "Sparkles"
    cta_text: Optional[str] = None
    cta_link: Optional[str] = None
    is_dismissible: Optional[bool] = True

@router.post("/announcements")
async def update_announcement(data: AnnouncementModel, admin=Depends(verify_admin)):
    """Update global announcement with full visual customization."""
    try:
        settings = {}
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                
        settings["global_announcement"] = {
            "message": data.message,
            "active": data.active,
            "banner_type": data.banner_type or "custom",
            "bg_color": data.bg_color or "linear-gradient(135deg, #6366f1 0%, #a855f7 50%, #ec4899 100%)",
            "text_color": data.text_color or "#ffffff",
            "btn_bg": data.btn_bg or "#ffffff",
            "btn_text": data.btn_text or "#000000",
            "icon_name": data.icon_name or "Sparkles",
            "cta_text": data.cta_text,
            "cta_link": data.cta_link,
            "is_dismissible": data.is_dismissible if data.is_dismissible is not None else True
        }
        
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False)
            
        return {"status": "success", "message": "تم حفظ ونشر إعدادات الإعلان المخصص بنجاح"}
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
            .update({"quota_remaining": 0, "current_plan_id": None}) \
            .in_("id", data.user_ids) \
            .execute()

        logger.info(f"✅ Admin {admin['id']} bulk revoked plans for users: {data.user_ids}")
        return {"status": "success", "message": f"تم إلغاء خطط {len(data.user_ids)} مستخدم بنجاح."}

    except Exception as e:
        logger.error(f"❌ Error bulk revoking plans: {str(e)}")
        raise HTTPException(status_code=500, detail="فشل إلغاء الخطط للمستخدمين المحددين")


@router.get("/export/users-excel")
async def export_users_excel(admin=Depends(verify_admin)):
    """
    Export all users to a professionally formatted Excel (.xlsx) file with:
    - User details (Name, Email, Phone, Role)
    - Academic profile (University, Major, Year, Interests, Preferred study style)
    - Activity stats (Quota remaining, lectures processed, published marketplace notebooks)
    - Account status and Registration date
    RTL Arabic worksheet styling with custom headers, zebra striping, and auto-filters.
    """
    try:
        supabase = get_supabase()

        # 1. Fetch all profiles
        profiles_res = supabase.table("profiles")\
            .select("id, full_name, phone_number, is_blocked, role, created_at, quota_remaining, current_plan_id, device_fingerprint, device_metadata")\
            .order("created_at", desc=True)\
            .execute()
        profiles = _as_dict_list(profiles_res.data)

        # 2. Fetch plans map
        plans_map: Dict[str, Any] = {}
        try:
            plans_res = supabase.table("subscription_plans").select("id, name").execute()
            for p in _as_dict_list(plans_res.data):
                p_id = p.get("id")
                p_name = p.get("name")
                if p_id and p_name:
                    plans_map[p_id] = p_name
        except Exception:
            pass

        # 3. Fetch active user subscriptions map
        subs_map: Dict[str, Any] = {}
        try:
            subs_res = supabase.table("user_subscriptions")\
                .select("user_id, plan_id, status")\
                .eq("status", "active")\
                .execute()
            for s in _as_dict_list(subs_res.data):
                p_id = s.get("plan_id")
                u_id = s.get("user_id")
                if p_id in plans_map and u_id:
                    subs_map[u_id] = plans_map[p_id]
        except Exception:
            pass

        # 4. Fetch auth user emails map
        email_map: Dict[str, str] = {}
        try:
            page = 1
            while True:
                auth_users = supabase.auth.admin.list_users(page=page, per_page=1000)
                if not auth_users:
                    break
                for u in auth_users:
                    uid = str(getattr(u, "id", "") or (u.get("id") if isinstance(u, dict) else ""))
                    uemail = getattr(u, "email", "") or (u.get("email") if isinstance(u, dict) else "")
                    if uid and uemail:
                        email_map[uid] = uemail
                if len(auth_users) < 1000:
                    break
                page += 1
        except Exception as ae:
            logger.warning(f"Could not load auth user emails for Excel: {ae}")

        # 5. Fetch processed lectures count per user
        lectures_count_map: Dict[str, int] = {}
        try:
            logs_res = supabase.table("usage_logs")\
                .select("user_id")\
                .eq("action_type", "lecture_process")\
                .eq("status", "success")\
                .execute()
            for row in _as_dict_list(logs_res.data):
                u_id = row.get("user_id")
                if u_id:
                    lectures_count_map[u_id] = lectures_count_map.get(u_id, 0) + 1
        except Exception as le:
            logger.warning(f"Could not load lecture stats: {le}")

        # 6. Fetch published notebooks count per user
        marketplace_count_map: Dict[str, int] = {}
        try:
            market_res = supabase.table("notebook_marketplace").select("publisher_id").execute()
            for row in _as_dict_list(market_res.data):
                p_id = row.get("publisher_id")
                if p_id:
                    marketplace_count_map[p_id] = marketplace_count_map.get(p_id, 0) + 1
        except Exception as me:
            logger.warning(f"Could not load marketplace stats: {me}")

        # 7. Create openpyxl Workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "مستخدمو وطلاب لماح"
        ws.views.sheetView[0].rightToLeft = True

        # Styles
        title_font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        title_fill = PatternFill(start_color="1E1B4B", end_color="1E1B4B", fill_type="solid")
        sub_font = Font(name="Calibri", size=10, bold=False, color="E0E7FF")
        sub_fill = PatternFill(start_color="312E81", end_color="312E81", fill_type="solid")

        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")

        data_font = Font(name="Calibri", size=10, color="0F172A")
        even_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
        odd_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

        thin_side = Side(border_style="thin", color="CBD5E1")
        cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

        center_align = Alignment(horizontal="center", vertical="center", wrap_text=False)
        right_align = Alignment(horizontal="right", vertical="center", wrap_text=False)

        headers = [
            "م",
            "اسم الطالب / المستخدم",
            "البريد الإلكتروني",
            "رقم الهاتف / الواتساب",
            "الجامعة / الكلية",
            "التخصص الأكاديمي",
            "السنة الدراسية",
            "الاهتمامات الأكاديمية",
            "نمط الدراسة المفضل",
            "نوع الحساب",
            "الاشتراك الحالي",
            "الرصيد المتبقي",
            "المحاضرات المعالجة",
            "ملازم بالمتجر",
            "حالة الحساب",
            "تاريخ التسجيل",
            "معرف المستخدم (UID)"
        ]

        # Row 1: Main Title Banner
        ws.merge_cells("A1:Q1")
        top_cell = ws["A1"]
        top_cell.value = "🎓 منصة لمّاح الذكية - سجل بيانات واهتمامات الطلاب والمستخدمين الشامل"
        top_cell.font = title_font
        top_cell.fill = title_fill
        top_cell.alignment = center_align
        ws.row_dimensions[1].height = 36

        # Row 2: Subtitle / Metadata
        ws.merge_cells("A2:Q2")
        sub_cell = ws["A2"]
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        sub_cell.value = f"تاريخ التصدير: {now_str} | إجمالي الحسابات: {len(profiles)} | منصة لماح للذكاء الاصطناعي الأكاديمي"
        sub_cell.font = sub_font
        sub_cell.fill = sub_fill
        sub_cell.alignment = center_align
        ws.row_dimensions[2].height = 24

        # Row 3: Blank separator
        ws.row_dimensions[3].height = 10

        # Row 4: Column Headers
        ws.row_dimensions[4].height = 30
        for col_idx, header_title in enumerate(headers, start=1):
            cell = ws.cell(row=4, column=col_idx, value=header_title)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = cell_border

        # Rows 5+: Data rows
        for row_idx, prof in enumerate(profiles, start=5):
            u_id = str(prof.get("id", ""))
            raw_meta = prof.get("device_metadata")
            academic: Dict[str, Any] = {}
            if raw_meta:
                try:
                    parsed_meta = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
                    if isinstance(parsed_meta, dict):
                        academic = parsed_meta.get("academic_profile", {})
                except Exception:
                    academic = {}

            c_plan_id = prof.get("current_plan_id")
            plan_name = subs_map.get(u_id) or (plans_map.get(c_plan_id) if c_plan_id else None) or "بدون باقة"
            role_label = "مشرف (Admin)" if prof.get("role") == "admin" else "طالب"
            status_label = "محظور 🚫" if prof.get("is_blocked") else "نشط ✅"
            
            created_at_raw = prof.get("created_at") or ""
            created_at_formatted = created_at_raw[:16].replace("T", " ") if created_at_raw else "-"

            interests_list = academic.get("interests", [])
            interests_str = ", ".join(interests_list) if isinstance(interests_list, list) and interests_list else (str(interests_list) if interests_list else "عام")

            phone_val = academic.get("phone") or prof.get("phone_number") or "-"

            row_data = [
                row_idx - 4,                                           # Index #
                prof.get("full_name") or "بدون اسم",                   # Name
                email_map.get(u_id, "غير متوفر"),                     # Email
                phone_val,                                            # Phone
                academic.get("university") or "لم تُحدد بعد",          # University
                academic.get("major") or "لم يُحدد بعد",              # Major
                academic.get("academic_year") or "لم تُحدد بعد",      # Year
                interests_str,                                        # Interests
                academic.get("study_preference") or "غير محدد",       # Preference
                role_label,                                           # Role
                plan_name,                                            # Plan
                prof.get("quota_remaining", 0),                       # Quota
                lectures_count_map.get(u_id, 0),                      # Lectures
                marketplace_count_map.get(u_id, 0),                   # Marketplace
                status_label,                                         # Status
                created_at_formatted,                                 # Created At
                u_id                                                  # UID
            ]

            fill_to_use = even_fill if (row_idx % 2 == 0) else odd_fill
            ws.row_dimensions[row_idx].height = 22

            for col_idx, value in enumerate(row_data, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.font = data_font
                cell.fill = fill_to_use
                cell.border = cell_border
                # Alignment: Right-align text columns, center others
                if col_idx in (2, 3, 5, 6, 8):
                    cell.alignment = right_align
                else:
                    cell.alignment = center_align

        # Auto filter
        last_row = len(profiles) + 4
        ws.auto_filter.ref = f"A4:Q{last_row}"

        # Calculate column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            # Inspect values in header and data rows
            for cell in col[3:]: # start from row 4
                v = str(cell.value or "")
                max_len = max(max_len, len(v))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        excel_bytes = output.getvalue()

        filename = f"Lammah_Users_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        logger.info(f"📊 Admin {admin['id']} exported {len(profiles)} users to Excel")

        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    except Exception as e:
        logger.error(f"❌ Error exporting users to Excel: {str(e)}")
        raise HTTPException(status_code=500, detail=f"فشل تصدير بيانات المستخدمين إلى إكسل: {str(e)}")

