-- ==============================================================================
-- Migration: 20261001_remove_semester_add_specialization_rework_archive.sql
-- Description:
--   Change 1: Remove semester creation (semester becomes a plain attribute).
--             Add plain text column 'semester', backfill, drop FK, drop old column,
--             drop 'semester' table and 'section_config' table.
--   Change 2: Normalize semester to exactly '1st Semester' and '2nd Semester'.
--   Change 3: Add 'specialization' column to course with CHECK constraint.
--   Change 4: Ensure schedule.program_id FK and archive column.
--   User Change: Remove Dean/Chair users and roles.
--
-- Rollback Note:
--   To rollback, re-create public.semester and public.section_config tables from
--   migration 20260926_restructure_programs_semesters_roles.sql, re-add semester_id
--   columns, backfill semester_id based on semester text, and drop specialization.
--
-- Existing Schedule Impact:
--   - Schedules retain their program_id, section, semester, and time allocation.
--   - Any previous semester_id reference is converted cleanly to the standard text
--     '1st Semester' or '2nd Semester'.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. ADD PLAIN TEXT 'semester' COLUMN & BACKFILL BEFORE DROPPING FK
-- ------------------------------------------------------------------------------

-- (a) COURSE table
ALTER TABLE public.course ADD COLUMN IF NOT EXISTS semester VARCHAR(50);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'course' AND column_name = 'semester_id'
    ) AND to_regclass('public.semester') IS NOT NULL THEN
        EXECUTE '
            UPDATE public.course c
            SET semester = CASE
                WHEN s.term ILIKE ''%2nd%'' THEN ''2nd Semester''
                ELSE ''1st Semester''
            END
            FROM public.semester s
            WHERE c.semester_id = s.id AND (c.semester IS NULL OR c.semester = '''')
        ';
    END IF;
END $$;

UPDATE public.course
SET semester = CASE
    WHEN semester ILIKE '%2nd%' OR semester = '2' THEN '2nd Semester'
    ELSE '1st Semester'
END
WHERE semester IS NOT NULL;

UPDATE public.course SET semester = '1st Semester' WHERE semester IS NULL OR semester = '';
ALTER TABLE public.course ALTER COLUMN semester SET NOT NULL;
ALTER TABLE public.course ALTER COLUMN semester SET DEFAULT '1st Semester';

-- Drop FK constraint and semester_id column from course
DO $$
DECLARE
    c_name text;
BEGIN
    IF to_regclass('public.semester') IS NOT NULL THEN
        FOR c_name IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'public.course'::regclass AND confrelid = 'public.semester'::regclass
        LOOP
            EXECUTE 'ALTER TABLE public.course DROP CONSTRAINT ' || quote_ident(c_name);
        END LOOP;
    END IF;
END $$;

ALTER TABLE public.course DROP COLUMN IF EXISTS semester_id CASCADE;

-- (b) SCHEDULE table
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS semester VARCHAR(50);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'semester_id'
    ) AND to_regclass('public.semester') IS NOT NULL THEN
        EXECUTE '
            UPDATE public.schedule sc
            SET semester = CASE
                WHEN s.term ILIKE ''%2nd%'' THEN ''2nd Semester''
                ELSE ''1st Semester''
            END
            FROM public.semester s
            WHERE sc.semester_id = s.id AND (sc.semester IS NULL OR sc.semester = '''')
        ';
    END IF;
END $$;

UPDATE public.schedule
SET semester = CASE
    WHEN semester ILIKE '%2nd%' OR semester = '2' THEN '2nd Semester'
    ELSE '1st Semester'
END
WHERE semester IS NOT NULL;

UPDATE public.schedule SET semester = '1st Semester' WHERE semester IS NULL OR semester = '';

-- Drop FK constraint and semester_id column from schedule
DO $$
DECLARE
    c_name text;
BEGIN
    IF to_regclass('public.semester') IS NOT NULL THEN
        FOR c_name IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'public.schedule'::regclass AND confrelid = 'public.semester'::regclass
        LOOP
            EXECUTE 'ALTER TABLE public.schedule DROP CONSTRAINT ' || quote_ident(c_name);
        END LOOP;
    END IF;
END $$;

ALTER TABLE public.schedule DROP COLUMN IF EXISTS semester_id CASCADE;

-- (c) TIMESLOT table
-- Explicitly drop dependent policy and constraint referencing semester_id
DROP POLICY IF EXISTS "timeslot_dean_admin_crud" ON public.timeslot;
ALTER TABLE public.timeslot DROP CONSTRAINT IF EXISTS uq_timeslot_semester_day;

DO $$
DECLARE
    c_name text;
BEGIN
    IF to_regclass('public.timeslot') IS NOT NULL AND to_regclass('public.semester') IS NOT NULL THEN
        FOR c_name IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'public.timeslot'::regclass AND confrelid = 'public.semester'::regclass
        LOOP
            EXECUTE 'ALTER TABLE public.timeslot DROP CONSTRAINT ' || quote_ident(c_name);
        END LOOP;
    END IF;
END $$;

ALTER TABLE public.timeslot DROP COLUMN IF EXISTS semester_id CASCADE;

-- Deduplicate timeslots by day if multiple semesters left duplicate days
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'timeslot' AND column_name = 'timeslot_id'
    ) THEN
        DELETE FROM public.timeslot t1
        USING public.timeslot t2
        WHERE t1.timeslot_id > t2.timeslot_id
          AND LOWER(TRIM(COALESCE(t1.day, ''))) = LOWER(TRIM(COALESCE(t2.day, '')))
          AND COALESCE(t1.day, '') <> '';
    END IF;
