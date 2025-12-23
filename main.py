# main.py
# 這是整個應用程式的主要入口點
# 主要功能：
# 1. API 路由處理
# 2. 使用者認證
# 3. 檔案上傳
# 4. 專案管理
# 5. 投標流程

# === 框架相關匯入 ===
from fastapi import FastAPI, Depends, Request, Form, HTTPException, UploadFile, File, Response  # Web 框架核心組件
from fastapi.templating import Jinja2Templates  # 模板引擎
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse  # HTTP 響應類型
from fastapi.staticfiles import StaticFiles  # 靜態文件服務
from pydantic import BaseModel  # 資料驗證和序列化
from typing import Optional, List  # 型別提示

# === 系統相關匯入 ===
import os  # 操作系統功能
import secrets  # 生成安全隨機值
from hash import hash_password, verify_password  # 密碼雜湊工具
from datetime import datetime  # 日期時間處理
from starlette.middleware.sessions import SessionMiddleware  # 會話管理
from passlib.context import CryptContext  # 密碼加密
import psycopg  # PostgreSQL 資料庫驅動
import logging  # 日誌記錄
from logging.handlers import RotatingFileHandler  # 循環日誌處理
import json  # JSON 處理
import tempfile  # 臨時文件處理

# === 密碼加密設定 ===
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")  # 使用 bcrypt 進行密碼加密

# === 資料庫操作函數匯入 ===
try:
    from db import getDB  # 資料庫連接管理
    # 匯入所有資料庫查詢函數
    from routes.dbQuery import (
        # 投標相關
        db_get_bid_by_id,         # 獲取特定投標
        db_get_upload_by_bid_id,  # 獲取投標相關的上傳檔案
        db_update_bid_status,     # 更新投標狀態
        db_update_project_status, # 更新專案狀態
        
        # 專案相關
        create_project as db_create_project,  # 創建新專案
        get_projects_by_client as db_get_projects_by_client,  # 獲取委託人的專案
        get_all_projects as db_get_all_projects,  # 獲取所有專案
        
        # 投標流程
        create_bid as db_create_bid,  # 創建新投標
        get_bids_by_contractor as db_get_bids_by_contractor,  # 獲取承包商的投標
        accept_bid as db_accept_bid,  # 接受投標
        get_bid_by_project_and_contractor as db_get_bid_by_project_and_contractor,  # 獲取特定專案和承包商的投標
        
        # 檔案上傳
        get_project_by_id as db_get_project_by_id,
        upsert_user as db_upsert_user,
        get_user_by_credentials as db_get_user_by_credentials,
        get_messages as db_get_messages,
        add_message as db_add_message,
        update_project as db_update_project,
        set_project_status as db_set_project_status,
        get_bids_for_client_projects as db_get_bids_for_client_projects,
        set_project_submitted,
        reject_other_bids,
        complete_bid_for_project,
        db_upload_file_db,  # 這行一定要加！
    )
except ImportError as e:
    raise ImportError(f"無法匯入 db 或 routes.dbQuery 模組: {str(e)}")

# === FastAPI 應用程式初始化 ===
app = FastAPI(
    title="投標系統",
    description="工程投標與檔案上傳系統",
    docs_url="/docs",        # Swagger UI 文件
    redoc_url="/redoc",      # ReDoc 文件
    openapi_url="/api/openapi.json",  # OpenAPI 規範
)

# === 日誌系統設定 ===
# 建立日誌目錄
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("app")
if not logger.handlers:
    # 設定循環日誌檔案（最大 5MB，保留 3 個備份）
    handler = RotatingFileHandler(
        "logs/app.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8"
    )
    # 設定日誌格式
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)  # 設定日誌級別為 DEBUG

# === 應用程式配置 ===

# 配置靜態檔案服務
# 將 /uploads 路徑映射到本地 uploads 目錄
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# 配置會話中介軟體
app.add_middleware(
    SessionMiddleware,
    secret_key="a-unique-and-secure-key-20251028",  # 會話加密密鑰
    max_age=3600,  # session 有效期限（1小時）
    same_site="lax",  # Cookie 安全設定
    https_only=False,  # 是否只在 HTTPS 下使用
)

# 配置 Jinja2 模板引擎
templates = Jinja2Templates(directory="templates")  # 設定模板目錄

