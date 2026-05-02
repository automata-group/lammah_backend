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
            from app.db.supabase_client import get_supabase, get_auth_client
            auth_client = get_auth_client()
            user_response = auth_client.auth.get_user(token)
            
            if not user_response or not user_response.user:
                return JSONResponse(status_code=401, content={"detail": {"user_message": "🔒 الجلسة منتهية."}})
            
            user_id = user_response.user.id
            supabase = get_supabase()
            profile_res = supabase.table("profiles").select("is_blocked, phone_verified").eq("id", user_id).execute()
            
            if not profile_res.data:
                return JSONResponse(status_code=403, content={"detail": {"user_message": "🚫 لم يتم العثور على ملف شخصي."}})
            
            profile = profile_res.data[0]
            if profile.get("is_blocked"):
                return JSONResponse(status_code=403, content={"detail": {"user_message": "🚫 حسابك محظور."}})
                
        except Exception as e:

            logger.error(f"Early security check failed: {e}")
            # We let it pass to the normal dependency handler if there's a technical error here
            # to avoid false positives, but usually this will catch invalid users.
            pass

    return await call_next(request)


# ====== Routes ======
app.include_router(summarize.router, prefix="/api/v1/summarize", tags=["summarize"])
app.include_router(subscription.router, prefix="/api/v1/subscription", tags=["subscription"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["transactions"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])

# ====== Global Error Handler ======
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_msg = f"Unhandled Exception at {request.url.path}: {str(exc)}"
    logger.error(error_msg, exc_info=True)
    
    error_str = str(exc)
    if "429" in error_str or "rate_limit" in error_str.lower():
        return JSONResponse(status_code=429, content={
            "detail": {"user_message": "⏳ تم تجاوز الحد المسموح. يرجى الانتظار ثم المحاولة مجدداً."}
        })
    
    return JSONResponse(status_code=500, content={
        "detail": {"user_message": "❌ حدث خطأ داخلي. يرجى المحاولة مرة أخرى أو التواصل مع الدعم."}
    })

@app.get("/")
def root():
    return {"message": "Welcome to Lammah API - لمّاح", "version": settings.VERSION}

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "queue": "active"}

# 4. CORSMiddleware (Outer-most layer)
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin).strip("/") for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
 
