from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import summarize
from app.api.routes import subscription
from app.api.routes import chat
from app.api.routes import transactions
from app.core.logger import logger
import traceback

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lammah.com",
        "https://www.lammah.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://192.168.1.3:8003" # Added based on .env.local
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== Routes ======
app.include_router(summarize.router, prefix="/api/v1/summarize", tags=["summarize"])
app.include_router(subscription.router, prefix="/api/v1/subscription", tags=["subscription"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
app.include_router(transactions.router, prefix="/api/v1/transactions", tags=["transactions"])

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
 
