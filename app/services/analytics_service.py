import sqlite3
import os
import json
import time
from datetime import datetime, timezone
import httpx
from typing import Dict, Any, List, Optional
from app.core.logger import logger

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "analytics.db")

# Cache for IP lookups in-memory to prevent duplicate network calls
IP_CACHE: Dict[str, Dict[str, str]] = {}

# Palestinian & Arab Cities translation dictionary to Arabic
GEO_ARABIC_MAP = {
    # Palestine
    "ramallah": "رام الله",
    "al-bireh": "البيرة",
    "nablus": "نابلس",
    "hebron": "الخليل",
    "jerusalem": "القدس",
    "gaza": "غزة",
    "jenin": "جنين",
    "tulkarm": "طولكرم",
    "bethlehem": "بيت لحم",
    "qalqilya": "قلقيلية",
    "salfit": "سلفيت",
    "tubas": "طوباس",
    "jericho": "أريحا",
    "khan yunis": "خان يونس",
    "rafah": "رفح",
    "deir al-balah": "دير البلح",
    # Countries
    "palestine": "فلسطين 🇵🇸",
    "state of palestine": "فلسطين 🇵🇸",
    "israel": "فلسطين المحتلة / القدس 🇵🇸",
    "jordan": "الأردن 🇯🇴",
    "amman": "عمّان",
    "irbid": "إربد",
    "zarqa": "الزرقاء",
    "egypt": "مصر 🇪🇬",
    "cairo": "القاهرة",
    "alexandria": "الإسكندرية",
    "saudi arabia": "السعودية 🇸🇦",
    "riyadh": "الرياض",
    "jeddah": "جدة",
    "united arab emirates": "الإمارات 🇦🇪",
    "dubai": "دبي",
    "abu dhabi": "أبوظبي",
    "turkey": "تركيا 🇹🇷",
    "qatar": "قطر 🇶🇦",
    "kuwait": "الكويت 🇰🇼",
    "germany": "ألمانيا 🇩🇪",
    "united states": "أمريكا 🇺🇸",
    "united kingdom": "بريطانيا 🇬🇧"
}

def translate_geo(name: str) -> str:
    if not name:
        return "غير محدد"
    cleaned = name.strip().lower()
    return GEO_ARABIC_MAP.get(cleaned, name)

