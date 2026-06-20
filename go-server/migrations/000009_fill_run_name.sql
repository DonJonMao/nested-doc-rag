-- +goose Up
ALTER TABLE fill_runs
    ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT '';

-- +goose Down
ALTER TABLE fill_runs
    DROP COLUMN IF EXISTS name;
