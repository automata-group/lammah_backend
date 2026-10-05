import uuid
from typing import Optional, List, Dict, Any, cast
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from pydantic import BaseModel, Field
from postgrest import CountMethod
from app.core.auth_guard import get_user_profile, verify_admin
from app.db.supabase_client import get_supabase
from app.core.logger import logger

router = APIRouter()

# ====================================================================
# Type Helpers for Supabase Responses
# ====================================================================

def _get_records(res: Any) -> List[Dict[str, Any]]:
    """Helper to safely extract list of dicts from Supabase response."""
    data = getattr(res, "data", res)
    if isinstance(data, list):
        return [cast(Dict[str, Any], r) for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [cast(Dict[str, Any], data)]
    return []

def _get_first_record(res: Any) -> Optional[Dict[str, Any]]:
    """Helper to safely extract first record dict from Supabase response."""
    data = getattr(res, "data", res)
    if isinstance(data, dict):
        return cast(Dict[str, Any], data)
    records = _get_records(res)
    return records[0] if records else None

# ====================================================================
# Pydantic Request Models
# ====================================================================

class TutorApplicationRequest(BaseModel):
    full_name: Optional[str] = Field(None, description="الاسم الكامل للمعلم (ثلاثي أو رباعي)")
    phone_number: Optional[str] = Field(None, description="رقم الهاتف للتواصل والواتساب")
    headline: str = Field(..., description="العنوان المهني التعريفي (مثلاً: طالب هندسة حاسوب - شرح خوارزميات ورياضيات)")
    bio: Optional[str] = Field("", description="نبذة عن المعلم وخبرته وطريقة شرحه")
    university: str = Field(..., description="اسم الجامعة")
    major: str = Field(..., description="التخصص الدراسي")
    academic_level: str = Field("student", description="المستوى: student / graduate / master / phd")
    study_year: Optional[str] = Field("", description="السنة الدراسية أو سنة التخرج")
    gpa: Optional[str] = Field("", description="المعدل التراكمي في الجامعة")
    gpa_scale: Optional[str] = Field("4.0", description="نظام المعدل: 4.0 أو 100")
    teaching_subjects: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="قائمة المواد المراد تدريسها مع علامة كل مادة")
    verification_doc_url: str = Field(..., description="رابط كشف العلامات الرسمي")
    second_doc_url: Optional[str] = Field("", description="رابط إثبات طالب أو شهادة التخرج")
    intro_video_url: Optional[str] = Field("", description="رابط فيديو تعريفي أو نموذج شرح")
    payout_details: Optional[Dict[str, Any]] = Field(default_factory=dict, description="بيانات استلام الأرباح (ريفلكت / البنك)")

class CreateServiceRequest(BaseModel):
    title: str = Field(..., description="عنوان الحصة أو المراجعة أو الكورس")
    subject_name: str = Field(..., description="اسم المادة / المساق")
    service_type: str = Field("one_on_one", description="one_on_one / group_session / recorded_course")
    price: float = Field(..., gt=0, description="سعر الحصة الإجمالي بالشيكل")
    duration_minutes: Optional[int] = Field(60, description="مدة الجلسة بالدقائق")
    description: Optional[str] = Field("", description="وصف المحتوى وما سيتم تناوله")

class CreateBookingRequest(BaseModel):
    service_id: str
    session_date: str # ISO string
    meeting_link: Optional[str] = ""
    student_phone: Optional[str] = ""
    student_name: Optional[str] = ""
    notes_from_student: Optional[str] = ""

class ReviewBookingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = ""

class AcceptBookingRequest(BaseModel):
    session_date: Optional[str] = Field(None, description="الموعد الدقيق المعتمد من المعلم")
    meeting_link: Optional[str] = Field(None, description="رابط Google Meet المعتمد")
    tutor_notes: Optional[str] = Field("", description="توجيهات أو ملاحظات المعلم للطالب")

class RejectBookingRequest(BaseModel):
    reason: Optional[str] = Field("المعلم غير متاح في هذا التوقيت.", description="سبب الاعتذار عن الجلسة")

class ReportNoShowRequest(BaseModel):
    notes: Optional[str] = Field("لم يحضر الطالب بعد مرور 15 دقيقة من الموعد.", description="ملاحظات توثيق عدم الحضور")

class UpdateMeetingLinkRequest(BaseModel):
    meeting_link: str = Field(..., description="رابط اجتماع Google Meet الجديد")

class PayoutRequest(BaseModel):
    amount: float = Field(..., gt=0)
    payout_method: Optional[str] = "reflect"
    account_info: Dict[str, Any] = Field(default_factory=dict)

class DisputeBookingRequest(BaseModel):
    reason: str = Field(..., min_length=3, description="سبب النزاع أو المشكلة (مثلاً: المعلم لم يحضر، مشكلة تقنية)")
    details: Optional[str] = Field("", description="تفاصيل إضافية للبلاغ")

# ====================================================================
# Helper Functions
# ====================================================================

