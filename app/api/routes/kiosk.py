import os
import uuid
import re
from typing import Optional, List, Dict, Any
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.core.auth_guard import get_user_profile
from app.api.routes.admin import verify_admin
from app.db.supabase_client import get_supabase
from app.core.logger import logger
from app.core.config import settings

router = APIRouter()

# Directory for kiosk uploaded images (aligned with Docker / App upload directory)
_base_upload_dir = os.getenv("UPLOAD_DIR")
if _base_upload_dir:
    KIOSK_UPLOAD_DIR = os.path.join(_base_upload_dir, "kiosk")
else:
    KIOSK_UPLOAD_DIR = os.path.join(os.getcwd(), "uploads", "kiosk")
os.makedirs(KIOSK_UPLOAD_DIR, exist_ok=True)

# Allowed categories
VALID_CATEGORIES = {
    "food": "أطعمة ومأكولات",
    "sweets": "حلويات ومخبوزات",
    "crafts": "أعمال يدوية وهدايا",
    "services": "خدمات أكاديمية ومشاريع",
    "printing": "طباعة وتصوير وتجليد",
    "fashion": "إكسسوارات وملابس",
    "tech": "تقنية وإلكترونيات",
    "other": "أخرى"
}

# ==================== DTO Schemas ====================

class CreateProjectRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    category: str = Field(..., description="فئة المشروع")
    description: str = Field(..., min_length=10, max_length=2500)
    price_range: Optional[str] = Field("", max_length=100)
    whatsapp_number: str = Field(..., min_length=7, max_length=25)
    campus_locations: List[str] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list, max_length=5)
    owner_faculty: Optional[str] = Field("", max_length=120)

class UpdateProjectStatusRequest(BaseModel):
    status: str = Field(..., description="'approved' or 'rejected' or 'archived'")
    rejection_reason: Optional[str] = ""

class CreateReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=2, max_length=1000)

# ==================== Helper Functions ====================

def _get_current_user_optional(request: Request) -> Optional[Dict[str, Any]]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        from app.db.supabase_client import get_auth_client
        auth_client = get_auth_client()
        user_res = auth_client.auth.get_user(token)
        if user_res and user_res.user:
            return {"id": user_res.user.id}
    except Exception:
        pass
    return None

# ==================== Static Image Serving ====================

@router.get("/images/{filename}")
async def get_kiosk_image(filename: str):
    # Sanitize and validate filename pattern
    safe_filename = os.path.basename(filename)
    if not re.match(r"^kiosk_[a-zA-Z0-9_\-]+\.(jpg|jpeg|png|webp)$", safe_filename, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="اسم ملف الصورة غير صالح")
    file_path = os.path.join(KIOSK_UPLOAD_DIR, safe_filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="الصورة غير موجودة")
    return FileResponse(file_path)

