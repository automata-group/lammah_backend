import os
import uuid
from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from app.core.auth_guard import check_quota, get_user_profile
from app.services.subscription_service import get_supabase
from app.core.logger import logger

router = APIRouter()

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

@router.post("/publish")
async def publish_notebook(
    request: PublishNotebookRequest,
    profile=Depends(check_quota)  # Any user with an active account can publish
):
    """Publishes a generated notebook to the marketplace."""
    supabase = get_supabase()
    user_id = profile["id"]
    
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
            "views_count": 0,
            "sales_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            result = supabase.table("notebook_marketplace").insert(notebook_data).execute()
        except Exception as db_err:
            if "description" in str(db_err) and "description" in notebook_data:
                logger.warning("Supabase table 'notebook_marketplace' lacks 'description' column. Bypassing description during publish...")
                del notebook_data["description"]
                result = supabase.table("notebook_marketplace").insert(notebook_data).execute()
            else:
                raise db_err
                
        return {"status": "success", "notebook": result.data[0]}
    except Exception as e:
        logger.error(f"Failed to publish notebook: {e}")
        raise HTTPException(status_code=500, detail="فشل في نشر الملزمة في المتجر.")

@router.get("/notebooks")
async def get_marketplace_notebooks():
    """Fetches all published notebooks with publisher details."""
    supabase = get_supabase()
    try:
        # We join with profiles to get the publisher's name
        result = supabase.table("notebook_marketplace") \
            .select("*, profiles:publisher_id(full_name)") \
            .order("created_at", desc=True) \
            .execute()
            
        return {"status": "success", "notebooks": result.data}
    except Exception as e:
        logger.error(f"Failed to fetch notebooks: {e}")
        raise HTTPException(status_code=500, detail="فشل في جلب الملازم من المتجر.")

@router.post("/{notebook_id}/view")
async def increment_notebook_view(notebook_id: str):
    """Increments the view count of a notebook when a user opens the preview."""
    supabase = get_supabase()
    try:
        # Fetch current views
        current = supabase.table("notebook_marketplace").select("views_count").eq("id", notebook_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="الملزمة غير موجودة.")
            
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
        raise HTTPException(status_code=500, detail="فشل في جلب ملازمك.")

@router.delete("/{notebook_id}")
async def delete_my_notebook(notebook_id: str, profile=Depends(get_user_profile)):
    """Allows a student to delete their own notebook from the marketplace."""
    supabase = get_supabase()
    try:
        # Verify ownership
        current = supabase.table("notebook_marketplace").select("publisher_id").eq("id", notebook_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="الملزمة غير موجودة.")
        
        if current.data[0]["publisher_id"] != profile["id"]:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لحذف هذه الملزمة.")
            
        supabase.table("notebook_marketplace").delete().eq("id", notebook_id).execute()
        return {"status": "success", "message": "تم حذف الملزمة بنجاح."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to delete notebook: {e}")
        raise HTTPException(status_code=500, detail="فشل حذف الملزمة.")

class EditNotebookRequest(BaseModel):
    title: Optional[str] = None
    teacher_name: Optional[str] = None
    price: Optional[float] = None
    description: Optional[str] = None
    student_name: Optional[str] = None
    reflect_details: Optional[str] = None

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
            raise HTTPException(status_code=404, detail="الملزمة غير موجودة.")
        
        if current.data[0]["publisher_id"] != profile["id"]:
            raise HTTPException(status_code=403, detail="ليس لديك صلاحية لتعديل هذه الملزمة.")
            
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
            
        if update_data:
            try:
                supabase.table("notebook_marketplace").update(update_data).eq("id", notebook_id).execute()
            except Exception as db_err:
                if "description" in str(db_err) and "description" in update_data:
                    logger.warning("Supabase table 'notebook_marketplace' lacks 'description' column. Bypassing description during update...")
                    del update_data["description"]
                    if update_data:
                        supabase.table("notebook_marketplace").update(update_data).eq("id", notebook_id).execute()
                else:
                    raise db_err
            
        return {"status": "success", "message": "تم تعديل الملزمة بنجاح."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to edit notebook: {e}")
        raise HTTPException(status_code=500, detail="فشل تعديل الملزمة.")
