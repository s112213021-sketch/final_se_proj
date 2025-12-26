# routes/dbQuery.py
from pydantic import BaseModel
from fastapi import HTTPException
from psycopg import rows
import logging

logger = logging.getLogger("app")


# =========================
# Models
# =========================
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


# =========================
# Projects
# =========================
async def create_project(conn, project: Project):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO projects (title, description, budget, deadline, status, client_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (project.title, project.description, project.budget, project.deadline, project.status, project.client_id),
            )
            result = await cur.fetchone()
            if not result:
                raise HTTPException(500, "無法創建專案")
            new_id = int(result["id"])
            await conn.commit()
            return new_id
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"創建專案時發生錯誤: {str(e)}")


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
        for row in results:
            if row["bids"] is None:
                row["bids"] = []
        return results


async def get_all_projects(conn):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT * FROM projects WHERE status='open' ORDER BY id DESC")
            return await cur.fetchall()
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"資料庫查詢錯誤: {str(e)}")


async def get_project_by_id(conn, project_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            return await cur.fetchone()
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"資料庫查詢錯誤: {str(e)}")


async def update_project(conn, project_id: int, title: str, description: str, budget: float, deadline: str, client_id: int):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE projects
            SET title=%s, description=%s, budget=%s, deadline=%s
            WHERE id=%s AND client_id=%s
            """,
            (title, description, budget, deadline, project_id, client_id),
        )
        await conn.commit()


# =========================
# Bids
# =========================
async def create_bid(conn, bid: Bid):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT id, status
                FROM bids
                WHERE project_id=%s AND contractor_id=%s
                ORDER BY id DESC
                LIMIT 1
                """,
                (bid.project_id, bid.contractor_id),
            )
            existing = await cur.fetchone()

            if existing:
                if existing["status"] == "accepted":
                    raise HTTPException(400, "已被接受的報價不可覆蓋")
                await cur.execute(
                    """
                    UPDATE bids
                    SET price=%s, status='pending', created_at=now()
                    WHERE id=%s
                    RETURNING id
                    """,
                    (bid.price, existing["id"]),
                )
            else:
                await cur.execute(
                    """
                    INSERT INTO bids (project_id, contractor_id, price, status)
                    VALUES (%s, %s, %s, 'pending')
                    RETURNING id
                    """,
                    (bid.project_id, bid.contractor_id, bid.price),
                )

            result = await cur.fetchone()
            if not result:
                raise HTTPException(500, "無法建立或更新報價")
            await conn.commit()
            return int(result["id"])
    except HTTPException:
        raise
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"建立報價失敗: {str(e)}")


async def get_bids_by_contractor(conn, contractor_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
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
        rows_ = await cur.fetchall()
        return [dict(r) for r in rows_]


async def get_bid_by_project_and_contractor(conn, project_id: int, contractor_id: int):
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
                ORDER BY b.id DESC
                LIMIT 1
                """,
                (project_id, contractor_id),
            )
            return await cur.fetchone()
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"資料庫查詢錯誤: {str(e)}")


async def reject_other_bids(conn, project_id: int, accepted_bid_id: int):
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
                (project_id, accepted_bid_id),
            )
            await conn.commit()
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"拒絕其他報價失敗: {str(e)}")


async def db_update_bid_status(conn, bid_id: int, status: str):
    async with conn.cursor() as cur:
        await cur.execute("UPDATE bids SET status = %s WHERE id = %s", (status, bid_id))
        await conn.commit()


async def db_update_project_status(conn, project_id: int, status: str):
    async with conn.cursor() as cur:
        await cur.execute("UPDATE projects SET status = %s WHERE id = %s", (status, project_id))
        await conn.commit()


async def set_project_submitted(conn, project_id: int):
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE projects SET status='submitted' WHERE id=%s AND status='in_progress'",
                (project_id,),
            )
            await conn.commit()
    except Exception as e:
        logger.info("set_project_submitted failed: %s", e)


# =========================
# Submissions / Upload
# =========================
async def db_upload_file_db(conn, bid_id: int, filename: str, file_path: str, uploader_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT project_id FROM bids WHERE id=%s", (bid_id,))
            bid_row = await cur.fetchone()
            if not bid_row:
                raise HTTPException(404, "報價不存在")
            project_id = bid_row["project_id"]

            await cur.execute(
                "SELECT id FROM submissions WHERE bid_id=%s ORDER BY uploaded_at DESC LIMIT 1",
                (bid_id,),
            )
            existing = await cur.fetchone()

            if existing:
                await cur.execute(
                    """
                    UPDATE submissions
                    SET filename=%s, file_path=%s, uploaded_by=%s, uploaded_at=NOW()
                    WHERE id=%s
                    """,
                    (filename, file_path, uploader_id, existing["id"]),
                )
            else:
                await cur.execute(
                    """
                    INSERT INTO submissions (bid_id, project_id, filename, file_path, uploaded_by, uploaded_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (bid_id, project_id, filename, file_path, uploader_id),
                )

            await conn.commit()
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"上傳失敗: {str(e)}")


