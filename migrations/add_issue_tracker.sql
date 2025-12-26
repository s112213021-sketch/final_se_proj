-- =========================================
-- Issue Tracker System Migration
-- 整合自 final_se_proj-ex3
-- 日期: 2025-12-26
-- =========================================

-- 1. Issues 表 - 問題追蹤主表
CREATE TABLE IF NOT EXISTS issues (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    assigned_to INTEGER REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'closed')),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMP,
    closed_by INTEGER REFERENCES users(id)
);

-- 2. Issue Comments 表 - 問題討論留言
CREATE TABLE IF NOT EXISTS issue_comments (
    id SERIAL PRIMARY KEY,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 3. Issue Attachments 表 - 問題附件（修正檔案）
CREATE TABLE IF NOT EXISTS issue_attachments (
    id SERIAL PRIMARY KEY,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    uploader_id INTEGER NOT NULL REFERENCES users(id),
    filename VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 建立索引以提升查詢效能
CREATE INDEX IF NOT EXISTS idx_issues_project_id ON issues(project_id);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issue_comments_issue_id ON issue_comments(issue_id);
CREATE INDEX IF NOT EXISTS idx_issue_attachments_issue_id ON issue_attachments(issue_id);

-- 註解說明
COMMENT ON TABLE issues IS 'Issue 追蹤表 - 記錄專案問題與待解決事項';
COMMENT ON TABLE issue_comments IS 'Issue 留言表 - 記錄 Issue 討論內容';
COMMENT ON TABLE issue_attachments IS 'Issue 附件表 - 記錄修正檔案';

COMMENT ON COLUMN issues.status IS '狀態: open(未處理), in_progress(處理中), closed(已關閉)';
COMMENT ON COLUMN issues.created_by IS '建立者 ID (通常是委託人)';
COMMENT ON COLUMN issues.assigned_to IS '指派給誰 (通常是承包商)';
COMMENT ON COLUMN issues.closed_by IS '關閉者 ID (通常是委託人)';
