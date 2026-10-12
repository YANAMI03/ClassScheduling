-- ==============================================================================
-- Migration: 20261012180000_soft_archive_flags.sql
-- Description: Introduce boolean soft-delete flags (mirroring schedule.archive):
--   * course.archive, program.archive, room.archive, working_hours.archive
--       archive = false  -> active / visible by default
--       archive = true   -> archived / hidden from pickers & lists
--   * users.disabled
--       disabled = false -> normal account
--       disabled = true  -> cannot log in and is signed out on next request
--
-- Notes:
--   * No archived_at column is added (per requirement).
--   * Existing rows default to false automatically because the new columns are
--     NOT NULL with a DEFAULT false.
--   * The schedule table and professor_load logic are intentionally untouched.
-- ==============================================================================

BEGIN;

-- 1. Soft-archive flag on the reference/config tables -------------------------
ALTER TABLE public.course
    ADD COLUMN IF NOT EXISTS archive boolean NOT NULL DEFAULT false;

ALTER TABLE public.program
    ADD COLUMN IF NOT EXISTS archive boolean NOT NULL DEFAULT false;

ALTER TABLE public.room
    ADD COLUMN IF NOT EXISTS archive boolean NOT NULL DEFAULT false;

ALTER TABLE public.working_hours
    ADD COLUMN IF NOT EXISTS archive boolean NOT NULL DEFAULT false;

-- 2. Disabled flag on accounts ------------------------------------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS disabled boolean NOT NULL DEFAULT false;

-- 3. Defensive back-fill (no-op when the DEFAULT already applied) -------------
UPDATE public.course        SET archive = false  WHERE archive IS NULL;
UPDATE public.program       SET archive = false  WHERE archive IS NULL;
UPDATE public.room          SET archive = false  WHERE archive IS NULL;
UPDATE public.working_hours SET archive = false  WHERE archive IS NULL;
UPDATE public.users         SET disabled = false WHERE disabled IS NULL;

-- 4. Helpful indexes for the active/archived list filters ---------------------
CREATE INDEX IF NOT EXISTS idx_course_archive        ON public.course (archive);
CREATE INDEX IF NOT EXISTS idx_program_archive       ON public.program (archive);
CREATE INDEX IF NOT EXISTS idx_room_archive          ON public.room (archive);
CREATE INDEX IF NOT EXISTS idx_working_hours_archive ON public.working_hours (archive);
CREATE INDEX IF NOT EXISTS idx_users_disabled        ON public.users (disabled);

-- 5. Reload PostgREST schema cache so the new columns are visible immediately -
NOTIFY pgrst, 'reload schema';

COMMIT;