def init_analytics_db():
    """Initializes high-performance SQLite database with WAL mode."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Enable Write-Ahead Logging for non-blocking concurrent writes
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        # Table 1: Sessions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT,
                user_email TEXT,
                ip TEXT,
                country TEXT,
                city TEXT,
                device_type TEXT,
                os_browser TEXT,
                screen_size TEXT,
                referrer TEXT,
                initial_path TEXT,
                current_path TEXT,
                max_scroll_depth INTEGER DEFAULT 0,
                duration_seconds INTEGER DEFAULT 0,
                is_drop_off INTEGER DEFAULT 0,
                drop_off_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table 2: Events & Actions (Timeline)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                event_name TEXT,
                event_arabic_title TEXT,
                event_type TEXT, -- pageview, click, rage_click, scroll, drop_off, feature
                page_path TEXT,
                details_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);")
        
        conn.commit()
        conn.close()
        logger.info("📊 In-house Analytics DB initialized successfully in WAL mode.")
    except Exception as e:
        logger.error(f"Error initializing analytics DB: {e}")

init_analytics_db()

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.row_factory = sqlite3.Row
    return conn

async def resolve_ip_geo(ip: str) -> Dict[str, str]:
    """Resolves IP to country and city in Arabic without third party cookies."""
    if not ip or ip in ("127.0.0.1", "localhost", "::1", "unknown"):
        return {"country": "فلسطين 🇵🇸 (محلي)", "city": "القدس / رام الله"}
    
    if ip in IP_CACHE:
        return IP_CACHE[ip]
    
    # Try fast IP-API (free, no token needed, returns JSON)
    try:
        async with httpx.AsyncClient(timeout=0.6) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields=status,country,city")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    country_raw = data.get("country", "")
                    city_raw = data.get("city", "")
                    country_ar = translate_geo(country_raw)
                    city_ar = translate_geo(city_raw)
                    res = {"country": country_ar, "city": city_ar}
                    IP_CACHE[ip] = res
                    return res
    except Exception as e:
        logger.warning(f"Geo-IP lookup error for {ip}: {e}")
    
    default_geo = {"country": "فلسطين 🇵🇸", "city": "غير محدد"}
    IP_CACHE[ip] = default_geo
    return default_geo

def record_telemetry(payload: Dict[str, Any], client_ip: str, geo: Dict[str, str]):
    """Records session update and events synchronously inside worker thread."""
    session_id = payload.get("session_id")
    if not session_id:
        return
    
    user_id = payload.get("user_id")
    user_email = payload.get("user_email")
    path = payload.get("path", "/")
    device = payload.get("device_type", "هاتف محمول")
    os_browser = payload.get("os_browser", "متصفح الويب")
    screen = payload.get("screen_size", "")
    referrer = payload.get("referrer", "مباشر")
    scroll = payload.get("scroll_depth", 0)
    duration = payload.get("duration", 0)
    events = payload.get("events", [])
    
    conn = get_db()
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Check if session exists
        cursor.execute("SELECT session_id, max_scroll_depth, duration_seconds FROM sessions WHERE session_id = ?", (session_id,))
        existing = cursor.fetchone()
        
        if existing:
            new_scroll = max(existing["max_scroll_depth"] or 0, scroll)
            new_dur = max(existing["duration_seconds"] or 0, duration)
            cursor.execute("""
                UPDATE sessions 
                SET user_id = COALESCE(?, user_id),
                    user_email = COALESCE(?, user_email),
                    current_path = ?,
                    max_scroll_depth = ?,
                    duration_seconds = ?,
                    updated_at = ?
                WHERE session_id = ?
            """, (user_id, user_email, path, new_scroll, new_dur, now_str, session_id))
        else:
            cursor.execute("""
                INSERT INTO sessions (
                    session_id, user_id, user_email, ip, country, city, 
                    device_type, os_browser, screen_size, referrer, initial_path, 
                    current_path, max_scroll_depth, duration_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, user_id, user_email, client_ip, geo["country"], geo["city"],
                device, os_browser, screen, referrer, path, path, scroll, duration,
                now_str, now_str
            ))
        
        # Insert events
        for ev in events:
            ev_name = ev.get("name", "حدث")
            ev_ar = ev.get("arabic_title", ev_name)
            ev_type = ev.get("type", "action")
            ev_path = ev.get("path", path)
            details_json = json.dumps(ev.get("details", {}), ensure_ascii=False)
            
            cursor.execute("""
                INSERT INTO events (session_id, event_name, event_arabic_title, event_type, page_path, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (session_id, ev_name, ev_ar, ev_type, ev_path, details_json, now_str))
            
            # Check for drop-off flag
            if ev_type == "drop_off":
                reason = ev.get("details", {}).get("reason", "خروج من صفحة الشراء أو الرفع")
                cursor.execute("UPDATE sessions SET is_drop_off = 1, drop_off_reason = ? WHERE session_id = ?", (reason, session_id))
        
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to record analytics telemetry: {e}")
    finally:
        conn.close()

def get_analytics_overview() -> Dict[str, Any]:
    """Computes comprehensive Arabic intelligence statistics for the owner."""
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        now_ts = time.time()
        # Active in last 10 minutes (approx)
        ten_mins_ago = datetime.fromtimestamp(now_ts - 600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
        
        # 1. Live Active Visitors
        cursor.execute("SELECT COUNT(DISTINCT session_id) as count FROM sessions WHERE updated_at >= ?", (ten_mins_ago,))
        live_count = cursor.fetchone()["count"] or 0
        
        # 2. Total Sessions Today
        cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE created_at >= ?", (today_start,))
        today_sessions = cursor.fetchone()["count"] or 0
        
        # 3. All time sessions
        cursor.execute("SELECT COUNT(*) as count FROM sessions")
        total_sessions = cursor.fetchone()["count"] or 0
        
        # 4. Countries breakdown
        cursor.execute("""
            SELECT country, COUNT(*) as count 
            FROM sessions 
            GROUP BY country 
            ORDER BY count DESC 
            LIMIT 6
        """)
        countries = [{"name": r["country"] or "غير محدد", "count": r["count"]} for r in cursor.fetchall()]
        
        # 5. Cities breakdown (Palestine & Arab cities)
        cursor.execute("""
            SELECT city, country, COUNT(*) as count 
            FROM sessions 
            WHERE city != 'غير محدد'
            GROUP BY city 
            ORDER BY count DESC 
            LIMIT 7
        """)
        cities = [{"city": r["city"], "country": r["country"], "count": r["count"]} for r in cursor.fetchall()]
        
        # 6. Devices breakdown
        cursor.execute("SELECT device_type, COUNT(*) as count FROM sessions GROUP BY device_type")
        devices = [{"device": r["device_type"] or "هاتف محمول", "count": r["count"]} for r in cursor.fetchall()]
        
        # 7. Drop-off and Friction Points
        cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE is_drop_off = 1")
        drop_off_count = cursor.fetchone()["count"] or 0
        
        cursor.execute("""
            SELECT current_path, COUNT(*) as count 
            FROM sessions 
            WHERE is_drop_off = 1 
            GROUP BY current_path 
            ORDER BY count DESC 
            LIMIT 5
        """)
        drop_off_paths = [{"path": r["current_path"], "count": r["count"]} for r in cursor.fetchall()]
        
        # 8. Rage Clicks (Buttons clicked rapidly out of frustration)
        cursor.execute("""
            SELECT event_arabic_title, page_path, COUNT(*) as count, details_json
            FROM events 
            WHERE event_type = 'rage_click' 
            GROUP BY event_arabic_title, page_path 
            ORDER BY count DESC 
            LIMIT 5
        """)
        rage_clicks = []
        for r in cursor.fetchall():
            rage_clicks.append({
                "title": r["event_arabic_title"],
                "path": r["page_path"],
                "count": r["count"]
            })
            
        # 9. Top Visited Pages
        cursor.execute("""
            SELECT current_path, COUNT(*) as count 
            FROM sessions 
            GROUP BY current_path 
            ORDER BY count DESC 
            LIMIT 6
        """)
        top_pages = [{"path": r["current_path"], "views": r["count"]} for r in cursor.fetchall()]
        
        # 10. Recent Live Stream Sessions (Last 25 detailed journeys)
        cursor.execute("""
            SELECT session_id, user_email, country, city, device_type, os_browser, 
                   initial_path, current_path, max_scroll_depth, duration_seconds, 
                   is_drop_off, drop_off_reason, created_at, updated_at
            FROM sessions 
            ORDER BY updated_at DESC 
            LIMIT 25
        """)
        sessions_list = []
        for s in cursor.fetchall():
            # Get last 5 events for this session
            cursor.execute("""
                SELECT event_arabic_title, event_type, created_at, page_path
                FROM events 
                WHERE session_id = ? 
                ORDER BY id DESC 
                LIMIT 5
            """, (s["session_id"],))
            ev_list = [{"title": e["event_arabic_title"], "type": e["event_type"], "path": e["page_path"]} for e in cursor.fetchall()]
            
            sessions_list.append({
                "session_id": s["session_id"],
                "user_email": s["user_email"] or "طالب زائر",
                "country": s["country"] or "فلسطين 🇵🇸",
                "city": s["city"] or "غير محدد",
                "device_type": s["device_type"] or "هاتف محمول",
                "os_browser": s["os_browser"],
                "initial_path": s["initial_path"],
                "current_path": s["current_path"],
                "scroll_depth": s["max_scroll_depth"] or 0,
                "duration_seconds": s["duration_seconds"] or 0,
                "is_drop_off": bool(s["is_drop_off"]),
                "drop_off_reason": s["drop_off_reason"],
                "updated_at": s["updated_at"],
                "recent_actions": ev_list
            })
            
        return {
            "live_visitors": live_count,
            "today_sessions": today_sessions,
            "total_sessions": total_sessions,
            "drop_off_count": drop_off_count,
            "countries": countries,
            "cities": cities,
            "devices": devices,
            "drop_off_paths": drop_off_paths,
            "rage_clicks": rage_clicks,
            "top_pages": top_pages,
            "recent_sessions": sessions_list
        }
    except Exception as e:
        logger.error(f"Error computing analytics overview: {e}")
        return {
            "live_visitors": 0,
            "today_sessions": 0,
            "total_sessions": 0,
            "drop_off_count": 0,
            "countries": [],
            "cities": [],
            "devices": [],
            "drop_off_paths": [],
            "rage_clicks": [],
            "top_pages": [],
            "recent_sessions": []
        }
    finally:
        conn.close()

def clear_analytics_db():
    """Wipes analytics database for fresh start."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM events;")
    cursor.execute("DELETE FROM sessions;")
    conn.commit()
    conn.close()

