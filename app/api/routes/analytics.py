from fastapi import APIRouter, Request, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List, Optional
from app.core.auth_guard import get_user_profile
from app.services import analytics_service
from app.core.logger import logger

router = APIRouter()

class TelemetryPayload(BaseModel):
    session_id: str
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    path: str = "/"
    device_type: str = "هاتف محمول"
    os_browser: str = "متصفح الويب"
    screen_size: Optional[str] = None
    referrer: Optional[str] = "مباشر"
    scroll_depth: int = 0
    duration: int = 0
    events: List[Dict[str, Any]] = []

async def verify_admin(profile=Depends(get_user_profile)):
    user_role = profile.get("role", "user")
    if user_role != "admin":
        raise HTTPException(status_code=403, detail="غير مصرح لك بالوصول لتحليلات المنصة.")
    return profile

@router.post("/collect")
async def collect_telemetry(payload: TelemetryPayload, request: Request, background_tasks: BackgroundTasks):
    """
    Ultra-lightweight telemetry ingestion endpoint.
    Returns in < 2ms, handles geo-resolution and DB persistence in background.
    """
    # Extract client IP
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        client_ip = request.client.host if request.client else "unknown"
    
    # Also check Cloudflare country header if deployed
    cf_country = request.headers.get("cf-ipcountry")
    
    async def process_telemetry():
        try:
            geo = await analytics_service.resolve_ip_geo(client_ip)
            if cf_country and cf_country != "XX":
                # Translate 2-letter ISO code if present
                country_mapped = analytics_service.translate_geo(cf_country)
                if country_mapped != cf_country:
                    geo["country"] = country_mapped
            analytics_service.record_telemetry(payload.dict(), client_ip, geo)
        except Exception as e:
            logger.error(f"Error processing telemetry: {e}")
            
    background_tasks.add_task(process_telemetry)
    return {"ok": True}

@router.get("/admin/overview")
async def get_analytics_overview(admin=Depends(verify_admin)):
    """Fetches real-time Arabic intelligence and telemetry overview for the admin."""
    return analytics_service.get_analytics_overview()

@router.get("/admin/session/{session_id}")
async def get_session_journey(session_id: str, admin=Depends(verify_admin)):
    """Fetches full chronological timeline and AI journey analysis for a specific student session."""
    data = analytics_service.get_session_full_journey(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="الجلسة غير موجودة أو انتهت صلاحيتها.")
    return data

@router.post("/admin/clear")
async def clear_analytics(admin=Depends(verify_admin)):
    """Clears all session recordings and events."""
    analytics_service.clear_analytics_db()
    return {"ok": True, "message": "تم تفريغ سجلات التتبع بنجاح."}
