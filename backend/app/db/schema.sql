-- ============================================================================
-- PRAGMAs (Executed on connection initialization)
-- ============================================================================
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 30000;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -64000; -- 64 MB page cache

-- ============================================================================
-- 1. DATASETS (Supports both Video-to-Dataset and Pre-sorted Image Folders)
-- ============================================================================
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,                          -- UUIDv4 or deterministic slug hash
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('image_folder', 'video', 'hybrid')),
    source_path TEXT NOT NULL,                    -- Path under /data/input
    status TEXT NOT NULL DEFAULT 'created' CHECK (
        status IN ('created', 'ingesting', 'curating', 'captioning', 'ready_to_export', 'exported', 'error')
    ),
    trigger_word TEXT NOT NULL DEFAULT 'ohwx_subject',
    trigger_position TEXT NOT NULL DEFAULT 'prefix' CHECK (
        trigger_position IN ('prefix', 'suffix', 'natural_integration')
    ),
    active_skill_id TEXT REFERENCES skills(id) ON DELETE SET NULL,
    target_count INTEGER NOT NULL DEFAULT 150,
    dedup_hamming_threshold INTEGER NOT NULL DEFAULT 6,
    video_metadata TEXT DEFAULT '{}',             -- JSON: {duration_s, fps, width, height, codec, extraction_mode, scene_threshold}
    stats_cache TEXT DEFAULT '{}',                -- JSON: cached counts & stratification summary
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_datasets_status ON datasets(status);

-- ============================================================================
-- 2. CLUSTERS (Perceptual Hash Deduplication Groups)
-- ============================================================================
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    representative_media_id INTEGER,              -- Best Q-score item in cluster
    centroid_phash TEXT NOT NULL,                 -- 16-char hex 64-bit pHash
    centroid_dhash TEXT NOT NULL,                 -- 16-char hex 64-bit dHash
    member_count INTEGER NOT NULL DEFAULT 1,
    max_quality_score REAL NOT NULL DEFAULT 0.0,
    scene_index INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_clusters_dataset ON clusters(dataset_id);
CREATE INDEX IF NOT EXISTS ix_clusters_phash ON clusters(dataset_id, centroid_phash);

-- ============================================================================
-- 3. MEDIA_ITEMS (Unified Images & Extracted Video Frames)
-- ============================================================================
CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    cluster_id INTEGER REFERENCES clusters(id) ON DELETE SET NULL,
    is_cluster_representative INTEGER NOT NULL DEFAULT 1,
    source_filename TEXT NOT NULL,
    source_path TEXT NOT NULL,                    -- Original file or video path
    extracted_path TEXT,                          -- Native-res frame path on Linux volume
    thumbnail_path TEXT,                          -- 384px WebP preview path on Linux volume
    timestamp_s REAL,                             -- Video PTS in seconds (NULL for static images)
    scene_index INTEGER DEFAULT 0,
    frame_extract_type TEXT DEFAULT 'image' CHECK (
        frame_extract_type IN ('image', 'scene_cut', 'iframe', 'interval_peak')
    ),
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    aspect_ratio REAL NOT NULL DEFAULT 1.0,
    -- Mathematical Quality Metrics (Module A)
    phash TEXT,                                   -- 16-char hex 64-bit DCT hash
    dhash TEXT,                                   -- 16-char hex 64-bit gradient hash
    hsv_hist BLOB,                                -- 24-float32 L2-normalized HSV histogram
    sharpness_laplacian REAL DEFAULT 0.0,
    sharpness_tenengrad REAL DEFAULT 0.0,
    sharpness_score REAL DEFAULT 0.0,             -- Normalized [0, 1]
    exposure_mean_luma REAL DEFAULT 128.0,
    exposure_clipping_frac REAL DEFAULT 0.0,      -- Fraction of pixels < 5 or > 250
    exposure_score REAL DEFAULT 0.0,              -- Normalized [0, 1]
    entropy_shannon REAL DEFAULT 0.0,             -- Bits [0, 8]
    entropy_score REAL DEFAULT 0.0,               -- Normalized [0, 1]
    contrast_rms REAL DEFAULT 0.0,
    quality_score REAL NOT NULL DEFAULT 0.0,      -- Composite Q in [0, 1]
    -- HITL Triage & Curation State (Module B)
    curation_state TEXT NOT NULL DEFAULT 'candidate' CHECK (
        curation_state IN ('keep', 'star', 'reject', 'candidate', 'duplicate_hidden', 'auto_rejected')
    ),
    reject_reason TEXT,
    crop_geometry TEXT,                           -- JSON: {x, y, width, height} normalized [0, 1]
    is_outlier INTEGER NOT NULL DEFAULT 0,
    -- Captioning State (Module C)
    active_caption TEXT,
    caption_medium TEXT,
    caption_short TEXT,
    caption_status TEXT NOT NULL DEFAULT 'uncaptioned' CHECK (
        caption_status IN ('uncaptioned', 'queued', 'vlm_accepted', 'needs_manual_caption', 'manual', 'approved')
    ),
    active_caption_model TEXT,
    active_caption_layer TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_media_dataset_state ON media_items(dataset_id, curation_state);