# === 全局錯誤處理 ===
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    統一處理 HTTP 異常，將錯誤轉換為 JSON 格式回應
    避免在使用 HTMLResponse 時的編碼錯誤
    """
    payload = {
        "status": "error",
        "code": exc.status_code,
        "message": str(exc.detail),
    }
    return JSONResponse(content=payload, status_code=exc.status_code)

# === 資料模型定義 ===
class Project(BaseModel):
    """專案資料模型"""
    id: Optional[int] = None      # 專案 ID（可選）
    title: str                    # 專案標題
    description: str              # 專案描述
    budget: float                 # 預算金額
    deadline: str                 # 截止日期
    status: str = "open"          # 專案狀態（預設為開放）
    client_id: int                # 委託人 ID

class Bid(BaseModel):
    """投標資料模型"""
    id: Optional[int] = None      # 投標 ID（可選）
    project_id: int               # 關聯的專案 ID
    contractor_id: int            # 承包商 ID
    price: float                  # 報價金額
    status: str = "pending"       # 投標狀態（預設為待處理）

# === 使用者認證相關功能 ===

def get_current_user(request: Request):
    """
    檢查當前用戶的登入狀態
    
    Args:
        request: FastAPI 請求物件
    
    Returns:
        dict: 包含用戶信息的字典
    
    Raises:
        HTTPException: 若用戶未登入或 session 無效
    """
    user = request.session.get("user")
    if not user or not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="請先登入或 session 無效")
    return user


def set_session_user(request: Request, user_obj: dict):
    """
    安全地設置用戶的 session 資訊
    
    Args:
        request: FastAPI 請求物件
        user_obj: 要儲存的用戶資訊字典
    
    注意：
        - 某些 session 後端支援 .modified 屬性
        - 其他後端使用純字典，此時設置 .modified 會失敗
        - 失敗時直接設置鍵值即可
    """
    request.session["user"] = user_obj
    try:
        request.session.modified = True
    except Exception:
        pass  # 不支援 .modified 屬性時略過

# === 基本頁面路由 ===

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    首頁路由
    顯示網站的主頁面
    """
    return templates.TemplateResponse("base.html", {"request": request})

# === 使用者認證路由 ===

@app.get("/login")
async def login_form(request: Request):
    """
    登入頁面路由
    顯示登入表單
    """
    return templates.TemplateResponse("base.html", {
        "request": request,
        "show_login": True  # 控制顯示登入表單
    })

@app.get("/register")
async def register_form(request: Request):
    """
    註冊頁面路由
    顯示註冊表單
    """
    return templates.TemplateResponse("base.html", {
        "request": request,
        "show_register": True  # 控制顯示註冊表單
    })

@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(getDB)
):
    """
    處理用戶登入請求
    
    Args:
        request: FastAPI 請求物件
        username: 用戶名
        password: 密碼
        conn: 資料庫連接（由依賴注入提供）
    
    Returns:
        - 登入成功：重定向到儀表板
        - 登入失敗：返回登入頁面並顯示錯誤訊息
    """
    # 密碼安全處理：去除空白並限制長度為 72 字元（bcrypt 限制）
    password = password.strip()[:72]
    
    try:
        # 從資料庫獲取用戶資訊
        row = await db_get_user_by_credentials(conn, username)
        
        if row:
            # 處理不同格式的資料庫返回結果
            if isinstance(row, dict):
                # 字典格式
                password_hash = row.get("password_hash")
                user_id = row.get("id")
                username_db = row.get("username")
                role = row.get("role")
            else:
                # 元組格式：(id, username, password_hash, role)
                user_id = row[0]
                username_db = row[1]
                password_hash = row[2]
                role = row[3]

            if password_hash and verify_password(password, password_hash):
                set_session_user(request, {"id": user_id, "username": username_db, "role": role})
                logger.debug("login succeeded, session=%s", request.session)
                return RedirectResponse(url="/dashboard", status_code=302)

        # 登入失敗（帳號或密碼錯誤）
        return templates.TemplateResponse("base.html", {"request": request, "error": "登入失敗，請檢查帳號密碼", "show_login": True})
    except Exception as e:
        logger.exception("登入過程發生例外")
        return templates.TemplateResponse("base.html", {"request": request, "error": f"登入錯誤: {str(e)}", "show_login": True})

