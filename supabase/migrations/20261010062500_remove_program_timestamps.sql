-- ==============================================================================
-- Migration: 20261010062500_remove_program_timestamps.sql
-- Description: Drops created_at and updated_at from public.program.
-- ==============================================================================

BEGIN;

-- Drop created_at and updated_at from public.program
ALTER TABLE public.program
    DROP COLUMN IF EXISTS created_at,
    DROP COLUMN IF EXISTS updated_at;

-- Reload schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
