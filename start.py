# -*- coding: utf-8 -*-
"""
فروشگاه لباس - نسخه ارتقایافته با دیتابیس پایدار

- PostgreSQL از طریق DATABASE_URL برای استقرار ابری
- SQLite فقط برای اجرای محلی در صورت نبود DATABASE_URL
- تصاویر محصولات داخل دیتابیس ذخیره می‌شوند؛ بنابراین به فایل محلی وابسته نیستند
- ساخت اولین مدیر بدون رمز پیش‌فرض
- محصولات، سفارش‌ها، موجودی و اطلاعات مشتری در دیتابیس ذخیره می‌شوند
- پرداخت واقعی عمداً شبیه‌سازی نشده است
"""

import os
import hashlib
import secrets
import html
import json
import base64
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL:
    # بعضی سرویس‌ها URL را با postgres:// می‌دهند.
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgresql://"):]
    ENGINE = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    DB_MODE = "postgresql"
else:
    # فقط برای اجرای محلی. روی Render بدون DATABASE_URL پایدار نیست.
    local_db = Path(os.environ.get("LOCAL_DB_PATH", "shop_local.db")).resolve()
    ENGINE = create_engine(f"sqlite:///{local_db}", future=True)
    DB_MODE = "sqlite"

SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip() or secrets.token_hex(32)
app = FastAPI(title="فروشگاه لباس - نسخه پایدار")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=60 * 60 * 24 * 7)


def db():
    return ENGINE.connect()


def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + key.hex()


def verify_password(password, stored):
    try:
        salt_hex, key_hex = stored.split(":", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
        return secrets.compare_digest(actual, bytes.fromhex(key_hex))
    except Exception:
        return False


def init_db():
    with ENGINE.begin() as conn:
        if DB_MODE == "postgresql":
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    price BIGINT NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT 'سایر',
                    sizes TEXT DEFAULT '',
                    colors TEXT DEFAULT '',
                    image_data BYTEA,
                    image_type TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    in_stock INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS admins (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    province TEXT NOT NULL,
                    city TEXT NOT NULL,
                    address TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    plaque TEXT DEFAULT '',
                    unit TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    items TEXT NOT NULL,
                    total BIGINT NOT NULL,
                    status TEXT DEFAULT 'در انتظار پرداخت',
                    payment_ref TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    description TEXT DEFAULT '',
                    category TEXT DEFAULT 'سایر',
                    sizes TEXT DEFAULT '',
                    colors TEXT DEFAULT '',
                    image_data BLOB,
                    image_type TEXT DEFAULT '',
                    active INTEGER DEFAULT 1,
                    in_stock INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    province TEXT NOT NULL,
                    city TEXT NOT NULL,
                    address TEXT NOT NULL,
                    postal_code TEXT NOT NULL,
                    plaque TEXT DEFAULT '',
                    unit TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    items TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    status TEXT DEFAULT 'در انتظار پرداخت',
                    payment_ref TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))

        # مهاجرت ساده برای نسخه‌های قدیمی SQLite/Postgres که image_data ندارند.
        cols = conn.execute(text("SELECT * FROM products LIMIT 0")).keys()
        if "image_data" not in cols:
            if DB_MODE == "postgresql":
                conn.execute(text("ALTER TABLE products ADD COLUMN image_data BYTEA"))
            else:
                conn.execute(text("ALTER TABLE products ADD COLUMN image_data BLOB"))
        if "image_type" not in cols:
            conn.execute(text("ALTER TABLE products ADD COLUMN image_type TEXT DEFAULT ''"))


init_db()


def money(value):
    return f"{int(value):,} تومان"


def e(value):
    return html.escape(str(value or ""))


def is_admin(request):
    return request.session.get("admin") is True


def redirect(path):
    return RedirectResponse(path, status_code=303)


def get_cart(request):
    cart = request.session.get("cart", {})
    if not isinstance(cart, dict):
        cart = {}
    result = {}
    for k, v in cart.items():
        try:
            n = int(v)
            if n > 0:
                result[str(k)] = n
        except Exception:
            pass
    return result


def save_cart(request, cart):
    request.session["cart"] = {str(k): int(v) for k, v in cart.items() if int(v) > 0}


def fetchone(conn, query, params=None):
    r = conn.execute(text(query), params or {})
    row = r.mappings().first()
    return row


def fetchall(conn, query, params=None):
    r = conn.execute(text(query), params or {})
    return r.mappings().all()