@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    conn=Depends(getDB)
):
    if role not in {"client", "contractor"}:
        return templates.TemplateResponse("base.html", {
            "request": request, "error": "角色錯誤", "show_register": True
        })
    # 清除空白 + 截斷 72 bytes
    password = password.strip()[:72]

    try:
        existing = await db_get_user_by_credentials(conn, username)
        if existing:
            return templates.TemplateResponse("base.html", {"request": request, "error": "帳號已存在", "show_register": True})

        hashed_password = hash_password(password)
        user_id = await db_upsert_user(conn, username, hashed_password, role)

        set_session_user(request, {"id": user_id, "username": username, "role": role})
        return RedirectResponse(url="/dashboard", status_code=302)
    except Exception as e:
        logger.exception("註冊過程發生例外")
        return templates.TemplateResponse("base.html", {"request": request, "error": f"註冊失敗: {str(e)}", "show_register": True})
# 登出
@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/", status_code=302)

# 儀表板
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user), conn=Depends(getDB)):
    role = user.get("role")
    logger.debug("dashboard called, session user=%s", request.session.get("user"))
    # main.py - dashboard 路由
    if role == "client":
        projects = await db_get_projects_by_client(conn, user.get("id"))  # 現在包含 bids
        return templates.TemplateResponse(
            "client_dashboard.html",
            {
                "request": request,
                "projects": projects,
                "session": request.session
            }
        )
    else:
        projects = await db_get_all_projects(conn)
        bids = await db_get_bids_by_contractor(conn, user.get("id"))
        return templates.TemplateResponse(
            "contractor_dashboard.html",
            {
                "request": request,
                "projects": projects,
                "bids": bids,
                "session": request.session  # 承包商也要加
            }
        )
    
# === 專案管理路由 ===

@app.get("/create_project", response_class=HTMLResponse)
async def create_project_form(request: Request, user: dict = Depends(get_current_user)):
    """
    顯示創建專案表單
    
    Args:
        request: FastAPI 請求物件
        user: 當前登入用戶（由依賴注入提供）
    
    Returns:
        專案創建表單頁面
        
    Raises:
        HTTPException: 當用戶不是委託人時拋出 403 錯誤
    """
    if not isinstance(user, dict) or user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    return templates.TemplateResponse("project_form.html", {"request": request})

@app.post("/create_project")
async def create_project(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    budget: float = Form(...),
    deadline: str = Form(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user)
):
    """
    處理新專案創建請求
    
    Args:
        request: FastAPI 請求物件
        title: 專案標題
        description: 專案描述
        budget: 預算金額
        deadline: 截止日期
        conn: 資料庫連接
        user: 當前登入用戶
    
    Returns:
        成功：重定向到儀表板
        失敗：返回表單頁面並顯示錯誤訊息
    """
    if not isinstance(user, dict) or user.get("role") != "client":
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "無權限"}
        )

    if not user.get("id"):
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "無效的使用者ID"}
        )

    # 驗證數據
    title = title.strip()
    description = description.strip()
    if not title or not description:
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "標題和描述不能為空"}
        )

    try:
        budget_value = float(budget)
        if budget_value <= 0:
            return templates.TemplateResponse(
                "project_form.html",
                {"request": request, "error": "預算必須是正數"}
            )
    except ValueError:
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "預算必須是有效的數字"}
        )

    try:
        datetime.strptime(deadline, "%Y-%m-%d")
    except ValueError:
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "無效的日期格式，請使用 YYYY-MM-DD"}
        )

    try:
        print(f"[APP DEBUG] create_project route called by user: {user}")
        print(f"[APP DEBUG] form values title={title!r}, budget={budget!r}, deadline={deadline!r}")
        new_project = Project(
            title=title,
            description=description,
            budget=budget_value,
            deadline=deadline,
            status="open",
            client_id=user["id"]
        )
        
        try:
            print(f"[APP DEBUG] calling db_create_project...")
            project_id = await db_create_project(conn, new_project)
            print(f"[APP DEBUG] db_create_project returned: {project_id}")
            if project_id:
                return RedirectResponse(url="/dashboard", status_code=302)
            else:
                return templates.TemplateResponse(
                    "project_form.html",
                    {"request": request, "error": "創建專案失敗（未返回有效 id）"}
                )
        except HTTPException as e:
            return templates.TemplateResponse(
                "project_form.html",
                {"request": request, "error": e.detail}
            )
        except Exception as e:
            return templates.TemplateResponse(
                "project_form.html",
                {"request": request, "error": f"創建專案時發生錯誤：{str(e)}"}
            )
    except Exception as e:
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": f"創建專案時發生錯誤：{str(e)}"}
        )
    except ValueError:
        return templates.TemplateResponse(
            "project_form.html",
            {"request": request, "error": "預算必須是有效的數字"}
        )


