#!/bin/bash
# 啟動腳本 - 使用正確的資料庫設定

# 設定環境變數
export DB_HOST=localhost
export DB_PORT=5433
export DB_NAME=bidding_system
export DB_USER=your_user
export DB_PASSWORD=your_password

# 切換到專案目錄
cd /Users/a-----/Downloads/final_se_proj-main

# 停止舊進程
pkill -9 -f "uvicorn main:app" 2>/dev/null
sleep 2

# 啟動應用程式
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
