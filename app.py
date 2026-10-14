import os
import hmac
import sqlite3
import requests
from functools import wraps
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, abort
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ==========================================================
# RAILWAY VARIABLES REQUIRED:
# ADMIN_USERNAME, ADMIN_PASSWORD (12+ chars), SECRET_KEY (24+ chars)
# Optional: BOT_TOKEN
# ==========================================================
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "")
if len(app.config["SECRET_KEY"]) < 24:
    raise RuntimeError("Railway Variables mein SECRET_KEY (24+ characters) set karo.")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
if len(ADMIN_PASSWORD) < 12:
    raise RuntimeError("Railway Variables mein ADMIN_PASSWORD (minimum 12 characters) set karo.")
CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "60 per hour"])
DB_PATH = os.getenv("COURSE_PANEL_DB", "course_panel.sqlite3")

# Single-file HTML templates
BASE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Course Bot Admin</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#edf2ff;font:15px/1.5 system-ui}
main{max-width:1120px;margin:auto;padding:18px}a{color:#91baff;text-decoration:none}
header,.row{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
nav a,button,.btn{display:inline-block;padding:9px 12px;border:1px solid #35496e;border-radius:8px;background:#1b2a48;color:#fff;font:inherit;cursor:pointer}
.card{background:#141d31;border:1px solid #2c3b5a;border-radius:13px;padding:15px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}.big{font-size:28px;font-weight:800}
.muted{color:#a8b4ce}label{display:block;margin:12px 0 4px}
input,textarea,select{box-sizing:border-box;width:100%;padding:10px;background:#0b1427;color:#fff;border:1px solid #334260;border-radius:8px;font:inherit}
textarea{min-height:90px}.primary{background:#315caa!important}.danger{background:#542b39!important}
.flash{padding:10px;border-radius:8px;background:#1b2a48;margin:8px 0}.success{border:1px solid #397956}.error{border:1px solid #8c4141}
.table{overflow-x:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px;border-bottom:1px solid #2c3b5a;vertical-align:top}
small{overflow-wrap:anywhere}.check{width:auto;display:inline}@media(max-width:600px){main{padding:12px}td,th{font-size:13px}}
</style></head><body><main>
{% if session.get('ok') %}
<header><b>📚 Course Bot Admin</b><form method="post" action="{{url_for('logout')}}">{{csrf_token()}}<button>Logout</button></form></header>
<nav><a href="{{url_for('home')}}">Dashboard</a><a href="{{url_for('plans')}}">Plans</a><a href="{{url_for('orders')}}">Payments</a><a href="{{url_for('users')}}">Users</a><a href="{{url_for('settings')}}">Settings</a></nav>
{% endif %}
{% for cat,msg in get_flashed_messages(with_categories=true) %}<div class="flash {{cat}}">{{msg}}</div>{% endfor %}
{{ body|safe }}
</main></body></html>
"""

def page(body, **context):
    return render_template_string(BASE, body=render_template_string(body, **context))

def connect():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,price TEXT NOT NULL DEFAULT '0',validity TEXT DEFAULT '',videos TEXT DEFAULT '',image TEXT DEFAULT '',qr TEXT DEFAULT '',caption TEXT DEFAULT '',active INTEGER DEFAULT 1,sort_order INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS demos(id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id INTEGER NOT NULL,media_type TEXT DEFAULT 'video',media_id TEXT DEFAULT '',media_url TEXT DEFAULT '',title TEXT DEFAULT '',sort_order INTEGER DEFAULT 0,FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS users(telegram_user_id INTEGER PRIMARY KEY,username TEXT DEFAULT '',first_name TEXT DEFAULT '',last_seen TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,telegram_user_id INTEGER NOT NULL,plan_id INTEGER,plan_name TEXT DEFAULT '',amount TEXT DEFAULT '',screenshot_file_id TEXT DEFAULT '',status TEXT DEFAULT 'pending',created_at TEXT DEFAULT CURRENT_TIMESTAMP,reviewed_at TEXT DEFAULT '',admin_note TEXT DEFAULT '');
        """)
        c.execute("INSERT OR IGNORE INTO settings VALUES('bot_token','')")
        c.execute("INSERT OR IGNORE INTO settings VALUES('support_link','https://t.me/')")
        if c.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0:
            c.executemany("INSERT INTO plans(name,price,validity,videos,caption,active,sort_order) VALUES(?,?,?,?,?,?,?)", [
                ("Course Plan 1","90","180 days","30000+","Add course details here",1,1),
                ("Course Plan 2","0","180 days","30000+","Add course details here",1,2),
                ("Course Plan 3","0","180 days","30000+","Add course details here",1,3)
            ])
init_db()

def setting(key, default=""):
    with connect() as c:
        r=c.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return r["value"] if r else default

def set_setting(key, value):
    with connect() as c:
        c.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,value))

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("ok"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def bot_token():
    return setting("bot_token") or os.getenv("BOT_TOKEN","").strip()

def telegram_message(user_id, text):
    token=bot_token()
    if not token: return False, "Bot token not configured."
    try:
        data=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id":int(user_id),"text":text},timeout=10).json()
        return bool(data.get("ok")),data.get("description","Telegram API error")
    except Exception as e:
        app.logger.exception("Telegram notification error")
        return False,str(e)

@app.route("/login", methods=["GET","POST"])
@limiter.limit("8 per minute")
def login():
    if request.method=="POST":
        if hmac.compare_digest(request.form.get("username",""),ADMIN_USERNAME) and hmac.compare_digest(request.form.get("password",""),ADMIN_PASSWORD):
            session.clear(); session["ok"]=True; session.permanent=True
            return redirect(request.args.get("next") or url_for("home"))
        flash("Username ya password galat hai.","error")
    return page("""
    <div class="card" style="max-width:430px;margin:8vh auto"><h1>Admin Login</h1>
    <form method="post">{{csrf_token()}}<label>Username</label><input name="username" autocomplete="username" required>
    <label>Password</label><input type="password" name="password" autocomplete="current-password" required>
    <p><button class="primary">Login</button></p></form></div>""")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def home():
    with connect() as c:
        counts={k:c.execute(q).fetchone()[0] for k,q in {
            "plans":"SELECT COUNT(*) FROM plans","active":"SELECT COUNT(*) FROM plans WHERE active=1",
            "orders":"SELECT COUNT(*) FROM orders","pending":"SELECT COUNT(*) FROM orders WHERE status='pending'",
            "approved":"SELECT COUNT(*) FROM orders WHERE status='approved'","users":"SELECT COUNT(*) FROM users"}.items()}
        recent=c.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id DESC LIMIT 10").fetchall()
    return page("""
    <h1>Dashboard</h1><div class="grid">{% for label,key in [('Plans','plans'),('Active plans','active'),('Orders','orders'),('Pending payments','pending'),('Approved','approved'),('Users','users')] %}
    <div class="card"><div class="muted">{{label}}</div><div class="big">{{counts[key]}}</div></div>{% endfor %}</div>
    <div class="card"><div class="row"><h2>Pending payments</h2><a href="{{url_for('orders')}}">View all</a></div>
    {% if recent %}<div class="table"><table><tr><th>Order</th><th>User ID</th><th>Plan</th><th>Amount</th><th>Date</th></tr>{% for o in recent %}<tr><td>#{{o.id}}</td><td>{{o.telegram_user_id}}</td><td>{{o.plan_name}}</td><td>₹{{o.amount}}</td><td>{{o.created_at}}</td></tr>{% endfor %}</table></div>{% else %}<p class="muted">Orders tabhi dikhenge jab bot unhe is database mein save karega.</p>{% endif %}</div>
    """, counts=counts, recent=recent)

@app.route("/plans")
@login_required
def plans():
    with connect() as c: rows=c.execute("SELECT * FROM plans ORDER BY sort_order,id").fetchall()
    return page("""
    <div class="row"><h1>Course plans</h1><a class="btn primary" href="{{url_for('edit_plan')}}">+ Add plan</a></div>
    <div class="card table"><table><tr><th>ID</th><th>Name</th><th>Price</th><th>Validity</th><th>Active</th><th>Actions</th></tr>
    {% for p in rows %}<tr><td>{{p.id}}</td><td>{{p.name}}</td><td>₹{{p.price}}</td><td>{{p.validity}}</td><td>{{'Yes' if p.active else 'No'}}</td><td>
    <div class="row"><a class="btn" href="{{url_for('edit_plan',pid=p.id)}}">Edit</a><a class="btn" href="{{url_for('demo_list',pid=p.id)}}">Demos</a>
    <form method="post" action="{{url_for('delete_plan',pid=p.id)}}" onsubmit="return confirm('Delete plan?')">{{csrf_token()}}<button class="danger">Delete</button></form></div></td></tr>{% endfor %}</table></div>
    """, rows=rows)

@app.route("/plan/edit",defaults={"pid":None},methods=["GET","POST"])
@app.route("/plan/edit/<int:pid>",methods=["GET","POST"])
@login_required
def edit_plan(pid):
    with connect() as c: p=c.execute("SELECT * FROM plans WHERE id=?",(pid,)).fetchone() if pid else None
    if pid and not p: abort(404)
    if request.method=="POST":
        name=request.form.get("name","").strip(); price=request.form.get("price","").strip()
        if not name or not price:
            flash("Plan name aur price required hain.","error"); return redirect(request.url)
        try: sort=int(request.form.get("sort_order") or 0)
        except ValueError: sort=0
        vals=(name,price,request.form.get("validity",""),request.form.get("videos",""),request.form.get("image",""),request.form.get("qr",""),request.form.get("caption",""),int(request.form.get("active")=="on"),sort)
        with connect() as c:
            if pid: c.execute("UPDATE plans SET name=?,price=?,validity=?,videos=?,image=?,qr=?,caption=?,active=?,sort_order=? WHERE id=?",vals+(pid,))
            else: c.execute("INSERT INTO plans(name,price,validity,videos,image,qr,caption,active,sort_order) VALUES(?,?,?,?,?,?,?,?,?)",vals)
        flash("Plan save ho gaya.","success"); return redirect(url_for("plans"))
    return page("""
    <h1>{{'Edit plan' if p else 'Add course plan'}}</h1><form class="card" method="post">{{csrf_token()}}
    <label>Plan name *</label><input name="name" required value="{{p.name if p else ''}}">
    <label>Price ₹ *</label><input name="price" required value="{{p.price if p else ''}}">
    <label>Validity</label><input name="validity" value="{{p.validity if p else '180 days'}}">
    <label>Videos / quantity</label><input name="videos" value="{{p.videos if p else ''}}">
    <label>Image URL or Telegram file_id</label><input name="image" value="{{p.image if p else ''}}">
    <label>Payment QR URL or Telegram file_id</label><input name="qr" value="{{p.qr if p else ''}}">
    <label>Description</label><textarea name="caption">{{p.caption if p else ''}}</textarea>
    <label>Sort order</label><input type="number" name="sort_order" value="{{p.sort_order if p else 0}}">
    <label><input class="check" type="checkbox" name="active" {% if not p or p.active %}checked{% endif %}> Active</label>
    <p><button class="primary">Save</button> <a class="btn" href="{{url_for('plans')}}">Cancel</a></p></form>
    """,p=p)

@app.post("/plan/delete/<int:pid>")
@login_required
def delete_plan(pid):
    with connect() as c:
        c.execute("DELETE FROM demos WHERE plan_id=?",(pid,))
        c.execute("DELETE FROM plans WHERE id=?",(pid,))
    flash("Plan delete ho gaya.","success"); return redirect(url_for("plans"))

@app.route("/demos/<int:pid>",methods=["GET","POST"])
@login_required
def demo_list(pid):
    with connect() as c:
        p=c.execute("SELECT * FROM plans WHERE id=?",(pid,)).fetchone()
        demos=c.execute("SELECT * FROM demos WHERE plan_id=? ORDER BY sort_order,id",(pid,)).fetchall()
    if not p: abort(404)
    if request.method=="POST":
        typ=request.form.get("media_type","video")
        if typ not in ("video","photo","document"): typ="video"
        media_id=request.form.get("media_id","").strip(); media_url=request.form.get("media_url","").strip()
        try: sort=int(request.form.get("sort_order") or 0)
        except ValueError: sort=0
        if not media_id and not media_url: flash("Telegram file_id ya URL do.","error")
        elif media_url and not media_url.startswith(("https://","http://")): flash("URL https:// ya http:// se shuru hona chahiye.","error")
        else:
            with connect() as c:c.execute("INSERT INTO demos(plan_id,media_type,media_id,media_url,title,sort_order) VALUES(?,?,?,?,?,?)",(pid,typ,media_id,media_url,request.form.get("title",""),sort))
            flash("Demo add ho gaya.","success")
        return redirect(url_for("demo_list",pid=pid))
    return page("""
    <h1>Demos: {{p.name}}</h1><form class="card" method="post">{{csrf_token()}}<h2>Add demo</h2>
    <label>Type</label><select name="media_type"><option value="video">Video</option><option value="photo">Photo</option><option value="document">Document</option></select>
    <label>Telegram file_id (recommended)</label><input name="media_id" placeholder="BAACAg..."><label>Or direct media URL</label><input name="media_url" placeholder="https://...">
    <label>Title</label><input name="title"><label>Sort order</label><input type="number" name="sort_order" value="0">
    <p class="muted">Normal webpage links may not work as video media; Telegram file_id is recommended.</p><button class="primary">Add demo</button></form>
    <div class="card"><h2>Current demos</h2>{% for d in demos %}<div class="row"><div><b>#{{d.id}} · {{d.media_type}}</b><br>{{d.title}}<br><small>{{d.media_id or d.media_url}}</small></div>
    <form method="post" action="{{url_for('delete_demo',did=d.id)}}">{{csrf_token()}}<button class="danger">Delete</button></form></div><hr>{% else %}<p>No demos yet.</p>{% endfor %}</div>
    """,p=p,demos=demos)

@app.post("/demo/delete/<int:did>")
@login_required
def delete_demo(did):
    with connect() as c:c.execute("DELETE FROM demos WHERE id=?",(did,))
    flash("Demo delete ho gaya.","success"); return redirect(url_for("plans"))

@app.route("/orders")
@login_required
def orders():
    status=request.args.get("status","pending")
    if status not in ("pending","approved","rejected","all"): status="pending"
    with connect() as c:
        rows=c.execute("SELECT * FROM orders"+("" if status=="all" else " WHERE status=?")+" ORDER BY id DESC LIMIT 500",() if status=="all" else (status,)).fetchall()
    return page("""
    <h1>Payment orders</h1><nav>{% for s in ['pending','approved','rejected','all'] %}<a href="{{url_for('orders',status=s)}}">{{s.title()}}</a>{% endfor %}</nav>
    <div class="card table"><table><tr><th>Order</th><th>User</th><th>Plan / amount</th><th>Screenshot file_id</th><th>Status</th><th>Review</th></tr>
    {% for o in rows %}<tr><td>#{{o.id}}<br><small>{{o.created_at}}</small></td><td>{{o.telegram_user_id}}</td><td>{{o.plan_name}}<br>₹{{o.amount}}</td><td><small>{{o.screenshot_file_id}}</small></td><td>{{o.status}}</td><td>
    {% if o.status=='pending' %}<form method="post" action="{{url_for('review_order',oid=o.id)}}">{{csrf_token()}}<input name="note" placeholder="Optional note"><p><button class="primary" name="action" value="approved">Approve</button> <button class="danger" name="action" value="rejected">Reject</button></p></form>{% else %}{{o.admin_note}}{% endif %}</td></tr>
    {% else %}<tr><td colspan="6">No orders.</td></tr>{% endfor %}</table></div><p class="muted">Bot ko orders isi database mein save karne honge.</p>
    """,rows=rows)

@app.post("/order/review/<int:oid>")
@login_required
def review_order(oid):
    with connect() as c:o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not o:abort(404)
    action=request.form.get("action")
    if action not in ("approved","rejected"):abort(400)
    with connect() as c:c.execute("UPDATE orders SET status=?,reviewed_at=CURRENT_TIMESTAMP,admin_note=? WHERE id=?",(action,request.form.get("note",""),oid))
    ok,detail=telegram_message(o["telegram_user_id"],f"Order #{oid} ({o['plan_name']}, ₹{o['amount']}) "+("approved. Thank you!" if action=="approved" else "rejected. Please contact support if needed."))
    flash(f"Order {action}." + (" User notified." if ok else " Telegram message failed: "+detail),"success" if ok else "error")
    return redirect(url_for("orders",status="pending"))

@app.route("/users")
@login_required
def users():
    with connect() as c:rows=c.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT 500").fetchall()
    return page("""
    <h1>Telegram users</h1><div class="card table"><table><tr><th>ID</th><th>Name</th><th>Username</th><th>Last seen</th></tr>
    {% for u in rows %}<tr><td>{{u.telegram_user_id}}</td><td>{{u.first_name}}</td><td>{{'@'+u.username if u.username else '—'}}</td><td>{{u.last_seen}}</td></tr>
    {% else %}<tr><td colspan="4">No users yet; bot must save users in DB.</td></tr>{% endfor %}</table></div>
    """,rows=rows)

@app.route("/settings",methods=["GET","POST"])
@login_required
def settings():
    if request.method=="POST":
        t=request.form.get("bot_token","").strip(); s=request.form.get("support_link","").strip()
        if t and ":" not in t: flash("Token format sahi nahi lag raha; BotFather se check karo.","error"); return redirect(url_for("settings"))
        if s and not s.startswith(("https://t.me/","https://telegram.me/")): flash("Support link Telegram link hona chahiye.","error"); return redirect(url_for("settings"))
        if t:set_setting("bot_token",t)
        set_setting("support_link",s)
        flash("Settings save ho gayi. Bot ko DB token read karne aur restart/reconnect ke liye code integration chahiye.","success")
        return redirect(url_for("settings"))
    return page("""
    <h1>Settings</h1><div class="card"><p>Token saved: <b>{{'Yes' if token_set else 'No'}}</b> (security ke liye token display nahi hota).</p>
    <form method="post">{{csrf_token()}}<label>New Telegram bot token (blank chhoda to current rahega)</label><input type="password" name="bot_token" autocomplete="new-password">
    <label>Support link</label><input name="support_link" value="{{support}}"><p><button class="primary">Save settings</button></p></form></div>
    <div class="card"><b>Important:</b> Website token save kar sakti hai, lekin main.py ko automatically edit nahi kar sakti. Bot ko database se token read karna aur restart/reconnect karna hoga.</div>
    """,token_set=bool(bot_token()),support=setting("support_link","https://t.me/"))

# Integration helpers: call these from the existing Telegram bot handlers.
def panel_record_user(user):
    with connect() as c:c.execute("INSERT INTO users(telegram_user_id,username,first_name) VALUES(?,?,?) ON CONFLICT(telegram_user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name,last_seen=CURRENT_TIMESTAMP",(user.id,getattr(user,"username","") or "",getattr(user,"first_name","") or ""))

def panel_create_order(user_id,plan_id,plan_name,amount,screenshot_file_id):
    with connect() as c:
        cur=c.execute("INSERT INTO orders(telegram_user_id,plan_id,plan_name,amount,screenshot_file_id) VALUES(?,?,?,?,?)",(int(user_id),int(plan_id),plan_name,str(amount),screenshot_file_id))
        return cur.lastrowid

def panel_get_active_plans():
    with connect() as c: ps=c.execute("SELECT * FROM plans WHERE active=1 ORDER BY sort_order,id").fetchall()
    result=[]
    for p in ps:
        d=dict(p)
        with connect() as c: demos=c.execute("SELECT * FROM demos WHERE plan_id=? ORDER BY sort_order,id",(p["id"],)).fetchall()
        d["demos"]=[{"type":x["media_type"],"id":x["media_id"],"url":x["media_url"],"title":x["title"]} for x in demos]
        result.append(d)
    return result

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