async def db_get_bid_by_id(conn, bid_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
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
            (bid_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def db_get_upload_by_bid_id(conn, bid_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            SELECT s.*, b.project_id
            FROM submissions s
            JOIN bids b ON s.bid_id = b.id
            WHERE s.bid_id = %s
            ORDER BY s.uploaded_at DESC
            LIMIT 1
            """,
            (bid_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# =========================
# Users
# =========================
async def upsert_user(conn, username: str, password_hash: str, role: str):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            INSERT INTO users (username, password_hash, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE
            SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role
            RETURNING id
            """,
            (username, password_hash, role),
        )
        result = await cur.fetchone()
        return result["id"] if result else None


async def get_user_by_credentials(conn, username: str):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            SELECT id, username, password_hash, role
            FROM users
            WHERE username = %s
            """,
            (username,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# =========================
# Messages
# =========================
async def get_messages(conn, project_id: int):
    async with conn.cursor(row_factory=rows.dict_row) as cur:
        await cur.execute(
            """
            SELECT m.*, u.username
            FROM messages m
            JOIN users u ON m.sender_id=u.id
            WHERE m.project_id=%s
            ORDER BY m.id ASC
            """,
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


# =========================
# Issue Tracker
# =========================
async def db_create_issue(conn, project_id: int, title: str, description: str, created_by: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO issues (project_id, title, description, created_by, status)
                VALUES (%s, %s, %s, %s, 'open')
                RETURNING id
                """,
                (project_id, title, description, created_by),
            )
            row = await cur.fetchone()
            await conn.commit()
            return int(row["id"])
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"建立 Issue 失敗: {str(e)}")


async def db_get_issues_by_project(conn, project_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT
                  i.*,
                  u1.username AS creator_name,
                  u2.username AS assignee_name
                FROM issues i
                JOIN users u1 ON i.created_by = u1.id
                LEFT JOIN users u2 ON i.assigned_to = u2.id
                WHERE i.project_id = %s
                ORDER BY i.created_at DESC
                """,
                (project_id,),
            )
            return await cur.fetchall()
    except Exception as e:
        raise HTTPException(500, f"讀取 Issue 失敗: {str(e)}")


async def db_get_issue_by_id(conn, issue_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute("SELECT * FROM issues WHERE id = %s", (issue_id,))
            return await cur.fetchone()
    except Exception as e:
        raise HTTPException(500, f"讀取 Issue 失敗: {str(e)}")


async def db_add_issue_comment(conn, issue_id: int, author_id: int, content: str):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO issue_comments (issue_id, author_id, content)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (issue_id, author_id, content),
            )
            row = await cur.fetchone()
            await conn.commit()
            return int(row["id"])
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"新增留言失敗: {str(e)}")


async def db_get_issue_comments(conn, issue_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT c.*, u.username AS author_name
                FROM issue_comments c
                JOIN users u ON c.author_id = u.id
                WHERE c.issue_id = %s
                ORDER BY c.created_at ASC
                """,
                (issue_id,),
            )
            return await cur.fetchall()
    except Exception as e:
        raise HTTPException(500, f"讀取留言失敗: {str(e)}")


async def db_close_issue(conn, issue_id: int, closed_by: int):
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE issues
                SET status='closed',
                    closed_at=NOW(),
                    closed_by=%s
                WHERE id=%s
                """,
                (closed_by, issue_id),
            )
            await conn.commit()
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"關閉 Issue 失敗: {str(e)}")


async def db_count_open_issues(conn, project_id: int) -> int:
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM issues
                WHERE project_id=%s
                  AND status IN ('open', 'in_progress')
                """,
                (project_id,),
            )
            row = await cur.fetchone()
            return int(row["cnt"])
    except Exception as e:
        raise HTTPException(500, f"計算 Issue 數量失敗: {str(e)}")


# =========================
# Issue Attachments
# =========================
async def db_add_issue_attachment(conn, issue_id: int, uploader_id: int, filename: str, file_path: str):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                INSERT INTO issue_attachments (issue_id, uploader_id, filename, file_path)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (issue_id, uploader_id, filename, file_path),
            )
            row = await cur.fetchone()
            await conn.commit()
            return int(row["id"])
    except Exception as e:
        try:
            await conn.rollback()
        except Exception:
            pass
        raise HTTPException(500, f"新增附件失敗: {str(e)}")


async def db_get_issue_attachments(conn, issue_id: int):
    try:
        async with conn.cursor(row_factory=rows.dict_row) as cur:
            await cur.execute(
                """
                SELECT a.*, u.username AS uploader_name
                FROM issue_attachments a
                JOIN users u ON a.uploader_id = u.id
                WHERE a.issue_id = %s
                ORDER BY a.uploaded_at DESC
                """,
                (issue_id,),
            )
            return await cur.fetchall()
    except Exception as e:
        raise HTTPException(500, f"讀取附件失敗: {str(e)}")
