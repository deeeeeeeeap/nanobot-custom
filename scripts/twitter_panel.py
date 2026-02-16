#!/usr/bin/env python3
"""
推特监控管理面板 - 管理关注名单 + 查看最新摘要。

用法：
    python3 twitter_panel.py
    
访问：
    http://IP:8088
"""

import os
import subprocess
import hashlib
import secrets
from pathlib import Path
from functools import wraps
from flask import Flask, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# === 配置 ===
PORT = 8088
DEFAULT_PASSWORD = os.getenv("PANEL_PASSWORD", "nanobot2026")
PASSWORD_FILE = Path("/root/.nanobot/workspace/.panel_password")
API_TOKEN = os.getenv("API_TOKEN", "nbt_" + hashlib.md5(DEFAULT_PASSWORD.encode()).hexdigest()[:16])
WATCHLIST_PATH = Path("/root/.nanobot/workspace/twitter_watchlist.txt")
SUMMARY_PATH = Path("/root/.nanobot/workspace/twitter_daily_summary.md")
FETCH_SCRIPT = Path("/root/.nanobot/workspace/scripts/fetch_tweets.py")
QUERIES_PATH = Path("/root/.nanobot/workspace/twitter_search_queries.txt")
CREDENTIALS_FILE = Path("/root/.nanobot/workspace/.twitter_credentials")
DEFAULT_QUERIES = [
    "AI breakthrough OR AGI OR artificial intelligence",
    "LLM OR GPT OR Claude OR Gemini",
    "AI agent OR AI coding OR AI model",
    "AI 人工智能 OR 大模型 OR 深度学习",
]


def get_password() -> str | None:
    """从文件读取密码，未设置返回 None。"""
    if PASSWORD_FILE.exists():
        pw = PASSWORD_FILE.read_text().strip()
        if pw:
            return pw
    return None


def set_password(new_password: str):
    """保存新密码到文件。"""
    PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    PASSWORD_FILE.write_text(new_password)


def load_credentials() -> dict:
    """读取 Twitter 凭证，优先文件，其次环境变量。"""
    auth_token = os.getenv("AUTH_TOKEN", "")
    ct0 = os.getenv("CT0", "")
    if CREDENTIALS_FILE.exists():
        import json as _json
        try:
            creds = _json.loads(CREDENTIALS_FILE.read_text())
            auth_token = creds.get("auth_token", "") or auth_token
            ct0 = creds.get("ct0", "") or ct0
        except Exception:
            pass
    return {"auth_token": auth_token, "ct0": ct0}


def save_credentials(auth_token: str, ct0: str):
    """保存 Twitter 凭证到文件。"""
    import json as _json
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(_json.dumps({"auth_token": auth_token, "ct0": ct0}))


# === 工具函数 ===

def load_users() -> list[dict]:
    """加载用户列表。"""
    if not WATCHLIST_PATH.exists():
        return []
    users = []
    for line in WATCHLIST_PATH.read_text().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        users.append(line)
    return users


def save_users(users: list[str]):
    """保存用户列表。"""
    WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_PATH.write_text("\n".join(users) + "\n")


def load_summary() -> str:
    """加载最新摘要。"""
    if SUMMARY_PATH.exists():
        return SUMMARY_PATH.read_text()
    return "暂无摘要，等待首次抓取..."


def load_queries() -> list[str]:
    """加载搜索关键词，不存在则初始化默认值。"""
    if not QUERIES_PATH.exists():
        save_queries(DEFAULT_QUERIES)
        return DEFAULT_QUERIES[:]
    queries = []
    for line in QUERIES_PATH.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            queries.append(line)
    return queries


def save_queries(queries: list[str]):
    """保存搜索关键词。"""
    QUERIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUERIES_PATH.write_text("\n".join(queries) + "\n")


def require_login(f):
    """登录验证装饰器。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # 未设置密码 → 跳转初始设置
        if get_password() is None:
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def require_api_token(f):
    """API Token 验证装饰器（供 bot 调用）。"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token != API_TOKEN:
            return jsonify({"error": "无效的 API Token"}), 401
        return f(*args, **kwargs)
    return decorated


