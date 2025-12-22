# routes/dbQuery.py
# 這個模組包含所有與資料庫互動的查詢函數
# 主要功能：
# 1. 定義資料模型
# 2. 處理資料庫 CRUD 操作
# 3. 處理業務邏輯相關的資料庫查詢
# 4. 錯誤處理和日誌記錄

from pydantic import BaseModel  # 用於資料驗證
from fastapi import HTTPException  # HTTP 錯誤處理
from psycopg import rows, errors as psycopg_errors  # PostgreSQL 資料庫操作
import logging  # 日誌記錄
from datetime import datetime  # 日期時間處理

# 設定日誌記錄器
logger = logging.getLogger("app")

# === 資料模型定義 ===
class Project(BaseModel):
    id: int | None = None
    title: str
    description: str
    budget: float
    deadline: str
    status: str
    client_id: int

class Bid(BaseModel):
    id: int | None = None
    project_id: int
    contractor_id: int
    price: float
    status: str

# === 專案相關 ===
async def create_project(conn, project: Project):
    try:
        print(f"[DB DEBUG] create_project called with project: {project.model_dump() if hasattr(project, 'model_dump') else project}")
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO projects (title, description, budget, deadline, status, client_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (project.title, project.description, project.budget, project.deadline, project.status, project.client_id),
            )
            result = await cur.fetchone()
            if not result:
                raise HTTPException(status_code=500, detail="無法創建專案")
            new_id = result[0] if isinstance(result, tuple) else result.get("id")
            new_id_int = int(new_id)
            if new_id_int <= 0:
                await conn.rollback()
                raise HTTPException(status_code=500, detail="創建專案回傳的 id 無效")
            await conn.commit()
            print(f"[DB DEBUG] created project id: {new_id_int}")
            return new_id_int
    except Exception as e:
        import traceback
        print(f"[DB DEBUG] create_project exception: {traceback.format_exc()}")
        if hasattr(conn, 'rollback'):
            try: await conn.rollback()
            except: pass
        raise HTTPException(status_code=500, detail=f"創建專案時發生錯誤: {str(e)}")

# routes/dbQuery.py
# routes/dbQuery.py
async def get_projects_by_client(conn, client_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            SELECT 
                p.*,
                COALESCE(json_agg(
                    json_build_object(
                        'id', b.id,
                        'contractor_id', b.contractor_id,
                        'contractor_name', u.username,
                        'price', b.price,
                        'status', b.status,
                        'upload_filename', s.filename,
                        'upload_path', s.file_path,
                        'upload_time', s.uploaded_at,
                        'can_view', (s.filename IS NOT NULL AND b.status != 'rejected')
                    )
                    ORDER BY b.id DESC
                ) FILTER (WHERE b.id IS NOT NULL), '[]') AS bids
            FROM projects p
            LEFT JOIN bids b ON p.id = b.project_id
            LEFT JOIN users u ON b.contractor_id = u.id
            LEFT JOIN submissions s ON b.project_id = s.project_id AND s.uploaded_by = b.contractor_id
            WHERE p.client_id = %s
            GROUP BY p.id
            ORDER BY p.id DESC
            """,
            (client_id,)
        )
        results = await cur.fetchall()
        print(type(results))
        # 確保 bids 是 list
        for row in results:
            if row['bids'] is None:
                row['bids'] = []
        return results

async def get_all_projects(conn):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT * FROM projects WHERE status='open' ORDER BY id DESC")
            return await cur.fetchall()
            print(f"[DB DEBUG] Fetched projects: {results}")
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=f"資料庫查詢錯誤: {str(e)}")

async def get_project_by_id(conn, project_id):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            return await cur.fetchone()
        
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=f"資料庫查詢錯誤: {str(e)}")

async def update_project(conn, project_id: int, title: str, description: str, budget: float, deadline: str, client_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE projects SET title=%s, description=%s, budget=%s, deadline=%s WHERE id=%s AND client_id=%s",
            (title, description, budget, deadline, project_id, client_id),
        )
        await conn.commit()

async def set_project_status(conn, project_id: int, status: str, client_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE projects SET status=%s WHERE id=%s AND client_id=%s",
            (status, project_id, client_id),
        )
        await conn.commit()

# === 報價相關 ===
async def create_bid(conn, bid: Bid):
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, status FROM bids WHERE project_id=%s AND contractor_id=%s ORDER BY id DESC LIMIT 1",
                (bid.project_id, bid.contractor_id),
            )
            existing = await cur.fetchone()
            if existing:
                existing_id = existing[0] if isinstance(existing, tuple) else existing.get("id")
                existing_status = existing[1] if isinstance(existing, tuple) else existing.get("status")
                if existing_status == 'accepted':
                    raise HTTPException(status_code=400, detail="已被接受的報價不可覆蓋")
                await cur.execute(
                    "UPDATE bids SET price=%s, status=%s, created_at=now() WHERE id=%s RETURNING id",
                    (bid.price, 'pending', existing_id),
                )
            else:
                await cur.execute(
                    "INSERT INTO bids (project_id, contractor_id, price, status) VALUES (%s, %s, %s, %s) RETURNING id",
                    (bid.project_id, bid.contractor_id, bid.price, 'pending'),
                )
            result = await cur.fetchone()
            if not result:
                raise HTTPException(status_code=500, detail="無法建立或更新報價")
            new_id = result[0] if isinstance(result, tuple) else result.get("id")
            await conn.commit()
            return int(new_id)
    except Exception as e:
        import traceback
        print(f"[DB DEBUG] create_bid error: {traceback.format_exc()}")
        try: await conn.rollback()
        except: pass
        error_msg = str(e)
        if "column" in error_msg and "does not exist" in error_msg:
            error_msg = f"資料庫欄位錯誤: {error_msg}\n建議：檢查資料表結構"
        elif "permission" in error_msg.lower():
            error_msg = f"權限不足: {error_msg}\n建議：授予資料庫寫入權限"
        raise HTTPException(status_code=500, detail=error_msg)

# routes/dbQuery.py
# dbQuery.py
# dbQuery.py
async def get_bids_by_contractor(conn, contractor_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 
                b.id, b.project_id, b.price, b.status,
                p.title AS project_title,
                p.deadline AS project_deadline,
                p.status AS project_status,
                s.filename AS upload_filename
            FROM bids b
            JOIN projects p ON b.project_id = p.id
            LEFT JOIN submissions s ON b.id = s.bid_id 
                AND s.uploaded_at = (
                    SELECT MAX(uploaded_at) 
                    FROM submissions s2 
                    WHERE s2.bid_id = b.id
                )
            WHERE b.contractor_id = %s
            ORDER BY b.created_at DESC
            """,
            (contractor_id,)
        )
        rows = await cur.fetchall()
        return [dict(row) for row in rows]
    