CREATE INDEX IF NOT EXISTS ix_media_dataset_quality ON media_items(dataset_id, quality_score DESC);
CREATE INDEX IF NOT EXISTS ix_media_cluster ON media_items(cluster_id);
CREATE INDEX IF NOT EXISTS ix_media_caption_status ON media_items(dataset_id, caption_status);
CREATE INDEX IF NOT EXISTS ix_media_phash_prefix ON media_items(dataset_id, substr(phash, 1, 4));

-- ============================================================================
-- 4. TAGS & 5. MEDIA_TAGS (Module B: Manual, Rule-Based, and ML Plugin Tags)
-- ============================================================================
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'style_element' CHECK (
        category IN ('pose', 'lighting', 'outfit', 'expression', 'style_element', 'shot_scale', 'environment', 'equipment')
    ),
    color TEXT DEFAULT '#3b82f6',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tags_category ON tags(category);

CREATE TABLE IF NOT EXISTS media_tags (
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'rule', 'ml_plugin')),
    plugin_id TEXT DEFAULT 'manual',
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'suggested', 'rejected')),
    bbox TEXT,                                    -- Optional JSON {x, y, w, h}
    updated_at REAL NOT NULL,
    PRIMARY KEY (media_id, tag_id, source)
);
CREATE INDEX IF NOT EXISTS ix_media_tags_tag ON media_tags(tag_id, status);
CREATE INDEX IF NOT EXISTS ix_media_tags_media ON media_tags(media_id, status);

-- ============================================================================
-- 6. SKILLS (Module C: Versionable VLM System & User Prompt Templates)
-- ============================================================================
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,                          -- e.g., 'krea2_character_v1', 'krea2_concept_v1', 'krea2_style_v1'
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    training_type TEXT NOT NULL CHECK (training_type IN ('character', 'concept', 'style', 'general')),
    system_prompt_template TEXT NOT NULL,
    user_prompt_template TEXT NOT NULL,
    glossary_template TEXT NOT NULL DEFAULT '',
    min_words INTEGER NOT NULL DEFAULT 75,
    max_words INTEGER NOT NULL DEFAULT 180,
    hard_min_words INTEGER NOT NULL DEFAULT 35,
    hard_max_words INTEGER NOT NULL DEFAULT 270,
    discouraged_regexes TEXT NOT NULL DEFAULT '[]', -- JSON array of regex strings
    require_class_noun INTEGER NOT NULL DEFAULT 0,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_skills_slug_version ON skills(slug, version DESC);

