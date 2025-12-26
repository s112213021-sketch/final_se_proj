"""
資料庫連線管理模組
使用 psycopg3 (AsyncConnectionPool) 管理 PostgreSQL 連線
"""

import os
import psycopg_pool
import psycopg
from psycopg import rows

# === 資料庫設定 ===
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "your_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "your_password")

def _get_dsn() -> str:
    """生成資料庫連線字串"""
    return f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"

# 建立異步連線池
pool = None

def init_pool():
    """初始化連線池"""
    global pool
    if pool is None:
        pool = psycopg_pool.AsyncConnectionPool(
            conninfo=_get_dsn(),
            min_size=2,
            max_size=10,
            timeout=30.0,
        )
    return pool

async def getDB():
    """
    獲取資料庫連線（依賴注入用）

    用法：
        @app.get("/example")
        async def example_route(conn=Depends(getDB)):
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM users")
    """
    global pool
    if pool is None:
        pool = init_pool()

    async with pool.connection() as conn:
        yield conn

# 連線池會在第一次使用時初始化（延遲初始化）
# 這樣可以避免在 import 時就連線資料庫
