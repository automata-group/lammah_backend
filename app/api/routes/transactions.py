from fastapi import APIRouter, Depends, HTTPException, Body
from app.core.auth_guard import get_user_profile, verify_admin
from app.db.supabase_client import get_supabase
from app.core.logger import logger
from app.services.subscription_service import create_subscription
import random
import string

router = APIRouter()

def generate_short_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

@router.post("/checkout")
async def create_checkout_transaction(
    payload: dict = Body(...),
    profile: dict = Depends(get_user_profile)
):
    plan_id = payload.get("plan_id")
    if not plan_id:
        raise HTTPException(status_code=400, detail={"user_message": "يرجى تحديد الباقة"})
        
    supabase = get_supabase()
    
    try:
        plan_result = supabase.table("subscription_plans").select("*").eq("id", plan_id).execute()
        if not plan_result.data:
            raise HTTPException(status_code=404, detail={"user_message": "الباقة غير موجودة"})
            
        plan = plan_result.data[0]
        
        # Check if the user already has a pending transaction for this plan
        existing_tx = supabase.table("transactions")\
            .select("*")\
            .eq("user_id", profile["id"])\
            .eq("plan_id", plan_id)\
            .eq("status", "pending")\
            .execute()
            
        if existing_tx.data:
            tx = existing_tx.data[0]
            from datetime import datetime, timezone
            # Bump the transaction to the top of the admin dashboard by updating created_at
            supabase.table("transactions").update({
                "created_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", tx["id"]).execute()
            
            return {
                "order_id": tx["short_order_id"], 
                "amount": plan["price_ils"],
                "plan_name": plan["name"],
                "payment_number": "0598298930" # Configured Reflect number
            }
            
        short_id = generate_short_id()
        
        new_tx = {
            "short_order_id": short_id,
            "user_id": profile["id"],
            "plan_id": plan_id,
            "amount": str(plan["price_ils"]),
            "status": "pending",
            "payment_method": "reflect"
        }
        
        result = supabase.table("transactions").insert(new_tx).execute()
        
        return {
            "order_id": short_id, 
            "amount": plan["price_ils"],
            "plan_name": plan["name"],
            "payment_number": "0598298930" # Configured Reflect number
        }
    except Exception as e:
        logger.error(f"Failed to create transaction: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل إنشاء الطلب. تأكد من تحديث قاعدة البيانات وإضافة جدول transactions."})

@router.post("/update-name")
async def update_user_name(payload: dict = Body(...), profile: dict = Depends(get_user_profile)):
    """Updates the user's full name securely from the backend"""
    full_name = payload.get("full_name")
    if not full_name:
        raise HTTPException(status_code=400, detail={"user_message": "يرجى توفير الاسم"})
        
    supabase = get_supabase()
    try:
        supabase.table("profiles").update({"full_name": full_name}).eq("id", profile["id"]).execute()
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to update name: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل تحديث الاسم"})

@router.get("/admin/pending")
async def get_pending_transactions(profile: dict = Depends(verify_admin)):
    supabase = get_supabase()
    try:
        result = supabase.table("transactions")\
            .select("id, short_order_id, amount, status, created_at, profiles(full_name, phone_number), subscription_plans(name, name_en, price_ils)")\
            .eq("status", "pending")\
            .order("created_at", desc=True)\
            .execute()
        return result.data
    except Exception as e:
        logger.error(f"Failed to fetch pending transactions: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل جلب الطلبات. تأكد من تحديث قاعدة البيانات."})

@router.post("/admin/{tx_id}/approve")
async def approve_transaction(tx_id: str, profile: dict = Depends(verify_admin)):
    supabase = get_supabase()
    try:
        tx_result = supabase.table("transactions").select("*").eq("id", tx_id).execute()
        if not tx_result.data:
            raise HTTPException(status_code=404, detail={"user_message": "الطلب غير موجود"})
            
        tx = tx_result.data[0]
        if tx["status"] != "pending":
            raise HTTPException(status_code=400, detail={"user_message": "الطلب ليس في حالة انتظار"})
            
        # Update transaction status
        supabase.table("transactions").update({"status": "completed"}).eq("id", tx_id).execute()
        
        # Create subscription (this also adds quota)
        sub, error = create_subscription(user_id=tx["user_id"], plan_id=tx["plan_id"])
        if error:
            logger.error(f"Failed to create subscription during approval: {error}")
            # we could potentially rollback tx here, but it's a manual process MVP
            raise HTTPException(status_code=500, detail={"user_message": error})
            
        return {"status": "success", "message": "تم تأكيد الدفع وتفعيل الاشتراك بنجاح"}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to approve transaction: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل في تأكيد الطلب. تحقق من السجلات."})