def analyze_student_journey(session: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Intelligent Arabic Analysis Engine for a single student's session journey.
    Detects bottlenecks, technical errors, rage clicks, interest level, and provides actionable recommendations.
    """
    errors = [e for e in events if e.get("event_type") == "error"]
    rage_clicks = [e for e in events if e.get("event_type") == "rage_click"]
    pageviews = [e for e in events if e.get("event_type") == "pageview"]
    clicks = [e for e in events if e.get("event_type") == "click"]
    drop_offs = [e for e in events if e.get("event_type") == "drop_off"]
    
    total_actions = len(events)
    duration = session.get("duration_seconds") or 0
    is_drop_off = session.get("is_drop_off") or (len(drop_offs) > 0)
    city = session.get("city") or "غير محدد"
    country = session.get("country") or "فلسطين 🇵🇸"
    device = session.get("device_type") or "هاتف محمول"
    email = session.get("user_email") or "طالب زائر"
    
    # 1. Determine Status & Sentiment
    if len(errors) > 0:
        overall_status = "واجه أخطاء تقنية ومشاكل 🚨"
        status_color = "rose"
        sentiment = "مستاء ويواجه مشكلة تقنية ⚠️"
    elif len(rage_clicks) > 0:
        overall_status = "واجه بطء أو ارتباك ونقر بغضب 😤"
        status_color = "amber"
        sentiment = "متوتر أو لم يستجب الزر معه سريعاً"
    elif is_drop_off:
        overall_status = "تردد وانسحب قبل إتمام الخطوة 💳"
        status_color = "amber"
        sentiment = "متردد أو يبحث عن وسيلة دفع أسهل / سعر أقل"
    elif total_actions > 8 or duration > 100:
        overall_status = "تجربة ممتازة وتفاعل نشط جداً ⭐"
        status_color = "emerald"
        sentiment = "مرتاح ومتفاعل ومندمج مع المنصة"
    else:
        overall_status = "زيارة واستكشاف عام للمنصة 🔍"
        status_color = "indigo"
        sentiment = "طبيعي / يستكشف الموقع"

    # 2. Compute Engagement Score (0 - 100)
    score = 25  # base
    score += min(len(pageviews) * 15, 30)
    score += min(len(clicks) * 5, 25)
    score += min(int(duration / 10), 20)
    if is_drop_off:
        score = max(score - 20, 10)
    if len(errors) > 0:
        score = max(score - 30, 5)
    engagement_score = min(max(score, 5), 100)

    # 3. Build Detected Issues List
    detected_issues = []
    for err in errors:
        details = err.get("details") or {}
        msg = details.get("message") or err.get("arabic_title") or "خطأ غير محدد"
        detected_issues.append({
            "type": "error",
            "title": "خطأ تقني أو فشل استجابة 🚨",
            "description": msg,
            "page": err.get("page_path", "/")
        })
    for rc in rage_clicks:
        details = rc.get("details") or {}
        elem = details.get("element") or "عنصر في الصفحة"
        detected_issues.append({
            "type": "rage_click",
            "title": "نقرات متكررة سريعة (Rage Click) 😤",
            "description": f"نقر متتالٍ وسريع على: '{elem}'",
            "page": rc.get("page_path", "/")
        })
    if is_drop_off:
        detected_issues.append({
            "type": "drop_off",
            "title": "نقطة انسحاب ومغادرة 🛑",
            "description": session.get("drop_off_reason") or "توقف عند صفحة محددة ولم يكمل الإجراء",
            "page": session.get("current_path", "/")
        })

    # 4. Generate Narrative Story (سرد رحلة الطالب باللغة العربية)
    pages_visited = list(dict.fromkeys([p.get("page_path") for p in pageviews if p.get("page_path")]))
    summary_parts = []
    summary_parts.append(f"بدأ {email} جلسته من {city}، {country} عبر {device}.")
    
    if len(pages_visited) > 1:
        summary_parts.append(f"تصفح {len(pages_visited)} صفحات: ({' ← '.join(pages_visited[:5])}).")
    elif len(pages_visited) == 1:
        summary_parts.append(f"تصفح صفحة ({pages_visited[0]}).")

    if len(clicks) > 0:
        summary_parts.append(f"قام بـ {len(clicks)} نقرات وتفاعلات مباشرة مع الأزرار والروابط.")

    if len(errors) > 0:
        summary_parts.append(f"🚨 واجه {len(errors)} أخطاء تقنية أثناء تصفحه.")
    elif len(rage_clicks) > 0:
        summary_parts.append(f"⚠️ ظهرت عليه علامات ارتباك ونقر متكرر {len(rage_clicks)} مرات.")

    if is_drop_off:
        summary_parts.append(f"توقف أخيراً عند صفحة ({session.get('current_path')}) ثم غادر.")
    else:
        summary_parts.append(f"استمرت الجلسة {duration} ثانية ووصل عمق القراءة إلى {session.get('max_scroll_depth', 0)}%.")

    narrative_summary = " ".join(summary_parts)

    # 5. Admin Recommendation (توصية عملية للمسؤول)
    if len(errors) > 0:
        rec = "يُنصح بفحص الخطأ التقني الذي واجهه الطالب ومعالجته فوراً. إن كان بريد الطالب معروفاً، يُنصح بالتواصل معه وتقديم دعم مباشر ورصيد تجريبي إضافي."
    elif is_drop_off and "/checkout" in (session.get("current_path") or ""):
        rec = "الطالب وصل لصفحة التحويل ثم تردد؛ هذا عميل ساخن جداً (Hot Lead)! يُنصح بمراسلته عبر واتساب لمساعدته في تأكيد التحويل أو تقديم خصم بسيط."
    elif len(rage_clicks) > 0:
        rec = "الزر أو الرابط الذي نقر عليه الطالب بشكل متكرر قد يعاني من بطء في التحميل؛ تأكد من وضوح مؤشر التحميل."
    elif "/plans" in pages_visited:
        rec = "الطالب مهتم بالاشتراك واطلع على الباقات؛ تذكيره بمزايا الباقات عبر إشعار عام فكرة ممتازة."
    else:
        rec = "سلوك الطالب سليم وتجربته تسير بنجاح وسلاسة دون أي معوقات برمجية."

    return {
        "overall_status": overall_status,
        "status_color": status_color,
        "sentiment": sentiment,
        "engagement_score": engagement_score,
        "narrative_summary": narrative_summary,
        "detected_issues": detected_issues,
        "admin_recommendation": rec
    }

def get_session_full_journey(session_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves full chronological timeline of all events and runs the AI analysis engine."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        s = cursor.fetchone()
        if not s:
            return None
        
        session_dict = dict(s)
        
        cursor.execute("""
            SELECT id, session_id, event_name, event_arabic_title, event_type, page_path, details_json, created_at
            FROM events
            WHERE session_id = ?
            ORDER BY id ASC
        """, (session_id,))
        
        raw_events = cursor.fetchall()
        events_list = []
        for e in raw_events:
            d_json = {}
            if e["details_json"]:
                try:
                    d_json = json.loads(e["details_json"])
                except:
                    d_json = {}
            events_list.append({
                "id": e["id"],
                "event_name": e["event_name"],
                "arabic_title": e["event_arabic_title"],
                "event_type": e["event_type"],
                "page_path": e["page_path"],
                "details": d_json,
                "created_at": e["created_at"]
            })
            
        analysis = analyze_student_journey(session_dict, events_list)
        
        return {
            "session": session_dict,
            "timeline": events_list,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"Error fetching session journey for {session_id}: {e}")
        return None
    finally:
        conn.close()

