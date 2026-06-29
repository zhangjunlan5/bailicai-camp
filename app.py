# -*- coding: utf-8 -*-
"""
百里才教育 · 全能小达人习惯营 —— 诚实合规版后端
设计原则：
  1) 开关说真话：家长开启“同步到荣誉墙”才会上传/保存照片；关闭则只有文字考勤。
  2) 阶段分档：幼儿（学龄前）默认只在本机留念，服务端强制不接收/不保存其照片、不公开；
     仅小学档允许自愿上传照片到荣誉墙。
  3) 数据分离：文字考勤（姓名/天数/打卡内容）默认入库供老师查看；照片仅在同意且小学档时保存。
  4) 隐私安全：照片由服务端用 Pillow 重新编码并缩放，自动抹除原图的 EXIF/GPS 元数据；
     随机文件名、存在 web 根目录之外，公开访问需校验“已同意且未下架”。

上线前必须做（见 README）：强管理员密码、HTTPS、报名时取得监护人单独同意、
私有服务器存储、保留期与“删除我孩子信息”的流程。
"""
import os
import io
import re
import base64
import uuid
import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, session, redirect, url_for,
                   render_template, send_file, abort, g)
from PIL import Image, ImageOps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
DB_PATH = os.path.join(DATA_DIR, 'checkin.db')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024
app.secret_key = os.environ.get('BAILICAI_SECRET') or secrets.token_hex(16)

ADMIN_PASSWORD = os.environ.get('BAILICAI_ADMIN_PASSWORD', 'changeme-bailicai')
ALLOWED_ORIGIN = os.environ.get('BAILICAI_ALLOWED_ORIGIN', '*')
ALWAYS_RECORD_ATTENDANCE = True     # 未公开时是否仍记录文字考勤（老师可见完成情况）
RETENTION_DAYS = 60
MAX_PHOTO_BYTES = 8 * 1024 * 1024
FNAME_RE = re.compile(r'^[0-9a-f]{32}\.jpg$')

# 简单敏感词（与前端一致，可继续扩充）
BAD_WORDS = ["傻逼", "操你", "草你", "滚蛋", "去死", "加微信", "返现"]