def create_notification(
    supabase,
    user_id: str,
    title: str,
    message: str,
    notif_type: str = "system",
    link: str = ""
):
    """Helper to safely insert in-app notification without crashing caller."""
    try:
        supabase.table("user_notifications").insert({
            "user_id": user_id,
            "title": title,
            "message": message,
            "type": notif_type,
            "link": link,
            "is_read": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
    except Exception as e:
        logger.warning(f"Could not create user notification: {e}")

def ensure_wallet_exists(supabase, tutor_id: str):
    """Ensures a tutor_wallets row exists for the tutor."""
    try:
        res = supabase.table("tutor_wallets").select("*").eq("tutor_id", tutor_id).execute()
        if not _get_records(res):
            supabase.table("tutor_wallets").insert({
                "tutor_id": tutor_id,
                "available_balance": 0.00,
                "pending_balance": 0.00,
                "total_earned": 0.00,
                "total_withdrawn": 0.00
            }).execute()
    except Exception as e:
        logger.warning(f"Failed to auto-create wallet (table might need migration): {e}")

def settle_booking_funds(supabase, booking: Dict[str, Any], release_reason: str = "تأكيد إتمام الحصة") -> bool:
    """
    Safely releases tutor share from pending_balance to available_balance.
    Guarantees funds cannot be settled more than once and session was paid.
    """
    if booking.get("is_settled"):
        return False

    price = float(booking.get("total_price", 0.0))
    is_paid = booking.get("is_paid", False) or price == 0.0
    if not is_paid:
        logger.warning(f"🚫 [ESCROW BLOCKED] Cannot settle unpaid booking {booking.get('id')}")
        return False

    tutor_id = str(booking.get("tutor_id", ""))
    tutor_share = float(booking.get("tutor_share", 0.0))
    booking_id = str(booking.get("id", ""))

    if not tutor_id or tutor_share <= 0:
        return False

    # 1. Update booking state to completed and is_settled = True
    try:
        supabase.table("tutoring_bookings").update({
            "status": "completed",
            "is_settled": True
        }).eq("id", booking_id).execute()
    except Exception as e:
        logger.error(f"Failed to update booking status on settle: {e}")
        return False

    # 2. Shift funds in tutor wallet from pending to available
    ensure_wallet_exists(supabase, tutor_id)
    try:
        w_res = supabase.table("tutor_wallets").select("*").eq("tutor_id", tutor_id).execute()
        w = _get_first_record(w_res)
        if w:
            avail = float(w.get("available_balance", 0.0))
            pending = float(w.get("pending_balance", 0.0))
            earned = float(w.get("total_earned", 0.0))

            new_avail = round(avail + tutor_share, 2)
            new_pending = max(0.0, round(pending - tutor_share, 2))
            new_earned = round(earned + tutor_share, 2)

            supabase.table("tutor_wallets").update({
                "available_balance": new_avail,
                "pending_balance": new_pending,
                "total_earned": new_earned,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("tutor_id", tutor_id).execute()
    except Exception as e:
        logger.error(f"Failed to update tutor wallet during settle: {e}")
        return False

    # 3. Send celebratory in-app notification to tutor
    create_notification(
        supabase=supabase,
        user_id=tutor_id,
        title="تم تحرير أرباح الحصة بنجاح! 💰",
        message=f"تم تحويل حصتك ({tutor_share} ₪) إلى رصيدك المتاح للسحب في محفظتك. ({release_reason})",
        notif_type="funds_released",
        link="/tutor/dashboard"
    )
    return True

def check_and_auto_settle_bookings(supabase, tutor_id: Optional[str] = None):
    """
    Auto-settles confirmed bookings where session_date is older than 24 hours,
    provided no dispute was opened and funds are not yet settled.
    Protects tutors if the student forgets to click confirm.
    """
    try:
        query = supabase.table("tutoring_bookings").select("*").eq("is_settled", False).eq("status", "confirmed")
        if tutor_id:
            query = query.eq("tutor_id", tutor_id)
        res = query.execute()
        records = _get_records(res)
        now_dt = datetime.now(timezone.utc)

        for b in records:
            s_date_str = b.get("session_date")
            if not s_date_str:
                continue
            try:
                s_dt = datetime.fromisoformat(str(s_date_str).replace("Z", "+00:00"))
                # Check if older than 24 hours (86400 seconds)
                if (now_dt - s_dt).total_seconds() >= 86400:
                    settle_booking_funds(supabase, b, "تسوية تلقائية بعد مرور 24 ساعة دون أي نزاع أو اعتراض")
            except Exception as e:
                logger.warning(f"Error checking auto settle for booking {b.get('id')}: {e}")
    except Exception as e:
        logger.warning(f"Auto-settle routine error: {e}")

def _ensure_valid_meeting_link(booking: dict, supabase) -> str:
    """
    Guarantees a permanent, non-fragmenting meeting room URL.
    Replaces any 'meet.google.com/new' (which dynamically spawns separate rooms per visitor)
    with a unified shared room dedicated to this exact booking ID.
    """
    link = (booking.get("meeting_link") or "").strip()
    booking_id = str(booking.get("id", ""))
    clean_id = booking_id.replace("-", "")[:12]
    fallback_room = f"https://meet.jit.si/Lammah-Room-{clean_id}"

    if not link or link.endswith("/new"):
        try:
            supabase.table("tutoring_bookings").update({"meeting_link": fallback_room}).eq("id", booking_id).execute()
        except Exception as e:
            logger.warning(f"Failed to auto-heal meeting link for booking {booking_id}: {e}")
        return fallback_room
    return link

# ====================================================================
# 1. التقديم للتدريس والملف الشخصي (Onboarding & Status)
# ====================================================================

@router.post("/apply")
async def apply_to_teach(
    request: TutorApplicationRequest,
    profile: dict = Depends(get_user_profile)
):
    """Allows a student or graduate to apply as a tutor."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        # Update profiles table with full_name and phone_number if provided
        profile_updates = {}
        if request.full_name and request.full_name.strip():
            profile_updates["full_name"] = request.full_name.strip()
        if request.phone_number and request.phone_number.strip():
            profile_updates["phone_number"] = request.phone_number.strip()
        if profile_updates:
            try:
                supabase.table("profiles").update(profile_updates).eq("id", user_id).execute()
            except Exception as e:
                logger.warning(f"Failed to update profile details: {e}")

        payout_details_payload = dict(request.payout_details or {})
        if request.full_name:
            payout_details_payload["full_name"] = request.full_name.strip()
        if request.phone_number:
            payout_details_payload["phone_number"] = request.phone_number.strip()

        payout_details_payload["academic_info"] = {
            "full_name": request.full_name.strip() if request.full_name else "",
            "phone_number": request.phone_number.strip() if request.phone_number else "",
            "study_year": request.study_year or "",
            "gpa": request.gpa or "",
            "gpa_scale": request.gpa_scale or "4.0",
            "teaching_subjects": request.teaching_subjects or [],
            "second_doc_url": request.second_doc_url or ""
        }

        tutor_data = {
            "id": user_id,
            "headline": request.headline,
            "bio": request.bio or "",
            "university": request.university,
            "major": request.major,
            "academic_level": request.academic_level,
            "verification_doc_url": request.verification_doc_url,
            "intro_video_url": request.intro_video_url or "",
            "payout_details": payout_details_payload,
            "status": "pending",
            "rating_avg": 5.00,
            "rating_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        # Check if already applied
        existing = supabase.table("tutor_profiles").select("*").eq("id", user_id).execute()
        existing_records = _get_records(existing)
        if existing_records:
            first_existing = existing_records[0]
            current_status = str(first_existing.get("status", "pending"))
            # Overwrite for pending or rejected to update data
            tutor_data["rejection_reason"] = None
            result = supabase.table("tutor_profiles").update(tutor_data).eq("id", user_id).execute()
            
            if current_status == "approved":
                return {
                    "status": "approved",
                    "message": "حسابك معتمد كمعلم بالفعل! يمكنك البدء بإضافة خدماتك.",
                    "tutor": first_existing
                }
            
            if profile.get("role") != "admin":
                try:
                    supabase.table("profiles").update({"role": "tutor_applicant"}).eq("id", user_id).execute()
                except Exception:
                    pass
            ensure_wallet_exists(supabase, user_id)
            return {
                "status": "success",
                "message": "تم تحديث طلبك بنجاح! سيتم مراجعة أوراقك الثبوتية وتفعيل حسابك خلال 24 ساعة.",
                "tutor": _get_first_record(result) or tutor_data
            }

        # Insert tutor profile for first time
        result = supabase.table("tutor_profiles").insert(tutor_data).execute()

        # Update user profile role to tutor_applicant if not already admin
        if profile.get("role") != "admin":
            try:
                supabase.table("profiles").update({"role": "tutor_applicant"}).eq("id", user_id).execute()
            except Exception:
                pass

        # Create wallet
        ensure_wallet_exists(supabase, user_id)

        return {
            "status": "success",
            "message": "تم تقديم طلبك بنجاح! سيتم مراجعة أوراقك الثبوتية وتفعيل حسابك خلال 24 ساعة.",
            "tutor": _get_first_record(result) or tutor_data
        }
    except Exception as e:
        logger.error(f"Failed to submit tutor application: {e}")
        raise HTTPException(status_code=500, detail={"user_message": f"فشل في إرسال طلب التقديم: {str(e)}"})

@router.get("/my-status")
async def get_my_tutor_status(profile: dict = Depends(get_user_profile)):
    """Returns tutor status for the logged-in user."""
    supabase = get_supabase()
    try:
        res = supabase.table("tutor_profiles").select("*").eq("id", profile["id"]).execute()
        tutor = _get_first_record(res)
        if not tutor:
            return {"is_tutor": False, "status": "none"}
        
        status = str(tutor.get("status", "none"))
        return {
            "is_tutor": status == "approved",
            "status": status,
            "tutor": tutor
        }
    except Exception as e:
        logger.error(f"Failed to get tutor status: {e}")
        return {"is_tutor": False, "status": "none"}

# ====================================================================
# 2. دليل وتصفح المعلمين للطلاب (Public Tutor Directory)
# ====================================================================

@router.get("/tutors")
async def get_all_tutors(
    search: Optional[str] = None,
    university: Optional[str] = None,
    major: Optional[str] = None
):
    """Public listing of approved tutors."""
    supabase = get_supabase()
    try:
        query = supabase.table("tutor_profiles")\
            .select("*")\
            .eq("status", "approved")\
            .order("rating_avg", desc=True)

        if university:
            query = query.ilike("university", f"%{university}%")
        if major:
            query = query.ilike("major", f"%{major}%")
            
        result = query.execute()
        tutors: List[Dict[str, Any]] = _get_records(result)

        if tutors:
            t_ids = [str(t["id"]) for t in tutors if "id" in t]
            if t_ids:
                profs_res = supabase.table("profiles").select("id, full_name, phone_number").in_("id", t_ids).execute()
                pmap: Dict[str, Dict[str, Any]] = {str(p["id"]): dict(p) for p in _get_records(profs_res) if "id" in p}
                for t in tutors:
                    tid = str(t.get("id", ""))
                    prof: Dict[str, Any] = dict(pmap.get(tid, {}))
                    if not prof.get("full_name"):
                        p_details = t.get("payout_details")
                        if isinstance(p_details, dict):
                            acad_info = p_details.get("academic_info")
                            acad_name = acad_info.get("full_name") if isinstance(acad_info, dict) else ""
                            prof["full_name"] = p_details.get("full_name") or acad_name or ""
                    if not prof.get("phone_number"):
                        p_details = t.get("payout_details")
                        if isinstance(p_details, dict):
                            prof["phone_number"] = p_details.get("phone_number") or p_details.get("identifier") or ""
                    t["profiles"] = prof

        if search:
            s = search.lower()
            filtered_tutors: List[Dict[str, Any]] = []
            for t in tutors:
                headline = str(t.get("headline", "")).lower()
                maj = str(t.get("major", "")).lower()
                prof_data = t.get("profiles")
                full_name = str(prof_data.get("full_name", "")).lower() if isinstance(prof_data, dict) else ""
                if s in headline or s in maj or s in full_name:
                    filtered_tutors.append(t)
            tutors = filtered_tutors

        return {"status": "success", "tutors": tutors}
    except Exception as e:
        logger.warning(f"Failed to list tutors: {e}")
        return {"status": "success", "tutors": []}

@router.get("/tutors/{tutor_id}")
async def get_tutor_details(tutor_id: str):
    """Fetches public details of a single tutor including active services and reviews."""
    supabase = get_supabase()
    try:
        tutor_res = supabase.table("tutor_profiles")\
            .select("*")\
            .eq("id", tutor_id)\
            .execute()
            
        tutor = _get_first_record(tutor_res)
        if not tutor:
            raise HTTPException(status_code=404, detail="المعلم غير موجود.")

        # Fetch profile
        prof_res = supabase.table("profiles").select("id, full_name, phone_number").eq("id", tutor_id).execute()
        prof_first = _get_first_record(prof_res)
        prof: Dict[str, Any] = dict(prof_first) if prof_first else {}
        if not prof.get("full_name"):
            p_details = tutor.get("payout_details")
            if isinstance(p_details, dict):
                acad_info = p_details.get("academic_info")
                acad_name = acad_info.get("full_name") if isinstance(acad_info, dict) else ""
                prof["full_name"] = p_details.get("full_name") or acad_name or ""
        if not prof.get("phone_number"):
            p_details = tutor.get("payout_details")
            if isinstance(p_details, dict):
                prof["phone_number"] = p_details.get("phone_number") or p_details.get("identifier") or ""
        tutor["profiles"] = prof

        # Fetch active services
        services_res = supabase.table("tutoring_services")\
            .select("*")\
            .eq("tutor_id", tutor_id)\
            .eq("is_active", True)\
            .execute()

        # Fetch reviews
        reviews_res = supabase.table("tutor_reviews")\
            .select("*")\
            .eq("tutor_id", tutor_id)\
            .order("created_at", desc=True)\
            .limit(20)\
            .execute()
            
        reviews: List[Dict[str, Any]] = _get_records(reviews_res)
        if reviews:
            st_ids = [str(r["student_id"]) for r in reviews if r.get("student_id")]
            if st_ids:
                st_profs = supabase.table("profiles").select("id, full_name").in_("id", st_ids).execute()
                smap: Dict[str, Dict[str, Any]] = {str(p["id"]): dict(p) for p in _get_records(st_profs) if "id" in p}
                for r in reviews:
                    sid = str(r.get("student_id", ""))
                    r["profiles"] = smap.get(sid, {})

        return {
            "status": "success",
            "tutor": tutor,
            "services": _get_records(services_res),
            "reviews": reviews
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch tutor details: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب بيانات المعلم.")

# ====================================================================
# 3. لوحة تحكم المعلم (Tutor Dashboard & Services)
# ====================================================================

@router.get("/tutor/dashboard/summary")
async def get_tutor_dashboard_summary(profile: dict = Depends(get_user_profile)):
    """Returns tutor metrics, balance, upcoming sessions and active services."""
    supabase = get_supabase()
    tutor_id = profile["id"]

    try:
        check_and_auto_settle_bookings(supabase, tutor_id)
        ensure_wallet_exists(supabase, tutor_id)
        
        # Wallet
        wallet_res = supabase.table("tutor_wallets").select("*").eq("tutor_id", tutor_id).execute()
        wallet = _get_first_record(wallet_res) or {
            "available_balance": 0.0,
            "pending_balance": 0.0,
            "total_earned": 0.0,
            "total_withdrawn": 0.0
        }

        # Services count
        services_res = supabase.table("tutoring_services").select("id", count=CountMethod.exact).eq("tutor_id", tutor_id).execute()
        services_count = services_res.count if services_res.count is not None else len(_get_records(services_res))

        # Bookings counts
        bookings_res = supabase.table("tutoring_bookings").select("id, status, total_price, tutor_share").eq("tutor_id", tutor_id).execute()
        all_bookings: List[Dict[str, Any]] = _get_records(bookings_res)
        
        completed_count = len([b for b in all_bookings if b.get("status") == "completed"])
        confirmed_count = len([b for b in all_bookings if b.get("status") == "confirmed"])

        return {
            "status": "success",
            "wallet": wallet,
            "metrics": {
                "services_count": services_count,
                "completed_sessions": completed_count,
                "upcoming_sessions": confirmed_count,
                "split_ratio": "50% للمعلم / 50% للمنصة"
            }
        }
    except Exception as e:
        logger.error(f"Failed to load tutor dashboard summary: {e}")
        raise HTTPException(status_code=500, detail="فشل في تحميل بيانات لوحة المعلم.")

@router.post("/tutor/services")
async def create_tutoring_service(
    request: CreateServiceRequest,
    profile: dict = Depends(get_user_profile)
):
    """Creates a new tutoring service/course with automatic 50/50 split calculation."""
    supabase = get_supabase()
    tutor_id = profile["id"]

    try:
        # Check tutor approval
        tutor_check = supabase.table("tutor_profiles").select("status").eq("id", tutor_id).execute()
        tutor_first = _get_first_record(tutor_check)
        if not tutor_first or tutor_first.get("status") != "approved":
            # Allow admins to create for testing, otherwise block
            if profile.get("role") != "admin":
                raise HTTPException(status_code=403, detail="يجب أن يكون حسابك معتمداً كمعلم لإضافة خدمات.")

        price = float(request.price)
        tutor_split = 50.00 # 50% split

        service_data = {
            "id": str(uuid.uuid4()),
            "tutor_id": tutor_id,
            "title": request.title,
            "subject_name": request.subject_name,
            "service_type": request.service_type,
            "price": price,
            "tutor_split_percent": tutor_split,
            "duration_minutes": request.duration_minutes or 60,
            "description": request.description or "",
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        result = supabase.table("tutoring_services").insert(service_data).execute()
        
        tutor_share = round(price * 0.50, 2)
        platform_share = round(price * 0.50, 2)

        return {
            "status": "success",
            "message": f"تمت إضافة الخدمة بنجاح! (سعر الطالب: {price} شيكل | حصتك: {tutor_share} شيكل | حصة المنصة: {platform_share} شيكل)",
            "service": _get_first_record(result) or service_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create tutoring service: {e}")
        raise HTTPException(status_code=500, detail="فشل في إضافة الخدمة.")

@router.get("/tutor/services")
async def get_my_services(profile: dict = Depends(get_user_profile)):
    """Returns services created by the logged-in tutor."""
    supabase = get_supabase()
    try:
        res = supabase.table("tutoring_services")\
            .select("*")\
            .eq("tutor_id", profile["id"])\
            .order("created_at", desc=True)\
            .execute()
            
        return {"status": "success", "services": _get_records(res)}
    except Exception as e:
        logger.error(f"Failed to fetch tutor services: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب خدماتك.")

@router.delete("/tutor/services/{service_id}")
async def delete_tutoring_service(service_id: str, profile: dict = Depends(get_user_profile)):
    """Deactivates/deletes a tutoring service."""
    supabase = get_supabase()
    try:
        res = supabase.table("tutoring_services").select("tutor_id").eq("id", service_id).execute()
        first_service = _get_first_record(res)
        if not first_service or first_service.get("tutor_id") != profile["id"]:
            if profile.get("role") != "admin":
                raise HTTPException(status_code=403, detail="ليس لديك صلاحية لحذف هذه الخدمة.")

        supabase.table("tutoring_services").delete().eq("id", service_id).execute()
        return {"status": "success", "message": "تم حذف الخدمة بنجاح."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete service: {e}")
        raise HTTPException(status_code=500, detail="فشل في حذف الخدمة.")

@router.get("/tutor/bookings")
async def get_tutor_bookings(profile: dict = Depends(get_user_profile)):
    """Fetches all student bookings for the logged-in tutor."""
    supabase = get_supabase()
    try:
        check_and_auto_settle_bookings(supabase, profile["id"])
        res = supabase.table("tutoring_bookings")\
            .select("*, tutoring_services(title, subject_name, duration_minutes), profiles:student_id(full_name, phone_number)")\
            .eq("tutor_id", profile["id"])\
            .order("session_date", desc=True)\
            .execute()
            
        bookings = _get_records(res)
        for b in bookings:
            b["meeting_link"] = _ensure_valid_meeting_link(b, supabase)
            
        return {"status": "success", "bookings": bookings}
    except Exception as e:
        logger.error(f"Failed to fetch tutor bookings: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب الحجوزات.")

@router.post("/tutor/wallet/payout-request")
async def request_tutor_payout(
    request: PayoutRequest,
    profile: dict = Depends(get_user_profile)
):
    """Allows a tutor to request payout of available balance."""
    supabase = get_supabase()
    tutor_id = profile["id"]

    try:
        ensure_wallet_exists(supabase, tutor_id)
        
        wallet_res = supabase.table("tutor_wallets").select("*").eq("tutor_id", tutor_id).execute()
        wallet = _get_first_record(wallet_res)
        if not wallet:
            raise HTTPException(status_code=400, detail="لا توجد محفظة مفعلة.")
            
        avail = float(wallet.get("available_balance", 0.0))
        req_amount = float(request.amount)

        if req_amount <= 0:
            raise HTTPException(status_code=400, detail="يرجى إدخال مبلغ صحيح.")
        if req_amount > avail:
            raise HTTPException(status_code=400, detail=f"الرصيد المتاح للسحب ({avail} شيكل) أقل من المبلغ المطلوب ({req_amount} شيكل).")
        if req_amount < 50.0:
            raise HTTPException(status_code=400, detail="الحد الأدنى لطلب سحب الأرباح هو 50 شيكل.")

        # Deduct from available balance immediately to prevent double spending
        new_avail = round(avail - req_amount, 2)
        supabase.table("tutor_wallets").update({
            "available_balance": new_avail,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("tutor_id", tutor_id).execute()

        # Insert payout request
        payout_data = {
            "id": str(uuid.uuid4()),
            "tutor_id": tutor_id,
            "amount": req_amount,
            "payout_method": request.payout_method or "reflect",
            "account_info": request.account_info or {},
            "status": "requested",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        result = supabase.table("tutor_payouts").insert(payout_data).execute()

        return {
            "status": "success",
            "message": f"تم إرسال طلب سحب مبلغ {req_amount} شيكل بنجاح! سيقوم فريق الإدارة بتحويل المبلغ وإرسال إشعار الدفع.",
            "payout": _get_first_record(result) or payout_data,
            "new_balance": new_avail
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process payout request: {e}")
        raise HTTPException(status_code=500, detail="فشل في تقديم طلب السحب.")

# ====================================================================
# 4. الحجوزات والطلاب (Student Bookings & Reviews)
# ====================================================================

@router.post("/bookings/create")
async def create_booking(
    request: CreateBookingRequest,
    profile: dict = Depends(get_user_profile)
):
    """Student sends a booking request with proposed date and notes (Requires tutor approval)."""
    supabase = get_supabase()
    student_id = profile["id"]

    try:
        service_res = supabase.table("tutoring_services").select("*").eq("id", request.service_id).execute()
        service = _get_first_record(service_res)
        if not service:
            raise HTTPException(status_code=404, detail="الخدمة غير موجودة.")
            
        price = float(service.get("price", 0.0))
        tutor_id = str(service.get("tutor_id", ""))
        subject_name = str(service.get("subject_name", "المساق"))

        # Prevent tutor booking own service
        if tutor_id == student_id:
            raise HTTPException(status_code=400, detail="لا يمكنك حجز خدمة قمت بإنشائها بنفسك.")

        tutor_share = round(price * 0.50, 2)
        platform_share = round(price * 0.50, 2)
        student_name = request.student_name or profile.get("full_name") or "طالب لمّاح"

        # Try pending_approval, fallback to pending_payment if constraint not yet migrated
        booking_data = {
            "id": str(uuid.uuid4()),
            "service_id": request.service_id,
            "student_id": student_id,
            "tutor_id": tutor_id,
            "session_date": request.session_date,
            "meeting_link": request.meeting_link or "",
            "status": "pending_approval",
            "total_price": price,
            "tutor_share": tutor_share,
            "platform_share": platform_share,
            "is_settled": False,
            "student_phone": request.student_phone or profile.get("phone_number") or "",
            "student_name": student_name,
            "notes_from_student": request.notes_from_student or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            result = supabase.table("tutoring_bookings").insert(booking_data).execute()
        except Exception:
            # Fallback to pending_payment if check constraint has not been refreshed
            booking_data["status"] = "pending_payment"
            result = supabase.table("tutoring_bookings").insert(booking_data).execute()

        # Send in-app notification to tutor
        create_notification(
            supabase=supabase,
            user_id=tutor_id,
            title="طلب حجز جلسة جديد 🎓",
            message=f"تلقيت طلب حجز جديد من الطالب {student_name} لمساق ({subject_name}). يرجى مراجعة الطلب وتأكيد الموعد.",
            notif_type="booking_request",
            link="/tutor/dashboard"
        )

        # Send in-app notification to student
        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="تم إرسال طلب الحجز بنجاح ⏳",
            message=f"تم إرسال طلبك لحجز حصة ({subject_name}) للمعلم. سنشعرك فور موافقته وتأكيده الموعد الدقيق.",
            notif_type="booking_sent",
            link="/sessions"
        )

        return {
            "status": "success",
            "message": f"تم إرسال طلب الحجز للمعلم بنجاح! سيتم إشعارك فور موافقة المعلم وتأكيده الموعد الدقيق للحصة.",
            "booking": _get_first_record(result) or booking_data,
            "payment_number": "0598298930"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create booking: {e}")
        raise HTTPException(status_code=500, detail="فشل في إتمام الحجز.")

@router.post("/bookings/{booking_id}/accept")
async def accept_booking_request(
    booking_id: str,
    payload: AcceptBookingRequest,
    profile: dict = Depends(get_user_profile)
):
    """Tutor accepts a booking request, sets the exact confirmed time and meeting link."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*, tutoring_services(subject_name, title)").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("tutor_id") != user_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط المعلم صاحب الحصة يمكنه قبول الطلب.")

        price = float(booking.get("total_price", 0.0))
        is_paid = booking.get("is_paid", False) or price == 0.0

        update_data: Dict[str, Any] = {
            "status": "confirmed" if is_paid else "pending_payment",
            "is_paid": is_paid
        }

        # If tutor adjusted or confirmed session date
        if payload.session_date:
            update_data["session_date"] = payload.session_date

        # Meeting link handler: Never allow generic meet.google.com/new
        link = (payload.meeting_link or "").strip()
        clean_booking_id = booking_id.replace("-", "")[:12]
        fallback_room = f"https://meet.jit.si/Lammah-Room-{clean_booking_id}"

        if link and not link.endswith("/new"):
            if not link.startswith("http://") and not link.startswith("https://"):
                link = f"https://{link}"
            update_data["meeting_link"] = link
        elif booking.get("meeting_link") and not str(booking.get("meeting_link")).endswith("/new"):
            update_data["meeting_link"] = booking.get("meeting_link")
        else:
            # Dedicated shared room guaranteed to unite student and tutor in the exact same room
            update_data["meeting_link"] = fallback_room

        if payload.tutor_notes:
            existing_notes = booking.get("notes_from_student") or ""
            update_data["notes_from_student"] = f"{existing_notes}\n[ملاحظات المعلم]: {payload.tutor_notes}".strip()

        result = supabase.table("tutoring_bookings").update(update_data).eq("id", booking_id).execute()

        # Update pending balance in wallet ONLY IF booking is verified and paid
        tutor_id = str(booking.get("tutor_id", ""))
        tutor_share = float(booking.get("tutor_share", 0.0))
        ensure_wallet_exists(supabase, tutor_id)

        if is_paid and tutor_share > 0:
            try:
                w_res = supabase.table("tutor_wallets").select("pending_balance").eq("tutor_id", tutor_id).execute()
                w_first = _get_first_record(w_res)
                if w_first:
                    curr_pending = float(w_first.get("pending_balance", 0.0))
                    supabase.table("tutor_wallets").update({
                        "pending_balance": round(curr_pending + tutor_share, 2),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("tutor_id", tutor_id).execute()
            except Exception as we:
                logger.warning(f"Failed to update pending wallet on accept: {we}")

        # Send in-app notification to student
        student_id = str(booking.get("student_id", ""))
        service_data = booking.get("tutoring_services")
        subject_name = service_data.get("subject_name", "المساق") if isinstance(service_data, dict) else "المساق"
        
        notif_msg = (
            f"وافق المعلم على موعد حصتك لمساق ({subject_name})! يمكنك الاطلاع على تفاصيل الحصة والعد التنازلي في صفحة حصصي."
            if is_paid else
            f"وافق المعلم على موعد حصتك لمساق ({subject_name})! يرجى سداد الرسوم المطلوبة ({price} ₪) لتأكيد الحجز النهائي وتفعيل رابط اللقاء."
        )

        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="تمت الموافقة على طلب الحجز! 🌟" if is_paid else "المعلم حدد الموعد - بانتظار السداد ⏳",
            message=notif_msg,
            notif_type="booking_confirmed" if is_paid else "payment_required",
            link="/sessions"
        )

        return {
            "status": "success",
            "message": "تم قبول طلب الحجز وتأكيد الموعد بنجاح!",
            "booking": _get_first_record(result)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to accept booking: {e}")
        raise HTTPException(status_code=500, detail="فشل في قبول الحجز.")

@router.post("/bookings/{booking_id}/reject")
async def reject_booking_request(
    booking_id: str,
    payload: RejectBookingRequest,
    profile: dict = Depends(get_user_profile)
):
    """Tutor declines a booking request."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*, tutoring_services(subject_name)").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("tutor_id") != user_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط المعلم صاحب الحصة يمكنه رفض الطلب.")

        reason = payload.reason or "المعلم غير متفرغ في هذا الموعد."

        result = supabase.table("tutoring_bookings").update({
            "status": "cancelled"
        }).eq("id", booking_id).execute()

        # Send in-app notification to student
        student_id = str(booking.get("student_id", ""))
        service_data = booking.get("tutoring_services")
        subject_name = service_data.get("subject_name", "المساق") if isinstance(service_data, dict) else "المساق"

        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="اعتذار عن موعد الحجز ⚠️",
            message=f"اعتذر المعلم عن موعد حصة ({subject_name}). السبب: {reason}. يمكنك اختيار موعد آخر أو معلم آخر.",
            notif_type="booking_rejected",
            link="/tutors"
        )

        return {
            "status": "success",
            "message": "تم رفض طلب الحجز وإشعار الطالب.",
            "booking": _get_first_record(result)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject booking: {e}")
        raise HTTPException(status_code=500, detail="فشل في رفض الحجز.")

@router.post("/bookings/{booking_id}/report-no-show")
async def report_student_no_show(
    booking_id: str,
    payload: ReportNoShowRequest,
    profile: dict = Depends(get_user_profile)
):
    """Tutor reports that student did not attend after waiting at least 15 minutes."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("tutor_id") != user_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط المعلم صاحب الحصة يمكنه تقديم بلاغ عدم الحضور.")

        if booking.get("status") != "confirmed":
            raise HTTPException(status_code=400, detail="يجب أن يكون الحجز مؤكداً لتقديم بلاغ عدم الحضور.")

        # Check time: must be past session_date + 15 minutes
        session_date_str = booking.get("session_date")
        if session_date_str:
            try:
                s_dt = datetime.fromisoformat(str(session_date_str).replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                diff_sec = (now_dt - s_dt).total_seconds()
                if diff_sec < 900: # less than 15 minutes
                    rem_min = int((900 - diff_sec) / 60) + 1
                    raise HTTPException(status_code=400, detail=f"يجب الانتظار لمدة 15 دقيقة على الأقل من بدء موعد الحصة لتوثيق الغياب. متبقي: {rem_min} دقيقة.")
            except (ValueError, TypeError):
                pass

        tutor_id = str(booking.get("tutor_id", ""))
        tutor_share = float(booking.get("tutor_share", 0.0))

        # Mark as completed with settled share to compensate tutor
        supabase.table("tutoring_bookings").update({
            "status": "completed",
            "is_settled": True
        }).eq("id", booking_id).execute()

        # Release to tutor available balance
        if not booking.get("is_settled"):
            ensure_wallet_exists(supabase, tutor_id)
            w_res = supabase.table("tutor_wallets").select("*").eq("tutor_id", tutor_id).execute()
            w = _get_first_record(w_res)
            if w:
                avail = float(w.get("available_balance", 0.0))
                pending = float(w.get("pending_balance", 0.0))
                earned = float(w.get("total_earned", 0.0))

                new_avail = round(avail + tutor_share, 2)
                new_pending = max(0.0, round(pending - tutor_share, 2))
                new_earned = round(earned + tutor_share, 2)

                supabase.table("tutor_wallets").update({
                    "available_balance": new_avail,
                    "pending_balance": new_pending,
                    "total_earned": new_earned,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("tutor_id", tutor_id).execute()

        # Send notification to student
        student_id = str(booking.get("student_id", ""))
        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="توثيق عدم حضور الجلسة ⚠️",
            message="قام المعلم بتوثيق عدم حضورك للجلسة بعد انتظار 15 دقيقة من الموعد المجدول. تم صرف حصة المعلم تعويضاً عن حجز وقته.",
            notif_type="no_show",
            link="/sessions"
        )

        return {
            "status": "success",
            "message": f"تم توثيق عدم حضور الطالب وصرف حصتك ({tutor_share} شيكل) في رصيدك المتاح للسحب تقديراً لالتزامك وحجز وقتك."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to report no show: {e}")
        raise HTTPException(status_code=500, detail="فشل في توثيق عدم الحضور.")

@router.post("/bookings/{booking_id}/complete")
async def complete_booking_and_settle(
    booking_id: str,
    profile: dict = Depends(get_user_profile)
):
    """
    Tutor marks session as delivered (pending student confirmation or 24-hr auto-settle),
    OR Student/Admin confirms completion, which immediately releases escrow funds into available balance.
    """
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*, tutoring_services(subject_name, title)").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        is_tutor = (user_id == booking.get("tutor_id"))
        is_student = (user_id == booking.get("student_id"))
        is_admin = (profile.get("role") == "admin")

        if not (is_tutor or is_student or is_admin):
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتحديث حالة هذا الحجز.")

        if booking.get("status") == "cancelled":
            raise HTTPException(status_code=400, detail="لا يمكن استكمال حجز تم إلغاؤه.")
        if booking.get("status") == "disputed":
            raise HTTPException(status_code=400, detail="هذه الجلسة قيد النزاع والتدقيق الإداري حالياً ولا يمكن تحرير الأرباح تلقائياً.")

        service_data = booking.get("tutoring_services")
        subject_name = service_data.get("subject_name", "المساق") if isinstance(service_data, dict) else "المساق"
        tutor_share = float(booking.get("tutor_share", 0.0))

        # CASE 1: Student or Admin confirms -> Immediate settlement to available balance
        if is_student or is_admin:
            settled = settle_booking_funds(supabase, booking, "تأكيد فوري من الطالب لاكتمال الحصة")
            return {
                "status": "success",
                "message": f"تم تأكيد اكتمال الحصة بنجاح! تم تحرير أرباح المعلم ({tutor_share} ₪) في رصيده المتاح للسحب. شكراً لحضورك وتقييمك!",
                "is_settled": True
            }

        # CASE 2: Tutor marks session delivered
        # Ensure session date has arrived (cannot claim delivery in advance)
        session_date_str = booking.get("session_date")
        if session_date_str:
            try:
                s_dt = datetime.fromisoformat(str(session_date_str).replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                if (s_dt - now_dt).total_seconds() > 300: # more than 5 min before start
                    raise HTTPException(status_code=400, detail="لا يمكنك توثيق تقديم الحصة قبل حلول موعدها المجدول.")
            except (ValueError, TypeError):
                pass

        # Document delivery claim in notes without prematurely releasing money to available_balance
        existing_notes = booking.get("notes_from_student") or ""
        delivery_tag = "[توثيق المعلم]: تم إعطاء الحصة بنجاح"
        if delivery_tag not in existing_notes:
            updated_notes = f"{existing_notes}\n{delivery_tag} ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})".strip()
            supabase.table("tutoring_bookings").update({
                "notes_from_student": updated_notes
            }).eq("id", booking_id).execute()

        # Send in-app notification to student
        student_id = str(booking.get("student_id", ""))
        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="المعلم وثّق تقديم الحصة 🎓",
            message=f"أكّد المعلم إتمام جلستكم لمساق ({subject_name}). يرجى تأكيد استلام الحصة وتقييم المعلم لتحرير أرباحه، أو الإبلاغ عن مشكلة إن لم تنعقد.",
            notif_type="delivery_claimed",
            link="/sessions"
        )

        return {
            "status": "success",
            "message": "تم توثيق تقديم الحصة بنجاح وإشعار الطالب لتأكيد الاستلام! أرباحك في أمان تام وستُحرر فور تأكيد الطالب أو تلقائياً بعد مرور 24 ساعة من موعد الحصة.",
            "is_settled": False
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process session completion: {e}")
        raise HTTPException(status_code=500, detail="فشل في توثيق إتمام الحجز.")

@router.post("/bookings/{booking_id}/confirm-by-student")
async def confirm_booking_by_student(
    booking_id: str,
    profile: dict = Depends(get_user_profile)
):
    """Allows student to confirm session completion, immediately releasing escrow funds to tutor."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("student_id") != user_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط الطالب صاحب الحصة يمكنه تأكيد استلامها.")

        if booking.get("status") == "disputed":
            raise HTTPException(status_code=400, detail="هذه الجلسة قيد النزاع الإداري حالياً.")

        tutor_share = float(booking.get("tutor_share", 0.0))
        settle_booking_funds(supabase, booking, "تأكيد ورضا الطالب عن الحصة")

        return {
            "status": "success",
            "message": f"شكراً لك! تم تأكيد إتمام الحصة ورضاك عنها بنجاح، وتم تحويل {tutor_share} ₪ إلى رصيد المعلم المتاح للسحب."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm booking by student: {e}")
        raise HTTPException(status_code=500, detail="فشل في تأكيد الحجز.")

@router.post("/bookings/{booking_id}/dispute")
async def dispute_booking(
    booking_id: str,
    payload: DisputeBookingRequest,
    profile: dict = Depends(get_user_profile)
):
    """Allows student or tutor to report an issue or dispute for an active/past session."""
    supabase = get_supabase()
    user_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*, tutoring_services(subject_name)").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        is_student = (user_id == booking.get("student_id"))
        is_tutor = (user_id == booking.get("tutor_id"))
        if not (is_student or is_tutor or profile.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتقديم بلاغ نزاع على هذا الحجز.")

        # Set status to disputed and ensure is_settled remains false
        existing_notes = booking.get("notes_from_student") or ""
        role_title = "الطالب" if is_student else "المعلم"
        dispute_entry = f"[بلاغ نزاع من {role_title}]: {payload.reason} - {payload.details or ''}".strip()
        updated_notes = f"{existing_notes}\n{dispute_entry}".strip()

        supabase.table("tutoring_bookings").update({
            "status": "disputed",
            "is_settled": False,
            "notes_from_student": updated_notes
        }).eq("id", booking_id).execute()

        # Notify opposing party
        target_id = booking.get("tutor_id") if is_student else booking.get("student_id")
        service_data = booking.get("tutoring_services")
        subject_name = service_data.get("subject_name", "المساق") if isinstance(service_data, dict) else "المساق"

        if target_id:
            create_notification(
                supabase=supabase,
                user_id=str(target_id),
                title="تم تقديم بلاغ مراجعة للجلسة ⚠️",
                message=f"قام {role_title} بتقديم بلاغ بخصوص حصة ({subject_name}): \"{payload.reason}\". تم تجميد تسوية الجلسة وسيقوم المشرف الإداري بمراجعة الأمر.",
                notif_type="dispute_opened",
                link="/sessions" if not is_student else "/tutor/dashboard"
            )

        return {
            "status": "success",
            "message": "تم تسجيل بلاغك وتجميد تسوية الجلسة بنجاح لحفظ حقوقك. سيقوم فريق الإدارة بمراجعة تفاصيل الجلسة وحل الأمر بأقرب وقت."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit booking dispute: {e}")
        raise HTTPException(status_code=500, detail="فشل في تقديم بلاغ النزاع.")

@router.post("/bookings/{booking_id}/review")
async def review_booking(
    booking_id: str,
    request: ReviewBookingRequest,
    profile: dict = Depends(get_user_profile)
):
    """Allows a student to leave a review after a session. Automatically settles funds if pending."""
    supabase = get_supabase()
    student_id = profile["id"]

    try:
        b_res = supabase.table("tutoring_bookings").select("*").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("student_id") != student_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط الطالب الذي حضر الجلسة يمكنه كتابة التقييم.")

        tutor_id = str(booking.get("tutor_id", ""))

        review_data = {
            "id": str(uuid.uuid4()),
            "booking_id": booking_id,
            "tutor_id": tutor_id,
            "student_id": student_id,
            "rating": request.rating,
            "comment": request.comment or "",
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        supabase.table("tutor_reviews").upsert(review_data, on_conflict="booking_id").execute()

        # Recalculate tutor avg rating
        all_revs = supabase.table("tutor_reviews").select("rating").eq("tutor_id", tutor_id).execute()
        rev_records = _get_records(all_revs)
        if rev_records:
            ratings: List[float] = [float(r.get("rating", 5)) for r in rev_records if r.get("rating") is not None]
            if ratings:
                avg_r = round(sum(ratings) / len(ratings), 2)
                supabase.table("tutor_profiles").update({
                    "rating_avg": avg_r,
                    "rating_count": len(ratings)
                }).eq("id", tutor_id).execute()

        # Auto-settle funds since student has reviewed and verified the session!
        if not booking.get("is_settled") and booking.get("status") != "disputed":
            settle_booking_funds(supabase, booking, f"تقييم الطالب ({request.rating} نجوم) وتوثيق استلام الحصة")

        return {"status": "success", "message": "شكراً لتقييمك! تم حفظ المراجعة واعتماد الحصة بنجاح."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit review: {e}")
        raise HTTPException(status_code=500, detail="فشل في حفظ التقييم.")

@router.patch("/bookings/{booking_id}/meeting-link")
async def update_booking_meeting_link(
    booking_id: str,
    payload: UpdateMeetingLinkRequest,
    profile: dict = Depends(get_user_profile)
):
    """Allows the tutor (or admin) to set/update the Google Meet link for a session."""
    supabase = get_supabase()
    user_id = profile["id"]
    try:
        b_res = supabase.table("tutoring_bookings").select("*").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")
            
        if booking.get("tutor_id") != user_id and profile.get("role") != "admin":
            raise HTTPException(status_code=403, detail="فقط المعلم المسؤول عن هذه الحصة يمكنه تحديث رابط الاجتماع.")
        
        link = payload.meeting_link.strip()
        if link.endswith("/new"):
            raise HTTPException(
                status_code=400,
                detail="الرابط meet.google.com/new ينشئ غرفة مختلفة لكل شخص يدخل عليها! يرجى إنشاء الغرفة في Google Meet ونسخ رابط الغرفة الدائم (مثلاً: meet.google.com/abc-defg-hij) ولصقه هنا."
            )
        if not link:
            clean_id = booking_id.replace("-", "")[:12]
            link = f"https://meet.jit.si/Lammah-Room-{clean_id}"
        elif not link.startswith("http://") and not link.startswith("https://"):
            link = f"https://{link}"

        try:
            supabase.table("tutoring_bookings").update({
                "meeting_link": link,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", booking_id).execute()
        except Exception:
            supabase.table("tutoring_bookings").update({
                "meeting_link": link
            }).eq("id", booking_id).execute()

        return {
            "status": "success",
            "message": "تم حفظ وتحديث رابط اجتماع Google Meet بنجاح!",
            "meeting_link": link
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update meeting link: {e}")
        raise HTTPException(status_code=500, detail="فشل في تحديث رابط الاجتماع.")

@router.post("/bookings/{booking_id}/log-attendance")
async def log_session_attendance(
    booking_id: str,
    profile: dict = Depends(get_user_profile)
):
    """
    Logs when student or tutor clicks 'Join Meeting'.
    Creates an undeniable digital audit trail to verify whether either party attended.
    """
    supabase = get_supabase()
    user_id = profile["id"]
    try:
        b_res = supabase.table("tutoring_bookings").select("*").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        is_student = (user_id == booking.get("student_id"))
        is_tutor = (user_id == booking.get("tutor_id"))
        if not (is_student or is_tutor or profile.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتسجيل الحضور.")

        role_str = "الطالب" if is_student else "المعلم"
        timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        log_entry = f"[سجل الحضور]: دخل {role_str} إلى اجتماع Google Meet في {timestamp_str}"

        existing_notes = booking.get("notes_from_student") or ""
        # Check if already logged in the last 2 minutes to prevent spamming
        recent_check = f"[سجل الحضور]: دخل {role_str}"
        if log_entry not in existing_notes:
            updated_notes = f"{existing_notes}\n{log_entry}".strip()
            supabase.table("tutoring_bookings").update({
                "notes_from_student": updated_notes
            }).eq("id", booking_id).execute()

        return {"status": "success", "logged_at": timestamp_str, "role": role_str}
    except Exception as e:
        logger.warning(f"Failed to log session attendance: {e}")
        return {"status": "ignored"}

@router.get("/my-bookings")
async def get_student_bookings(profile: dict = Depends(get_user_profile)):
    """Returns student's booked sessions with full details and reviews."""
    supabase = get_supabase()
    try:
        check_and_auto_settle_bookings(supabase)
        res = supabase.table("tutoring_bookings")\
            .select("*, tutoring_services(title, subject_name, duration_minutes, price, service_type), tutor_profiles:tutor_id(headline, university, major)")\
            .eq("student_id", profile["id"])\
            .order("session_date", desc=True)\
            .execute()
            
        bookings: List[Dict[str, Any]] = _get_records(res)
        if bookings:
            tutor_ids = list(set(str(b["tutor_id"]) for b in bookings if b.get("tutor_id")))
            if tutor_ids:
                profs_res = supabase.table("profiles").select("id, full_name, phone_number").in_("id", tutor_ids).execute()
                pmap: Dict[str, Dict[str, Any]] = {str(p["id"]): dict(p) for p in _get_records(profs_res) if "id" in p}
                for b in bookings:
                    raw_prof = b.get("tutor_profiles")
                    t_prof: Dict[str, Any] = dict(raw_prof) if isinstance(raw_prof, dict) else {}
                    t_id = str(b.get("tutor_id", ""))
                    if t_id and t_id in pmap:
                        t_prof["profiles"] = pmap[t_id]
                    b["tutor_profiles"] = t_prof

            booking_ids = [str(b["id"]) for b in bookings if "id" in b]
            if booking_ids:
                rev_res = supabase.table("tutor_reviews").select("*").in_("booking_id", booking_ids).execute()
                rev_map: Dict[str, Dict[str, Any]] = {str(r["booking_id"]): dict(r) for r in _get_records(rev_res) if "booking_id" in r}
                for b in bookings:
                    b["review"] = rev_map.get(str(b.get("id")))

            for b in bookings:
                b["meeting_link"] = _ensure_valid_meeting_link(b, supabase)

        return {"status": "success", "bookings": bookings}
    except Exception as e:
        logger.error(f"Failed to fetch student bookings: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب حجوزاتك.")

# ====================================================================
# 5. لوحة الإدارة (Admin Tutoring & Payout Approvals)
# ====================================================================

@router.get("/admin/applications")
async def get_admin_applications(admin: dict = Depends(verify_admin)):
    """Admin: lists all pending/approved/rejected tutor applications."""
    supabase = get_supabase()
    try:
        res = supabase.table("tutor_profiles")\
            .select("*")\
            .order("created_at", desc=True)\
            .execute()
            
        tutors: List[Dict[str, Any]] = _get_records(res)
        if tutors:
            ids = [str(t["id"]) for t in tutors if "id" in t]
            if ids:
                profs_res = supabase.table("profiles").select("id, full_name, phone_number, role, created_at").in_("id", ids).execute()
                pmap: Dict[str, Dict[str, Any]] = {str(p["id"]): dict(p) for p in _get_records(profs_res) if "id" in p}
                for t in tutors:
                    tid = str(t.get("id", ""))
                    prof: Dict[str, Any] = dict(pmap.get(tid, {}))
                    if not prof.get("full_name"):
                        p_details = t.get("payout_details")
                        if isinstance(p_details, dict):
                            acad_info = p_details.get("academic_info")
                            acad_name = acad_info.get("full_name") if isinstance(acad_info, dict) else ""
                            prof["full_name"] = p_details.get("full_name") or acad_name or ""
                    if not prof.get("phone_number"):
                        p_details = t.get("payout_details")
                        if isinstance(p_details, dict):
                            prof["phone_number"] = p_details.get("phone_number") or p_details.get("identifier") or ""
                    t["profiles"] = prof

        return {"status": "success", "applications": tutors}
    except Exception as e:
        logger.error(f"Failed to fetch admin tutor applications: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب طلبات المدرسين.")

@router.post("/admin/applications/{tutor_id}/approve")
async def approve_tutor_application(tutor_id: str, admin: dict = Depends(verify_admin)):
    """Admin: approves a tutor application and upgrades user role to 'tutor'."""
    supabase = get_supabase()
    try:
        supabase.table("tutor_profiles").update({
            "status": "approved",
            "rejection_reason": None,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", tutor_id).execute()

        try:
            supabase.table("profiles").update({"role": "tutor"}).eq("id", tutor_id).execute()
        except Exception as pe:
            logger.warning(f"Could not update profile role to tutor: {pe}")

        ensure_wallet_exists(supabase, tutor_id)

        return {"status": "success", "message": "تم قبول واعتماد المعلم بنجاح!"}
    except Exception as e:
        logger.error(f"Failed to approve tutor: {e}")
        raise HTTPException(status_code=500, detail="فشل في اعتماد المعلم.")

@router.post("/admin/applications/{tutor_id}/reject")
async def reject_tutor_application(
    tutor_id: str,
    payload: dict = Body(...),
    admin: dict = Depends(verify_admin)
):
    """Admin: rejects a tutor application with a reason."""
    reason = payload.get("reason", "المستندات المرفقة غير مطابقة للشروط.")
    supabase = get_supabase()
    try:
        supabase.table("tutor_profiles").update({
            "status": "rejected",
            "rejection_reason": reason,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", tutor_id).execute()

        supabase.table("profiles").update({"role": "user"}).eq("id", tutor_id).execute()

        return {"status": "success", "message": "تم رفض طلب التدريس."}
    except Exception as e:
        logger.error(f"Failed to reject tutor: {e}")
        raise HTTPException(status_code=500, detail="فشل في رفض الطلب.")

@router.get("/admin/payouts")
async def get_admin_payout_requests(admin: dict = Depends(verify_admin)):
    """Admin: list all tutor payout requests."""
    supabase = get_supabase()
    try:
        res = supabase.table("tutor_payouts")\
            .select("*")\
            .order("created_at", desc=True)\
            .execute()
            
        payouts: List[Dict[str, Any]] = _get_records(res)
        if payouts:
            tutor_ids = list(set(str(p["tutor_id"]) for p in payouts if p.get("tutor_id")))
            if tutor_ids:
                t_res = supabase.table("tutor_profiles").select("id, headline, university, major, payout_details").in_("id", tutor_ids).execute()
                tmap: Dict[str, Dict[str, Any]] = {}
                for t in _get_records(t_res):
                    if "id" in t:
                        td = dict(t)
                        pdetails = td.get("payout_details")
                        if isinstance(pdetails, dict):
                            td["payout_method"] = pdetails.get("payout_method", "reflect")
                            td["payout_identifier"] = pdetails.get("payout_identifier", "")
                            td["bank_name"] = pdetails.get("bank_name", "")
                            td["bank_iban"] = pdetails.get("bank_iban", "")
                        tmap[str(t["id"])] = td
                
                p_res = supabase.table("profiles").select("id, full_name, phone_number").in_("id", tutor_ids).execute()
                pmap: Dict[str, Dict[str, Any]] = {str(p["id"]): dict(p) for p in _get_records(p_res) if "id" in p}
                
                for p in payouts:
                    tid = str(p.get("tutor_id", ""))
                    t_prof: Dict[str, Any] = dict(tmap.get(tid, {}))
                    t_prof["profiles"] = pmap.get(tid, {})
                    p["tutor_profiles"] = t_prof

        return {"status": "success", "payouts": payouts}
    except Exception as e:
        logger.error(f"Failed to fetch payouts: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب طلبات السحب.")

@router.post("/admin/payouts/{payout_id}/fulfill")
async def fulfill_payout_request(
    payout_id: str,
    payload: dict = Body(...),
    admin: dict = Depends(verify_admin)
):
    """Admin: marks a payout as fulfilled/completed and attaches receipt image."""
    receipt_url = payload.get("receipt_image_url", "")
    admin_notes = payload.get("admin_notes", "تم التحويل بنجاح عبر تطبيق ريفلكت.")
    supabase = get_supabase()

    try:
        p_res = supabase.table("tutor_payouts").select("*").eq("id", payout_id).execute()
        payout = _get_first_record(p_res)
        if not payout:
            raise HTTPException(status_code=404, detail="طلب السحب غير موجود.")
            
        if payout.get("status") == "completed":
            raise HTTPException(status_code=400, detail="تم تحويل هذا الطلب مسبقاً.")

        tutor_id = str(payout.get("tutor_id", ""))
        amount = float(payout.get("amount", 0.0))

        # Update payout
        supabase.table("tutor_payouts").update({
            "status": "completed",
            "receipt_image_url": receipt_url,
            "admin_notes": admin_notes,
            "settled_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", payout_id).execute()

        # Update total withdrawn in wallet
        w_res = supabase.table("tutor_wallets").select("total_withdrawn").eq("tutor_id", tutor_id).execute()
        w_rec = _get_first_record(w_res)
        if w_rec:
            curr_withdrawn = float(w_rec.get("total_withdrawn", 0.0))
            supabase.table("tutor_wallets").update({
                "total_withdrawn": round(curr_withdrawn + amount, 2),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("tutor_id", tutor_id).execute()

        return {"status": "success", "message": f"تم تأكيد تحويل مبلغ {amount} شيكل للمعلم بنجاح!"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fulfill payout: {e}")
        raise HTTPException(status_code=500, detail="فشل في تأكيد التحويل.")

@router.post("/admin/payouts/{payout_id}/reject")
async def reject_payout_request(
    payout_id: str,
    payload: dict = Body(...),
    admin: dict = Depends(verify_admin)
):
    """Admin: rejects a payout request and refunds the money back to available balance."""
    reason = payload.get("reason", "بيانات الحساب غير صحيحة.")
    supabase = get_supabase()

    try:
        p_res = supabase.table("tutor_payouts").select("*").eq("id", payout_id).execute()
        payout = _get_first_record(p_res)
        if not payout:
            raise HTTPException(status_code=404, detail="طلب السحب غير موجود.")
            
        if payout.get("status") != "requested":
            raise HTTPException(status_code=400, detail="الطلب ليس في حالة انتظار.")

        tutor_id = str(payout.get("tutor_id", ""))
        amount = float(payout.get("amount", 0.0))

        # Update payout status
        supabase.table("tutor_payouts").update({
            "status": "rejected",
            "admin_notes": reason
        }).eq("id", payout_id).execute()

        # Refund back to available balance
        w_res = supabase.table("tutor_wallets").select("available_balance").eq("tutor_id", tutor_id).execute()
        w_rec = _get_first_record(w_res)
        if w_rec:
            curr_avail = float(w_rec.get("available_balance", 0.0))
            supabase.table("tutor_wallets").update({
                "available_balance": round(curr_avail + amount, 2),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("tutor_id", tutor_id).execute()

        return {"status": "success", "message": f"تم رفض طلب السحب وإعادة {amount} شيكل لرصيد المعلم."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject payout: {e}")
        raise HTTPException(status_code=500, detail="فشل في رفض طلب السحب.")

@router.post("/admin/bookings/{booking_id}/confirm-payment")
async def admin_confirm_booking_payment(
    booking_id: str,
    admin: dict = Depends(verify_admin)
):
    """Admin verifies and confirms bank/Reflect payment for a tutoring booking."""
    supabase = get_supabase()
    try:
        b_res = supabase.table("tutoring_bookings").select("*, tutoring_services(subject_name)").eq("id", booking_id).execute()
        booking = _get_first_record(b_res)
        if not booking:
            raise HTTPException(status_code=404, detail="الحجز غير موجود.")

        if booking.get("is_paid"):
            return {"status": "success", "message": "هذا الحجز مدفوع ومعتمد مسبقاً."}

        # 1. Mark booking as paid and confirmed
        supabase.table("tutoring_bookings").update({
            "is_paid": True,
            "status": "confirmed"
        }).eq("id", booking_id).execute()

        # 2. Credit pending_balance in tutor wallet
        tutor_id = str(booking.get("tutor_id", ""))
        tutor_share = float(booking.get("tutor_share", 0.0))
        ensure_wallet_exists(supabase, tutor_id)
        if tutor_share > 0:
            w_res = supabase.table("tutor_wallets").select("pending_balance").eq("tutor_id", tutor_id).execute()
            w_first = _get_first_record(w_res)
            if w_first:
                curr_pending = float(w_first.get("pending_balance", 0.0))
                supabase.table("tutor_wallets").update({
                    "pending_balance": round(curr_pending + tutor_share, 2),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }).eq("tutor_id", tutor_id).execute()

        # 3. Notify student and tutor
        student_id = str(booking.get("student_id", ""))
        service_data = booking.get("tutoring_services")
        subject_name = service_data.get("subject_name", "المساق") if isinstance(service_data, dict) else "المساق"

        create_notification(
            supabase=supabase,
            user_id=student_id,
            title="تم تأكيد دفع الحصة بنجاح! 💳✅",
            message=f"تم استلام وتأكيد سداد حصة ({subject_name}). الموعد معتمد ورابط الجلسة متاح الآن في صفحة حصصي.",
            notif_type="payment_confirmed",
            link="/sessions"
        )
        create_notification(
            supabase=supabase,
            user_id=tutor_id,
            title="تم سداد رسوم الحصة من الطالب! 💰",
            message=f"أتم الطالب سداد رسوم حصة ({subject_name}) وتم قيد أرباحك ({tutor_share} ₪) في رصيدك المعلق لحين اكتمال الحصة.",
            notif_type="funds_escrowed",
            link="/tutor/dashboard"
        )

        return {"status": "success", "message": "تم تأكيد دفع الحصة بنجاح وتفعيل الرصيد المعلق للمعلم."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm booking payment: {e}")
        raise HTTPException(status_code=500, detail="فشل في تأكيد سداد الحصة.")

# ====================================================================
# 6. الإشعارات والتنبيهات (User In-App Notifications)
# ====================================================================

@router.get("/notifications")
async def get_my_notifications(profile: dict = Depends(get_user_profile)):
    """Returns list of notifications and unread count for logged-in user."""
    supabase = get_supabase()
    user_id = profile["id"]
    try:
        res = supabase.table("user_notifications")\
            .select("*")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(30)\
            .execute()
        notifications: List[Dict[str, Any]] = _get_records(res)
        unread_count = len([n for n in notifications if not n.get("is_read")])
        return {
            "status": "success",
            "notifications": notifications,
            "unread_count": unread_count
        }
    except Exception as e:
        logger.warning(f"Could not load notifications: {e}")
        return {
            "status": "success",
            "notifications": [],
            "unread_count": 0
        }

@router.post("/notifications/read-all")
async def mark_all_notifications_read(profile: dict = Depends(get_user_profile)):
    """Marks all notifications as read for logged-in user."""
    supabase = get_supabase()
    user_id = profile["id"]
    try:
        supabase.table("user_notifications")\
            .update({"is_read": True})\
            .eq("user_id", user_id)\
            .eq("is_read", False)\
            .execute()
        return {"status": "success", "message": "تم تحديد كافة الإشعارات كمقروءة."}
    except Exception as e:
        logger.warning(f"Could not mark notifications read: {e}")
        return {"status": "success"}

@router.post("/notifications/{notification_id}/read")
async def mark_single_notification_read(
    notification_id: str,
    profile: dict = Depends(get_user_profile)
):
    """Marks a single notification as read."""
    supabase = get_supabase()
    user_id = profile["id"]
    try:
        supabase.table("user_notifications")\
            .update({"is_read": True})\
            .eq("id", notification_id)\
            .eq("user_id", user_id)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        logger.warning(f"Could not mark notification read: {e}")
        return {"status": "success"}

