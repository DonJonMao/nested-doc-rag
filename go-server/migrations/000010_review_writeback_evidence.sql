ALTER TABLE review_items
  ADD COLUMN IF NOT EXISTS writeback_status TEXT,
  ADD COLUMN IF NOT EXISTS writeback_action TEXT,
  ADD COLUMN IF NOT EXISTS evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS writeback_error_code TEXT;
