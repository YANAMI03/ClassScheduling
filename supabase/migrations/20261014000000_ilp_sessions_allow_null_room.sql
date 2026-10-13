-- ============================================================================
-- ILP sessions do not occupy a room.
--
-- Individual Learning Process (ILP) sessions are generated per section with
-- no room assignment, so schedule.room_id must allow NULL values.
--
-- This migration:
--   1. Drops any NOT NULL rule on schedule.room_id (idempotent).
--   2. Leaves every other column and constraint untouched.
--
-- FK behaviour with NULL:
--   schedule.room_id references public.room(room_id). Foreign keys use
--   MATCH SIMPLE semantics: a constraint is satisfied whenever any referencing
--   column is NULL. A NULL room_id therefore never violates the FK, and the
--   FK is preserved exactly as it is.
-- ============================================================================

-- 1. Drop a legacy named CHECK (room_id IS NOT NULL) constraint, if present.
DO $$
DECLARE
    v_conname text;
BEGIN
    SELECT c.conname
      INTO v_conname
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = 'public'
       AND t.relname = 'schedule'
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) ILIKE '%room_id%IS NOT NULL%'
     LIMIT 1;

    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE public.schedule DROP CONSTRAINT %I', v_conname);
    END IF;
END $$;

-- 2. Drop the column-level NOT NULL (a no-op when already nullable).
ALTER TABLE public.schedule
    ALTER COLUMN room_id DROP NOT NULL;

-- 3. Verification (run manually to confirm):
--    SELECT is_nullable
--      FROM information_schema.columns
--     WHERE table_schema = 'public'
--       AND table_name = 'schedule'
--       AND column_name = 'room_id';
--    -- expected: YES
--
--    SELECT conname, pg_get_constraintdef(oid)
--      FROM pg_constraint
--     WHERE conrelid = 'public.schedule'::regclass
--       AND contype = 'f'
--       AND pg_get_constraintdef(oid) ILIKE '%room_id%';
--    -- expected: FOREIGN KEY (room_id) REFERENCES room(room_id)
