from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.db.supabase_client import get_supabase, get_auth_client
from app.core.logger import logger
from datetime import datetime, timezone

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


async def get_user_profile(user=Depends(verify_token)):
    """Get user profile. Auto-create if missing. FAIL LOUD on errors."""
    supabase = get_supabase()
    
    # Step 1: Try to get existing profile
    try:
        result = supabase.table("profiles").select("*").eq("id", user.id).execute()
        profile_data = result.data[0] if result.data else None
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ فشل جلب الملف الشخصي: {error_msg}")
        
        # If table doesn't exist, tell the user clearly
        if "relation" in error_msg and "does not exist" in error_msg:
            raise HTTPException(status_code=500, detail={
                "user_message": "⚠️ جدول profiles غير موجود. يجب تنفيذ supabase_schema.sql في Supabase Dashboard أولاً.",
                "technical": error_msg
            })
        raise HTTPException(status_code=500, detail={
            "user_message": f"❌ خطأ في قاعدة البيانات: {error_msg}",
            "technical": error_msg
        })
    
    # Step 2: Auto-create profile if not exists
    if not profile_data:
        new_profile = {
            "id": user.id,
            "phone_number": getattr(user, 'phone', None),
            "phone_verified": bool(getattr(user, 'phone', None)),
            "quota_remaining": 1,
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
            
            if "violates row-level security" in error_msg:
                raise HTTPException(status_code=500, detail={
                    "user_message": "⚠️ سياسة RLS تمنع إنشاء الملف الشخصي. يجب إضافة INSERT policy لجدول profiles.",
                    "technical": error_msg
                })
            elif "relation" in error_msg and "does not exist" in error_msg:
                raise HTTPException(status_code=500, detail={
                    "user_message": "⚠️ جدول profiles غير موجود. نفذ supabase_schema.sql أولاً.",
                    "technical": error_msg
                })
            else:
                raise HTTPException(status_code=500, detail={
                    "user_message": f"❌ فشل إنشاء الحساب: {error_msg}",
                    "technical": error_msg
                })
    
    # Step 3: Check if blocked
    if profile_data.get("is_blocked"):
        raise HTTPException(status_code=403, detail={
            "user_message": "🚫 تم حظر حسابك. يرجى التواصل مع الدعم الفني."
        })
    
    return profile_data


async def check_quota(profile=Depends(get_user_profile)):
    """Verify user has remaining quota before processing."""
    quota = profile.get("quota_remaining", 0)
    
    if quota <= 0:
        supabase = get_supabase()
        try:
            active_sub = supabase.table("user_subscriptions") \
                .select("*, subscription_plans(*)") \
                .eq("user_id", profile["id"]) \
                .eq("status", "active") \
                .gte("expires_at", datetime.now(timezone.utc).isoformat()) \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
        except Exception as e:
            logger.error(f"❌ خطأ في فحص الاشتراك: {str(e)}")
            raise HTTPException(status_code=403, detail={
                "user_message": "📦 لا يوجد اشتراك نشط.",
                "type": "no_quota",
                "technical": str(e)
            })
        
        if not active_sub.data:
            raise HTTPException(status_code=403, detail={
                "user_message": "📦 لا يوجد لديك اشتراك نشط أو نفدت حصتك. يرجى شراء أو تجديد باقتك.",
                "type": "no_quota"
            })
        
        sub = active_sub.data[0]
        plan = sub.get("subscription_plans", {})
        raise HTTPException(status_code=403, detail={
            "user_message": f"📊 استخدمت كل محاضراتك ({plan.get('lecture_quota', 0)}). يرجى الترقية.",
            "type": "quota_exceeded"
        })
    
    return profile
