-- ==============================================================================
-- PHASE 2: Destructive Migration
-- Drops foreign keys, the prof_id column on professor_load, users.professor_id,
-- dependent junction tables, policies, and finally the professor table.
--
-- Rollback note: If rolled back, restore from the JSON database backup taken
-- before running this migration.
-- ==============================================================================

BEGIN;

-- 1. Drop existing policies on professor_load that reference prof_id / professor
DROP POLICY IF EXISTS "professor_load_dean_admin_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_admin_crud" ON public.professor_load;

-- 2. Drop existing unique constraint and FK on professor_load that depend on prof_id
ALTER TABLE public.professor_load
    DROP CONSTRAINT IF EXISTS uq_prof_course_pair;

DO $$
DECLARE
    r RECORD;
    v_prof_oid regclass := to_regclass('public.professor');
BEGIN
    IF v_prof_oid IS NOT NULL THEN
        FOR r IN (
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.professor_load'::regclass
              AND contype = 'f'
              AND confrelid = v_prof_oid
        ) LOOP
            EXECUTE 'ALTER TABLE public.professor_load DROP CONSTRAINT IF EXISTS ' || quote_ident(r.conname);
        END LOOP;
    END IF;
END $$;

-- 3. Drop prof_id column from professor_load (CASCADE ensures any residual dependents are removed)
ALTER TABLE public.professor_load
    DROP COLUMN IF EXISTS prof_id CASCADE;

-- 4. Recreate RLS policies for professor_load (using program_id and course scoping)
ALTER TABLE public.professor_load ENABLE ROW LEVEL SECURITY;

CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "professor_load_scoped_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (
                program_id = public.get_user_program_id()
                OR course_id IN (SELECT course_id FROM public.course WHERE program_id = public.get_user_program_id() OR program_id IS NULL)
                OR program_id IS NULL
            )
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (
                program_id = public.get_user_program_id()
                OR course_id IN (SELECT course_id FROM public.course WHERE program_id = public.get_user_program_id() OR program_id IS NULL)
                OR program_id IS NULL
            )
        )
    );

-- 5. Drop users.professor_id (if it exists)
DO $$
BEGIN
    IF to_regclass('public.users') IS NOT NULL THEN
        ALTER TABLE public.users DROP COLUMN IF EXISTS professor_id CASCADE;
    END IF;
END $$;

-- 6. Drop obsolete prof_id from schedule and schedule_archive (if existing)
DO $$
BEGIN
    IF to_regclass('public.schedule') IS NOT NULL THEN
        ALTER TABLE public.schedule DROP COLUMN IF EXISTS prof_id CASCADE;
    END IF;
    IF to_regclass('public.schedule_archive') IS NOT NULL THEN
        ALTER TABLE public.schedule_archive DROP COLUMN IF EXISTS prof_id CASCADE;
    END IF;
END $$;

-- 7. Drop professor_program junction table (CASCADE automatically drops its policies and indexes)
DROP TABLE IF EXISTS public.professor_program CASCADE;

-- 8. Drop the professor table (CASCADE automatically drops its policies, indexes, triggers, and foreign keys)
DROP TABLE IF EXISTS public.professor CASCADE;

-- 9. Update restore_database_backup stored procedure (remove references to professor)
CREATE OR REPLACE FUNCTION public.restore_database_backup(payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    t_data jsonb;
    result jsonb := '{}'::jsonb;
    total_restored integer := 0;
    inserted_count integer;
BEGIN
    IF payload IS NULL THEN
        RAISE EXCEPTION 'Payload cannot be null';
    END IF;

    IF payload ? 'tables' THEN
        t_data := payload->'tables';
    ELSE
        t_data := payload;
    END IF;

    -- Truncate / clear in reverse dependency order (prof_course/professor removed)
    IF t_data ? 'activity_log' THEN DELETE FROM public.activity_log; END IF;
    IF t_data ? 'delete_requests' THEN DELETE FROM public.delete_requests; END IF;
    IF t_data ? 'irregular_student_schedule' THEN DELETE FROM public.irregular_student_schedule; END IF;
    IF t_data ? 'irregular_students' THEN DELETE FROM public.irregular_students; END IF;
    IF t_data ? 'schedule' THEN DELETE FROM public.schedule; END IF;
    IF t_data ? 'professor_load' THEN DELETE FROM public.professor_load; END IF;
    IF t_data ? 'course' THEN DELETE FROM public.course; END IF;
    IF t_data ? 'room' THEN DELETE FROM public.room; END IF;
    IF t_data ? 'timeslot' THEN DELETE FROM public.timeslot; END IF;
    IF t_data ? 'program' THEN DELETE FROM public.program; END IF;

    RETURN jsonb_build_object('success', true);
END;
$$;

-- 10. Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