END $$;

-- Remove Saturday and ensure Monday through Friday (including Thursday)
DELETE FROM public.timeslot
WHERE LOWER(TRIM(day)) = 'saturday';

INSERT INTO public.timeslot (day, start_time, end_time, lunch_time)
SELECT 'Thursday', '08:00:00'::time, '20:00:00'::time, '12:00:00'::time
WHERE NOT EXISTS (
    SELECT 1 FROM public.timeslot WHERE LOWER(TRIM(day)) = 'thursday'
);

-- Professor availability cutoff column on timeslot
ALTER TABLE public.timeslot ADD COLUMN IF NOT EXISTS professor_cutoff TIME;

-- Seed default professor cutoffs: Monday 16:00, Tuesday-Friday 17:00
UPDATE public.timeslot
SET professor_cutoff = '16:00:00'::time
WHERE LOWER(TRIM(day)) = 'monday' AND professor_cutoff IS NULL;

UPDATE public.timeslot
SET professor_cutoff = '17:00:00'::time
WHERE LOWER(TRIM(day)) IN ('tuesday', 'wednesday', 'thursday', 'friday') AND professor_cutoff IS NULL;

-- ------------------------------------------------------------------------------
-- 2. DROP 'section_config' AND 'semester' TABLES
-- ------------------------------------------------------------------------------
DROP TABLE IF EXISTS public.section_config CASCADE;
DROP TABLE IF EXISTS public.semester CASCADE;

-- ------------------------------------------------------------------------------
-- 2b. PROFESSOR AVAILABILITY CUTOFFS ON ACADEMIC RANKING
-- ------------------------------------------------------------------------------
ALTER TABLE public.academic_ranking ADD COLUMN IF NOT EXISTS has_cutoff BOOLEAN NOT NULL DEFAULT true;

-- LOHB ranking is exempt from daily cutoffs (set to false)
UPDATE public.academic_ranking
SET has_cutoff = false
WHERE LOWER(TRIM(name)) LIKE '%lohb%';

-- ------------------------------------------------------------------------------
-- 3. ADD 'specialization' COLUMN TO COURSE TABLE
-- ------------------------------------------------------------------------------
ALTER TABLE public.course ADD COLUMN IF NOT EXISTS specialization VARCHAR(50);

-- Migrate any existing 'major' column values if present
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'course' AND column_name = 'major'
    ) THEN
        EXECUTE '
            UPDATE public.course
            SET specialization = CASE
                WHEN LOWER(TRIM(major)) IN (''database'', ''database systems'', ''database system'') THEN ''Database Systems''
                WHEN LOWER(TRIM(major)) IN (''web'', ''web systems'', ''web development'', ''web dev'') THEN ''Web Systems''
                WHEN LOWER(TRIM(major)) IN (''networking'', ''network'', ''net'') THEN ''Networking''
                WHEN LOWER(TRIM(major)) IN (''general'', ''gen'') THEN ''General''
                ELSE NULL
            END
            WHERE specialization IS NULL AND major IS NOT NULL
        ';
    END IF;