@router.post("/upload-image")
async def upload_kiosk_image(
    file: UploadFile = File(...),
    profile=Depends(get_user_profile)
):
    """
    Upload a product image (up to 5MB, format: jpg/png/webp).
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="الملف المرفوع يجب أن يكون صورة")

    # Read and check size (5MB max)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="الحد الأقصى لحجم الصورة هو 5 ميجابايت")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
        ext = ".jpg"

    unique_filename = f"kiosk_{uuid.uuid4().hex[:12]}{ext}"
    dest_path = os.path.join(KIOSK_UPLOAD_DIR, unique_filename)

    with open(dest_path, "wb") as f:
        f.write(content)

    image_url = f"/api/v1/kiosk/images/{unique_filename}"
    return {
        "status": "success",
        "url": image_url,
        "filename": unique_filename
    }

# ==================== Public Endpoints ====================

@router.get("/categories")
async def get_categories():
    """
    Returns available categories with counts of approved projects.
    """
    supabase = get_supabase()
    counts: Dict[str, int] = {k: 0 for k in VALID_CATEGORIES}
    try:
        res = supabase.table("kiosk_projects").select("category").eq("status", "approved").execute()
        for row in (res.data or []):
            cat = row.get("category")
            if cat in counts:
                counts[cat] += 1
    except Exception as e:
        logger.warning(f"Error fetching category counts: {e}")

    categories_list = [
        {"id": k, "name": v, "count": counts.get(k, 0)}
        for k, v in VALID_CATEGORIES.items()
    ]
    return {"status": "success", "categories": categories_list}

@router.get("/projects")
async def list_projects(
    request: Request,
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("trending", description="trending, newest, highest_rated, most_liked, clicks"),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50)
):
    """
    Lists approved projects with filters, sorting, and user like status.
    """
    supabase = get_supabase()
    current_user = _get_current_user_optional(request)
    user_id = current_user.get("id") if current_user else None

    query = supabase.table("kiosk_projects").select("*", count="exact").eq("status", "approved")

    if category and category != "all" and category in VALID_CATEGORIES:
        query = query.eq("category", category)

    if search and search.strip():
        term = f"%{search.strip().replace(',', ' ')}%"
        query = query.or_(f"title.ilike.{term},description.ilike.{term},owner_name.ilike.{term},owner_faculty.ilike.{term}")

    # Sorting
    if sort_by == "newest":
        query = query.order("created_at", desc=True)
    elif sort_by in ("highest_rated", "top_rated"):
        query = query.order("rating_avg", desc=True).order("rating_count", desc=True)
    elif sort_by == "most_liked":
        query = query.order("likes_count", desc=True)
    elif sort_by == "clicks":
        query = query.order("clicks_count", desc=True)
    else:  # trending
        # Sort primarily by rating_avg, likes, and clicks
        query = query.order("likes_count", desc=True).order("clicks_count", desc=True).order("rating_avg", desc=True)

    start = (page - 1) * page_size
    end = start + page_size - 1
    query = query.range(start, end)

    try:
        res = query.execute()
        projects = res.data or []
        total = res.count or len(projects)

        # Check user likes if logged in
        user_liked_ids = set()
        if user_id and projects:
            p_ids = [p["id"] for p in projects]
            likes_res = supabase.table("kiosk_project_likes") \
                .select("project_id") \
                .eq("user_id", user_id) \
                .in_("project_id", p_ids) \
                .execute()
            user_liked_ids = {row["project_id"] for row in (likes_res.data or [])}

        for p in projects:
            p["is_liked"] = p["id"] in user_liked_ids
            # Ensure images is a list
            if not p.get("images"):
                p["images"] = []

        return {
            "status": "success",
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
            "projects": projects
        }
    except Exception as e:
        logger.error(f"Error fetching kiosk projects: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب مشاريع الكشك")

@router.get("/projects/trending")
async def get_trending_projects(request: Request, limit: int = Query(6, ge=1, le=12)):
    """
    Returns trending projects based on engagement score:
    (likes * 2 + comments * 3 + rating_avg * rating_count + clicks * 0.5)
    CONDITIONAL: If no projects qualify or all have 0 engagement, returns empty list so UI hides cleanly!
    """
    supabase = get_supabase()
    current_user = _get_current_user_optional(request)
    user_id = current_user.get("id") if current_user else None

    try:
        # Fetch top projects with at least 1 like, review, or click
        res = supabase.table("kiosk_projects") \
            .select("*") \
            .eq("status", "approved") \
            .order("likes_count", desc=True) \
            .order("clicks_count", desc=True) \
            .order("rating_avg", desc=True) \
            .limit(limit * 2) \
            .execute()

        raw_projects = res.data or []
        
        # Calculate trending score and filter out projects with zero engagement
        trending_list = []
        for p in raw_projects:
            likes = p.get("likes_count") or 0
            comments = p.get("comments_count") or 0
            clicks = p.get("clicks_count") or 0
            rating = float(p.get("rating_avg") or 0.0)
            rating_cnt = p.get("rating_count") or 0

            score = (likes * 2.0) + (comments * 3.0) + (rating * rating_cnt) + (clicks * 0.5)
            # Only qualify if there is actual interaction
            if score > 0:
                p["trending_score"] = round(score, 1)
                trending_list.append(p)

        trending_list.sort(key=lambda x: x.get("trending_score", 0), reverse=True)
        final_trending = trending_list[:limit]

        # Check likes
        if user_id and final_trending:
            p_ids = [p["id"] for p in final_trending]
            likes_res = supabase.table("kiosk_project_likes") \
                .select("project_id") \
                .eq("user_id", user_id) \
                .in_("project_id", p_ids) \
                .execute()
            user_liked_ids = {row["project_id"] for row in (likes_res.data or [])}
            for p in final_trending:
                p["is_liked"] = p["id"] in user_liked_ids
        else:
            for p in final_trending:
                p["is_liked"] = False

        return {
            "status": "success",
            "count": len(final_trending),
            "projects": final_trending
        }
    except Exception as e:
        logger.error(f"Error fetching trending kiosk projects: {e}")
        return {"status": "success", "count": 0, "projects": []}

@router.get("/projects/{project_id}")
async def get_project_details(project_id: str, request: Request):
    """
    Returns single project details, owner info, reviews list, and user like status.
    """
    supabase = get_supabase()
    current_user = _get_current_user_optional(request)
    user_id = current_user.get("id") if current_user else None

    try:
        p_res = supabase.table("kiosk_projects").select("*").eq("id", project_id).execute()
        if not p_res.data:
            raise HTTPException(status_code=404, detail="المشروع غير موجود")
        
        project = p_res.data[0]
        # Only owner or approved projects are viewable
        if project.get("status") != "approved" and project.get("owner_id") != user_id:
            raise HTTPException(status_code=403, detail="المشروع قيد المراجعة ولم يتم اعتماده بعد")

        # Fetch reviews
        r_res = supabase.table("kiosk_project_reviews") \
            .select("*") \
            .eq("project_id", project_id) \
            .order("created_at", desc=True) \
            .execute()
        reviews = r_res.data or []

        # Check if current user liked
        is_liked = False
        user_review = None
        if user_id:
            like_res = supabase.table("kiosk_project_likes") \
                .select("id") \
                .eq("project_id", project_id) \
                .eq("user_id", user_id) \
                .execute()
            is_liked = bool(like_res.data)

            # Find user's own review if any
            for r in reviews:
                if r.get("user_id") == user_id:
                    user_review = r
                    break

        project["is_liked"] = is_liked
        project["reviews"] = reviews
        project["user_review"] = user_review

        return {
            "status": "success",
            "project": project
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء جلب تفاصيل المشروع")

@router.post("/projects/{project_id}/click")
async def record_project_click(project_id: str):
    """
    Atomically records a store visit/click.
    """
    supabase = get_supabase()
    try:
        # Call atomic stored function
        res = supabase.rpc("increment_kiosk_project_clicks", {"p_project_id": project_id}).execute()
        clicks = res.data if res.data is not None else 0
        return {"status": "success", "clicks_count": clicks}
    except Exception as e:
        logger.warning(f"Failed RPC increment_kiosk_project_clicks: {e}. Falling back to manual update.")
        try:
            p_res = supabase.table("kiosk_projects").select("clicks_count").eq("id", project_id).execute()
            if p_res.data:
                new_c = (p_res.data[0].get("clicks_count") or 0) + 1
                supabase.table("kiosk_projects").update({"clicks_count": new_c}).eq("id", project_id).execute()
                return {"status": "success", "clicks_count": new_c}
        except Exception:
            pass
        return {"status": "success", "clicks_count": 0}

# ==================== Authenticated Actions ====================

@router.post("/projects")
async def create_kiosk_project(
    payload: CreateProjectRequest,
    profile=Depends(get_user_profile)
):
    """
    Student submits their project. Status set to 'pending' for admin review.
    """
    user_id = profile["id"]
    supabase = get_supabase()

    if payload.category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail="فئة المشروع غير صالحة")

    # Clean whatsapp number
    clean_phone = re.sub(r"[^\d+]", "", payload.whatsapp_number.strip())
    if len(clean_phone) < 7:
        raise HTTPException(status_code=400, detail="رقم الواتساب غير صالح")

    # Limit images to max 5
    images = payload.images[:5]

    project_data = {
        "owner_id": user_id,
        "owner_name": profile.get("full_name") or "طالب لمّاح",
        "owner_username": profile.get("username") or "",
        "owner_faculty": payload.owner_faculty or "",
        "title": payload.title.strip(),
        "category": payload.category,
        "description": payload.description.strip(),
        "price_range": payload.price_range.strip() if payload.price_range else "",
        "whatsapp_number": clean_phone,
        "campus_locations": payload.campus_locations or ["الحرم الجامعي"],
        "images": images,
        "status": "pending",
        "clicks_count": 0,
        "likes_count": 0,
        "rating_avg": 0.0,
        "rating_count": 0,
        "comments_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        res = supabase.table("kiosk_projects").insert(project_data).execute()
        if not res.data:
            raise RuntimeError("Failed to insert kiosk project")
        return {
            "status": "success",
            "message": "تم تقديم مشروعك بنجاح! سيتم مراجعته واعتماده من قبل الإدارة قريباً 🚀",
            "project": res.data[0]
        }
    except Exception as e:
        logger.error(f"Error creating kiosk project: {e}")
        raise HTTPException(status_code=500, detail="فشل تسجيل المشروع، يرجى المحاولة مجدداً")

@router.get("/my-projects")
async def get_my_kiosk_projects(profile=Depends(get_user_profile)):
    """
    Returns all projects created by the logged-in student and their statuses.
    """
    user_id = profile["id"]
    supabase = get_supabase()
    try:
        res = supabase.table("kiosk_projects") \
            .select("*") \
            .eq("owner_id", user_id) \
            .order("created_at", desc=True) \
            .execute()
        return {"status": "success", "projects": res.data or []}
    except Exception as e:
        logger.error(f"Error fetching my kiosk projects: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب مشاريعك")

@router.post("/projects/{project_id}/like")
async def toggle_project_like(project_id: str, profile=Depends(get_user_profile)):
    """
    Toggles like on a project.
    """
    user_id = profile["id"]
    supabase = get_supabase()

    try:
        # Check if project exists
        p_check = supabase.table("kiosk_projects").select("id").eq("id", project_id).execute()
        if not p_check.data:
            raise HTTPException(status_code=404, detail="المشروع غير موجود")

        # Check if already liked
        existing = supabase.table("kiosk_project_likes") \
            .select("id") \
            .eq("project_id", project_id) \
            .eq("user_id", user_id) \
            .execute()

        if existing.data:
            # Unlike
            supabase.table("kiosk_project_likes").delete().eq("id", existing.data[0]["id"]).execute()
            is_liked = False
        else:
            # Like
            supabase.table("kiosk_project_likes").insert({
                "project_id": project_id,
                "user_id": user_id,
                "created_at": datetime.now(timezone.utc).isoformat()
            }).execute()
            is_liked = True

        # Resilient sync: calculate likes in Python as safeguard
        try:
            cnt_res = supabase.table("kiosk_project_likes").select("id", count="exact").eq("project_id", project_id).execute()
            likes_count = cnt_res.count if cnt_res.count is not None else len(cnt_res.data or [])
            supabase.table("kiosk_projects").update({
                "likes_count": likes_count,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", project_id).execute()
        except Exception as sync_err:
            logger.warning(f"Fallback likes sync warning: {sync_err}")
            # Fetch updated likes count
            p_res = supabase.table("kiosk_projects").select("likes_count").eq("id", project_id).execute()
            likes_count = p_res.data[0].get("likes_count", 0) if p_res.data else 0

        return {
            "status": "success",
            "is_liked": is_liked,
            "likes_count": likes_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling like on project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="فشل تحديث الإعجاب")

@router.post("/projects/{project_id}/reviews")
async def submit_project_review(
    project_id: str,
    payload: CreateReviewRequest,
    profile=Depends(get_user_profile)
):
    """
    Submits or updates student's star rating and review.
    """
    user_id = profile["id"]
    user_name = profile.get("full_name") or "طالب جامعي"
    
    # Safely parse user faculty from device metadata
    user_faculty = ""
    try:
        raw_meta = profile.get("device_metadata")
        if isinstance(raw_meta, str):
            raw_meta = json.loads(raw_meta)
        if isinstance(raw_meta, dict):
            user_faculty = raw_meta.get("academic_profile", {}).get("major") or ""
    except Exception:
        user_faculty = ""
        
    supabase = get_supabase()

    # Prevent owner from reviewing own project & check project existence
    p_check = supabase.table("kiosk_projects").select("owner_id").eq("id", project_id).execute()
    if not p_check.data:
        raise HTTPException(status_code=404, detail="المشروع غير موجود")
    if p_check.data[0].get("owner_id") == user_id:
        raise HTTPException(status_code=400, detail="لا يمكنك تقييم مشروعك الخاص")

    review_data = {
        "project_id": project_id,
        "user_id": user_id,
        "user_name": user_name,
        "user_faculty": user_faculty or "",
        "rating": payload.rating,
        "comment": payload.comment.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        # Check existing review
        existing = supabase.table("kiosk_project_reviews") \
            .select("id") \
            .eq("project_id", project_id) \
            .eq("user_id", user_id) \
            .execute()

        if existing.data:
            res = supabase.table("kiosk_project_reviews") \
                .update(review_data) \
                .eq("id", existing.data[0]["id"]) \
                .execute()
        else:
            review_data["created_at"] = datetime.now(timezone.utc).isoformat()
            res = supabase.table("kiosk_project_reviews").insert(review_data).execute()

        # Resilient sync: calculate stats in Python as safeguard
        try:
            r_all = supabase.table("kiosk_project_reviews").select("rating").eq("project_id", project_id).execute()
            r_rows = r_all.data or []
            r_cnt = len(r_rows)
            r_avg = round(sum(r["rating"] for r in r_rows) / r_cnt, 2) if r_cnt > 0 else 0.0
            supabase.table("kiosk_projects").update({
                "rating_avg": r_avg,
                "rating_count": r_cnt,
                "comments_count": r_cnt,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", project_id).execute()
        except Exception as sync_err:
            logger.warning(f"Fallback rating sync warning: {sync_err}")

        # Fetch updated project rating
        p_res = supabase.table("kiosk_projects").select("rating_avg, rating_count, comments_count").eq("id", project_id).execute()
        stats = p_res.data[0] if p_res.data else {}

        return {
            "status": "success",
            "message": "شكراً لمشاركتك تقييمك وتجربتك! ⭐",
            "review": res.data[0] if res.data else review_data,
            "project_stats": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting review for project {project_id}: {e}")
        raise HTTPException(status_code=500, detail="فشل حفظ التقييم")

# ==================== Admin Management Endpoints ====================

@router.get("/admin/projects")
async def admin_list_kiosk_projects(
    status: Optional[str] = Query(None, description="all, pending, approved, rejected"),
    admin=Depends(verify_admin)
):
    """
    Admin reviews all submitted kiosk projects.
    """
    supabase = get_supabase()
    try:
        query = supabase.table("kiosk_projects").select("*").order("created_at", desc=True)
        if status and status != "all":
            query = query.eq("status", status)
        res = query.execute()
        return {"status": "success", "projects": res.data or []}
    except Exception as e:
        logger.error(f"Admin error listing kiosk projects: {e}")
        raise HTTPException(status_code=500, detail="فشل جلب المشاريع للإدارة")

@router.patch("/admin/projects/{project_id}/status")
async def admin_update_project_status(
    project_id: str,
    payload: UpdateProjectStatusRequest,
    admin=Depends(verify_admin)
):
    """
    Admin approves or rejects a kiosk project.
    """
    if payload.status not in ["approved", "rejected", "pending", "archived"]:
        raise HTTPException(status_code=400, detail="الحالة المطلوبة غير صالحة")

    supabase = get_supabase()
    update_data = {
        "status": payload.status,
        "rejection_reason": payload.rejection_reason.strip() if payload.status == "rejected" else "",
        "updated_at": datetime.now(timezone.utc).isoformat()
    }

    try:
        res = supabase.table("kiosk_projects").update(update_data).eq("id", project_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="المشروع غير موجود")

        msg = "تم اعتماد المشروع ونشره في الكشك بنجاح! 🚀" if payload.status == "approved" else "تم تحديث حالة المشروع"

        # Send in-app notification to the project owner
        try:
            owner_id = res.data[0].get("owner_id")
            p_title = res.data[0].get("title", "مشروعك")
            notif_msg = None
            if payload.status == "approved":
                notif_msg = f"🎉 مبروك! تم اعتماد ونشر مشروعك '{p_title}' في كشك المشاريع بنجاح."
            elif payload.status == "rejected":
                reason = payload.rejection_reason or "يرجى مراجعة تفاصيل المشروع"
                notif_msg = f"نعتذر، تم رفض مشروعك '{p_title}'. السبب: {reason}"

            if notif_msg and owner_id:
                supabase.table("user_notifications").insert({
                    "user_id": owner_id,
                    "title": "كشك المشاريع الطلابية",
                    "message": notif_msg,
                    "type": "kiosk",
                    "created_at": datetime.now(timezone.utc).isoformat()
                }).execute()
        except Exception as notif_err:
            logger.warning(f"Could not send kiosk notification: {notif_err}")

        return {
            "status": "success",
            "message": msg,
            "project": res.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating project status {project_id}: {e}")
        raise HTTPException(status_code=500, detail="فشل تحديث حالة المشروع")
