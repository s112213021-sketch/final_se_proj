# main.py
from fastapi import (
    FastAPI, Depends, Request, Form, HTTPException,
    UploadFile, File
)
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from pydantic import BaseModel
from typing import Optional

import os
import secrets
import json
import tempfile
from datetime import datetime

import logging
from logging.handlers import RotatingFileHandler

from passlib.context import CryptContext

# =========================
# Password (bcrypt)
# =========================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _clean_password(pw: str) -> str:
    # bcrypt 限制 72 bytes（這裡用字元截斷，通常足夠）
    return (pw or "").strip()[:72]


# =========================
# DB / Query imports
# =========================
try:
    from db import getDB
    from routes.dbQuery import (
        # project / bid / upload existing
        create_project as db_create_project,
        get_projects_by_client as db_get_projects_by_client,
        get_all_projects as db_get_all_projects,

        create_bid as db_create_bid,
        get_bids_by_contractor as db_get_bids_by_contractor,
        get_bid_by_project_and_contractor as db_get_bid_by_project_and_contractor,

        get_project_by_id as db_get_project_by_id,
        update_project as db_update_project,
        upsert_user as db_upsert_user,
        get_user_by_credentials as db_get_user_by_credentials,

        reject_other_bids,
        set_project_submitted,

        db_upload_file_db,
        db_get_bid_by_id,
        db_get_upload_by_bid_id,
        db_update_bid_status,
        db_update_project_status,

        # messages
        get_messages as db_get_messages,
        add_message as db_add_message,

        # ===== Issue Tracker =====
        db_create_issue,
        db_get_issues_by_project,
        db_get_issue_by_id,
        db_add_issue_comment,
        db_get_issue_comments,
        db_close_issue,
        db_count_open_issues,

        # (optional) attachments
        db_get_issue_attachments,
        db_add_issue_attachment,
    )
except ImportError as e:
    raise ImportError(f"無法匯入 db 或 routes.dbQuery 模組: {str(e)}")


# =========================
# App init
# =========================
app = FastAPI(
    title="投標系統",
    description="工程投標與檔案上傳系統",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/api/openapi.json",
)

# =========================
# Log
# =========================
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("app")
if not logger.handlers:
    handler = RotatingFileHandler(
        "logs/app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

# =========================
# Static / Templates / Session
# =========================
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

templates = Jinja2Templates(directory="templates")

app.add_middleware(
    SessionMiddleware,
    # ✅ 建議：正式環境改成環境變數
    secret_key="a-unique-and-secure-key-20251028",
    max_age=3600,
    same_site="lax",
    https_only=False,  # 本機可 False；上線請 True + https
)

# =========================
# Helpers
# =========================
def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    # 瀏覽器通常帶 text/html
    return "text/html" in accept or "*/*" in accept


def flash(request: Request, msg: str, typ: str = "info"):
    request.session["flash_message"] = msg
    request.session["flash_type"] = typ


def set_session_user(request: Request, user_obj: dict):
    request.session["user"] = user_obj
    try:
        request.session.modified = True
    except Exception:
        pass


def get_current_user(request: Request):
    user = request.session.get("user")
    if not user or not isinstance(user, dict):
        # ✅ 交給 exception handler：瀏覽器會被導去 /login
        raise HTTPException(status_code=401, detail="請先登入或 session 無效")
    return user


# =========================
# Global HTTPException handler
# =========================
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # ✅ 瀏覽器走 HTML：401 直接導去 /login，避免刷新看到 JSON
    if _wants_html(request):
        if exc.status_code == 401:
            flash(request, "請先登入", "warning")
            return RedirectResponse(url="/login", status_code=302)
        # 其他錯誤用同一個 base.html 顯示
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "error": str(exc.detail)},
            status_code=exc.status_code,
        )

    # API/非瀏覽器才回 JSON
    payload = {
        "status": "error",
        "code": exc.status_code,
        "message": str(exc.detail),
    }
    return JSONResponse(content=payload, status_code=exc.status_code)


# =========================
# Models
# =========================
class Project(BaseModel):
    id: Optional[int] = None
    title: str
    description: str
    budget: float
    deadline: str
    status: str = "open"
    client_id: int


