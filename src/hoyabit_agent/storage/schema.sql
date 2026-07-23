-- 分析回合的 schema。
--
-- 為什麼是 Postgres + pgvector 而不是純向量庫：證據溯源本質上是一連串 join
-- （判斷 → 證據 ID → 來源片段 → 出處），那是 SQL 擅長而向量庫不擅長的。
-- 用一套儲存體解決向量與關聯式，省掉「兩邊資料不一致」這個最不划算的問題。

CREATE EXTENSION IF NOT EXISTS vector;

-- 一次分析回合。被幣種閘門拒絕的回合也會留下紀錄（report_* 欄位為 NULL），
-- 因為「我們拒絕了什麼」本身就是要能稽核的事。
CREATE TABLE IF NOT EXISTS analysis_run (
    run_id            TEXT PRIMARY KEY,
    asset             TEXT,
    stance            TEXT,
    rejection_reason  TEXT,
    -- 信心度可能算不出來，所以值可為 NULL，但「為什麼算不出來」一定要記。
    confidence_value          DOUBLE PRECISION,
    confidence_cause          TEXT,
    confidence_facet_stances  JSONB NOT NULL DEFAULT '{}'::jsonb,
    facets_present            JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 證據。不可變，識別碼在同一個回合內唯一。
-- 刻意以 (run_id, evidence_id) 為主鍵而非全域唯一：同一項證據在不同回合
-- 會有不同的擷取時間與上下文，硬要去重反而會讓某次分析的溯源指向別次的資料。
CREATE TABLE IF NOT EXISTS evidence (
    run_id       TEXT NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    evidence_id  TEXT NOT NULL,
    facet        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    stance_hint  DOUBLE PRECISION NOT NULL,
    event_key    TEXT,
    position     INTEGER NOT NULL,
    PRIMARY KEY (run_id, evidence_id)
);

-- 來源片段。**傾向分數只存在這一層** —— 分數永遠屬於一則片段。
-- 不存在「BTC 的情緒分數」，那是情緒彙總，必須能列舉出組成它的每一則片段。
-- 因此這裡刻意沒有任何幣種層級的分數欄位。
CREATE TABLE IF NOT EXISTS source_excerpt (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    url           TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    locator       TEXT NOT NULL,
    text          TEXT NOT NULL,
    position      INTEGER NOT NULL,
    FOREIGN KEY (run_id, evidence_id) REFERENCES evidence(run_id, evidence_id) ON DELETE CASCADE
);

-- 判斷。`kept` 為 false 代表它沒通過引用檢核 —— 被丟棄的判斷同樣要留下來，
-- 軌跡前端要顯示「系統拒絕了什麼」，那是檢核確實在運作的證明。
CREATE TABLE IF NOT EXISTS claim (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    facet         TEXT NOT NULL,
    evidence_ids  JSONB NOT NULL,
    kept          BOOLEAN NOT NULL,
    position      INTEGER NOT NULL
);

-- 推論軌跡。seq 保證順序 —— 軌跡的意義有一半在「先後」。
CREATE TABLE IF NOT EXISTS trace_node (
    run_id           TEXT NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    kind             TEXT NOT NULL,
    reason           TEXT NOT NULL,
    evidence_ids     JSONB NOT NULL,
    gap_before       JSONB NOT NULL,
    gap_after        JSONB NOT NULL,
    elapsed_seconds  DOUBLE PRECISION NOT NULL,
    detail           JSONB NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE INDEX IF NOT EXISTS evidence_by_facet ON evidence (run_id, facet);
CREATE INDEX IF NOT EXISTS excerpt_by_evidence ON source_excerpt (run_id, evidence_id);
CREATE INDEX IF NOT EXISTS run_by_recency ON analysis_run (created_at DESC);