async def get_bid_by_project_and_contractor(conn, project_id, contractor_id):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT 
                    b.*,
                    p.status as project_status
                FROM bids b
                JOIN projects p ON b.project_id = p.id
                WHERE b.project_id = %s AND b.contractor_id = %s
                """,
                (project_id, contractor_id),
            )
            return await cur.fetchone()
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=f"資料庫查詢錯誤: {str(e)}")

async def accept_bid(conn, bid_id, client_id):
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT b.id, p.client_id, b.project_id FROM bids b JOIN projects p ON b.project_id = p.id WHERE b.id = %s",
                (bid_id,),
            )
            row = await cur.fetchone()
            if not row:
                raise HTTPException(status_code=403, detail="無權限或報價不存在")
            row_client_id = row[1] if isinstance(row, tuple) else row.get("client_id")
            project_id = row[2] if isinstance(row, tuple) else row.get("project_id")
            if row_client_id != client_id:
                raise HTTPException(status_code=403, detail="無權限或報價不存在")
            await cur.execute("UPDATE bids SET status='accepted' WHERE id=%s", (bid_id,))
            await cur.execute("UPDATE bids SET status='rejected' WHERE project_id=%s AND id<>%s", (project_id, bid_id))
            await cur.execute("UPDATE projects SET status='in_progress' WHERE id=%s", (project_id,))
            await conn.commit()
    except Exception:
        await conn.rollback()
        raise

# === 檔案上傳 === upload_file_db
## routes/dbQuery.py
# dbQuery.py
async def db_upload_file_db(conn, bid_id: int, filename: str, file_path: str, uploader_id: int):
    try:
        async with conn.cursor() as cur:
            # 取得 project_id
            await cur.execute("SELECT project_id FROM bids WHERE id = %s", (bid_id,))
            result = await cur.fetchone()
            if not result:
                raise HTTPException(404, "報價不存在")
            project_id = result["project_id"]

            # 檢查是否已有上傳記錄
            await cur.execute(
                "SELECT id FROM submissions WHERE bid_id = %s ORDER BY uploaded_at DESC LIMIT 1",
                (bid_id,)
            )
            existing = await cur.fetchone()

            if existing:
                # 更新舊記錄
                await cur.execute(
                    """
                    UPDATE submissions 
                    SET filename = %s, file_path = %s, uploaded_by = %s, uploaded_at = NOW()
                    WHERE id = %s
                    """,
                    (filename, file_path, uploader_id, existing["id"])
                )
                print(f"[DB DEBUG] Updated submission for bid {bid_id}: {filename}")
            else:
                # 新增
                await cur.execute(
                    """
                    INSERT INTO submissions 
                    (bid_id, project_id, filename, file_path, uploaded_by, uploaded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (bid_id, project_id, filename, file_path, uploader_id)
                )
                print(f"[DB DEBUG] Inserted new submission for bid {bid_id}: {filename}")

            await conn.commit()
        return {"success": True}

    except Exception as e:
        await conn.rollback()
        import traceback
        print(f"[DB ERROR] {traceback.format_exc()}")
        raise HTTPException(500, f"上傳失敗: {str(e)}")
        
