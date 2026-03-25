import logging
import sys
import os
from logging.handlers import RotatingFileHandler

# إنشاء مجلد للسجلات
os.makedirs("logs", exist_ok=True)

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
    "logs/app.log", maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_format)

# إضافة المُعالجات فقط إذا لم تكن موجودة لتجنب التكرار
if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
