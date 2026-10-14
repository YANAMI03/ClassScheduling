-- ==============================================================================
-- Migration: 20261019000000_users_drop_created_at.sql
-- Description:
--   Drop public.users.created_at.
--   - No trigger or function references public.users.created_at; the auth sync
--     trigger and admin_create_auth_user insert explicit column lists that do
--     not include it, so no function needs redefining.
--   - activity_log is NOT touched; it remains the source of truth for change
--     history (including when an account was first seen).
-- Idempotent: safe to run more than once.
-- ==============================================================================

BEGIN;

ALTER TABLE public.users DROP COLUMN IF EXISTS created_at;

NOTIFY pgrst, 'reload schema';

COMMIT;


-- ==============================================================================
-- ROLLBACK (run only if you need to revert this migration)
-- ==============================================================================
-- BEGIN;
--
-- ALTER TABLE public.users ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
--
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- ==============================================================================
