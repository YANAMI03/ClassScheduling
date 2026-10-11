-- ==============================================================================
-- Migration: 20261011150000_harden_rls_admin_viewer_read_only.sql
-- Description:
--   1. Enforce strict READ-ONLY access for Admin and Viewer on public.schedule.
--      - Admin and Viewer can SELECT all schedules.
--      - Admin and Viewer are BLOCKED from INSERT, UPDATE, and DELETE via RLS.
--      - Only Scheduler can INSERT/UPDATE schedule records scoped to their program.
--   2. Clean up obsolete roles ('dean', 'chair') from RLS policies on
--      public.professor_load and public.room.
--
-- IMPORTANT SAFETY NOTICE:
--   DO NOT execute this script without first taking a full database backup
--   using pg_dump or the Supabase Project Dashboard Backup tool.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. public.schedule RLS Policies
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

-- 1.1 SELECT: Admin and Viewer see ALL active and archived schedules.
--            Scheduler sees active schedules and their own program's archived schedules.
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
DROP POLICY IF EXISTS "schedule_scoped_all" ON public.schedule;
DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;

CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin', 'viewer')
    OR (
        public.get_user_role() = 'scheduler'
        AND (
            archive = false
            OR professor_load_id IS NULL
            OR EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON pl.course_id = c.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND (c.program_id = public.get_user_program_id() OR c.program_id IS NULL)
            )
        )
    )
    OR (archive = false)
);

-- 1.2 INSERT: Restricted STRICTLY to Scheduler for their assigned program.
--            Admin and Viewer are BLOCKED.
DROP POLICY IF EXISTS "schedule_insert" ON public.schedule;

CREATE POLICY "schedule_insert" ON public.schedule
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() = 'scheduler'
    AND (
        professor_load_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM public.professor_load pl
            JOIN public.course c ON pl.course_id = c.course_id
            WHERE pl.id = schedule.professor_load_id
              AND (c.program_id = public.get_user_program_id() OR c.program_id IS NULL)
        )
    )
);

-- 1.3 UPDATE: Restricted STRICTLY to Scheduler for their assigned program.
--            Admin and Viewer are BLOCKED.
DROP POLICY IF EXISTS "schedule_update" ON public.schedule;

CREATE POLICY "schedule_update" ON public.schedule
FOR UPDATE TO authenticated
USING (
    public.get_user_role() = 'scheduler'
    AND (
        professor_load_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM public.professor_load pl
            JOIN public.course c ON pl.course_id = c.course_id
            WHERE pl.id = schedule.professor_load_id
              AND (c.program_id = public.get_user_program_id() OR c.program_id IS NULL)
        )
    )
)
WITH CHECK (
    public.get_user_role() = 'scheduler'
    AND (
        professor_load_id IS NULL
        OR EXISTS (
            SELECT 1
            FROM public.professor_load pl
            JOIN public.course c ON pl.course_id = c.course_id
            WHERE pl.id = schedule.professor_load_id
              AND (c.program_id = public.get_user_program_id() OR c.program_id IS NULL)
        )
    )
);

-- 1.4 DELETE: Direct PostgREST hard-deletion is disabled for all roles.
--            Schedules must be archived via soft-delete/RPC.
DROP POLICY IF EXISTS "schedule_delete" ON public.schedule;

CREATE POLICY "schedule_delete" ON public.schedule
FOR DELETE TO authenticated
USING (false);

-- ------------------------------------------------------------------------------
-- 2. public.professor_load RLS Policies (Clean up 'dean' and 'chair')
-- ------------------------------------------------------------------------------
ALTER TABLE public.professor_load ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "professor_load_dean_admin_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scheduler_program_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;

CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "professor_load_scheduler_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() = 'scheduler'
        AND (
            program_id = public.get_user_program_id()
            OR course_id IN (
                SELECT course_id FROM public.course 
                WHERE program_id = public.get_user_program_id() OR program_id IS NULL
            )
            OR program_id IS NULL
        )
    )
    WITH CHECK (
        public.get_user_role() = 'scheduler'
        AND (
            program_id = public.get_user_program_id()
            OR course_id IN (
                SELECT course_id FROM public.course 
                WHERE program_id = public.get_user_program_id() OR program_id IS NULL
            )
            OR program_id IS NULL
        )
    );

-- ------------------------------------------------------------------------------
-- 3. public.room RLS Policies (Clean up 'dean' and 'chair')
-- ------------------------------------------------------------------------------
ALTER TABLE public.room ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "room_dean_admin_crud" ON public.room;
DROP POLICY IF EXISTS "room_admin_all" ON public.room;
DROP POLICY IF EXISTS "room_select_authenticated" ON public.room;

CREATE POLICY "room_select_authenticated" ON public.room
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "room_admin_manage" ON public.room
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('super_admin', 'admin'))
    WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));
