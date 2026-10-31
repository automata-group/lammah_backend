import time
import hashlib
import json
import os
import jwt
from datetime import datetime, timezone
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.db.supabase_client import get_supabase, get_auth_client
from app.core.logger import logger

_PROFILE_CACHE = {}  # {user_id: (timestamp, profile_data)}
_PROFILE_CACHE_TTL = 60.0  # Cache profile for 60s to eliminate redundant DB calls

_TOKEN_CACHE = {}  # {token_hash: (expires_at, user_object)}
_TOKEN_CACHE_TTL = 300.0  # Cache verified tokens for 5 minutes

def invalidate_profile_cache(user_id: str):
    """Evict user profile from memory cache upon mutation."""
    _PROFILE_CACHE.pop(user_id, None)

class LocalUser:
    """Lightweight user object fully compatible with Supabase User."""
    def __init__(self, id: str, email: str = None, phone: str = None, user_metadata: dict = None, app_metadata: dict = None):
        self.id = id
        self.email = email
        self.phone = phone
        self.user_metadata = user_metadata or {}
        self.app_metadata = app_metadata or {}

security = HTTPBearer()


async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Ultra-fast JWT verification with in-memory caching and optional local secret decode."""
    token = credentials.credentials
    now = time.time()
    token_exp = None

    # 1. Quick pre-check: inspect token expiration without hitting any network
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        token_exp = unverified.get("exp")
        if token_exp and now >= token_exp:
            raise HTTPException(status_code=401, detail={
                "user_message": "🔒 انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى."
            })
    except jwt.DecodeError:
        pass

    # 2. In-memory Token Cache check (< 0.05ms)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = _TOKEN_CACHE.get(token_hash)
    if cached:
        cached_exp, cached_user = cached
        if now < cached_exp:
            return cached_user
        else:
            _TOKEN_CACHE.pop(token_hash, None)

    # 3. Optional local verification if SUPABASE_JWT_SECRET is configured
    try:
        from app.core.config import settings
        jwt_secret = getattr(settings, "SUPABASE_JWT_SECRET", None) or os.getenv("SUPABASE_JWT_SECRET")
        if jwt_secret:
            payload = jwt.decode(token, jwt_secret, algorithms=["HS256"], audience="authenticated")
            user_obj = LocalUser(
                id=payload.get("sub"),
                email=payload.get("email"),
                phone=payload.get("phone"),
                user_metadata=payload.get("user_metadata", {}),
                app_metadata=payload.get("app_metadata", {})
            )
            cache_exp = min(payload.get("exp", now + _TOKEN_CACHE_TTL), now + _TOKEN_CACHE_TTL)
            _TOKEN_CACHE[token_hash] = (cache_exp, user_obj)
            return user_obj
    except Exception as e:
        logger.debug(f"Local JWT verification fallback to Supabase: {e}")

    # 4. Supabase Auth Verification (Fall-back)
    auth_client = get_auth_client()
    try:
        user_response = auth_client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail={
                "user_message": "🔒 انتهت صلاحية الجلسة. يرجى تسجيل الدخول مرة أخرى."
            })
        user = user_response.user
        
        # Cache for subsequent requests
        cache_until = now + _TOKEN_CACHE_TTL
        if token_exp:
            cache_until = min(token_exp, cache_until)
        _TOKEN_CACHE[token_hash] = (cache_until, user)
        return user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Auth verification failed: {str(e)}")
        raise HTTPException(status_code=401, detail={
            "user_message": f"🔒 فشل التحقق: {str(e)}"
        })


async def get_user_profile(request: Request, user=Depends(verify_token)):
    """Get user profile. Auto-create if missing. Capture device metadata."""
    now = time.time()
    if user.id in _PROFILE_CACHE:
        cached_time, cached_profile = _PROFILE_CACHE[user.id]
        if now - cached_time < _PROFILE_CACHE_TTL:
            if isinstance(cached_profile, dict):
                if not cached_profile.get("id"):
                    cached_profile["id"] = user.id
                if "quota_remaining" in cached_profile:
                    return cached_profile
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
    
    # Save to memory cache for 60 seconds
    _PROFILE_CACHE[user.id] = (time.time(), profile_data)
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
    from app.services.subscription_service import get_user_subscription_info
    user_id = profile.get("id") if isinstance(profile, dict) else getattr(profile, "id", None)
    if not user_id:
        logger.error(f"🛡️ [CHECK_QUOTA] Profile missing id: {profile}")
        raise HTTPException(status_code=401, detail={
            "user_message": "🔒 لم يتم التعرف على هوية الحساب بشكل صحيح. يرجى إعادة تسجيل الدخول.",
            "type": "invalid_session"
        })
    logger.info(f"🛡️ [CHECK_QUOTA] Checking quota for user: {user_id}")
    
    sub_info = get_user_subscription_info(user_id)
    quota_remaining = sub_info.get("quota_remaining", 0)
    active_sub = sub_info.get("active_subscription")
    
    # Synchronize profile object in memory with true quota
    profile["quota_remaining"] = quota_remaining
    
    if quota_remaining > 0:
        return profile
        
    if not active_sub:
        raise HTTPException(status_code=403, detail={
            "user_message": "يلزم وجود اشتراك نشط لمعالجة المحاضرات. يرجى الاشتراك وتفعيل حسابك.",
            "type": "no_quota"
        })
        
    plan = active_sub.get("subscription_plans", {}) or {}
    raise HTTPException(status_code=403, detail={
        "user_message": f"📊 استخدمت كل رصيدك في باقة ({plan.get('name', 'الحالية')}). يرجى الترقية أو التجديد.",
        "type": "quota_exceeded"
    })

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

