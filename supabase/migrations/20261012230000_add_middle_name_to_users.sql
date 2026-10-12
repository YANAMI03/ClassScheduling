-- ==============================================================================
-- Migration: 20261012230000_add_middle_name_to_users.sql
-- Adds an optional middle name to user accounts, so faculty (Viewer) accounts
-- can be matched to their professor_load name (which often carries a middle
-- initial, e.g. "Alexander S. Cochanco").
-- ==============================================================================

BEGIN;

ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS middle_name text;

NOTIFY pgrst, 'reload schema';

COMMIT;
