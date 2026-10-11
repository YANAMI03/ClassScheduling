-- ==============================================================================
-- Migration: 20261010061500_remove_professor_load_program_id.sql
-- Description: Drops redundant program_id from public.professor_load.
--              Program ownership of a teaching load is strictly derived from
--              the associated course (course.program_id).
--              Updates indexes, constraints, and Row Level Security policies.
-- ==============================================================================

BEGIN;

-- 1. Drop existing policies that depend on professor_load.program_id
DROP POLICY IF EXISTS "professor_load_scheduler_program_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_dean_admin_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_admin_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;

-- 2. Drop existing unique constraints and indexes that include program_id
ALTER TABLE public.professor_load
    DROP CONSTRAINT IF EXISTS uq_professor_load_prog_key_course;

DROP INDEX IF EXISTS public.idx_professor_load_prog_key;

-- 3. Drop the redundant program_id column
ALTER TABLE public.professor_load
    DROP COLUMN IF EXISTS program_id;

-- 4. Ensure unique constraint on (course_id, professor_name)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_professor_load_course_prof'
    ) THEN
        ALTER TABLE public.professor_load
            ADD CONSTRAINT uq_professor_load_course_prof
            UNIQUE (course_id, professor_name);
    END IF;
EXCEPTION
    WHEN duplicate_table OR duplicate_object THEN
        NULL;
END $$;

-- 5. Recreate RLS policies for professor_load deriving program from public.course
ALTER TABLE public.professor_load ENABLE ROW LEVEL SECURITY;

-- Read policy: All authenticated users can view teaching loads
CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated
    USING (true);

-- Write policy: Admins have full access; Schedulers are restricted to their program's courses
CREATE POLICY "professor_load_scoped_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('scheduler', 'dean', 'chair')
            AND EXISTS (
                SELECT 1 FROM public.course c
                WHERE c.course_id = professor_load.course_id
                  AND (c.program_id = public.get_user_program_id() OR public.get_user_program_id() IS NULL)
            )
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('scheduler', 'dean', 'chair')
            AND EXISTS (
                SELECT 1 FROM public.course c
                WHERE c.course_id = professor_load.course_id
                  AND (c.program_id = public.get_user_program_id() OR public.get_user_program_id() IS NULL)
            )
        )
    );

-- 6. Reload schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
