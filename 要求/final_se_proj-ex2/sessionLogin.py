# sessionLogin.py
# 這個模組處理用戶登入相關的功能
# 主要功能：
# 1. 用戶認證
# 2. 會話管理
# 3. 權限控制
# 4. 登入/登出處理

from fastapi import FastAPI, Form, Request, Depends, HTTPException  # Web 框架相關
from fastapi.responses import HTMLResponse, RedirectResponse  # HTTP 回應類型
from fastapi.staticfiles import StaticFiles  # 靜態文件處理
from starlette.middleware.sessions import SessionMiddleware  # 會話中間件

# 初始化 FastAPI 應用
app = FastAPI()

# 配置會話中間件
# 用於管理用戶登入狀態和權限控制
app.add_middleware(
    SessionMiddleware,
    secret_key="a-unique-and-secure-key-20251028",
    serialize_json=False,     # 避免 dict → str
    max_age=86400,
    same_site="lax",
    https_only=False
)

# 模擬用戶資料
users = {
    "client1": {"id": 1, "role": "client", "password": "123456"},
    "contractor1": {"id": 2, "role": "contractor", "password": "abc123"}
}

# 檢查登入
def get_current_user(request: Request):
    user = request.session.get("user")
    print(f"[DEBUG] Session user: {user}")  # 除錯用
    if not user or not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

# 首頁
@app.get("/")
async def home(request: Request):
    return HTMLResponse("<h1>歡迎！<a href='/login'>登入</a> | <a href='/dashboard'>儀表板</a></h1>")

# 登入頁
@app.get("/login")
async def login_form(request: Request):
    return HTMLResponse("""
        <h2>登入</h2>
        <form method="post" action="/login">
            <label>帳號: <input type="text" name="username" value="client1" required></label><br><br>
            <label>密碼: <input type="password" name="password" value="123456" required></label><br><br>
            <button type="submit">登入</button>
        </form>
        <p>測試帳號：client1 / 123456</p>
    """)

# 登入處理
@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username in users and users[username]["password"] == password:
        request.session["user"] = {
            "id": users[username]["id"],
            "username": username,
            "role": users[username]["role"]
        }
        request.session.modified = True
        print(f"[SUCCESS] 登入成功: {request.session['user']}")
        return RedirectResponse(url="/dashboard", status_code=302)
    
    return HTMLResponse("帳號或密碼錯誤！<a href='/login'>重試</a>", status_code=401)

# 儀表板（需要登入）
@app.get("/dashboard")
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    return HTMLResponse(f"""
        <h1>歡迎, {user['username']}!</h1>
        <p>角色: {user['role']}</p>
        <p><a href='/logout'>登出</a></p>
        <hr>
        <h3>這證明 Session 正常運作！</h3>
    """)

# 登出
@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/", status_code=302)

# 掛載靜態檔案（可選）
app.mount("/www", StaticFiles(directory="www"), name="www")