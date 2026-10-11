-- ==============================================================================
-- Migration: 20261010063500_remove_room_program_id.sql
-- Description: Drops program_id from public.room.
--              Rooms serve as a shared campus-wide facility pool across all programs.
--              Updates indexes and Row Level Security policies.
-- ==============================================================================

BEGIN;

-- 1. Drop existing policies on room that depend on program_id
DROP POLICY IF EXISTS "room_dean_admin_crud" ON public.room;
DROP POLICY IF EXISTS "room_admin_scheduler_all" ON public.room;
DROP POLICY IF EXISTS "room_admin_all" ON public.room;
DROP POLICY IF EXISTS "room_select_authenticated" ON public.room;

-- 2. Drop index on room(program_id)
DROP INDEX IF EXISTS public.idx_room_program_id;

-- 3. Drop redundant program_id column (and obsolete department if present)
ALTER TABLE public.room
    DROP COLUMN IF EXISTS program_id,
    DROP COLUMN IF EXISTS department;

-- 4. Recreate RLS policies for public.room as a shared campus facility pool
ALTER TABLE public.room ENABLE ROW LEVEL SECURITY;

-- Read policy: All authenticated users (schedulers, admins, faculty) can view rooms
CREATE POLICY "room_select_authenticated" ON public.room
    FOR SELECT TO authenticated
    USING (true);

-- Write policy: Only administrators can add, update, or delete physical rooms
CREATE POLICY "room_admin_all" ON public.room
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('super_admin', 'admin'))
    WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

-- 5. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