# === 檢視上傳檔案 ===
async def db_get_bid_by_id(conn, bid_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 
                b.id, b.project_id, b.contractor_id, b.price, b.status,
                p.client_id AS project_client_id,
                p.title AS project_title,
                p.status AS project_status,
                u.username AS contractor_name
            FROM bids b
            JOIN projects p ON b.project_id = p.id
            JOIN users u ON b.contractor_id = u.id
            WHERE b.id = %s
            """,
            (bid_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

# dbQuery.py
async def db_get_upload_by_bid_id(conn, bid_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT s.*, b.project_id 
            FROM submissions s
            JOIN bids b ON s.bid_id = b.id
            WHERE s.bid_id = %s
            ORDER BY s.uploaded_at DESC
            LIMIT 1
            """,
            (bid_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

# === 狀態更新 ===
async def db_update_bid_status(conn, bid_id: int, status: str):
    async with conn.cursor() as cur:
        await cur.execute("UPDATE bids SET status = %s WHERE id = %s", (status, bid_id))
        await conn.commit()

async def db_update_project_status(conn, project_id: int, status: str):
    async with conn.cursor() as cur:
        await cur.execute("UPDATE projects SET status = %s WHERE id = %s", (status, project_id))
        await conn.commit()

# === 使用者 ===
async def upsert_user(conn, username: str, password_hash: str, role: str):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE
            SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            RETURNING id
            """,
            (username, password_hash, role)
        )
        result = await cur.fetchone()
        return result["id"] if result else None

async def get_user_by_credentials(conn, username: str):
    async with conn.cursor() as cur:
            await cur.execute("""
                SELECT
                    id,
                    username,
                    password_hash,
                    role
                FROM users
                WHERE username = %s
                """,
                (username,)
            )
            row = await cur.fetchone()
            if row:
                return {
                    "id": row[0] if isinstance(row, tuple) else row.get("id"),
                    "username": row[1] if isinstance(row, tuple) else row.get("username"),
                    "password_hash": row[2] if isinstance(row, tuple) else row.get("password_hash"),
                    "role": row[3] if isinstance(row, tuple) else row.get("role")
                }
            return None

# === 訊息 ===
async def get_messages(conn, project_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            "SELECT m.*, u.username FROM messages m JOIN users u ON m.sender_id=u.id WHERE m.project_id=%s ORDER BY m.id ASC",
            (project_id,),
        )
        return await cur.fetchall()

async def add_message(conn, project_id: int, sender_id: int, content: str):
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO messages (project_id, sender_id, content) VALUES (%s, %s, %s)",
            (project_id, sender_id, content),
        )
        await conn.commit()

# === 其他 ===
async def get_bids_for_client_projects(conn, client_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            SELECT
                b.*,
                p.title as project_title,
                u.username as contractor_name
            FROM bids b
            JOIN projects p ON b.project_id = p.id
            JOIN users u ON b.contractor_id = u.id
            WHERE p.client_id = %s
            ORDER BY b.id DESC
            """,
            (client_id,),
        )
        return await cur.fetchall()
    
    # routes/dbQuery.py
# === 新增：拒絕其他報價 ===
async def reject_other_bids(conn, project_id: int, accepted_bid_id: int):
    """接受一個報價後，自動將同專案的其他 pending 報價設為 rejected"""
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE bids 
                SET status = 'rejected' 
                WHERE project_id = %s 
                  AND id != %s 
                  AND status = 'pending'
                """,
                (project_id, accepted_bid_id)
            )
            await conn.commit()
        print(f"[DB] Rejected other bids for project {project_id}")
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=f"拒絕其他報價失敗: {str(e)}")

# === 新增：上傳後設專案為 submitted ===
# routes/dbQuery.py
async def set_project_submitted(conn, project_id: int):
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE projects SET status = 'submitted' WHERE id = %s AND status = 'in_progress'",
                (project_id,)
            )
            await conn.commit()
    except Exception as e:
        print(f"[DB INFO] 無法更新專案 {project_id} 為 submitted: {e}")

# === 新增：結案時同步 bid 狀態 ===
async def complete_bid_for_project(conn, project_id: int):
    """將該專案下 accepted 的 bid 設為 completed"""
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE bids SET status = 'completed' WHERE project_id = %s AND status = 'accepted'",
                (project_id,)
            )
            await conn.commit()
    except Exception as e:
        await conn.rollback()
        raise HTTPException(status_code=500, detail=f"結案同步 bid 失敗: {str(e)}")