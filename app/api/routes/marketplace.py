import os
import uuid
import re
import json
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from app.core.auth_guard import check_quota, get_user_profile
from app.db.supabase_client import get_supabase
from app.core.logger import logger
from app.services.marketplace_order_service import (
    create_order,
    get_buyer_purchases,
    get_seller_orders,
    approve_order,
    reject_order,
    get_order_by_id,
    generate_watermarked_pdf_for_order
)

router = APIRouter()

class SetSellerProfileRequest(BaseModel):
    full_name: str
    username: str

class PublishNotebookRequest(BaseModel):
    title: str
    teacher_name: str
    page_count: int
    price: float
    reflect_details: str
    pdf_url: str
    preview_pdf_url: str
    description: Optional[str] = ""
    student_name: Optional[str] = ""
    publisher_username: Optional[str] = ""
    covered_topics: Optional[str] = ""

@router.get("/check-username")
async def check_username(username: str = Query(...), exclude_user_id: Optional[str] = None):
    """
    Live validation endpoint for seller usernames.
    Checks syntax (3-20 chars, a-z, 0-9, _) and availability in database.
    """
    supabase = get_supabase()
    clean_username = username.strip().lstrip("@").lower()
    
    # 1. Syntax check
    if not clean_username or len(clean_username) < 3:
        return {
            "available": False,
            "username": clean_username,
            "message": "اسم المستخدم قصير جداً (3 أحرف على الأقل)"
        }
    if len(clean_username) > 20:
        return {
            "available": False,
            "username": clean_username,
            "message": "اسم المستخدم طويل جداً (حد أقصى 20 حرفاً)"
        }
    if not re.match(r"^[a-z0-9_]+$", clean_username):
        return {
            "available": False,
            "username": clean_username,
            "message": "يسمح فقط بالأحرف الإنجليزية والأرقام والشرطة السفلية (_) بدون مسافات أو رموز"
        }
    
    # Reserved usernames
    reserved = ["admin", "root", "support", "lammah", "moderator", "system", "marketplace", "api", "tutor", "anonymous", "null", "undefined"]
    if clean_username in reserved:
        return {
            "available": False,
            "username": clean_username,
            "message": "هذا الاسم محجوز للنظام، يرجى اختيار اسم آخر"
        }

    # 2. Check profiles table for existing username
    try:
        # Check direct username column if exists
        try:
            query = supabase.table("profiles").select("id").eq("username", clean_username)
            if exclude_user_id:
                query = query.neq("id", exclude_user_id)
            res = query.execute()
            if res.data and len(res.data) > 0:
                return {
                    "available": False,
                    "username": clean_username,
                    "message": "اسم المستخدم محجوز مسبقاً ❌، اختر يوزر نيم آخر"
                }
        except Exception:
            pass

        # Check device_metadata for {"seller_username": clean_username}
        try:
            meta_res = supabase.table("profiles").select("id, device_metadata").like("device_metadata", f'%"seller_username": "{clean_username}"%').execute()
            if meta_res.data:
                for row in meta_res.data:
                    if exclude_user_id and row["id"] == exclude_user_id:
                        continue
                    return {
                        "available": False,
                        "username": clean_username,
                        "message": "اسم المستخدم محجوز مسبقاً ❌، اختر يوزر نيم آخر"
                    }
        except Exception:
            pass

        # Check notebook_marketplace publisher_username or student_name
        try:
            nb_res = supabase.table("notebook_marketplace").select("publisher_id, student_name").like("student_name", f'%@{clean_username}%').execute()
            if nb_res.data:
                for row in nb_res.data:
                    if exclude_user_id and row.get("publisher_id") == exclude_user_id:
                        continue
                    return {
                        "available": False,
                        "username": clean_username,
                        "message": "اسم المستخدم محجوز مسبقاً ❌، اختر يوزر نيم آخر"
                    }
        except Exception:
            pass

        return {
            "available": True,
            "username": clean_username,
            "message": "اسم المستخدم متاح للاستخدام! ✅"
        }

    except Exception as e:
        logger.error(f"Error checking username availability: {e}")
        return {
            "available": True,
            "username": clean_username,
            "message": "اسم المستخدم متاح للاستخدام! ✅"
        }