# === HTML 模板 ===

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🐦 推特监控 - 登录</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-box { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px);
                     border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
                     padding: 40px; width: 360px; }
        h1 { color: #fff; text-align: center; margin-bottom: 30px; font-size: 24px; }
        input { width: 100%%; padding: 12px 16px; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px; background: rgba(255,255,255,0.08); color: #fff;
                font-size: 16px; outline: none; margin-bottom: 16px; }
        input:focus { border-color: #6c63ff; }
        button { width: 100%%; padding: 12px; border: none; border-radius: 8px;
                 background: linear-gradient(135deg, #6c63ff, #3f51b5); color: #fff;
                 font-size: 16px; cursor: pointer; transition: transform 0.2s; }
        button:hover { transform: translateY(-2px); }
        .error { color: #ff6b6b; text-align: center; margin-bottom: 16px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>🐦 推特监控面板</h1>
        %(error)s
        <form method="POST">
            <input type="password" name="password" placeholder="输入密码" autofocus>
            <button type="submit">登录</button>
        </form>
    </div>
</body>
</html>
"""

MAIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🐦 推特监控面板</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
               min-height: 100vh; color: #e0e0e0; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        
        header { display: flex; justify-content: space-between; align-items: center;
                 padding: 20px 0; border-bottom: 1px solid rgba(255,255,255,0.1); margin-bottom: 24px; }
        header h1 { color: #fff; font-size: 24px; }
        .header-actions { display: flex; gap: 10px; }
        .btn { padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer;
               font-size: 14px; transition: all 0.2s; text-decoration: none; display: inline-block; }
        .btn-primary { background: linear-gradient(135deg, #6c63ff, #3f51b5); color: #fff; }
        .btn-danger { background: linear-gradient(135deg, #ff6b6b, #ee5a24); color: #fff; }
        .btn-success { background: linear-gradient(135deg, #00b894, #00a86b); color: #fff; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .btn-sm { padding: 4px 10px; font-size: 12px; }
        
        .card { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px);
                border: 1px solid rgba(255,255,255,0.1); border-radius: 12px;
                padding: 20px; margin-bottom: 20px; }
        .card h2 { color: #fff; margin-bottom: 16px; font-size: 18px; }
        
        .user-list { list-style: none; }
        .user-item { display: flex; justify-content: space-between; align-items: center;
                     padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,0.05);
                     transition: background 0.2s; }
        .user-item:hover { background: rgba(255,255,255,0.05); border-radius: 8px; }
        .user-item:last-child { border-bottom: none; }
        .user-name { font-size: 15px; }
        .user-name span { color: #6c63ff; font-weight: 600; }
        
        .add-form { display: flex; gap: 10px; margin-top: 12px; }
        .add-form input { flex: 1; padding: 10px 14px; border: 1px solid rgba(255,255,255,0.2);
                          border-radius: 8px; background: rgba(255,255,255,0.08); color: #fff;
                          font-size: 14px; outline: none; }
        .add-form input:focus { border-color: #6c63ff; }
        
        .summary-content { white-space: pre-wrap; font-size: 14px; line-height: 1.8;
                           color: #ccc; max-height: 500px; overflow-y: auto;
                           padding: 10px; background: rgba(0,0,0,0.2); border-radius: 8px; }
        
        .stats { display: flex; gap: 16px; margin-bottom: 20px; }
        .stat-box { flex: 1; text-align: center; padding: 16px;
                    background: rgba(255,255,255,0.05); border-radius: 10px; }
        .stat-num { font-size: 28px; font-weight: 700; color: #6c63ff; }
        .stat-label { font-size: 12px; color: #999; margin-top: 4px; }
        
        .msg { padding: 10px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
        .msg-ok { background: rgba(0,184,148,0.15); color: #00b894; border: 1px solid rgba(0,184,148,0.3); }
        .msg-err { background: rgba(255,107,107,0.15); color: #ff6b6b; border: 1px solid rgba(255,107,107,0.3); }
        
        .api-info { font-size: 12px; color: #666; margin-top: 8px; }
        code { background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 4px; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🐦 推特监控面板</h1>
            <div class="header-actions">
                <form method="POST" action="/trigger" style="display:inline;">
                    <button class="btn btn-success" onclick="this.textContent='⏳ 抓取中...'; this.disabled=true; this.form.submit();">
                        ▶ 手动抓取
                    </button>
                </form>
                <a href="/settings" class="btn btn-primary">⚙ 设置</a>
                <a href="/logout" class="btn btn-danger">退出</a>
            </div>
        </header>
        
        %(message)s
        
        <div class="stats">
            <div class="stat-box">
                <div class="stat-num">%(user_count)s</div>
                <div class="stat-label">监控用户</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">%(query_count)s</div>
                <div class="stat-label">搜索关键词</div>
            </div>
            <div class="stat-box">
                <div class="stat-num">%(summary_time)s</div>
                <div class="stat-label">最近更新</div>
            </div>
        </div>
        
        <div class="card">
            <h2>📋 关注名单</h2>
            <ul class="user-list">
                %(user_list)s
            </ul>
            <form class="add-form" method="POST" action="/add">
                <input type="text" name="user" placeholder="输入用户信息，如: Sam Altman (@sama)" required>
                <button class="btn btn-primary" type="submit">+ 添加</button>
            </form>
            <div class="api-info">
                🤖 Bot API: <code>POST /api/users</code> Header: <code>Authorization: Bearer %(api_token)s</code>
            </div>
        </div>
        
        <div class="card">
            <h2>🔍 搜索关键词</h2>
            <ul class="user-list">
                %(query_list)s
            </ul>
            <form class="add-form" method="POST" action="/add_query">
                <input type="text" name="query" placeholder="输入搜索关键词，如: AI safety OR alignment" required>
                <button class="btn btn-primary" type="submit">+ 添加</button>
            </form>
            <div class="api-info">
                💡 支持 OR 运算符串联多个关键词，拓取时自动搜索全网热门推文
            </div>
        </div>
        
        <div class="card">
            <h2>📊 最新摘要</h2>
            <div class="summary-content">%(summary)s</div>
        </div>
    </div>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙ 设置 - 推特监控</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .settings-box { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px);
                        border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
                        padding: 40px; width: 460px; }
        h1 { color: #fff; text-align: center; margin-bottom: 24px; font-size: 22px; }
        h2 { color: #ccc; font-size: 16px; margin: 20px 0 12px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,0.1); }
        h2:first-of-type { margin-top: 0; padding-top: 0; border-top: none; }
        label { color: #aaa; font-size: 13px; display: block; margin-bottom: 6px; }
        input { width: 100%%; padding: 12px 16px; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px; background: rgba(255,255,255,0.08); color: #fff;
                font-size: 14px; outline: none; margin-bottom: 16px; font-family: monospace; }
        input[type="password"] { font-family: -apple-system, sans-serif; }
        input:focus { border-color: #6c63ff; }
        .btn-row { display: flex; gap: 10px; }
        button, .back-link { flex: 1; padding: 12px; border: none; border-radius: 8px;
                 font-size: 15px; cursor: pointer; transition: transform 0.2s; text-align: center; text-decoration: none; display: inline-block; }
        button { background: linear-gradient(135deg, #6c63ff, #3f51b5); color: #fff; }
        .back-link { background: rgba(255,255,255,0.1); color: #ccc; line-height: 1.2; display: flex; align-items: center; justify-content: center; }
        button:hover, .back-link:hover { transform: translateY(-2px); }
        .error { color: #ff6b6b; text-align: center; margin-bottom: 16px; font-size: 14px; }
        .success { color: #00b894; text-align: center; margin-bottom: 16px; font-size: 14px; }
        .hint { color: #666; font-size: 12px; margin-top: -10px; margin-bottom: 14px; }
        .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%%; margin-right: 6px; }
        .dot-ok { background: #00b894; }
        .dot-err { background: #ff6b6b; }
    </style>
</head>
<body>
    <div class="settings-box">
        <h1>⚙ 系统设置</h1>
        %(message)s
        
        <h2>🔑 Twitter 凭证</h2>
        <p style="color:#888;font-size:13px;margin-bottom:12px;">
            <span class="status-dot %(cred_status)s"></span>%(cred_text)s
        </p>
        <form method="POST">
            <input type="hidden" name="action" value="credentials">
            <label>AUTH_TOKEN</label>
            <input type="text" name="auth_token" value="%(auth_token_masked)s" placeholder="从浏览器 Cookie 中获取">
            <label>CT0</label>
            <input type="text" name="ct0" value="%(ct0_masked)s" placeholder="从浏览器 Cookie 中获取">
            <div class="hint">💡 登录 x.com 后从浏览器 DevTools → Application → Cookies 中复制</div>
            <button type="submit">保存凭证</button>
        </form>
        
        <h2>🔒 修改密码</h2>
        <form method="POST">
            <input type="hidden" name="action" value="password">
            <label>旧密码</label>
            <input type="password" name="old_password" required>
            <label>新密码</label>
            <input type="password" name="new_password" required>
            <label>确认新密码</label>
            <input type="password" name="confirm_password" required>
            <button type="submit">保存密码</button>
        </form>
        
        <div class="btn-row" style="margin-top:20px;">
            <a href="/" class="back-link">← 返回面板</a>
        </div>
    </div>
</body>
</html>
"""

SETUP_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🐦 推特监控 - 初始设置</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
               min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .setup-box { background: rgba(255,255,255,0.05); backdrop-filter: blur(20px);
                     border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
                     padding: 40px; width: 400px; }
        h1 { color: #fff; text-align: center; margin-bottom: 8px; font-size: 24px; }
        .subtitle { color: #999; text-align: center; margin-bottom: 24px; font-size: 14px; }
        label { color: #aaa; font-size: 13px; display: block; margin-bottom: 6px; }
        input { width: 100%%; padding: 12px 16px; border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px; background: rgba(255,255,255,0.08); color: #fff;
                font-size: 15px; outline: none; margin-bottom: 16px; }
        input:focus { border-color: #6c63ff; }
        button { width: 100%%; padding: 12px; border: none; border-radius: 8px;
                 background: linear-gradient(135deg, #6c63ff, #3f51b5); color: #fff;
                 font-size: 16px; cursor: pointer; transition: transform 0.2s; }
        button:hover { transform: translateY(-2px); }
        .error { color: #ff6b6b; text-align: center; margin-bottom: 16px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="setup-box">
        <h1>🐦 欢迎使用</h1>
        <div class="subtitle">首次使用，请设置管理密码</div>
        %(message)s
        <form method="POST">
            <label>设置密码</label>
            <input type="password" name="new_password" placeholder="至少 4 位" required autofocus>
            <label>确认密码</label>
            <input type="password" name="confirm_password" placeholder="再输入一次" required>
            <button type="submit">开始使用</button>
        </form>
    </div>
</body>
</html>
"""

# === 路由 ===

@app.route("/login", methods=["GET", "POST"])
def login():
    # 未设置密码 → 跳转初始设置
    if get_password() is None:
        return redirect(url_for("setup"))
    error = ""
    if request.method == "POST":
        if request.form.get("password") == get_password():
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = '<div class="error">密码错误</div>'
    return LOGIN_HTML % {"error": error}


@app.route("/")
@require_login
def index():
    users = load_users()
    queries = load_queries()
    summary = load_summary()
    
    # 用户列表 HTML
    user_html = ""
    for i, u in enumerate(users):
        user_html += f'''
        <li class="user-item">
            <span class="user-name">{i+1}. <span>{u}</span></span>
            <form method="POST" action="/remove" style="display:inline;">
                <input type="hidden" name="index" value="{i}">
                <button class="btn btn-danger btn-sm" type="submit">删除</button>
            </form>
        </li>'''
    
    if not user_html:
        user_html = '<li class="user-item"><span class="user-name" style="color:#666;">暂无用户</span></li>'
    
    # 搜索关键词列表 HTML
    query_html = ""
    for i, q in enumerate(queries):
        query_html += f'''
        <li class="user-item">
            <span class="user-name">{i+1}. <span>{q}</span></span>
            <form method="POST" action="/remove_query" style="display:inline;">
                <input type="hidden" name="index" value="{i}">
                <button class="btn btn-danger btn-sm" type="submit">删除</button>
            </form>
        </li>'''
    
    if not query_html:
        query_html = '<li class="user-item"><span class="user-name" style="color:#666;">暂无关键词</span></li>'
    
    # 摘要时间
    summary_time = "未知"
    if SUMMARY_PATH.exists():
        mtime = SUMMARY_PATH.stat().st_mtime
        from datetime import datetime, timezone, timedelta
        dt = datetime.fromtimestamp(mtime, tz=timezone(timedelta(hours=8)))
        summary_time = dt.strftime("%m-%d %H:%M")
    
    # 消息
    msg_html = ""
    msg = request.args.get("msg")
    msg_type = request.args.get("type", "ok")
    if msg:
        msg_html = f'<div class="msg msg-{msg_type}">{msg}</div>'
    
    return MAIN_HTML % {
        "user_count": len(users),
        "query_count": len(queries),
        "summary_time": summary_time,
        "user_list": user_html,
        "query_list": query_html,
        "summary": summary.replace("<", "&lt;").replace(">", "&gt;"),
        "message": msg_html,
        "api_token": API_TOKEN,
    }


@app.route("/add", methods=["POST"])
@require_login
def add_user():
    user = request.form.get("user", "").strip()
    if user:
        users = load_users()
        users.append(user)
        save_users(users)
        return redirect(url_for("index", msg=f"已添加: {user}", type="ok"))
    return redirect(url_for("index", msg="用户名不能为空", type="err"))


@app.route("/remove", methods=["POST"])
@require_login
def remove_user():
    idx = int(request.form.get("index", -1))
    users = load_users()
    if 0 <= idx < len(users):
        removed = users.pop(idx)
        save_users(users)
        return redirect(url_for("index", msg=f"已删除: {removed}", type="ok"))
    return redirect(url_for("index", msg="无效的索引", type="err"))


@app.route("/add_query", methods=["POST"])
@require_login
def add_query():
    query = request.form.get("query", "").strip()
    if query:
        queries = load_queries()
        queries.append(query)
        save_queries(queries)
        return redirect(url_for("index", msg=f"已添加关键词: {query}", type="ok"))
    return redirect(url_for("index", msg="关键词不能为空", type="err"))


@app.route("/remove_query", methods=["POST"])
@require_login
def remove_query():
    idx = int(request.form.get("index", -1))
    queries = load_queries()
    if 0 <= idx < len(queries):
        removed = queries.pop(idx)
        save_queries(queries)
        return redirect(url_for("index", msg=f"已删除关键词: {removed}", type="ok"))
    return redirect(url_for("index", msg="无效的索引", type="err"))


@app.route("/trigger", methods=["POST"])
@require_login
def trigger():
    """手动触发抓取。"""
    creds = load_credentials()
    if not creds["auth_token"] or not creds["ct0"]:
        return redirect(url_for("index", msg="请先在设置页配置 Twitter 凭证（AUTH_TOKEN / CT0）", type="err"))
    
    env = os.environ.copy()
    env["AUTH_TOKEN"] = creds["auth_token"]
    env["CT0"] = creds["ct0"]
    
    try:
        subprocess.Popen(
            ["python3", str(FETCH_SCRIPT)],
            env=env,
            stdout=open("/var/log/twitter_fetch.log", "w"),
            stderr=subprocess.STDOUT,
        )
        return redirect(url_for("index", msg="抓取任务已启动（后台运行中）", type="ok"))
    except Exception as e:
        return redirect(url_for("index", msg=f"启动失败: {e}", type="err"))


@app.route("/settings", methods=["GET", "POST"])
@require_login
def settings():
    """系统设置页：凭证 + 密码。"""
    msg = ""
    if request.method == "POST":
        action = request.form.get("action", "")
        
        if action == "credentials":
            auth_token = request.form.get("auth_token", "").strip()
            ct0 = request.form.get("ct0", "").strip()
            if auth_token and ct0:
                save_credentials(auth_token, ct0)
                msg = '<div class="success">凭证保存成功！</div>'
            else:
                msg = '<div class="error">AUTH_TOKEN 和 CT0 都不能为空</div>'
        
        elif action == "password":
            old_pw = request.form.get("old_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")
            if old_pw != get_password():
                msg = '<div class="error">旧密码错误</div>'
            elif not new_pw or len(new_pw) < 4:
                msg = '<div class="error">新密码不能少于 4 位</div>'
            elif new_pw != confirm_pw:
                msg = '<div class="error">两次输入的新密码不一致</div>'
            else:
                set_password(new_pw)
                msg = '<div class="success">密码修改成功！</div>'
    
    # 凭证状态
    creds = load_credentials()
    has_creds = bool(creds["auth_token"] and creds["ct0"])
    
    # 掩码显示（只显示前6位和后4位）
    def mask(s):
        if not s:
            return ""
        if len(s) <= 10:
            return s[:3] + "***"
        return s[:6] + "***" + s[-4:]
    
    return SETTINGS_HTML % {
        "message": msg,
        "cred_status": "dot-ok" if has_creds else "dot-err",
        "cred_text": "凭证已配置" if has_creds else "未配置凭证",
        "auth_token_masked": mask(creds["auth_token"]),
        "ct0_masked": mask(creds["ct0"]),
    }


@app.route("/setup", methods=["GET", "POST"])
def setup():
    """首次设置密码（无需旧密码）。"""
    # 已设过密码 → 跳转登录
    if get_password() is not None:
        return redirect(url_for("login"))
    
    msg = ""
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if not new_pw or len(new_pw) < 4:
            msg = '<div class="error">密码不能少于 4 位</div>'
        elif new_pw != confirm_pw:
            msg = '<div class="error">两次输入的密码不一致</div>'
        else:
            set_password(new_pw)
            session["logged_in"] = True
            return redirect(url_for("index"))
    
    return SETUP_HTML % {"message": msg}


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# === Bot API（无需登录，用 Token 认证） ===

@app.route("/api/users", methods=["GET"])
@require_api_token
def api_list_users():
    """获取用户列表。"""
    return jsonify({"users": load_users()})


@app.route("/api/users", methods=["POST"])
@require_api_token
def api_add_user():
    """添加用户。"""
    data = request.get_json() or {}
    user = data.get("user", "").strip()
    if not user:
        return jsonify({"error": "缺少 user 字段"}), 400
    users = load_users()
    users.append(user)
    save_users(users)
    return jsonify({"ok": True, "message": f"已添加: {user}", "total": len(users)})


@app.route("/api/users/<int:index>", methods=["DELETE"])
@require_api_token
def api_remove_user(index):
    """删除用户。"""
    users = load_users()
    if 0 <= index < len(users):
        removed = users.pop(index)
        save_users(users)
        return jsonify({"ok": True, "message": f"已删除: {removed}", "total": len(users)})
    return jsonify({"error": "无效的索引"}), 400


@app.route("/api/summary", methods=["GET"])
@require_api_token
def api_summary():
    """获取最新摘要。"""
    return jsonify({"summary": load_summary()})


if __name__ == "__main__":
    print(f"🐦 推特监控面板启动: http://0.0.0.0:{PORT}")
    print(f"🔑 API Token: {API_TOKEN}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