class Bid(BaseModel):
    id: Optional[int] = None
    project_id: int
    contractor_id: int
    price: float
    status: str = "pending"


# =========================
# Pages
# =========================
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("base.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("base.html", {"request": request, "show_login": True})


@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse("base.html", {"request": request, "show_register": True})


# =========================
# Auth
# =========================
@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    conn=Depends(getDB),
):
    password = _clean_password(password)
    username = (username or "").strip()

    try:
        row = await db_get_user_by_credentials(conn, username)
        if row:
            password_hash = row.get("password_hash")
            if password_hash and pwd_context.verify(password, password_hash):
                set_session_user(
                    request,
                    {"id": row.get("id"), "username": row.get("username"), "role": row.get("role")},
                )
                return RedirectResponse(url="/dashboard", status_code=302)

        return templates.TemplateResponse(
            "base.html",
            {"request": request, "error": "登入失敗，請檢查帳號密碼", "show_login": True},
        )
    except Exception as e:
        logger.exception("登入過程發生例外")
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "error": f"登入錯誤: {str(e)}", "show_login": True},
        )


@app.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    conn=Depends(getDB),
):
    role = (role or "").strip()
    if role not in {"client", "contractor"}:
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "error": "角色錯誤", "show_register": True},
        )

    username = (username or "").strip()
    password = _clean_password(password)

    try:
        existing = await db_get_user_by_credentials(conn, username)
        if existing:
            return templates.TemplateResponse(
                "base.html",
                {"request": request, "error": "帳號已存在", "show_register": True},
            )

        hashed_password = pwd_context.hash(password)
        user_id = await db_upsert_user(conn, username, hashed_password, role)
        set_session_user(request, {"id": user_id, "username": username, "role": role})
        return RedirectResponse(url="/dashboard", status_code=302)

    except Exception as e:
        logger.exception("註冊過程發生例外")
        return templates.TemplateResponse(
            "base.html",
            {"request": request, "error": f"註冊失敗: {str(e)}", "show_register": True},
        )


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/", status_code=302)


# =========================
# Dashboard
# =========================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user), conn=Depends(getDB)):
    role = user.get("role")

    if role == "client":
        projects = await db_get_projects_by_client(conn, user.get("id"))
        return templates.TemplateResponse(
            "client_dashboard.html",
            {"request": request, "projects": projects, "session": request.session},
        )

    projects = await db_get_all_projects(conn)
    bids = await db_get_bids_by_contractor(conn, user.get("id"))
    return templates.TemplateResponse(
        "contractor_dashboard.html",
        {"request": request, "projects": projects, "bids": bids, "session": request.session},
    )


# =========================
# Project create / edit
# =========================
@app.get("/create_project", response_class=HTMLResponse)
async def create_project_form(request: Request, user: dict = Depends(get_current_user)):
    if user.get("role") != "client":
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
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "client":
        return templates.TemplateResponse("project_form.html", {"request": request, "error": "無權限"})

    title = (title or "").strip()
    description = (description or "").strip()
    if not title or not description:
        return templates.TemplateResponse("project_form.html", {"request": request, "error": "標題和描述不能為空"})

    try:
        budget_value = float(budget)
        if budget_value <= 0:
            return templates.TemplateResponse("project_form.html", {"request": request, "error": "預算必須是正數"})
    except Exception:
        return templates.TemplateResponse("project_form.html", {"request": request, "error": "預算必須是有效的數字"})

    try:
        datetime.strptime(deadline, "%Y-%m-%d")
    except Exception:
        return templates.TemplateResponse("project_form.html", {"request": request, "error": "日期格式錯誤（YYYY-MM-DD）"})

    new_project = Project(
        title=title,
        description=description,
        budget=budget_value,
        deadline=deadline,
        status="open",
        client_id=user["id"],
    )
    project_id = await db_create_project(conn, new_project)
    if project_id:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("project_form.html", {"request": request, "error": "創建專案失敗"})