@router.post("/set-seller-profile")
async def set_seller_profile(
    request: SetSellerProfileRequest,
    profile=Depends(get_user_profile)
):
    """
    Saves or updates the user's seller full name and unique username.
    """
    supabase = get_supabase()
    user_id = profile["id"]
    clean_username = request.username.strip().lstrip("@").lower()
    full_name = request.full_name.strip()
    
    if not full_name:
        raise HTTPException(status_code=400, detail="يرجى إدخال اسم البائع.")
        
    check = await check_username(clean_username, exclude_user_id=user_id)
    if not check["available"]:
        raise HTTPException(status_code=400, detail=check["message"])

    # Update profiles
    update_dict = {"full_name": full_name}
    
    # Update device_metadata JSON with seller_username
    raw_meta = profile.get("device_metadata") or "{}"
    try:
        meta_dict = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
    except Exception:
        meta_dict = {}
    meta_dict["seller_username"] = clean_username
    update_dict["device_metadata"] = json.dumps(meta_dict)

    # First attempt updating username column directly
    try:
        supabase.table("profiles").update({**update_dict, "username": clean_username}).eq("id", user_id).execute()
    except Exception as e:
        logger.warning(f"profiles lacks username column, updating device_metadata fallback: {e}")
        supabase.table("profiles").update(update_dict).eq("id", user_id).execute()

    return {
        "status": "success",
        "full_name": full_name,
        "username": clean_username,
        "message": "تم حفظ ملف البائع وتأكيد اليوزر نيم بنجاح!"
    }