@router.post("/admin/{tx_id}/reject")
async def reject_transaction(tx_id: str, profile: dict = Depends(verify_admin)):
    supabase = get_supabase()
    try:
        tx_result = supabase.table("transactions").select("*").eq("id", tx_id).execute()
        if not tx_result.data:
            raise HTTPException(status_code=404, detail={"user_message": "الطلب غير موجود"})
            
        tx = tx_result.data[0]
        if tx["status"] != "pending":
            raise HTTPException(status_code=400, detail={"user_message": "الطلب ليس في حالة انتظار"})
            
        # Update transaction status
        supabase.table("transactions").update({"status": "rejected"}).eq("id", tx_id).execute()
            
        return {"status": "success", "message": "تم رفض الطلب بنجاح"}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Failed to reject transaction: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل في رفض الطلب."})

@router.get("/admin/stats")
async def get_admin_stats(profile: dict = Depends(verify_admin)):
    supabase = get_supabase()
    try:
        # Get error logs from usage_logs
        errors_result = supabase.table("usage_logs")\
            .select("id, created_at, action_type, metadata, profiles(full_name, phone_number)")\
            .eq("status", "failed")\
            .order("created_at", desc=True)\
            .limit(20)\
            .execute()
            
        return {
            "status": "success",
            "errors": errors_result.data or [],
            "system_health": {
                "gemini": "Active (Google Free Tier)",
                "groq": "Active (Beta Free Tier)"
            }
        }
    except Exception as e:
        logger.error(f"Failed to fetch admin stats: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل جلب الإحصائيات."})

@router.post("/admin/bulk-approve")
async def bulk_approve_transactions(
    payload: dict = Body(...),
    profile: dict = Depends(verify_admin)
):
    tx_ids = payload.get("transaction_ids")
    if not tx_ids or not isinstance(tx_ids, list):
        raise HTTPException(status_code=400, detail={"user_message": "يرجى تحديد معرفات الطلبات"})
        
    supabase = get_supabase()
    results = {"approved": [], "failed": []}
    
    for tx_id in tx_ids:
        try:
            tx_result = supabase.table("transactions").select("*").eq("id", tx_id).execute()
            if not tx_result.data:
                results["failed"].append({"id": tx_id, "error": "الطلب غير موجود"})
                continue
                
            tx = tx_result.data[0]
            if tx["status"] != "pending":
                results["failed"].append({"id": tx_id, "error": "الطلب ليس في حالة انتظار"})
                continue
                
            # Update transaction status
            supabase.table("transactions").update({"status": "completed"}).eq("id", tx_id).execute()
            
            # Create subscription
            sub, error = create_subscription(user_id=tx["user_id"], plan_id=tx["plan_id"])
            if error:
                logger.error(f"Failed to create subscription during approval for tx {tx_id}: {error}")
                # Rollback status to pending on error
                supabase.table("transactions").update({"status": "pending"}).eq("id", tx_id).execute()
                results["failed"].append({"id": tx_id, "error": error})
                continue
                
            results["approved"].append(tx_id)
        except Exception as e:
            logger.error(f"Error bulk approving transaction {tx_id}: {str(e)}")
            results["failed"].append({"id": tx_id, "error": str(e)})
            
    return {"status": "success", "results": results}

@router.post("/admin/bulk-reject")
async def bulk_reject_transactions(
    payload: dict = Body(...),
    profile: dict = Depends(verify_admin)
):
    tx_ids = payload.get("transaction_ids")
    if not tx_ids or not isinstance(tx_ids, list):
        raise HTTPException(status_code=400, detail={"user_message": "يرجى تحديد معرفات الطلبات"})
        
    supabase = get_supabase()
    try:
        supabase.table("transactions")\
            .update({"status": "rejected"})\
            .in_("id", tx_ids)\
            .eq("status", "pending")\
            .execute()
            
        return {"status": "success", "message": "تم رفض الطلبات المحددة بنجاح"}
    except Exception as e:
        logger.error(f"Failed to bulk reject transactions: {str(e)}")
        raise HTTPException(status_code=500, detail={"user_message": "فشل رفض الطلبات المحددة."})

