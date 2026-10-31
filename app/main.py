from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import summarize
from app.api.routes import subscription
from app.api.routes import chat
from app.api.routes import transactions, admin
from app.core.logger import logger
import traceback

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

# 1. Early Security Middleware (Before CORS)
@app.middleware("http")
async def early_security_middleware(request: Request, call_next):
    """
    Intersects heavy requests BEFORE multipart parsing.
    If the user is blocked or not verified, reject immediately.
    """
    # Only check heavy processing routes
    if request.method == "OPTIONS":
        return await call_next(request)

    if request.url.path == "/api/v1/summarize/process-lecture":
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401, 
                content={"detail": {"user_message": "🔒 غير مصرح بالوصول."}}
            )
        
        token = auth_header.split(" ")[1]
        try:
            from app.core.auth_guard import _TOKEN_CACHE, _PROFILE_CACHE
            import hashlib, time

            now = time.time()
            user_id = None
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            cached_token = _TOKEN_CACHE.get(token_hash)
            
            if cached_token and now < cached_token[0]:
                user_id = cached_token[1].id
            else:
                from app.db.supabase_client import get_auth_client
                auth_client = get_auth_client()
                user_response = auth_client.auth.get_user(token)
                
                if not user_response or not user_response.user:
                    return JSONResponse(status_code=401, content={"detail": {"user_message": "🔒 الجلسة منتهية."}})
                
                user = user_response.user
                user_id = user.id
                _TOKEN_CACHE[token_hash] = (now + 300.0, user)
            
            # Check profile cache
            cached_profile = _PROFILE_CACHE.get(user_id)
            if cached_profile and (now - cached_profile[0] < 60.0):
                if cached_profile[1].get("is_blocked"):
                    return JSONResponse(status_code=403, content={"detail": {"user_message": "🚫 حسابك محظور."}})
            else:
                from app.db.supabase_client import get_supabase
                supabase = get_supabase()
                profile_res = supabase.table("profiles").select("*").eq("id", user_id).execute()
                
                if not profile_res.data:
                    return JSONResponse(status_code=403, content={"detail": {"user_message": "🚫 لم يتم العثور على ملف شخصي."}})
                
                profile = profile_res.data[0]
                if not profile.get("id"):
                    profile["id"] = user_id
                _PROFILE_CACHE[user_id] = (now, profile)
                if profile.get("is_blocked"):
                    return JSONResponse(status_code=403, content={"detail": {"user_message": "🚫 حسابك محظور."}})
                    
        except Exception as e:
            logger.error(f"Early security check failed: {e}")
            pass

    return await call_next(request)


from app.api.routes import notebook, marketplace, tutoring, analytics, kiosk

# ====== Routes ======
app.include_router(summarize.router, prefix="/api/v1/summarize", tags=["summarize"])
app.include_router(subscription.router, prefix="/api/v1/subscription", tags=["subscription"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["transactions"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(notebook.router, prefix="/api/v1/notebook", tags=["notebook"])
app.include_router(marketplace.router, prefix="/api/v1/marketplace", tags=["marketplace"])
app.include_router(tutoring.router, prefix="/api/v1/tutoring", tags=["tutoring"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])
app.include_router(kiosk.router, prefix="/api/v1/kiosk", tags=["kiosk"])

# ====== Global Error Handler ======
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = f"Unhandled Exception at {request.url.path}: {str(exc)}"
    logger.error(error_msg, exc_info=True)
    
    error_str = str(exc)
    origin = request.headers.get("origin")
    headers = {}
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"

    if "429" in error_str or "rate_limit" in error_str.lower():
        return JSONResponse(status_code=429, content={
            "detail": {"user_message": "⏳ تم تجاوز الحد المسموح. يرجى الانتظار ثم المحاولة مجدداً."}
        }, headers=headers)
    
    return JSONResponse(status_code=500, content={
        "detail": {"user_message": "❌ حدث خطأ داخلي. يرجى المحاولة مرة أخرى أو التواصل مع الدعم."}
    }, headers=headers)

@app.get("/")
async def root():
    return {"message": "Welcome to Lammah API - لمّاح", "version": settings.VERSION}

@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "queue": "active"}

# 4. CORSMiddleware (Outer-most layer)
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).strip("/") for origin in settings.BACKEND_CORS_ORIGINS],
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
 