def cart_items(request):
    cart = get_cart(request)
    if not cart:
        return [], 0
    ids = [int(x) for x in cart.keys() if x.isdigit()]
    if not ids:
        return [], 0
    params = {f"p{i}": pid for i, pid in enumerate(ids)}
    marks = ",".join(f":p{i}" for i in range(len(ids)))
    with db() as conn:
        rows = fetchall(conn, f"SELECT * FROM products WHERE id IN ({marks})", params)
    found = {str(r["id"]): r for r in rows}
    items, total, clean = [], 0, {}
    for pid, qty in cart.items():
        row = found.get(pid)
        if not row or not row["active"] or not row["in_stock"]:
            continue
        qty = max(1, min(99, int(qty)))
        subtotal = int(row["price"]) * qty
        items.append({"product": row, "qty": qty, "subtotal": subtotal})
        total += subtotal
        clean[pid] = qty
    save_cart(request, clean)
    return items, total


def layout(title, body, request=None):
    cart_count = sum(get_cart(request).values()) if request else 0
    top = f"""
    <header><div class="nav">
      <a class="brand" href="/">فروشگاه لباس</a>
      <div class="navlinks"><a href="/">خانه</a><a href="/cart">🛒 سبد خرید ({cart_count})</a><a href="/admin">مدیریت</a></div>
    </div></header>"""
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;font-family:Tahoma,Arial,sans-serif;background:#f5f6f8;color:#222}}a{{color:inherit;text-decoration:none}}header{{background:#fff;border-bottom:1px solid #ddd;position:sticky;top:0;z-index:10}}.nav{{max-width:1150px;margin:auto;padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:15px}}.brand{{font-size:22px;font-weight:800;color:#d32f2f}}.navlinks{{display:flex;gap:12px;flex-wrap:wrap}}.navlinks a{{padding:8px 11px;border-radius:8px;background:#f3f4f6}}.container{{max-width:1150px;margin:25px auto;padding:0 15px}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:18px}}.card{{background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e5e7eb;box-shadow:0 2px 8px #0000000b}}.cardimg{{height:220px;background:#eee;position:relative;display:flex;align-items:center;justify-content:center}}.cardimg img{{width:100%;height:100%;object-fit:cover}}.sold{{position:absolute;inset:0;background:#0008;color:#fff;display:flex;align-items:center;justify-content:center;font-size:25px;font-weight:800}}.pad{{padding:14px}}.price{{font-weight:800;font-size:18px;margin-top:8px}}.muted{{color:#666;font-size:13px}}.btn{{display:inline-block;border:0;border-radius:9px;padding:10px 14px;background:#d32f2f;color:white;cursor:pointer;font-size:14px}}.btn.gray{{background:#555}}.btn.green{{background:#188038}}.btn.orange{{background:#d97706}}.btn.red{{background:#b91c1c}}.btn.block{{width:100%}}.status{{display:inline-block;border-radius:999px;padding:5px 9px;font-size:12px;margin-top:7px}}.ok{{background:#dcfce7;color:#166534}}.no{{background:#fee2e2;color:#991b1b}}.panel{{background:#fff;border:1px solid #ddd;border-radius:14px;padding:18px;margin-bottom:20px}}form{{display:grid;gap:10px}}input,textarea,select{{width:100%;padding:11px;border:1px solid #ccc;border-radius:8px;font:inherit}}textarea{{min-height:100px;resize:vertical}}label{{font-weight:700;font-size:14px}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:10px;border-bottom:1px solid #eee;text-align:right;vertical-align:top}}.small{{font-size:12px}}.notice{{padding:12px;border-radius:9px;background:#fff7ed;border:1px solid #fed7aa}}.success{{padding:12px;border-radius:9px;background:#ecfdf5;border:1px solid #a7f3d0}}.error{{padding:12px;border-radius:9px;background:#fef2f2;border:1px solid #fecaca}}.actions{{display:flex;gap:7px;flex-wrap:wrap}}footer{{padding:35px;text-align:center;color:#777}}@media(max-width:700px){{.two{{grid-template-columns:1fr}}.nav{{align-items:flex-start;flex-direction:column}}table{{font-size:12px}}}}
</style></head><body>{top}<main class="container">{body}</main><footer>فروشگاه لباس — نسخه پایدار</footer></body></html>"""


def image_url(pid):
    return f"/product-image/{int(pid)}"


def product_card(p):
    image = f'<img src="{image_url(p["id"])}">' if p["image_data"] else '<span>بدون تصویر</span>'
    sold = "" if p["in_stock"] else '<div class="sold">ناموجود</div>'
    stock = '<span class="status ok">موجود</span>' if p["in_stock"] else '<span class="status no">ناموجود</span>'
    button = f'<form method="post" action="/cart/add/{p["id"]}"><button class="btn block">افزودن به سبد</button></form>' if p["in_stock"] else '<button class="btn gray block" disabled>ناموجود</button>'
    return f'<article class="card"><a href="/product/{p["id"]}"><div class="cardimg">{image}{sold}</div></a><div class="pad"><div class="muted">{e(p["category"])}</div><h3>{e(p["name"])}</h3><div>{stock}</div><div class="price">{money(p["price"])}</div><div style="margin-top:10px">{button}</div></div></article>'


@app.get("/product-image/{pid}")
async def product_image(pid: int):
    with db() as conn:
        row = fetchone(conn, "SELECT image_data, image_type FROM products WHERE id=:id", {"id": pid})
    if not row or not row["image_data"]:
        return Response(status_code=404)
    media = row["image_type"] or "image/jpeg"
    return Response(content=bytes(row["image_data"]), media_type=media)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = "", category: str = ""):
    with db() as conn:
        if q:
            rows = fetchall(conn, "SELECT * FROM products WHERE active=1 AND (name LIKE :q OR description LIKE :q OR category LIKE :q) ORDER BY id DESC", {"q": f"%{q}%"})
        elif category:
            rows = fetchall(conn, "SELECT * FROM products WHERE active=1 AND category=:category ORDER BY id DESC", {"category": category})
        else:
            rows = fetchall(conn, "SELECT * FROM products WHERE active=1 ORDER BY id DESC")
        categories = fetchall(conn, "SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category")
    cats = "".join(f'<a class="btn gray" href="/?category={e(c["category"])}">{e(c["category"])}</a>' for c in categories)
    cards = "".join(product_card(p) for p in rows) or '<div class="panel">محصولی پیدا نشد.</div>'
    body = f'<div class="panel"><h1>فروشگاه لباس</h1><p class="muted">محصولات لباس را انتخاب کنید و سفارش خود را ثبت کنید.</p><form method="get"><input name="q" value="{e(q)}" placeholder="جستجوی لباس..."><button class="btn">جستجو</button></form><div class="actions" style="margin-top:12px">{cats}</div></div><div class="grid">{cards}</div>'
    return layout("فروشگاه لباس", body, request)


@app.get("/product/{pid}", response_class=HTMLResponse)
async def product_detail(request: Request, pid: int):
    with db() as conn:
        p = fetchone(conn, "SELECT * FROM products WHERE id=:id AND active=1", {"id": pid})
    if not p:
        return HTMLResponse(layout("یافت نشد", '<div class="panel">محصول پیدا نشد.</div>', request), status_code=404)
    image = f'<img src="{image_url(p["id"])}" style="max-width:100%;max-height:500px;object-fit:contain">' if p["image_data"] else '<div class="cardimg">بدون تصویر</div>'
    stock = '<span class="status ok">موجود</span>' if p["in_stock"] else '<span class="status no">ناموجود</span>'
    action = f'<form method="post" action="/cart/add/{p["id"]}"><button class="btn">افزودن به سبد خرید</button></form>' if p["in_stock"] else '<button class="btn gray" disabled>این محصول ناموجود است</button>'
    body = f'<div class="panel"><div class="two"><div>{image}</div><div><div class="muted">{e(p["category"])}</div><h1>{e(p["name"])}</h1><div>{stock}</div><div class="price">{money(p["price"])}</div><p>{e(p["description"])}</p><p><b>سایزها:</b> {e(p["sizes"]) or "ثبت نشده"}</p><p><b>رنگ‌ها:</b> {e(p["colors"]) or "ثبت نشده"}</p><div style="margin-top:18px">{action}</div></div></div></div>'
    return layout(e(p["name"]), body, request)


@app.post("/cart/add/{pid}")
async def add_cart(request: Request, pid: int):
    with db() as conn:
        p = fetchone(conn, "SELECT id, active, in_stock FROM products WHERE id=:id", {"id": pid})
    if not p or not p["active"] or not p["in_stock"]:
        return redirect(f"/product/{pid}")
    cart = get_cart(request); cart[str(pid)] = min(99, cart.get(str(pid), 0) + 1); save_cart(request, cart)
    return redirect("/cart")


@app.get("/cart", response_class=HTMLResponse)
async def cart_page(request: Request):
    items, total = cart_items(request)
    if not items:
        return layout("سبد خرید", '<div class="panel"><h1>سبد خرید</h1><p>سبد خرید شما خالی است.</p><a class="btn" href="/">بازگشت به فروشگاه</a></div>', request)
    rows = ""
    for item in items:
        p = item["product"]
        image = f'<img src="{image_url(p["id"])}" style="width:90px;height:90px;object-fit:cover;border-radius:8px">' if p["image_data"] else "بدون تصویر"
        rows += f'<div class="panel"><div class="two"><div>{image}</div><div><h3>{e(p["name"])}</h3><p>تعداد: {item["qty"]}</p><p>قیمت واحد: {money(p["price"])}</p><p><b>جمع: {money(item["subtotal"])}</b></p><form method="post" action="/cart/remove/{p["id"]}"><button class="btn red">حذف از سبد</button></form></div></div></div>'
    body = f'<h1>سبد خرید</h1>{rows}<div class="panel"><h2>مبلغ کل: {money(total)}</h2><div class="actions"><a class="btn" href="/checkout">ادامه و ثبت سفارش</a><a class="btn gray" href="/">ادامه خرید</a></div></div>'
    return layout("سبد خرید", body, request)


@app.post("/cart/remove/{pid}")
async def remove_cart(request: Request, pid: int):
    cart = get_cart(request); cart.pop(str(pid), None); save_cart(request, cart); return redirect("/cart")


def checkout_form(values=None, error=""):
    values = values or {}; err = f'<div class="error">{e(error)}</div>' if error else ""
    return f'''<div class="panel"><h1>ثبت سفارش</h1>{err}<form method="post" action="/checkout">
<label>نام و نام خانوادگی *</label><input name="customer_name" required value="{e(values.get("customer_name",""))}">
<div class="two"><div><label>شماره موبایل *</label><input name="phone" required value="{e(values.get("phone",""))}" placeholder="09xxxxxxxxx"></div><div><label>کد پستی *</label><input name="postal_code" required value="{e(values.get("postal_code",""))}"></div></div>
<div class="two"><div><label>استان *</label><input name="province" required value="{e(values.get("province",""))}"></div><div><label>شهر *</label><input name="city" required value="{e(values.get("city",""))}"></div></div>
<label>آدرس کامل *</label><textarea name="address" required>{e(values.get("address",""))}</textarea>
<div class="two"><div><label>پلاک</label><input name="plaque" value="{e(values.get("plaque",""))}"></div><div><label>واحد</label><input name="unit" value="{e(values.get("unit",""))}"></div></div>
<label>توضیحات ارسال</label><textarea name="note">{e(values.get("note",""))}</textarea><button class="btn" type="submit">ثبت سفارش</button></form></div>'''


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request):
    items, total = cart_items(request)
    if not items: return redirect("/cart")
    summary = "".join(f"<li>{e(x['product']['name'])} × {x['qty']} — {money(x['subtotal'])}</li>" for x in items)
    body = checkout_form() + f'<div class="panel"><h2>خلاصه سفارش</h2><ul>{summary}</ul><h3>مبلغ کل: {money(total)}</h3><div class="notice">پرداخت آنلاین واقعی هنوز متصل نشده است؛ هیچ پرداخت موفقی به‌صورت ساختگی اعلام نمی‌شود.</div></div>'
    return layout("ثبت سفارش", body, request)


@app.post("/checkout", response_class=HTMLResponse)
async def checkout(request: Request, customer_name: str = Form(...), phone: str = Form(...), province: str = Form(...), city: str = Form(...), address: str = Form(...), postal_code: str = Form(...), plaque: str = Form(""), unit: str = Form(""), note: str = Form("")):
    values = locals(); items, total = cart_items(request)
    if not items: return redirect("/cart")
    required = [customer_name, phone, province, city, address, postal_code]
    if any(not x.strip() for x in required):
        return HTMLResponse(layout("خطا", checkout_form(values, "لطفاً همه فیلدهای ضروری را کامل کنید."), request), status_code=400)
    item_data = [{"product_id": x["product"]["id"], "name": x["product"]["name"], "price": x["product"]["price"], "quantity": x["qty"], "subtotal": x["subtotal"]} for x in items]
    with ENGINE.begin() as conn:
        # بررسی نهایی موجودی قبل از ثبت
        for item in items:
            p = fetchone(conn, "SELECT active, in_stock FROM products WHERE id=:id", {"id": item["product"]["id"]})
            if not p or not p["active"] or not p["in_stock"]:
                return HTMLResponse(layout("خطا", checkout_form(values, "یکی از محصولات دیگر موجود نیست."), request), status_code=400)
        if DB_MODE == "postgresql":
            r = conn.execute(text("""INSERT INTO orders(customer_name,phone,province,city,address,postal_code,plaque,unit,note,items,total,status) VALUES(:customer_name,:phone,:province,:city,:address,:postal_code,:plaque,:unit,:note,:items,:total,:status) RETURNING id"""), {"customer_name":customer_name.strip(),"phone":phone.strip(),"province":province.strip(),"city":city.strip(),"address":address.strip(),"postal_code":postal_code.strip(),"plaque":plaque.strip(),"unit":unit.strip(),"note":note.strip(),"items":json.dumps(item_data, ensure_ascii=False),"total":total,"status":"در انتظار پرداخت"})
            order_id = r.scalar_one()
        else:
            r = conn.execute(text("""INSERT INTO orders(customer_name,phone,province,city,address,postal_code,plaque,unit,note,items,total,status) VALUES(:customer_name,:phone,:province,:city,:address,:postal_code,:plaque,:unit,:note,:items,:total,:status)"""), {"customer_name":customer_name.strip(),"phone":phone.strip(),"province":province.strip(),"city":city.strip(),"address":address.strip(),"postal_code":postal_code.strip(),"plaque":plaque.strip(),"unit":unit.strip(),"note":note.strip(),"items":json.dumps(item_data, ensure_ascii=False),"total":total,"status":"در انتظار پرداخت"})
            order_id = r.lastrowid
    save_cart(request, {})
    body = f'<div class="panel"><div class="success"><h1>سفارش با موفقیت ثبت شد</h1><p>شماره سفارش: <b>#{order_id}</b></p><p>مبلغ سفارش: <b>{money(total)}</b></p></div><div class="notice" style="margin-top:15px">وضعیت فعلی: <b>در انتظار پرداخت</b><br>پرداخت آنلاین واقعی هنوز متصل نشده است.</div><div class="actions" style="margin-top:15px"><a class="btn" href="/">بازگشت به فروشگاه</a></div></div>'
    return layout("سفارش ثبت شد", body, request)


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    if is_admin(request): return redirect("/admin")
    with db() as conn:
        count = fetchone(conn, "SELECT COUNT(*) AS c FROM admins")["c"]
    if count == 0:
        body = '<div class="panel"><h1>ساخت حساب مدیر</h1><p class="muted">هیچ حساب یا رمز پیش‌فرضی وجود ندارد.</p><form method="post" action="/admin/setup"><label>نام کاربری</label><input name="username" minlength="4" required><label>رمز عبور</label><input type="password" name="password" minlength="8" required><button class="btn">ساخت حساب مدیر</button></form></div>'
    else:
        body = '<div class="panel"><h1>ورود مدیر</h1><form method="post" action="/admin/login"><label>نام کاربری</label><input name="username" required><label>رمز عبور</label><input type="password" name="password" required><button class="btn">ورود</button></form></div>'
    return layout("ورود مدیریت", body, request)


@app.post("/admin/setup")
async def admin_setup(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip()
    if len(username) < 4 or len(password) < 8:
        return HTMLResponse(layout("خطا", '<div class="panel error">نام کاربری حداقل ۴ و رمز عبور حداقل ۸ کاراکتر باشد.</div>', request), status_code=400)
    try:
        with ENGINE.begin() as conn:
            count = fetchone(conn, "SELECT COUNT(*) AS c FROM admins")["c"]
            if count:
                return redirect("/admin/login")
            conn.execute(text("INSERT INTO admins(username,password_hash) VALUES(:u,:p)"), {"u":username,"p":hash_password(password)})
    except IntegrityError:
        return redirect("/admin/login")
    request.session["admin"] = True
    return redirect("/admin")


@app.post("/admin/login")
async def admin_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as conn:
        admin = fetchone(conn, "SELECT * FROM admins WHERE username=:u", {"u": username.strip()})
    if not admin or not verify_password(password, admin["password_hash"]):
        return HTMLResponse(layout("ورود ناموفق", '<div class="panel error">نام کاربری یا رمز عبور اشتباه است.</div>', request), status_code=401)
    request.session["admin"] = True
    return redirect("/admin")


ORDER_STATUSES = ["در انتظار پرداخت", "پرداخت‌شده", "در حال آماده‌سازی", "ارسال‌شده", "تحویل‌شده", "پرداخت ناموفق"]


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not is_admin(request): return redirect("/admin/login")
    with db() as conn:
        products = fetchall(conn, "SELECT * FROM products ORDER BY id DESC")
        orders = fetchall(conn, "SELECT * FROM orders ORDER BY id DESC LIMIT 50")
    order_rows = "".join(f'<tr><td>#{o["id"]}</td><td>{e(o["customer_name"])}</td><td>{e(o["phone"])}</td><td>{e(o["city"])}</td><td>{money(o["total"])}</td><td>{e(o["status"])}</td><td><a class="btn small" href="/admin/orders/{o["id"]}">جزئیات</a></td></tr>' for o in orders) or '<tr><td colspan="7">هنوز سفارشی ثبت نشده است.</td></tr>'
    product_rows = ""
    for p in products:
        stock_btn = f'<form method="post" action="/admin/products/{p["id"]}/stock"><button class="btn {"green" if not p["in_stock"] else "orange"}">{"موجود کردن" if not p["in_stock"] else "ناموجود کردن"}</button></form>'
        active_btn = f'<form method="post" action="/admin/products/{p["id"]}/toggle"><button class="btn gray">{"فعال کردن" if not p["active"] else "غیرفعال کردن"}</button></form>'
        delete_btn = f'<form method="post" action="/admin/products/{p["id"]}/delete" onsubmit="return confirm(\'حذف شود؟\')"><button class="btn red">حذف</button></form>'
        product_rows += f'<tr><td>{p["id"]}</td><td>{e(p["name"])}</td><td>{money(p["price"])}</td><td>{e(p["category"])}</td><td>{"موجود" if p["in_stock"] else "ناموجود"}</td><td>{"فعال" if p["active"] else "غیرفعال"}</td><td><div class="actions">{stock_btn}{active_btn}{delete_btn}</div></td></tr>'
    body = f'''<div class="panel"><div class="actions" style="justify-content:space-between"><h1>پنل مدیریت</h1><a class="btn gray" href="/admin/logout">خروج</a></div></div>
<div class="panel"><h2>افزودن محصول</h2><form method="post" action="/admin/products" enctype="multipart/form-data"><label>نام محصول *</label><input name="name" required><div class="two"><div><label>قیمت به تومان *</label><input name="price" type="number" min="0" required></div><div><label>دسته‌بندی</label><input name="category" placeholder="مثلاً تی‌شرت"></div></div><label>توضیحات</label><textarea name="description"></textarea><div class="two"><div><label>سایزها</label><input name="sizes" placeholder="S, M, L, XL"></div><div><label>رنگ‌ها</label><input name="colors" placeholder="مشکی، سفید، آبی"></div></div><label>تصویر محصول</label><input name="image" type="file" accept="image/*"><button class="btn">افزودن محصول</button></form></div>
<div class="panel"><h2>سفارش‌ها</h2><div style="overflow:auto"><table><tr><th>شماره</th><th>مشتری</th><th>موبایل</th><th>شهر</th><th>مبلغ</th><th>وضعیت</th><th>جزئیات</th></tr>{order_rows}</table></div></div>
<div class="panel"><h2>محصولات</h2><div style="overflow:auto"><table><tr><th>ID</th><th>نام</th><th>قیمت</th><th>دسته</th><th>موجودی</th><th>نمایش</th><th>عملیات</th></tr>{product_rows}</table></div></div>'''
    return layout("پنل مدیریت", body, request)


@app.post("/admin/products")
async def add_product(request: Request, name: str = Form(...), price: int = Form(...), description: str = Form(""), category: str = Form("سایر"), sizes: str = Form(""), colors: str = Form(""), image: UploadFile | None = File(None)):
    if not is_admin(request): return redirect("/admin/login")
    image_data = None; image_type = ""
    if image and image.filename:
        ext = Path(image.filename).suffix.lower()
        allowed = {".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".webp":"image/webp", ".gif":"image/gif"}
        if ext in allowed:
            data = await image.read()
            if len(data) <= 8 * 1024 * 1024:
                image_data = data; image_type = allowed[ext]
    with ENGINE.begin() as conn:
        conn.execute(text("INSERT INTO products(name,price,description,category,sizes,colors,image_data,image_type,active,in_stock) VALUES(:name,:price,:description,:category,:sizes,:colors,:image_data,:image_type,1,1)"), {"name":name.strip(),"price":max(0,price),"description":description.strip(),"category":category.strip() or "سایر","sizes":sizes.strip(),"colors":colors.strip(),"image_data":image_data,"image_type":image_type})
    return redirect("/admin")


@app.post("/admin/products/{pid}/stock")
async def toggle_stock(request: Request, pid: int):
    if not is_admin(request): return redirect("/admin/login")
    with ENGINE.begin() as conn: conn.execute(text("UPDATE products SET in_stock = CASE WHEN in_stock=1 THEN 0 ELSE 1 END WHERE id=:id"), {"id":pid})
    return redirect("/admin")


@app.post("/admin/products/{pid}/toggle")
async def toggle_active(request: Request, pid: int):
    if not is_admin(request): return redirect("/admin/login")
    with ENGINE.begin() as conn: conn.execute(text("UPDATE products SET active = CASE WHEN active=1 THEN 0 ELSE 1 END WHERE id=:id"), {"id":pid})
    return redirect("/admin")


@app.post("/admin/products/{pid}/delete")
async def delete_product(request: Request, pid: int):
    if not is_admin(request): return redirect("/admin/login")
    with ENGINE.begin() as conn: conn.execute(text("DELETE FROM products WHERE id=:id"), {"id":pid})
    return redirect("/admin")


@app.get("/admin/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: int):
    if not is_admin(request): return redirect("/admin/login")
    with db() as conn: order = fetchone(conn, "SELECT * FROM orders WHERE id=:id", {"id":order_id})
    if not order: return HTMLResponse(layout("سفارش پیدا نشد", '<div class="panel">سفارش پیدا نشد.</div>', request), status_code=404)
    try: items = json.loads(order["items"])
    except Exception: items = []
    lines = "".join(f"<li>{e(x.get('name',''))} × {int(x.get('quantity',0))} — {money(x.get('subtotal',0))}</li>" for x in items)
    options = "".join(f'<option value="{e(s)}" {"selected" if s == order["status"] else ""}>{e(s)}</option>' for s in ORDER_STATUSES)
    body = f'<div class="panel"><a class="btn gray" href="/admin">بازگشت</a><h1>سفارش #{order["id"]}</h1><p><b>تاریخ:</b> {e(order["created_at"])}</p><p><b>نام:</b> {e(order["customer_name"])}</p><p><b>موبایل:</b> {e(order["phone"])}</p><p><b>استان:</b> {e(order["province"])}</p><p><b>شهر:</b> {e(order["city"])}</p><p><b>کد پستی:</b> {e(order["postal_code"])}</p><p><b>پلاک:</b> {e(order["plaque"]) or "-"}</p><p><b>واحد:</b> {e(order["unit"]) or "-"}</p><p><b>آدرس:</b> {e(order["address"])}</p><p><b>توضیحات:</b> {e(order["note"]) or "-"}</p><hr><h2>اقلام</h2><ul>{lines}</ul><h2>مبلغ کل: {money(order["total"])}</h2></div><div class="panel"><h2>تغییر وضعیت سفارش</h2><form method="post" action="/admin/orders/{order["id"]}/status"><select name="status">{options}</select><button class="btn">ذخیره وضعیت</button></form></div>'
    return layout(f"سفارش #{order_id}", body, request)


@app.post("/admin/orders/{order_id}/status")
async def update_order_status(request: Request, order_id: int, status: str = Form(...)):
    if not is_admin(request): return redirect("/admin/login")
    if status not in ORDER_STATUSES: status = "در انتظار پرداخت"
    with ENGINE.begin() as conn: conn.execute(text("UPDATE orders SET status=:status WHERE id=:id"), {"status":status,"id":order_id})
    return redirect(f"/admin/orders/{order_id}")


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear(); return redirect("/admin/login")


@app.get("/health")
async def health():
    with db() as conn: conn.execute(text("SELECT 1"))
    return {"ok": True, "database": DB_MODE}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
