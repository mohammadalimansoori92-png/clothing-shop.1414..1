نسخه ارتقایافته فروشگاه لباس

این نسخه برای حل مشکل حذف اطلاعات بعد از Restart/Redeploy طراحی شده است.

اصل مهم:
- روی Render باید DATABASE_URL به یک PostgreSQL پایدار اشاره کند.
- عکس محصولات هم داخل دیتابیس ذخیره می‌شوند و به پوشه uploads وابسته نیستند.
- بدون DATABASE_URL برنامه محلی با SQLite اجرا می‌شود؛ این حالت روی Render پایدار نیست.

Render:
Build Command:
pip install -r requirements.txt

Start Command:
uvicorn start:app --host 0.0.0.0 --port $PORT

Environment Variables:
DATABASE_URL = آدرس اتصال PostgreSQL پایدار
SESSION_SECRET = یک رشته تصادفی طولانی و محرمانه

نکته:
Free PostgreSQL خود Render فقط 30 روز اعتبار دارد. برای ماندگاری واقعی باید DATABASE_URL به دیتابیسی وصل شود که شرایط نگهداری دائمی دارد.

امنیت:
- DATABASE_URL را داخل GitHub قرار ندهید.
- SESSION_SECRET را داخل GitHub قرار ندهید.
- پرداخت واقعی هنوز متصل نشده است و موفقیت پرداخت ساختگی اعلام نمی‌شود.