@app.get("/edit_project/{project_id}", response_class=HTMLResponse)
async def edit_project_form(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    project = await db_get_project_by_id(conn, project_id)
    if not project or project.get("client_id") != user.get("id"):
        raise HTTPException(status_code=404, detail="專案不存在")
    return templates.TemplateResponse("project_form.html", {"request": request, "project": dict(project)})


@app.post("/edit_project/{project_id}")
async def edit_project(
    project_id: int,
    title: str = Form(...),
    description: str = Form(...),
    budget: float = Form(...),
    deadline: str = Form(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "client":
        raise HTTPException(status_code=403, detail="無權限")
    await db_update_project(conn, project_id, title, description, budget, deadline, user.get("id"))
    return RedirectResponse(url="/dashboard", status_code=302)


# =========================
# Bid accept / reject / submit
# =========================
@app.post("/accept_bid/{bid_id}")
async def accept_bid(bid_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    if user["role"] != "client":
        raise HTTPException(403, "無權限")

    bid = await db_get_bid_by_id(conn, bid_id)
    if not bid or bid.get("project_client_id") != user["id"]:
        raise HTTPException(404, "報價不存在")

    project_id = bid["project_id"]

    await db_update_bid_status(conn, bid_id, "accepted")
    await reject_other_bids(conn, project_id, bid_id)
    await db_update_project_status(conn, project_id, "in_progress")

    flash(request, "已接受報價，專案進行中", "success")
    return RedirectResponse("/dashboard", 302)


@app.post("/reject_bid/{bid_id}")
async def reject_bid(bid_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    if user["role"] != "client":
        raise HTTPException(403, "無權限")
    await db_update_bid_status(conn, bid_id, "rejected")
    flash(request, "已退件，承包商可重新上傳", "warning")
    return RedirectResponse("/dashboard", 302)


@app.get("/submit_bid/{project_id}", response_class=HTMLResponse)
async def submit_bid_form(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")

    project = await db_get_project_by_id(conn, project_id)
    if not project or project.get("status") != "open":
        raise HTTPException(status_code=404, detail="專案不存在或已關閉")

    return templates.TemplateResponse("bid_form.html", {"request": request, "project": project})


@app.post("/submit_bid/{project_id}")
async def submit_bid(project_id: int, price: float = Form(...), conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")
    bid = Bid(project_id=project_id, contractor_id=user.get("id"), price=float(price))
    await db_create_bid(conn, bid)
    return RedirectResponse(url="/dashboard", status_code=302)


# =========================
# Upload
# =========================
@app.get("/upload/{project_id}", response_class=HTMLResponse)
async def upload_file_form(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    if user.get("role") != "contractor":
        raise HTTPException(status_code=403, detail="無權限")

    bid = await db_get_bid_by_project_and_contractor(conn, project_id, user.get("id"))
    if not bid:
        raise HTTPException(status_code=403, detail="找不到相關報價")
    if bid.get("project_status") == "completed":
        raise HTTPException(status_code=403, detail="專案已結案")
    if bid.get("status") not in ["accepted", "rejected"]:
        raise HTTPException(status_code=403, detail="報價未被接受或退件")

    return templates.TemplateResponse("upload.html", {"request": request, "project_id": project_id})


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

    db_success = False

    try:
        bid = await db_get_bid_by_project_and_contractor(conn, project_id, user.get("id"))
        if not bid or bid.get("status") not in ["accepted", "rejected"]:
            raise HTTPException(status_code=403, detail="無權限上傳（報價需為接受或退件狀態）")
        if bid.get("project_status") == "completed":
            raise HTTPException(status_code=403, detail="專案已結案，無法上傳")

        allowed_extensions = {".pdf", ".docx", ".txt", ".jpg", ".png"}
        raw = await file.read()
        file_size = len(raw)
        ext = os.path.splitext(file.filename)[1].lower()

        if ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail="不支援的檔案類型")
        if file_size > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="檔案大小超過 10MB")

        unique_filename = f"{secrets.token_hex(8)}{ext}"
        file_path = os.path.join("uploads", unique_filename)
        with open(file_path, "wb") as f:
            f.write(raw)

        try:
            await db_upload_file_db(conn, bid["id"], unique_filename, file_path, user["id"])
            db_success = True

            if bid.get("status") == "rejected":
                await db_update_bid_status(conn, bid["id"], "accepted")
        except Exception as e:
            logger.warning("DB 寫入失敗: %s", e)

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

            data = []
            if os.path.exists(pending_path):
                try:
                    with open(pending_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = []

            data.append(entry)
            fd, tmp = tempfile.mkstemp(prefix="pending_", dir="uploads")
            with os.fdopen(fd, "w", encoding="utf-8") as tf:
                json.dump(data, tf, ensure_ascii=False, indent=2)
            os.replace(tmp, pending_path)

            flash(request, "檔案已儲存，但資料庫同步失敗，系統將稍後重試。", "warning")
        else:
            flash(request, "檔案已成功上傳！", "success")

        try:
            await set_project_submitted(conn, project_id)
        except Exception:
            pass

        return RedirectResponse("/dashboard", status_code=302)

    except HTTPException as e:
        flash(request, str(e.detail), "error")
        return RedirectResponse("/dashboard", status_code=302)
    except Exception as e:
        logger.exception("上傳未知錯誤")
        flash(request, f"上傳失敗：{str(e)}", "error")
        return RedirectResponse("/dashboard", status_code=302)


# =========================
# View upload + Issues (✅ contractor 也看得到)
# =========================
@app.get("/view_upload/{bid_id}", response_class=HTMLResponse)
async def view_upload(bid_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    bid = await db_get_bid_by_id(conn, bid_id)
    if not bid:
        raise HTTPException(status_code=404, detail="報價不存在")

    # 權限
    if user["role"] == "client" and bid.get("project_client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor" and bid.get("contractor_id") != user["id"]:
        raise HTTPException(403, "無權限")

    upload = await db_get_upload_by_bid_id(conn, bid_id)

    # fallback: pending_uploads.json
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
                            "file_path": e["file_path"],
                        }
                        break
            except Exception:
                pass

    if not upload:
        raise HTTPException(status_code=404, detail="尚未上傳檔案")

    project_id = bid["project_id"]

    # ✅ Issue：讓 view_upload.html 顯示 issue 清單 + 留言
    issues = await db_get_issues_by_project(conn, project_id)
    issue_blocks = []
    for it in issues:
        iid = it.get("id")
        comments = await db_get_issue_comments(conn, iid)
        attachments = []
        try:
            attachments = await db_get_issue_attachments(conn, iid)
        except Exception:
            attachments = []
        issue_blocks.append({"issue": it, "comments": comments, "attachments": attachments})

    try:
        open_issues = await db_count_open_issues(conn, project_id)
    except Exception:
        open_issues = 0

    return templates.TemplateResponse(
        "view_upload.html",
        {
            "request": request,
            "bid": bid,
            "upload": upload,
            "user": user,
            "open_issues": open_issues,
            "project_id": project_id,     # ✅ 給按鈕用
        },
    )


# =========================
# Messages
# =========================
@app.get("/messages/{project_id}", response_class=HTMLResponse)
async def get_project_messages(request: Request, project_id: int, conn=Depends(getDB), user: dict = Depends(get_current_user)):
    project = await db_get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="專案不存在")

    if user["role"] == "client" and project.get("client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor":
        bid = await db_get_bid_by_project_and_contractor(conn, project_id, user["id"])
        if not bid:
            raise HTTPException(403, "無權限")

    msgs = await db_get_messages(conn, project_id)
    return templates.TemplateResponse(
        "base.html",
        {"request": request, "messages": msgs, "project_id": project_id, "show_messages": True},
    )


@app.post("/messages/{project_id}")
async def post_project_message(project_id: int, content: str = Form(...), conn=Depends(getDB), user: dict = Depends(get_current_user)):
    await db_add_message(conn, project_id, user.get("id"), content)
    return RedirectResponse(url=f"/messages/{project_id}", status_code=302)


@app.post("/clear_flash")
async def clear_flash(request: Request):
    request.session.pop("flash_message", None)
    request.session.pop("flash_type", None)
    return {"status": "cleared"}


# =========================
# Issue Tracker (專案頁)
# =========================
@app.get("/issues/{project_id}", response_class=HTMLResponse)
async def issues_page(project_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    project = await db_get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(404, "專案不存在")

    # 權限：甲方或該案乙方（有 bid）
    if user["role"] == "client" and project.get("client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor":
        bid = await db_get_bid_by_project_and_contractor(conn, project_id, user["id"])
        if not bid:
            raise HTTPException(403, "無權限")

    issues = await db_get_issues_by_project(conn, project_id)

    issue_blocks = []
    for it in issues:
        issue_id = it.get("id")
        comments = await db_get_issue_comments(conn, issue_id)
        attachments = []
        try:
            attachments = await db_get_issue_attachments(conn, issue_id)
        except Exception:
            attachments = []
        issue_blocks.append({"issue": it, "comments": comments, "attachments": attachments})

    open_count = await db_count_open_issues(conn, project_id)

    return templates.TemplateResponse(
        "issues.html",
        {
            "request": request,
            "project": project,
            "issues": issue_blocks,   # ✅ 模板用 issues
            "open_count": open_count,
            "user": user,
            "session": request.session,
        },
    )


# ✅ 正確路由：/issues/{project_id}/create
@app.post("/issues/{project_id}/create")
async def create_issue(
    project_id: int,
    title: str = Form(...),
    description: str = Form(""),
    request: Request = None,
    user=Depends(get_current_user),
    conn=Depends(getDB),
):
    if user["role"] != "client":
        raise HTTPException(403, "只有甲方可以開 Issue")

    title = (title or "").strip()
    description = (description or "").strip()
    if not title:
        raise HTTPException(400, "Issue 標題不可為空")

    await db_create_issue(conn, project_id, title, description, user["id"])
    return RedirectResponse(f"/issues/{project_id}", 302)


# ✅ 相容你打的 /issues/create/{project_id}
@app.post("/issues/create/{project_id}")
async def create_issue_compat(
    project_id: int,
    title: str = Form(...),
    description: str = Form(""),
    request: Request = None,
    user=Depends(get_current_user),
    conn=Depends(getDB),
):
    return await create_issue(project_id, title, description, request, user, conn)


@app.post("/issues/{issue_id}/comment")
async def add_issue_comment(
    issue_id: int,
    content: str = Form(...),
    user=Depends(get_current_user),
    conn=Depends(getDB),
):
    issue = await db_get_issue_by_id(conn, issue_id)
    if not issue:
        raise HTTPException(404, "Issue 不存在")

    content = (content or "").strip()
    if not content:
        return RedirectResponse(f"/issues/{issue['project_id']}", 302)

    # 權限：甲方 or 該案乙方（有 bid）
    project = await db_get_project_by_id(conn, issue["project_id"])
    if user["role"] == "client" and project.get("client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor":
        bid = await db_get_bid_by_project_and_contractor(conn, issue["project_id"], user["id"])
        if not bid:
            raise HTTPException(403, "無權限")

    await db_add_issue_comment(conn, issue_id, user["id"], content)
    return RedirectResponse(f"/issues/{issue['project_id']}", 302)


# ✅ 甲方滿意後：resolve(=close)
@app.post("/issues/{issue_id}/resolve")
async def resolve_issue(issue_id: int, user=Depends(get_current_user), conn=Depends(getDB)):
    issue = await db_get_issue_by_id(conn, issue_id)
    if not issue:
        raise HTTPException(404, "Issue 不存在")

    project = await db_get_project_by_id(conn, issue["project_id"])
    if user["role"] != "client" or project.get("client_id") != user["id"]:
        raise HTTPException(403, "只有甲方可以標記完成")

    await db_close_issue(conn, issue_id, user["id"])
    return RedirectResponse(f"/issues/{issue['project_id']}", 302)


# =========================
# Issue: contractor re-upload deliverable from issue
# =========================
@app.post("/issues/{issue_id}/upload")
async def upload_from_issue(
    issue_id: int,
    request: Request,
    file: UploadFile = File(...),
    conn=Depends(getDB),
    user: dict = Depends(get_current_user),
):
    if user.get("role") != "contractor":
        raise HTTPException(403, "僅乙方可在 Issue 中上傳新檔案")

    issue = await db_get_issue_by_id(conn, issue_id)
    if not issue:
        raise HTTPException(404, "Issue 不存在")

    project_id = issue["project_id"]

    bid = await db_get_bid_by_project_and_contractor(conn, project_id, user.get("id"))
    if not bid or bid.get("status") not in ["accepted", "rejected"]:
        raise HTTPException(403, "無權限上傳（需為該案乙方且已被選擇/退件）")
    if bid.get("project_status") == "completed":
        raise HTTPException(403, "專案已結案，無法上傳")

    allowed_extensions = {".pdf", ".docx", ".txt", ".jpg", ".png"}
    raw = await file.read()
    file_size = len(raw)
    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in allowed_extensions:
        raise HTTPException(400, "不支援的檔案類型")
    if file_size > 10 * 1024 * 1024:
        raise HTTPException(400, "檔案大小超過 10MB")

    unique_filename = f"{secrets.token_hex(8)}{ext}"
    file_path = os.path.join("uploads", unique_filename)
    with open(file_path, "wb") as f:
        f.write(raw)

    # ✅ 附件表（若你表有建）
    try:
        await db_add_issue_attachment(conn, issue_id, user["id"], unique_filename, file_path)
    except Exception:
        pass

    # ✅ 交付檔更新（view_upload 看到最新）
    await db_upload_file_db(conn, bid["id"], unique_filename, file_path, user["id"])

    if bid.get("status") == "rejected":
        await db_update_bid_status(conn, bid["id"], "accepted")

    try:
        await set_project_submitted(conn, project_id)
    except Exception:
        pass

    flash(request, "已在 Issue 中上傳新版本檔案！", "success")
    return RedirectResponse(f"/issues/{project_id}", 302)


# =========================
# Complete project (must have 0 open issues)
# =========================
@app.post("/complete_project/{project_id}")
async def complete_project(project_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    if user["role"] != "client":
        raise HTTPException(403, "僅委託人可結案")

    project = await db_get_project_by_id(conn, project_id)
    if not project or project.get("client_id") != user["id"]:
        raise HTTPException(404, "專案不存在")

    open_count = await db_count_open_issues(conn, project_id)
    if open_count > 0:
        flash(request, "尚有未完成 Issue，請先全部處理完成再結案。", "warning")
        return RedirectResponse(f"/issues/{project_id}", 302)

    try:
        await db_update_project_status(conn, project_id, "completed")

        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE bids SET status = 'completed' WHERE project_id = %s AND status = 'accepted'",
                (project_id,),
            )
            await conn.commit()

        flash(request, "專案已成功結案！", "success")
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"結案失敗: {str(e)}")

    return RedirectResponse("/dashboard", 302)


# ✅ 讓模板不用改：/projects/{{ project.id }}/complete_if_no_open_issues
@app.post("/projects/{project_id}/complete_if_no_open_issues")
async def complete_if_no_open_issues(project_id: int, request: Request, user=Depends(get_current_user), conn=Depends(getDB)):
    return await complete_project(project_id, request, user, conn)


# =========================
# Optional: Issue page quick view latest deliverable
# =========================
@app.get("/issues/{project_id}/view_latest", response_class=HTMLResponse)
async def issue_view_latest_upload(
    project_id: int,
    request: Request,
    user=Depends(get_current_user),
    conn=Depends(getDB),
):
    project = await db_get_project_by_id(conn, project_id)
    if not project:
        raise HTTPException(404, "專案不存在")

    if user["role"] == "client" and project.get("client_id") != user["id"]:
        raise HTTPException(403, "無權限")
    if user["role"] == "contractor":
        bid = await db_get_bid_by_project_and_contractor(conn, project_id, user["id"])
        if not bid:
            raise HTTPException(403, "無權限")

    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM bids WHERE project_id=%s AND status IN ('accepted','completed') ORDER BY id DESC LIMIT 1",
            (project_id,),
        )
        row = await cur.fetchone()

    if not row:
        raise HTTPException(404, "尚未有可檢視的交付檔案")

    bid_id = row[0] if isinstance(row, tuple) else row.get("id")
    return RedirectResponse(f"/view_upload/{bid_id}", 302)
