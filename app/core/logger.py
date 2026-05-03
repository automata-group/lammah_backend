import logging
import sys
import os
from logging.handlers import RotatingFileHandler

# إنشاء مجلد للسجلات بمسار مطلق لضمان الثبات
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# مسارات الملفات المطلقة
APP_LOG_PATH = os.path.join(LOG_DIR, "app.log")
ERROR_LOG_PATH = os.path.join(LOG_DIR, "error.log")

# تنسيق السجل: [الوقت] [مستوى الخطأ] [الملف:رقم السطر] الرسالة
log_format = logging.Formatter(
    fmt="[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger("SmartLectureApp")
logger.setLevel(logging.DEBUG)

# طباعة الأخطاء في الطرفية (Terminal)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_format)

# حفظ الأخطاء في ملف مع تدوير الملف لكي لا يكبر حجمه
file_handler = RotatingFileHandler(
    APP_LOG_PATH, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_format)

# حفظ الأخطاء الجسيمة (Errors) فقط في ملف منفصل
error_handler = RotatingFileHandler(
    ERROR_LOG_PATH, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
)
error_handler.setFormatter(log_format)
error_handler.setLevel(logging.ERROR)

# إضافة المُعالجات فقط إذا لم تكن موجودة لتجنب التكرار
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