def get_db():
    db = getattr(g, '_db', None)
    if db is None:
        db = g._db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(_exc):
    db = getattr(g, '_db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('''CREATE TABLE IF NOT EXISTS checkin_records(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        student_name TEXT    NOT NULL,
        day_count   INTEGER NOT NULL,
        tier        TEXT    NOT NULL DEFAULT 'primary',  -- preschool | primary
        task_text   TEXT    DEFAULT '',
        photo_file  TEXT    DEFAULT NULL,                -- 仅小学档且家长同意时才有值
        is_public   INTEGER NOT NULL DEFAULT 0,
        hidden      INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT    NOT NULL
    )''')
    db.commit()
    db.close()


init_db()


@app.after_request
def add_cors(resp):
    if request.path.startswith('/api/'):
        resp.headers['Access-Control-Allow-Origin'] = ALLOWED_ORIGIN
        resp.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return resp


def clean_name(s):
    s = (s or '').strip()
    s = re.sub(r'[<>"\'/\\&]', '', s)
    return s[:12]


def clean_task(s):
    s = (s or '').strip()
    s = re.sub(r'https?://\S+', '', s, flags=re.I)
    s = re.sub(r'www\.\S+', '', s, flags=re.I)
    s = re.sub(r'[<>{}\[\]|\\^~`@#$%*=_]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:18]


def task_ok(s):
    return bool(s) and not any(w in s for w in BAD_WORDS)


def save_photo_from_dataurl(data_url):
    """落盘照片：重新编码 + 缩放 + 抹除 EXIF/GPS。返回文件名或 None。"""
    if not data_url or ',' not in data_url:
        return None
    _, b64 = data_url.split(',', 1)
    raw = base64.b64decode(b64)
    if len(raw) > MAX_PHOTO_BYTES:
        raise ValueError('photo too large')
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')
    img.thumbnail((1280, 1280))
    fn = uuid.uuid4().hex + '.jpg'
    img.save(os.path.join(UPLOAD_DIR, fn), 'JPEG', quality=85, optimize=True)
    return fn


def login_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not session.get('admin'):
            return redirect(url_for('admin_login'))
        return f(*a, **k)
    return wrapper


@app.route('/')
def index():
    return send_file(os.path.join(BASE_DIR, 'checkin.html'))


@app.route('/api/checkin', methods=['POST', 'OPTIONS'])
def api_checkin():
    if request.method == 'OPTIONS':
        return ('', 204)
    data = request.get_json(silent=True) or {}

    name = clean_name(data.get('student_name'))
    if not name:
        return jsonify(ok=False, error='缺少姓名'), 400
    try:
        day = int(data.get('day_count', 0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error='天数非法'), 400
    if not (1 <= day <= 21):
        return jsonify(ok=False, error='天数超出范围'), 400

    tier = 'preschool' if data.get('tier') == 'preschool' else 'primary'
    task_text = clean_task(data.get('task_text'))
    if task_text and not task_ok(task_text):
        task_text = ''     # 含敏感词则不入库该内容，避免被合成/展示

    want_public = bool(data.get('is_public'))

    # —— 服务端强制阶段规则：幼儿档绝不接收/保存照片、绝不公开 ——
    photo_file = None
    is_public = 0
    if tier == 'primary' and want_public:
        try:
            photo_file = save_photo_from_dataurl(data.get('photo'))
        except Exception:
            return jsonify(ok=False, error='照片处理失败'), 400
        if photo_file:
            is_public = 1
    # 幼儿档，或未开启开关：忽略任何 photo 字段，绝不保存

    if not ALWAYS_RECORD_ATTENDANCE and not is_public:
        return jsonify(ok=True, recorded=False, public=False)

    db = get_db()
    db.execute(
        '''INSERT INTO checkin_records
           (student_name, day_count, tier, task_text, photo_file, is_public, hidden, created_at)
           VALUES (?,?,?,?,?,?,0,?)''',
        (name, day, tier, task_text, photo_file, is_public,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    db.commit()
    return jsonify(ok=True, recorded=True, public=bool(is_public))


@app.route('/api/wall')
def api_wall():
    db = get_db()
    rows = db.execute(
        '''SELECT student_name, day_count, task_text, photo_file, created_at
           FROM checkin_records
           WHERE is_public=1 AND hidden=0 AND photo_file IS NOT NULL
           ORDER BY id DESC LIMIT 200''').fetchall()
    items = [{
        'name': r['student_name'],
        'day': r['day_count'],
        'message': r['task_text'] or '',
        'photo': url_for('media', fn=r['photo_file']),
        'time': r['created_at'],
    } for r in rows]
    return jsonify(ok=True, items=items)


@app.route('/media/<fn>')
def media(fn):
    if not FNAME_RE.match(fn or ''):
        abort(404)
    db = get_db()
    ok = db.execute(
        'SELECT 1 FROM checkin_records WHERE photo_file=? AND is_public=1 AND hidden=0',
        (fn,)).fetchone()
    if not ok:
        abort(404)
    path = os.path.join(UPLOAD_DIR, fn)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype='image/jpeg')


@app.route('/wall')
def wall():
    return render_template('wall.html')


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin'))
        error = '密码错误'
    return render_template('admin_login.html', error=error)


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin')
@login_required
def admin():
    db = get_db()
    rows = db.execute('SELECT * FROM checkin_records ORDER BY id DESC LIMIT 500').fetchall()
    total = db.execute('SELECT COUNT(*) c FROM checkin_records').fetchone()['c']
    public = db.execute(
        'SELECT COUNT(*) c FROM checkin_records WHERE is_public=1 AND hidden=0').fetchone()['c']
    today = db.execute(
        "SELECT COUNT(*) c FROM checkin_records WHERE substr(created_at,1,10)=?",
        (datetime.now().strftime('%Y-%m-%d'),)).fetchone()['c']
    return render_template('admin.html', rows=rows, total=total, public=public, today=today)


@app.route('/admin/media/<fn>')
@login_required
def admin_media(fn):
    if not FNAME_RE.match(fn or ''):
        abort(404)
    path = os.path.join(UPLOAD_DIR, fn)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype='image/jpeg')


@app.route('/api/admin/hide/<int:rid>', methods=['POST'])
@login_required
def admin_hide(rid):
    db = get_db()
    row = db.execute('SELECT hidden FROM checkin_records WHERE id=?', (rid,)).fetchone()
    if not row:
        return jsonify(ok=False), 404
    new = 0 if row['hidden'] else 1
    db.execute('UPDATE checkin_records SET hidden=? WHERE id=?', (new, rid))
    db.commit()
    return jsonify(ok=True, hidden=new)


@app.route('/api/admin/delete/<int:rid>', methods=['POST'])
@login_required
def admin_delete(rid):
    db = get_db()
    row = db.execute('SELECT photo_file FROM checkin_records WHERE id=?', (rid,)).fetchone()
    if not row:
        return jsonify(ok=False), 404
    if row['photo_file']:
        try:
            os.remove(os.path.join(UPLOAD_DIR, row['photo_file']))
        except OSError:
            pass
    db.execute('DELETE FROM checkin_records WHERE id=?', (rid,))
    db.commit()
    return jsonify(ok=True)


@app.route('/api/admin/cleanup', methods=['POST'])
@login_required
def cleanup():
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    db = get_db()
    olds = db.execute('SELECT id, photo_file FROM checkin_records WHERE created_at < ?',
                      (cutoff,)).fetchall()
    for r in olds:
        if r['photo_file']:
            try:
                os.remove(os.path.join(UPLOAD_DIR, r['photo_file']))
            except OSError:
                pass
    db.execute('DELETE FROM checkin_records WHERE created_at < ?', (cutoff,))
    db.commit()
    return jsonify(ok=True, deleted=len(olds))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