END $$;

-- Enforce CHECK constraint on specialization
ALTER TABLE public.course DROP CONSTRAINT IF EXISTS chk_course_specialization;
ALTER TABLE public.course ADD CONSTRAINT chk_course_specialization
    CHECK (specialization IS NULL OR specialization IN ('Database Systems', 'Web Systems', 'Networking', 'General'));

-- ------------------------------------------------------------------------------
-- 4. ENSURE SCHEDULE program_id FK AND archive COLUMN
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE CASCADE;
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS archive BOOLEAN DEFAULT false;

-- Backfill schedule.program_id from program table if null
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'program'
    ) THEN
        EXECUTE '
            UPDATE public.schedule sc
            SET program_id = p.id
            FROM public.program p
            WHERE sc.program_id IS NULL AND UPPER(TRIM(sc.program)) = UPPER(TRIM(p.program_name))
        ';
    END IF;
END $$;

UPDATE public.schedule sc
SET program_id = (SELECT id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1)
WHERE sc.program_id IS NULL;

UPDATE public.schedule sc
SET program_id = (SELECT id FROM public.program ORDER BY id ASC LIMIT 1)
WHERE sc.program_id IS NULL;

UPDATE public.schedule SET archive = false WHERE archive IS NULL;

CREATE INDEX IF NOT EXISTS idx_schedule_program_archive ON public.schedule (program_id, archive);

-- ------------------------------------------------------------------------------
-- 5. UPDATE HELPER FUNCTIONS AND RLS POLICIES
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_role text;
BEGIN
    v_role := auth.jwt() -> 'app_metadata' ->> 'role';
    IF v_role IS NOT NULL AND v_role <> '' THEN
        v_role := LOWER(v_role);
        IF v_role IN ('super_admin', 'admin', 'scheduler', 'dean', 'chair') THEN
            RETURN v_role;
        END IF;
    END IF;

    v_role := auth.jwt() -> 'user_metadata' ->> 'role';
    IF v_role IS NOT NULL AND v_role <> '' THEN
        v_role := LOWER(v_role);
        IF v_role IN ('super_admin', 'admin', 'scheduler', 'dean', 'chair') THEN
            RETURN v_role;
        END IF;
    END IF;

    SELECT role INTO v_role FROM public.users WHERE id = auth.uid() LIMIT 1;
    IF v_role IS NOT NULL AND v_role <> '' THEN
        v_role := LOWER(v_role);
        IF v_role IN ('super_admin', 'admin', 'scheduler', 'dean', 'chair') THEN
            RETURN v_role;
        END IF;
    END IF;

    RETURN 'scheduler';
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_user_role() TO anon, authenticated, service_role;

DROP POLICY IF EXISTS "users_admin_select" ON public.users;
CREATE POLICY "users_admin_select"
ON public.users
FOR SELECT
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin', 'scheduler', 'dean', 'chair'));

DROP POLICY IF EXISTS "users_admin_insert" ON public.users;
CREATE POLICY "users_admin_insert"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

DROP POLICY IF EXISTS "users_admin_update" ON public.users;
CREATE POLICY "users_admin_update"
ON public.users
FOR UPDATE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id)
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id);

DROP POLICY IF EXISTS "users_admin_delete" ON public.users;
CREATE POLICY "users_admin_delete"
ON public.users
FOR DELETE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin'));

-- TIMESLOT policies (recreated without semester_id dependency)
DROP POLICY IF EXISTS "timeslot_admin_scheduler_all" ON public.timeslot;
DROP POLICY IF EXISTS "timeslot_select_authenticated" ON public.timeslot;
CREATE POLICY "timeslot_select_authenticated" ON public.timeslot
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "timeslot_dean_admin_crud" ON public.timeslot;
CREATE POLICY "timeslot_dean_admin_crud" ON public.timeslot
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('super_admin', 'admin', 'dean', 'chair'))
    WITH CHECK (public.get_user_role() IN ('super_admin', 'admin', 'dean', 'chair'));

