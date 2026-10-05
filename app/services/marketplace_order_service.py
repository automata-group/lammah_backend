import os
import sqlite3
import uuid
import re
import fitz
import arabic_reshaper
from bidi.algorithm import get_display
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from app.db.supabase_client import get_supabase
from app.core.logger import logger

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "marketplace_orders.db")
WATERMARKED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "outputs", "watermarked")

def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(WATERMARKED_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS marketplace_orders (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL,
                notebook_title TEXT NOT NULL,
                teacher_name TEXT DEFAULT '',
                seller_id TEXT NOT NULL,
                seller_name TEXT DEFAULT '',
                seller_username TEXT DEFAULT '',
                seller_reflect TEXT DEFAULT '',
                buyer_id TEXT NOT NULL,
                buyer_name TEXT NOT NULL,
                buyer_email TEXT DEFAULT '',
                price REAL NOT NULL,
                sender_name TEXT NOT NULL,
                reference_code TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                watermarked_pdf_path TEXT DEFAULT ''
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_buyer_id ON marketplace_orders(buyer_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seller_id ON marketplace_orders(seller_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON marketplace_orders(status);")
        conn.commit()
    finally:
        conn.close()

# Initialize local fallback DB on load
init_db()

def create_order(
    notebook_id: str,
    buyer_id: str,
    buyer_name: str,
    buyer_email: str,
    sender_name: str,
    reference_code: str = ""
) -> Dict[str, Any]:
    """Creates a new P2P purchase request from a buyer to a seller in Supabase (with SQLite fallback)."""
    supabase = get_supabase()
    
    # 1. Fetch notebook details
    nb_res = supabase.table("notebook_marketplace").select("*").eq("id", notebook_id).execute()
    if not nb_res.data:
        raise ValueError("الملزمة المطلوبة غير متوفرة في المتجر.")
    
    nb = nb_res.data[0]
    seller_id = nb["publisher_id"]
    
    if seller_id == buyer_id:
        raise ValueError("لا يمكنك شراء ملزمتك الخاصة، أنت ناشرها بالفعل.")

    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    seller_name = (nb.get("student_name") or "صاحب الملزمة").split("(")[0].strip().replace("@", "")

    # Try Supabase first
    try:
        # Check approved
        app_res = supabase.table("marketplace_orders").select("*") \
            .eq("notebook_id", notebook_id).eq("buyer_id", buyer_id).eq("status", "approved").execute()
        if app_res.data:
            return app_res.data[0]

        # Check pending
        pen_res = supabase.table("marketplace_orders").select("*") \
            .eq("notebook_id", notebook_id).eq("buyer_id", buyer_id).eq("status", "pending").execute()
        if pen_res.data:
            up_res = supabase.table("marketplace_orders").update({
                "sender_name": sender_name,
                "reference_code": reference_code,
                "updated_at": now
            }).eq("id", pen_res.data[0]["id"]).execute()
            if up_res.data:
                return up_res.data[0]

        order_data = {
            "id": order_id,
            "notebook_id": notebook_id,
            "notebook_title": nb.get("title", "ملزمة دراسية"),
            "teacher_name": nb.get("teacher_name", ""),
            "seller_id": seller_id,
            "seller_name": seller_name,
            "seller_username": nb.get("publisher_username", ""),
            "seller_reflect": nb.get("reflect_details", ""),
            "buyer_id": buyer_id,
            "buyer_name": buyer_name,
            "buyer_email": buyer_email,
            "price": float(nb.get("price", 0.0)),
            "sender_name": sender_name,
            "reference_code": reference_code,
            "status": "pending",
            "watermarked_pdf_path": "",
            "created_at": now,
            "updated_at": now
        }
        ins_res = supabase.table("marketplace_orders").insert(order_data).execute()
        if ins_res.data:
            return ins_res.data[0]
    except Exception as sb_err:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase order insert failed, using SQLite fallback: {sb_err}")

    # Fallback: SQLite
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM marketplace_orders WHERE notebook_id = ? AND buyer_id = ? AND status = 'approved'",
            (notebook_id, buyer_id)
        ).fetchone()
        if existing:
            return dict(existing)

        pending = conn.execute(
            "SELECT * FROM marketplace_orders WHERE notebook_id = ? AND buyer_id = ? AND status = 'pending'",
            (notebook_id, buyer_id)
        ).fetchone()
        if pending:
            conn.execute(
                "UPDATE marketplace_orders SET sender_name = ?, reference_code = ?, updated_at = ? WHERE id = ?",
                (sender_name, reference_code, now, pending["id"])
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (pending["id"],)).fetchone()
            return dict(updated)

        conn.execute("""
            INSERT INTO marketplace_orders (
                id, notebook_id, notebook_title, teacher_name,
                seller_id, seller_name, seller_username, seller_reflect,
                buyer_id, buyer_name, buyer_email, price,
                sender_name, reference_code, status,
                created_at, updated_at, watermarked_pdf_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, '')
        """, (
            order_id,
            notebook_id,
            nb.get("title", "ملزمة دراسية"),
            nb.get("teacher_name", ""),
            seller_id,
            seller_name,
            nb.get("publisher_username", ""),
            nb.get("reflect_details", ""),
            buyer_id,
            buyer_name,
            buyer_email,
            float(nb.get("price", 0.0)),
            sender_name,
            reference_code,
            now,
            now
        ))
        conn.commit()
        created = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
        return dict(created)
    finally:
        conn.close()

def get_buyer_purchases(buyer_id: str) -> List[Dict[str, Any]]:
    """Returns all purchase orders submitted by a buyer."""
    supabase = get_supabase()
    try:
        res = supabase.table("marketplace_orders").select("*").eq("buyer_id", buyer_id).order("created_at", desc=True).execute()
        if res.data is not None:
            return res.data
    except Exception as e:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase get_buyer_purchases failed, falling back to SQLite: {e}")

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_orders WHERE buyer_id = ? ORDER BY created_at DESC",
            (buyer_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_seller_orders(seller_id: str) -> Dict[str, Any]:
    """Returns incoming orders for a seller with summary metrics."""
    orders: List[Dict[str, Any]] = []
    supabase = get_supabase()
    try:
        res = supabase.table("marketplace_orders").select("*").eq("seller_id", seller_id).order("created_at", desc=True).execute()
        if res.data is not None:
            orders = res.data
    except Exception as e:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase get_seller_orders failed, falling back to SQLite: {e}")

    if not orders:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM marketplace_orders WHERE seller_id = ? ORDER BY created_at DESC",
                (seller_id,)
            ).fetchall()
            orders = [dict(r) for r in rows]
        finally:
            conn.close()

    pending_count = sum(1 for o in orders if o.get("status") == "pending")
    approved_count = sum(1 for o in orders if o.get("status") == "approved")
    total_earnings = sum(float(o.get("price") or 0.0) for o in orders if o.get("status") == "approved")

    return {
        "orders": orders,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "total_earnings": total_earnings
    }

def approve_order(order_id: str, seller_id: str) -> Dict[str, Any]:
    """Seller approves payment. Generates watermarked PDF and increments sales_count."""
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("طلب الشراء غير موجود.")
    if order["seller_id"] != seller_id:
        raise PermissionError("ليس لديك صلاحية لتأكيد هذا الطلب.")

    now = datetime.now(timezone.utc).isoformat()
    supabase = get_supabase()

    # 1. Increment sales count on notebook_marketplace
    try:
        cur_nb = supabase.table("notebook_marketplace").select("sales_count").eq("id", order["notebook_id"]).execute()
        if cur_nb.data:
            cnt = (cur_nb.data[0].get("sales_count") or 0) + 1
            supabase.table("notebook_marketplace").update({"sales_count": cnt}).eq("id", order["notebook_id"]).execute()
    except Exception as e:
        logger.error(f"Failed to increment sales_count: {e}")

    # 2. Trigger dynamic watermark generation
    watermark_path = ""
    try:
        watermark_path = generate_watermarked_pdf_for_order(order_id)
    except Exception as e:
        logger.error(f"Failed to generate watermark upon approval: {e}")

    # 3. Update status in Supabase
    try:
        up_res = supabase.table("marketplace_orders").update({
            "status": "approved",
            "updated_at": now,
            "watermarked_pdf_path": watermark_path
        }).eq("id", order_id).execute()
        if up_res.data:
            return up_res.data[0]
    except Exception as e:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase approve_order failed, falling back to SQLite: {e}")

    # SQLite Fallback
    conn = get_connection()
    try:
        conn.execute("UPDATE marketplace_orders SET status = 'approved', updated_at = ?, watermarked_pdf_path = ? WHERE id = ?", (now, watermark_path, order_id))
        conn.commit()
        updated = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
        return dict(updated)
    finally:
        conn.close()

def reject_order(order_id: str, seller_id: str, reason: str = "") -> Dict[str, Any]:
    """Seller rejects an unverified or invalid payment order."""
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("طلب الشراء غير موجود.")
    if order["seller_id"] != seller_id:
        raise PermissionError("ليس لديك صلاحية لتعديل هذا الطلب.")

    now = datetime.now(timezone.utc).isoformat()
    supabase = get_supabase()

    try:
        up_res = supabase.table("marketplace_orders").update({
            "status": "rejected",
            "updated_at": now
        }).eq("id", order_id).execute()
        if up_res.data:
            return up_res.data[0]
    except Exception as e:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase reject_order failed, falling back to SQLite: {e}")

    conn = get_connection()
    try:
        conn.execute("UPDATE marketplace_orders SET status = 'rejected', updated_at = ? WHERE id = ?", (now, order_id))
        conn.commit()
        updated = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
        return dict(updated)
    finally:
        conn.close()

def get_order_by_id(order_id: str) -> Optional[Dict[str, Any]]:
    supabase = get_supabase()
    try:
        res = supabase.table("marketplace_orders").select("*").eq("id", order_id).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        logger.warning(f"[MARKETPLACE_ORDER] Supabase get_order_by_id failed, falling back to SQLite: {e}")

    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM marketplace_orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def resolve_source_pdf(pdf_url: str) -> Optional[str]:
    """Finds the source PDF on disk from notebook pdf_url."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    filename = os.path.basename(pdf_url.split("?")[0])
    candidates = [
        os.path.join(base_dir, "outputs", filename),
        os.path.join(base_dir, "backend", "outputs", filename),
    ]
    
    match = re.search(r"notebook_[a-zA-Z0-9_-]+", pdf_url)
    if match:
        job_name = f"{match.group(0)}.pdf"
        candidates.extend([
            os.path.join(base_dir, "outputs", job_name),
            os.path.join(base_dir, "backend", "outputs", job_name),
        ])
        
    for p in candidates:
        if os.path.exists(p):
            return p
            
    outputs_dir = os.path.join(base_dir, "outputs")
    if os.path.exists(outputs_dir) and match:
        target = match.group(0).lower()
        for f in os.listdir(outputs_dir):
            if target in f.lower() and f.endswith(".pdf"):
                return os.path.join(outputs_dir, f)
                
    return None

def generate_watermarked_pdf_for_order(order_id: str) -> str:
    """
    Applies the Anti-Leak Dynamic Watermark to every page of the purchased notebook.
    Embeds:
    1. Lock & student ownership security footer on every page
    2. Subtle translucent diagonal watermark across page center
    3. Metadata stamping
    Always safely cleans up doc file descriptors in a finally block.
    """
    order = get_order_by_id(order_id)
    if not order:
        raise ValueError("الطلب غير موجود")
        
    out_file = os.path.join(WATERMARKED_DIR, f"order_{order_id}_licensed.pdf")
    if os.path.exists(out_file) and os.path.getsize(out_file) > 1024:
        return out_file

    supabase = get_supabase()
    nb_res = supabase.table("notebook_marketplace").select("pdf_url, title").eq("id", order["notebook_id"]).execute()
    if not nb_res.data:
        raise ValueError("ملف الملزمة الأصلي غير متوفر.")
        
    pdf_url = nb_res.data[0]["pdf_url"]
    source_pdf = resolve_source_pdf(pdf_url)
    
    if not source_pdf or not os.path.exists(source_pdf):
        logger.warning(f"Could not resolve exact PDF for {pdf_url}, checking outputs...")
        outputs_dir = os.path.join(os.path.dirname(__file__), "..", "..", "outputs")
        if os.path.exists(outputs_dir):
            for f in os.listdir(outputs_dir):
                if f.endswith(".pdf") and not f.startswith("preview_"):
                    source_pdf = os.path.join(outputs_dir, f)
                    break
                    
    if not source_pdf or not os.path.exists(source_pdf):
        raise FileNotFoundError(f"لم يتم العثور على ملف الملزمة الأصلي: {pdf_url}")

    buyer_name = order.get("buyer_name") or order.get("sender_name") or "طالب مرخص"
    buyer_id_short = (order.get("buyer_id") or "usr_00000000")[:8]
    notebook_title = order.get("notebook_title") or "ملزمة دراسية"
    
    footer_raw = f"🔒 نسخة مرخصة حصرياً للطالب: {buyer_name} • حساب لمّاح: {buyer_id_short} • يمنع نشر أو تداول الملف تحت طائلة المسؤولية"
    diag_raw = f"نسخة مرخصة للطالب: {buyer_name} • لمّاح"
    
    reshaped_footer = get_display(arabic_reshaper.reshape(footer_raw))
    reshaped_diag = get_display(arabic_reshaper.reshape(diag_raw))
    
    font_path = "C:/Windows/Fonts/arial.ttf"
    if not os.path.exists(font_path):
        for alt in ["C:/Windows/Fonts/seguiemj.ttf", "C:/Windows/Fonts/tahoma.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
            if os.path.exists(alt):
                font_path = alt
                break

    doc = fitz.open(source_pdf)
    try:
        for page in doc:
            try:
                page.insert_font(fontname="f_ar", fontfile=font_path)
                
                # 1. Diagonal translucent watermark in center of page
                cx = page.rect.width / 2
                cy = page.rect.height / 2
                rot_mat = fitz.Matrix(45)
                page.insert_text(
                    fitz.Point(cx - 130, cy),
                    reshaped_diag,
                    fontname="f_ar",
                    fontsize=15,
                    morph=(fitz.Point(cx, cy), rot_mat),
                    color=(0.82, 0.82, 0.86)
                )
                
                # 2. Bottom security ribbon
                footer_rect = fitz.Rect(15, page.rect.height - 24, page.rect.width - 15, page.rect.height - 6)
                page.draw_rect(footer_rect, color=(0.90, 0.92, 0.96), fill=(0.95, 0.96, 0.99), width=0.5)
                page.insert_text(
                    fitz.Point(25, page.rect.height - 13),
                    reshaped_footer,
                    fontname="f_ar",
                    fontsize=7.5,
                    color=(0.28, 0.30, 0.45)
                )
            except Exception as pe:
                logger.warning(f"Error watermarking page: {pe}")

        doc.set_metadata({
            "title": notebook_title,
            "author": f"{buyer_name} (نسخة مرخصة)",
            "subject": "ملزمة دراسية موثقة عبر منصة لمّاح",
            "keywords": f"Lammah, Licensed to {buyer_name}, ID: {buyer_id_short}"
        })
        
        doc.save(out_file, garbage=4, deflate=True)
    finally:
        doc.close()
        
    return out_file