@app.get("/edit_project/{project_id}", response_class=HTMLResponse)
async def edit_project_form(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    project = await db_get_project_by_id(conn, project_id) 
    print(f"[DEBUG] Fetched project for editing: {project}")  # 除錯用
    print(type(project))
    if not project or project["client_id"] != user.get("id"):
        raise HTTPException(status_code=404, detail="專案不存在")
    return templates.TemplateResponse("project_form.html", {"request": request, "project": dict(project)})

@app.post("/edit_project/{project_id}")
async def edit_project(project_id: int, title: str = Form(...), description: str = Form(...), budget: float = Form(...), deadline: str = Form(...), conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    await db_update_project(conn, project_id, title, description, budget, deadline, user.get("id"))
    return RedirectResponse(url="/dashboard", status_code=302)

# 接受報價
@app.post("/accept_bid/{bid_id}")
async def accept_bid(bid_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    if user["role"] != "client":
        raise HTTPException(403, "無權限")

    bid = await db_get_bid_by_id(conn, bid_id)
    if not bid or bid["project_client_id"] != user["id"]:
        raise HTTPException(404, "報價不存在")

    project_id = bid["project_id"]

    # 接受此報價
    await db_update_bid_status(conn, bid_id, "accepted")
    # 拒絕其他報價
    await reject_other_bids(conn, project_id, bid_id)
    # 專案進入進行中
    await db_update_project_status(conn, project_id, "in_progress")

    request.session["flash_message"] = "已接受報價，專案進行中"
    request.session["flash_type"] = "success"
    return RedirectResponse("/dashboard", 302)
# === 專案結案相關路由 ===

@app.post("/close_project/{project_id}")
async def close_project(
    project_id: int,
    action: str = Form(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user)
):
    """
    處理專案結案動作（接受或退件）
    
    Args:
        project_id: 專案 ID
        action: 結案動作（accept/reject）
        conn: 資料庫連接
        user: 當前登入用戶
    
    Returns:
        重定向到儀表板
    
    Raises:
        HTTPException:
            - 403: 用戶不是委託人
            - 400: 無效的結案動作
    """
    # 檢查用戶權限
    if user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    
    # 驗證結案動作
    if action not in {"accept", "reject"}:
        raise HTTPException(status_code=400, detail="無效動作")
    
    # 設定專案狀態
    status = "completed" if action == "accept" else "rejected"
    await db_set_project_status(conn, project_id, status, user.get("id"))
    
    return RedirectResponse(url="/dashboard", status_code=302)

# === 投標相關路由 ===

@app.get("/submit_bid/{project_id}", response_class=HTMLResponse)
async def submit_bid_form(
    request: Request,
    project_id: int,
    conn=Depends(getDB),
    user: dict = Depends(get_current_user)
):
    """
    顯示投標表單頁面
    
    Args:
        request: FastAPI 請求物件
        project_id: 要投標的專案 ID
        conn: 資料庫連接
        user: 當前登入用戶
    
    Returns:
        投標表單頁面
        
    Raises:
        HTTPException: 
            - 403: 用戶不是承包商
            - 404: 專案不存在或已關閉
    """
    # 驗證用戶權限
    if not isinstance(user, dict) or user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")
    
    # 檢查專案狀態
    project = await db_get_project_by_id(conn, project_id)
    if not project or project["status"] != "open":
        raise HTTPException(status_code=404, detail="專案不存在或已關閉")
    
    return templates.TemplateResponse("bid_form.html", {"request": request, "project": project})

@app.post("/submit_bid/{project_id}")
async def submit_bid(
    project_id: int,
    price: float = Form(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user)
):
    """
    處理投標提交
    
    Args:
        project_id: 要投標的專案 ID
        price: 投標金額
        conn: 資料庫連接
        user: 當前登入用戶
    
    Returns:
        成功：重定向到儀表板
        失敗：顯示錯誤訊息
    """
    if not isinstance(user, dict) or user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")
    bid = Bid(project_id=project_id, contractor_id=user.get("id"), price=price)
    try:
        await db_create_bid(conn, bid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"提交報價失敗: {str(e)}")
    return RedirectResponse(url="/dashboard", status_code=302)

# === 檔案上傳相關路由 ===

@app.get("/upload/{project_id}", response_class=HTMLResponse)
async def upload_file_form(
    request: Request,
    project_id: int,
    conn=Depends(getDB),
    user: dict = Depends(get_current_user)
):
    """
    顯示檔案上傳表單頁面
    
    Args:
        request: FastAPI 請求物件
        project_id: 專案 ID
        conn: 資料庫連接
        user: 當前登入用戶
    
    Returns:
        檔案上傳表單頁面
        
    Raises:
        HTTPException:
            - 403: 用戶不是承包商或沒有已接受的報價
    """
    # 檢查用戶權限
    if user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")
    
    # 檢查是否有對應的報價
    bid = await db_get_bid_by_project_and_contractor(conn, project_id, user.get("id"))
    if not bid:
        raise HTTPException(status_code=403, detail="找不到相關報價")
    
    # 檢查專案狀態
    if bid["project_status"] == "completed":
        raise HTTPException(status_code=403, detail="專案已結案")
        
    # 檢查報價狀態（允許 accepted 和 rejected 狀態的報價上傳）
    if bid["status"] not in ["accepted", "rejected"]:
        raise HTTPException(status_code=403, detail="報價未被接受或退件")
    
    return templates.TemplateResponse("upload.html", {"request": request, "project_id": project_id})

# === 檔案上傳處理路由 ===

@app.post("/upload/{project_id}")
async def upload_file(
    request: Request,
    project_id: int,
    file: UploadFile = File(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="僅承包商可上傳檔案")

    try:
        # 1. 取得 bid，允許 accepted 或 rejected
        bid = await db_get_bid_by_project_and_contractor(conn, project_id, user.get("id"))
        if not bid or bid["status"] not in ["accepted", "rejected"]:
            raise HTTPException(status_code=403, detail="無權限上傳（報價需為接受或退件狀態）")
        if bid["project_status"] == "completed":
            raise HTTPException(403, "專案已結案，無法上傳")

        # 2. 檔案驗證
        allowed_extensions = {".pdf", ".docx", ".txt", ".jpg", ".png"}
        raw = await file.read()
        file_size = len(raw)
        file_extension = os.path.splitext(file.filename)[1].lower()

        if file_extension not in allowed_extensions:
            raise HTTPException(status_code=400, detail="不支援的檔案類型")
        if file_size > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="檔案大小超過 10MB")

        # 3. 儲存檔案
        os.makedirs("uploads", exist_ok=True)
        unique_filename = f"{secrets.token_hex(8)}{file_extension}"
        file_path = os.path.join("uploads", unique_filename)
        with open(file_path, "wb") as f:
            f.write(raw)

        # 4. 寫入 DB（傳入 user["id"]）
        try:
            await db_upload_file_db(conn, bid["id"], unique_filename, file_path, user["id"])
            db_success = True

            if bid["status"] == "rejected":
                await db_update_bid_status(conn, bid["id"], "accepted")

        except Exception as e:
            logger.warning(f"DB 寫入失敗: {e}")

        # 5. Fallback: 寫入 pending_uploads.json
        if not db_success:
            pending_path = "uploads/pending_uploads.json"
            entry = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "bid_id": bid["id"],
                "project_id": project_id,
                "uploader_id": user["id"],
                "filename": unique_filename,
                "orig_filename": file.filename,
                "file_path": file_path,
                "note": "pending_db_insert",
            }

            try:
                data = []
                if os.path.exists(pending_path):
                    with open(pending_path, "r", encoding="utf-8") as f:
                        try:
                            data = json.load(f)
                        except:
                            data = []
                data.append(entry)

                fd, tmp = tempfile.mkstemp(prefix="pending_", dir="uploads")
                with os.fdopen(fd, "w", encoding="utf-8") as tf:
                    json.dump(data, tf, ensure_ascii=False, indent=2)
                os.replace(tmp, pending_path)

                request.session["flash_message"] = "檔案已儲存，但資料庫同步失敗，系統將稍後重試。"
                request.session["flash_type"] = "warning"
            except Exception as ex:
                logger.exception("寫入 pending_uploads.json 失敗")
                request.session["flash_message"] = f"上傳成功但無法記錄 metadata（請聯絡管理員）"
                request.session["flash_type"] = "error"

        # 6. 更新專案狀態（僅 in_progress → submitted）
        try:
            await set_project_submitted(conn, project_id)
        except:
            pass  # 已是 submitted，忽略

        return RedirectResponse("/dashboard", status_code=302)

    except HTTPException as e:
        request.session["flash_message"] = str(e.detail)
        request.session["flash_type"] = "error"
        return RedirectResponse("/dashboard", status_code=302)
    except Exception as e:
        logger.exception("上傳未知錯誤")
        request.session["flash_message"] = f"上傳失敗：{str(e)}"
        request.session["flash_type"] = "error"
        return RedirectResponse("/dashboard", status_code=302)
    
# 簡易訊息頁與發送
@app.get("/messages/{project_id}", response_class=HTMLResponse)
async def get_project_messages(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    project = await db_get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")
    msgs = await db_get_messages(conn, project_id)
    return templates.TemplateResponse("base.html", {"request": request, "messages": msgs, "project_id": project_id, "show_messages": True})

@app.post("/messages/{project_id}")
async def post_project_message(project_id: int, content: str = Form(...), conn=Depends(getDB), user: dict = Depends(get_current_user)):
    await db_add_message(conn, project_id, user.get("id"), content)
    return RedirectResponse(url=f"/messages/{project_id}", status_code=302)

@app.post("/clear_flash")
async def clear_flash(request: Request):
    request.session.pop("flash_message", None)
    request.session.pop("flash_type", None)
    return {"status": "cleared"}

# main.py

@app.get("/view_upload/{bid_id}")
async def view_upload(
    bid_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn=Depends(getDB)
):
    # 1. 查 bid
    bid = await db_get_bid_by_id(conn, bid_id)
    if not bid:
        print(f"[404] Bid {bid_id} 不存在")
        raise HTTPException(status_code=404, detail="報價不存在")

    # 2. 權限檢查
    if user["role"] == "client" and bid.get("project_client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor" and bid.get("contractor_id") != user["id"]:
        raise HTTPException(403, "無權限")

    # 3. 查 upload（用 bid_id）
    upload = await db_get_upload_by_bid_id(conn, bid_id)

    # 4. fallback: pending_uploads.json
    if not upload:
        pending_path = "uploads/pending_uploads.json"
        if os.path.exists(pending_path):
            try:
                with open(pending_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for e in data:
                    if e.get("bid_id") == bid_id:
                        upload = {
                            "filename": e["filename"],
                            "created_at": e["timestamp"],
                            "file_path": e["file_path"]
                        }
                        print(f"[FALLBACK] 使用 pending.json 找到 bid {bid_id}")
                        break
            except Exception as e:
                print(f"[FALLBACK ERROR] {e}")

    if not upload:
        print(f"[404] Bid {bid_id} 無上傳記錄")
        raise HTTPException(status_code=404, detail="尚未上傳檔案")

    return templates.TemplateResponse("view_upload.html", {
        "request": request,
        "bid": bid,
        "upload": upload,
        "user": user
    })

@app.post("/reject_bid/{bid_id}")
async def reject_bid(bid_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    if user["role"] != "client": raise HTTPException(403)
    await db_update_bid_status(conn, bid_id, "rejected")
    request.session["flash_message"] = "已退件，承包商可重新上傳"
    request.session["flash_type"] = "warning"
    return RedirectResponse("/dashboard", 302)

# === 結案：同步專案 + bid 狀態 ===
@app.post("/complete_project/{project_id}")
async def complete_project(
    project_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn=Depends(getDB)
):
    if user["role"] != "client":
        raise HTTPException(403, "僅委託人可結案")

    # 檢查專案
    project = await db_get_project_by_id(conn, project_id)
    if not project or project["client_id"] != user["id"]:
        raise HTTPException(404, "專案不存在")

    try:
        # 1. 更新專案狀態
        await db_update_project_status(conn, project_id, "completed")

        # 2. 更新 accepted 的 bid 為 completed
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE bids SET status = 'completed' WHERE project_id = %s AND status = 'accepted'",
                (project_id,)
            )
            await conn.commit()

        request.session["flash_message"] = "專案已成功結案！"
        request.session["flash_type"] = "success"
    except Exception as e:
        await conn.rollback()
        raise HTTPException(500, f"結案失敗: {str(e)}")

    return RedirectResponse("/dashboard", 302)