@router.post("/publish")
async def publish_notebook(
    request: PublishNotebookRequest,
    profile=Depends(get_user_profile)  # Any user with an active account can publish
):
    """Publishes a generated notebook to the marketplace."""
    supabase = get_supabase()
    user_id = profile["id"]
    clean_username = (request.publisher_username or "").strip().lstrip("@").lower()
    
    try:
        notebook_data = {
            "id": str(uuid.uuid4()),
            "publisher_id": user_id,
            "title": request.title,
            "teacher_name": request.teacher_name,
            "page_count": request.page_count,
            "price": request.price,
            "reflect_details": request.reflect_details,
            "pdf_url": request.pdf_url,
            "preview_pdf_url": request.preview_pdf_url,
            "description": request.description or "",
            "student_name": request.student_name or "",
            "publisher_username": clean_username,
            "covered_topics": request.covered_topics or "",
            "views_count": 0,
            "sales_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        while True:
            try:
                result = supabase.table("notebook_marketplace").insert(notebook_data).execute()
                break
            except Exception as db_err:
                err_str = str(db_err)
                if "publisher_username" in err_str and "publisher_username" in notebook_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'publisher_username' column. Skipping it...")
                    del notebook_data["publisher_username"]
                elif "covered_topics" in err_str and "covered_topics" in notebook_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'covered_topics' column. Skipping it...")
                    del notebook_data["covered_topics"]
                elif "description" in err_str and "description" in notebook_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'description' column. Bypassing description during publish...")
                    del notebook_data["description"]
                else:
                    raise db_err
                
        return {"status": "success", "notebook": result.data[0]}
    except Exception as e:
        logger.error(f"Failed to publish notebook: {e}")
        raise HTTPException(status_code=500, detail=f"فشل في نشر الدفتر في المتجر: {str(e)}")

@router.get("/notebooks")
async def get_marketplace_notebooks():
    """Fetches all published notebooks with publisher details."""
    supabase = get_supabase()
    try:
        # We join with profiles to get publisher name and metadata
        try:
            result = supabase.table("notebook_marketplace") \
                .select("*, profiles:publisher_id(full_name, device_metadata)") \
                .order("created_at", desc=True) \
                .execute()
        except Exception:
            result = supabase.table("notebook_marketplace") \
                .select("*, profiles:publisher_id(full_name)") \
                .order("created_at", desc=True) \
                .execute()
            
        notebooks = []
        for nb in (result.data or []):
            prof = nb.get("profiles") or {}
            
            # Extract username
            username = nb.get("publisher_username") or ""
            if not username and prof:
                username = prof.get("username") or ""
                if not username and prof.get("device_metadata"):
                    try:
                        meta = json.loads(prof["device_metadata"]) if isinstance(prof["device_metadata"], str) else prof["device_metadata"]
                        username = meta.get("seller_username") or ""
                    except Exception:
                        pass
            
            raw_student = nb.get("student_name") or ""
            if not username and "@" in raw_student:
                match = re.search(r"@([a-zA-Z0-9_]+)", raw_student)
                if match:
                    username = match.group(1)
            
            # Clean author display name
            clean_name = raw_student
            clean_name = re.sub(r"@([a-zA-Z0-9_]+)", "", clean_name)
            clean_name = re.sub(r"\([^)]*\)", "", clean_name).strip()
            if not clean_name:
                clean_name = prof.get("full_name") or "طالب متميز"

            nb["author_name"] = clean_name
            nb["author_username"] = username.lower() if username else ""
            notebooks.append(nb)

        return {"status": "success", "notebooks": notebooks}
    except Exception as e:
        logger.error(f"Failed to fetch notebooks: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب الدفاتر من المتجر.")

@router.post("/{notebook_id}/view")
async def increment_notebook_view(notebook_id: str):
    """Increments the view count of a notebook when a user opens the preview."""
    supabase = get_supabase()
    try:
        # Fetch current views
        current = supabase.table("notebook_marketplace").select("views_count").eq("id", notebook_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="الدفتر غير موجود.")
            
        views = current.data[0].get("views_count", 0) + 1
        
        # Update views
        supabase.table("notebook_marketplace").update({"views_count": views}).eq("id", notebook_id).execute()
        return {"status": "success", "views_count": views}
    except Exception as e:
        logger.error(f"Failed to increment views: {e}")
        return {"status": "error"}

@router.get("/my-notebooks")
async def get_my_published_notebooks(profile=Depends(get_user_profile)):
    """Fetches all notebooks published by the logged-in student."""
    supabase = get_supabase()
    try:
        result = supabase.table("notebook_marketplace") \
            .select("*") \
            .eq("publisher_id", profile["id"]) \
            .order("created_at", desc=True) \
            .execute()
        return {"status": "success", "notebooks": result.data}
    except Exception as e:
        logger.error(f"Failed to fetch student's own notebooks: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب دفاترك.")

@router.delete("/{notebook_id}")
async def delete_my_notebook(notebook_id: str, profile=Depends(get_user_profile)):
    """Allows a student to delete their own notebook from the marketplace."""
    supabase = get_supabase()
    try:
        # Verify ownership
        current = supabase.table("notebook_marketplace").select("publisher_id").eq("id", notebook_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="الدفتر غير موجود.")
        
        if current.data[0]["publisher_id"] != profile["id"]:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لحذف هذا الدفتر.")
            
        supabase.table("notebook_marketplace").delete().eq("id", notebook_id).execute()
        return {"status": "success", "message": "تم حذف الدفتر بنجاح."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to delete notebook: {e}")
        raise HTTPException(status_code=500, detail="فشل حذف الدفتر.")

class EditNotebookRequest(BaseModel):
    title: Optional[str] = None
    teacher_name: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    student_name: Optional[str] = None
    reflect_details: Optional[str] = None
    covered_topics: Optional[str] = None

@router.put("/{notebook_id}")
async def edit_my_notebook(
    notebook_id: str,
    request: EditNotebookRequest,
    profile=Depends(get_user_profile)
):
    """Allows a student to edit their own notebook details."""
    supabase = get_supabase()
    try:
        # Verify ownership
        current = supabase.table("notebook_marketplace").select("publisher_id").eq("id", notebook_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="الدفتر غير موجود.")
        
        if current.data[0]["publisher_id"] != profile["id"]:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتعديل هذا الدفتر.")
            
        update_data = {}
        if request.title is not None:
            update_data["title"] = request.title
        if request.teacher_name is not None:
            update_data["teacher_name"] = request.teacher_name
        if request.price is not None:
            update_data["price"] = request.price
        if request.description is not None:
            update_data["description"] = request.description
        if request.student_name is not None:
            update_data["student_name"] = request.student_name
        if request.reflect_details is not None:
            update_data["reflect_details"] = request.reflect_details
        if request.covered_topics is not None:
            update_data["covered_topics"] = request.covered_topics
            
        if update_data:
            try:
                supabase.table("notebook_marketplace").update(update_data).eq("id", notebook_id).execute()
            except Exception as db_err:
                err_str = str(db_err)
                if "covered_topics" in err_str and "covered_topics" in update_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'covered_topics' column. Skipping it during update...")
                    del update_data["covered_topics"]
                    if update_data:
                        supabase.table("notebook_marketplace").update(update_data).eq("id", notebook_id).execute()
                elif "description" in err_str and "description" in update_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'description' column. Bypassing description during update...")
                    del update_data["description"]
                    if update_data:
                        supabase.table("notebook_marketplace").update(update_data).eq("id", notebook_id).execute()
                else:
                    raise db_err
            
        return {"status": "success", "message": "تم تعديل الدفتر بنجاح."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to edit notebook: {e}")
        raise HTTPException(status_code=500, detail="فشل تعديل الدفتر.")

# --- P2P Marketplace Orders & Anti-Leak Watermark Protection ---

class PurchaseRequestModel(BaseModel):
    notebook_id: str
    sender_name: str
    reference_code: Optional[str] = ""

@router.post("/purchase-request")
async def submit_purchase_request(
    request: PurchaseRequestModel,
    profile=Depends(get_user_profile)
):
    """
    Submits a P2P purchase request from buyer to notebook publisher.
    Stores transfer proof/sender name and alerts seller for verification.
    """
    buyer_id = profile["id"]
    buyer_name = profile.get("full_name") or request.sender_name.strip()
    buyer_email = profile.get("email") or ""
    
    if not request.sender_name.strip():
        raise HTTPException(status_code=400, detail="يرجى كتابة اسمك أو الرقم المرجعي للحوالة.")
        
    try:
        order = create_order(
            notebook_id=request.notebook_id,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            sender_name=request.sender_name.strip(),
            reference_code=(request.reference_code or "").strip()
        )
        return {
            "status": "success",
            "order": order,
            "message": "تم تسجيل طلب الشراء وإشعار البائع بنجاح! سيتم فتح نسختك المرخصة فور التحقق."
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"Failed to submit purchase request: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تسجيل طلب الشراء.")

@router.get("/my-purchases")
async def get_my_purchases(profile=Depends(get_user_profile)):
    """Returns all purchase orders placed by the current student."""
    try:
        purchases = get_buyer_purchases(profile["id"])
        return {"status": "success", "purchases": purchases}
    except Exception as e:
        logger.error(f"Failed to fetch student purchases: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب قائمة الدفاتر المشتراة.")

@router.get("/seller-orders")
async def get_my_seller_orders(profile=Depends(get_user_profile)):
    """Returns incoming purchase orders for the seller with summary metrics."""
    try:
        data = get_seller_orders(profile["id"])
        return {"status": "success", **data}
    except Exception as e:
        logger.error(f"Failed to fetch seller orders: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب طلبات الشراء الواردة.")

@router.post("/order/{order_id}/approve")
async def approve_purchase_order(
    order_id: str,
    profile=Depends(get_user_profile)
):
    """
    Seller approves buyer's payment.
    Increments sales_count and generates anti-leak watermarked PDF for buyer.
    """
    try:
        updated = approve_order(order_id, profile["id"])
        return {
            "status": "success",
            "order": updated,
            "message": "تم تأكيد استلام الحوالة بنجاح! تم فتح النسخة المرخصة للطالب مع علامته المائية الموثقة."
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        logger.error(f"Failed to approve order: {e}")
        raise HTTPException(status_code=500, detail="فشل في تأكيد طلب الشراء.")

@router.post("/order/{order_id}/reject")
async def reject_purchase_order(
    order_id: str,
    profile=Depends(get_user_profile)
):
    """Seller rejects an invalid or unreceived transfer."""
    try:
        updated = reject_order(order_id, profile["id"])
        return {
            "status": "success",
            "order": updated,
            "message": "تم رفض طلب الشراء."
        }
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        logger.error(f"Failed to reject order: {e}")
        raise HTTPException(status_code=500, detail="فشل في رفض طلب الشراء.")

@router.get("/order/{order_id}/download")
async def download_watermarked_notebook(
    order_id: str,
    profile=Depends(get_user_profile)
):
    """
    Serves the anti-leak dynamically watermarked PDF to the authorized buyer.
    Stamps student name, ID, and anti-leak watermark across all pages.
    """
    order = get_order_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="طلب الشراء غير موجود.")
        
    user_id = profile["id"]
    # Allow buyer, seller, or platform admin
    if order["buyer_id"] != user_id and order["seller_id"] != user_id:
        raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتحميل هذه النسخة.")
        
    if order["status"] != "approved" and order["seller_id"] != user_id:
        raise HTTPException(status_code=400, detail="لم يتم تأكيد هذا الطلب من قبل البائع بعد.")

    try:
        pdf_path = order.get("watermarked_pdf_path")
        if not pdf_path or not os.path.exists(pdf_path):
            pdf_path = generate_watermarked_pdf_for_order(order_id)
            
        clean_title = re.sub(r'[\\/*?:"<>| ]', '_', order.get("notebook_title") or "notebook")
        filename = f"Lammah_{clean_title}_{order_id[:6]}.pdf"
        
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=filename,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Failed to deliver watermarked PDF for order {order_id}: {e}")
        raise HTTPException(status_code=500, detail="حدث خطأ أثناء تحضير النسخة المرخصة للدفتر.")

