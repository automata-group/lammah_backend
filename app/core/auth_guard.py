from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.db.supabase_client import get_supabase, get_auth_client
from app.core.logger import logger
from datetime import datetime, timezone
import json

security = HTTPBearer()


async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token from Supabase Auth and return user data."""
    token = credentials.credentials
    auth_client = get_auth_client()
    
    try:
        user_response = auth_client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail={
                "user_message": "🔒 انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى."
            })
        return user_response.user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Auth verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail={
            "user_message": f"🔒 فشل التحقق: {str(e)}"
        })


async def get_user_profile(request: Request, user=Depends(verify_token)):
    """Get user profile. Auto-create if missing. Capture device metadata."""
    supabase = get_supabase()
    
    # Step 1: Try to get existing profile
    try:
        result = supabase.table("profiles").select("*").eq("id", user.id).execute()
        profile_data = result.data[0] if result.data else None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ فشل جلب الملف الشخصي: {error_msg}")
        
        # If table doesn't exist, tell the user clearly
        raise HTTPException(status_code=500, detail={
            "user_message": f"❌ خطأ في قاعدة البيانات: {error_msg}"
        })
    
    # Step 2: Auto-create profile if not exists
    if not profile_data:
        new_profile = {
            "id": user.id,
            "phone_number": getattr(user, 'phone', None),
            "phone_verified": bool(getattr(user, 'phone', None)),
            "quota_remaining": 0,
        }
        
        try:
            insert_result = supabase.table("profiles").insert(new_profile).execute()
            if not insert_result.data:
                raise Exception("INSERT returned empty data")
            profile_data = insert_result.data[0]
            logger.info(f"✅ تم إنشاء ملف شخصي جديد: {user.id}")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ فشل إنشاء الملف الشخصي: {error_msg}")
            raise HTTPException(status_code=500, detail={
                "user_message": f"❌ فشل إنشاء الحساب: {error_msg}"
            })
    
    # Step 3: CAPTURE DEVICE METADATA
    fingerprint = request.headers.get("X-Fingerprint")
    device_meta = request.headers.get("X-Device-Metadata")
    
    update_data = {}
    if fingerprint and not profile_data.get("device_fingerprint"):
        update_data["device_fingerprint"] = fingerprint
        profile_data["device_fingerprint"] = fingerprint
    
    if device_meta:
        existing = profile_data.get("device_metadata")
        existing_dict = {}
        if existing:
            try:
                existing_dict = json.loads(existing) if isinstance(existing, str) else (existing if isinstance(existing, dict) else {})
            except Exception:
                existing_dict = {}

        new_dict = {}
        try:
            new_dict = json.loads(device_meta) if isinstance(device_meta, str) else (device_meta if isinstance(device_meta, dict) else {})
        except Exception:
            new_dict = {"raw": str(device_meta)}

        merged = {**existing_dict, **new_dict}
        # Safeguard important custom nested objects
        if "academic_profile" in existing_dict and "academic_profile" not in new_dict:
            merged["academic_profile"] = existing_dict["academic_profile"]
        if "seller_username" in existing_dict and "seller_username" not in new_dict:
            merged["seller_username"] = existing_dict["seller_username"]

        merged_str = json.dumps(merged, ensure_ascii=False)
        update_data["device_metadata"] = merged_str
        profile_data["device_metadata"] = merged_str
        
    if update_data:
        try:
            supabase.table("profiles").update(update_data).eq("id", profile_data["id"]).execute()
        except Exception as e:
            logger.error(f"❌ Failed to update device tracking info: {e}")

    # Step 4: Ensure lamaszero@gmail.com is always officially ADMIN
    user_email = (getattr(user, 'email', None) or "").lower().strip()
    if user_email == "lamaszero@gmail.com" and profile_data.get("role") != "admin":
        try:
            supabase.table("profiles").update({"role": "admin"}).eq("id", profile_data["id"]).execute()
            profile_data["role"] = "admin"
            logger.info(f"👑 Automatically granted ADMIN role to {user_email}")
        except Exception as admin_err:
            logger.error(f"❌ Failed to set admin role for {user_email}: {admin_err}")

    # Step 5: Check if blocked
    if profile_data.get("is_blocked"):
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 تم حظر حسابك. يرجى التواصل مع الدعم الفني."
        })
    
    return profile_data


async def verify_phone_active(profile=Depends(get_user_profile)):
    """
    Fast dependency to check account status.
    Phone verification check removed per user request.
    """
    if profile.get("is_blocked"):
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 تم حظر حسابك. يرجى التواصل مع الدعم الفني."
        })
    return profile


async def check_quota(request: Request, profile=Depends(get_user_profile)):
    """Verify user has remaining quota and prevent device-level trial abuse."""
    logger.info(f"🛡️ [CHECK_QUOTA] Checking quota for user: {profile.get('id')} | Current quota: {profile.get('quota_remaining')}")
    supabase = get_supabase()
    quota = profile.get("quota_remaining", 0)
    fingerprint = profile.get("device_fingerprint") # Already updated by get_user_profile
    
    # 2. Check for active subscription first (Paid users bypass blocks)
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
        logger.error(f"❌ Error checking subscription: {str(e)}")

    # 3. Device trial protection removed - site operates strictly on paid subscriptions

    # 4. Quota enforcement
    if quota <= 0:
        if not active_sub:
            raise HTTPException(status_code=403, detail={
                "user_message": "📦 لا يوجد لديك اشتراك نشط أو نفدت حصتك. يرجى شراء أو تجديد باقتك.",
                "type": "no_quota"
            })
        
        plan = active_sub.get("subscription_plans", {})
        used = active_sub.get("lectures_used", 0) or 0
        quota_total = plan.get("lecture_quota", 0)
        
        if used < quota_total:
            return profile
            
        raise HTTPException(status_code=403, detail={
            "user_message": f"📊 استخدمت كل محاضراتك في باقة ({plan.get('name', 'الحالية')}). يرجى الترقية أو التجديد.",
            "type": "quota_exceeded"
        })
    
    return profile

async def check_premium_plans(request: Request, profile=Depends(check_quota)):
    """Enforces access control: ONLY 'شهري - برو' or 'أسبوعي - VIP' subscribers are authorized."""
    logger.info(f"🛡️ [check_premium_plans] Verifying premium status for user: {profile.get('id')}")
    supabase = get_supabase()
    
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
        logger.error(f"❌ Error checking premium plan: {str(e)}")
        active_sub = None
        
    if not active_sub:
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 هذه الميزة الحصرية متاحة فقط للمشتركين في الباقات المدفوعة. يرجى ترقية حسابك للاستمتاع بها! 🚀"
        })
        
    plan = active_sub.get("subscription_plans", {})
    plan_name = plan.get("name", "")
    plan_price = float(plan.get("price_ils", 0))
    
    # Enforce only the 100 shekels Gold plan ("باقة الفصل الدراسي (الذهبية)")
    if plan_price < 100.0 and "الذهبية" not in plan_name:
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 ميزة إنشاء وتوليد دفاتر البائعين للمتجر متاحة حصرياً لمشتركي (باقة الفصل الدراسي - 100 شيكل) فقط. يرجى ترقية باقتك للبدء في جني الأرباح! 💰🚀"
        })
        
    # Inject active subscription into profile to reuse it in routing
    profile["active_subscription"] = active_sub
    return profile

async def verify_admin(profile=Depends(get_user_profile)):
    """Ensure the user has an admin role."""
    if profile.get("role") != "admin":
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 ليس لديك صلاحية للوصول إلى هذه الصفحة."
        })
    return profile