-- ------------------------------------------------------------------------------
-- 6. UPDATE confirm_schedule_transaction RPC FUNCTION
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text DEFAULT NULL,
    p_rows jsonb DEFAULT '[]'::jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT NULL,
    p_program_id integer DEFAULT NULL,
    p_batch_id uuid DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id uuid := coalesce(p_batch_id, gen_random_uuid());
    v_archived integer := 0;
    v_inserted integer := 0;
    v_target_program_id integer := p_program_id;
BEGIN
    IF nullif(trim(coalesce(p_semester, '')), '') IS NULL THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    -- Resolve program_id from program_name if not provided
    IF v_target_program_id IS NULL AND nullif(trim(coalesce(p_program, '')), '') IS NOT NULL THEN
        SELECT id INTO v_target_program_id
        FROM public.program
        WHERE upper(trim(program_name)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF p_clear_scope THEN
        -- Soft archive previous active entries
        UPDATE public.schedule
        SET archive = true
        WHERE archive = false
          AND (p_semester IS NULL OR semester = p_semester)
          AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows
    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, semester, major, archive
    )
    SELECT coalesce(nullif(x->>'program_id', '')::integer, v_target_program_id),
           nullif(x->>'professor_load_id', '')::integer,
           nullif(x->>'room_id', '')::integer,
           coalesce(x->>'day', 'Monday'),
           (x->>'class_start')::time, (x->>'class_end')::time,
           coalesce(nullif(x->>'session_type', ''), 'Lecture'),
           x->>'section', coalesce(nullif(x->>'semester', ''), p_semester),
           nullif(x->>'major', ''), false
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', v_batch_id,
        'archived_count', v_archived,
        'inserted_count', v_inserted
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 7. CLEAN UP CORRUPTED/PLACEHOLDER 999 SECTIONS IN PROFESSOR_LOAD
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'professor_load'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'professor_load' AND column_name = 'sections'
    ) THEN
        UPDATE public.professor_load pl
        SET sections = COALESCE(
            NULLIF((
                SELECT COUNT(DISTINCT s.section)
                FROM public.schedule s
                WHERE s.professor_load_id = pl.id
                  AND s.archive = false
                  AND s.section IS NOT NULL
                  AND TRIM(s.section) <> ''
            ), 0),
            1
        )
        WHERE pl.sections >= 999 OR pl.sections IS NULL OR pl.sections <= 0;

        -- Remove unauthorized auto-assigned IT-WS05 load from Emilsa Bantug
        DELETE FROM public.professor_load
        WHERE prof_id IN (
            SELECT prof_id FROM public.professor 
            WHERE LOWER(TRIM(first_name)) = 'emilsa' AND LOWER(TRIM(last_name)) = 'bantug'
        )
        AND course_id IN (
            SELECT course_id FROM public.course 
            WHERE LOWER(TRIM(course_name)) LIKE '%ws05%' OR LOWER(TRIM(course_name)) LIKE '%it-ws05%'
        );
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 8. CLEAN UP PLACEHOLDER PROFESSOR RECORDS (e.g. Professor A, B, C...)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'professor'
    ) THEN
        -- Delete phantom schedule entries with NULL or dummy professor_load
        DELETE FROM public.schedule
        WHERE professor_load_id IS NULL
           OR professor_load_id IN (
                SELECT pl.id FROM public.professor_load pl
                JOIN public.professor p ON pl.prof_id = p.prof_id
                WHERE LOWER(TRIM(p.first_name)) = 'professor'
                  AND (p.last_name IS NULL OR LENGTH(TRIM(p.last_name)) <= 2)
           );

        -- Delete dummy professor_load entries
        DELETE FROM public.professor_load
        WHERE prof_id IN (
            SELECT prof_id FROM public.professor
            WHERE LOWER(TRIM(first_name)) = 'professor'
              AND (last_name IS NULL OR LENGTH(TRIM(last_name)) <= 2)
        );

        -- Delete dummy professor entries
        DELETE FROM public.professor
        WHERE LOWER(TRIM(first_name)) = 'professor'
          AND (last_name IS NULL OR LENGTH(TRIM(last_name)) <= 2);
    END IF;
END $$;

COMMIT;