-- ============================================================================
-- 7. CAPTION_HISTORY (Module C: Full Audit Trail of VLM & Manual Captions)
-- ============================================================================
CREATE TABLE IF NOT EXISTS caption_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    job_id INTEGER,
    caption_text TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('vlm', 'manual', 'imported', 'rule_stub')),
    skill_id TEXT REFERENCES skills(id) ON DELETE SET NULL,
    skill_version INTEGER,
    model_used TEXT,
    cascade_layer TEXT CHECK (cascade_layer IN ('primary', 'fallback_1', 'fallback_2', 'manual', 'cached')),
    trigger_word TEXT,
    trigger_position TEXT,
    word_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    warnings TEXT DEFAULT '[]',                   -- JSON array of linter warnings
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_caption_history_media ON caption_history(media_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_caption_history_dataset ON caption_history(dataset_id);

-- ============================================================================
-- 8. REJECTION_LOGS (Module C: Rejection-Guard Telemetry & Diagnostics)
-- ============================================================================
CREATE TABLE IF NOT EXISTS rejection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    job_id INTEGER,
    cascade_layer TEXT NOT NULL CHECK (cascade_layer IN ('primary', 'fallback_1', 'fallback_2')),
    model_attempted TEXT NOT NULL,
    rejection_type TEXT NOT NULL CHECK (
        rejection_type IN ('refusal_regex', 'provider_safety_filter', 'too_short', 'truncated_length', 'meta_preamble', 'minor_coded_rule', 'http_error')
    ),
    rejection_reasons TEXT NOT NULL,              -- JSON array of detailed failure reasons
    raw_response_snippet TEXT,                    -- First 500 chars of raw VLM output for debugging
    finish_reason TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rejection_logs_dataset ON rejection_logs(dataset_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_rejection_logs_media ON rejection_logs(media_id);

-- ============================================================================
-- 9. SYSTEM_SETTINGS (Global Configuration, Cascade Models & Redacted Secrets)
-- ============================================================================
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,                          -- JSON-serialized value
    is_secret INTEGER NOT NULL DEFAULT 0,         -- 1 for openrouter_api_key (never returned raw by GET)
    description TEXT,
    updated_at REAL NOT NULL
);

-- ============================================================================
-- 10. RAG_DOCUMENTS, 11. RAG_FTS (FTS5), & 12. RAG_VECTORS (Module D)
-- ============================================================================
CREATE TABLE IF NOT EXISTS rag_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT REFERENCES datasets(id) ON DELETE CASCADE,
    media_id INTEGER UNIQUE REFERENCES media_items(id) ON DELETE CASCADE,
    stem TEXT NOT NULL,
    trigger_word TEXT,
    full_caption TEXT NOT NULL,
    equipment_text TEXT NOT NULL DEFAULT '',
    surroundings_text TEXT NOT NULL DEFAULT '',
    position_text TEXT NOT NULL DEFAULT '',
    lighting_text TEXT NOT NULL DEFAULT '',
    tags_text TEXT NOT NULL DEFAULT '',           -- Space/comma-separated confirmed tags for BM25
    tags_json TEXT NOT NULL DEFAULT '{}',         -- JSON dict by category
    word_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    split TEXT NOT NULL DEFAULT 'train' CHECK (split IN ('train', 'val')),
    indexed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rag_docs_dataset ON rag_documents(dataset_id);
CREATE INDEX IF NOT EXISTS ix_rag_docs_hash ON rag_documents(content_hash);

-- SQLite FTS5 Virtual Table for sub-millisecond BM25 lexical search
CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
    full_caption,
    equipment_text,
    surroundings_text,
    position_text,
    lighting_text,
    tags_text,
    content='rag_documents',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Automatic Triggers keeping rag_fts synchronized with rag_documents
CREATE TRIGGER IF NOT EXISTS tr_rag_docs_ai AFTER INSERT ON rag_documents BEGIN
    INSERT INTO rag_fts(rowid, full_caption, equipment_text, surroundings_text, position_text, lighting_text, tags_text)
    VALUES (new.id, new.full_caption, new.equipment_text, new.surroundings_text, new.position_text, new.lighting_text, new.tags_text);
END;

CREATE TRIGGER IF NOT EXISTS tr_rag_docs_ad AFTER DELETE ON rag_documents BEGIN
    INSERT INTO rag_fts(rag_fts, rowid, full_caption, equipment_text, surroundings_text, position_text, lighting_text, tags_text)
    VALUES ('delete', old.id, old.full_caption, old.equipment_text, old.surroundings_text, old.position_text, old.lighting_text, old.tags_text);
END;

CREATE TRIGGER IF NOT EXISTS tr_rag_docs_au AFTER UPDATE ON rag_documents BEGIN
    INSERT INTO rag_fts(rag_fts, rowid, full_caption, equipment_text, surroundings_text, position_text, lighting_text, tags_text)
    VALUES ('delete', old.id, old.full_caption, old.equipment_text, old.surroundings_text, old.position_text, old.lighting_text, old.tags_text);
    INSERT INTO rag_fts(rowid, full_caption, equipment_text, surroundings_text, position_text, lighting_text, tags_text)
    VALUES (new.id, new.full_caption, new.equipment_text, new.surroundings_text, new.position_text, new.lighting_text, new.tags_text);
END;

-- Tier-2 Dense Vectors (128-D Local TF-IDF/SVD LSA or OpenRouter API Embeddings)
CREATE TABLE IF NOT EXISTS rag_vectors (
    doc_id INTEGER NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
    backend TEXT NOT NULL CHECK (backend IN ('tfidf_svd_128', 'openrouter_embedding')),
    model_name TEXT NOT NULL,                     -- e.g., 'sklearn-lsa-128' or 'openai/text-embedding-3-small'
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,                         -- L2-normalized float32 bytes
    updated_at REAL NOT NULL,
    PRIMARY KEY (doc_id, backend)
);
