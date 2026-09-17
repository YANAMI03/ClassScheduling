-- ==============================================================================
-- Migration: Enforce "archive" boolean flag on schedule and drop schedule_archive table
-- Date: 2026-09-16
--
-- Flag Semantics:
--   archive = false -> Active / Current Schedule (Default)
--   archive = true  -> Archived Schedule
--
-- Safeguards:
--   - Scoped strictly by semester
--   - Confirming 2nd Semester NEVER touches or archives 1st Semester schedules
--   - Confirming only soft-archives previous active schedules (archive = false -> archive = true)
--   - Newly confirmed records are inserted with archive = false
--   - Safe check on optional tables (irregular_student_schedule)
-- ==============================================================================

DO $$
BEGIN
    -- 1. Ensure "archive" boolean column exists on public.schedule
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'is_archived'
    ) THEN
        ALTER TABLE public.schedule RENAME COLUMN is_archived TO archive;
    ELSIF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'status'
    ) THEN
        -- If status previously existed (where true=active), invert values so archive: false=active, true=archived
        ALTER TABLE public.schedule ADD COLUMN archive BOOLEAN NOT NULL DEFAULT false;
        UPDATE public.schedule SET archive = NOT status;
        ALTER TABLE public.schedule DROP COLUMN status;
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'archive'
    ) THEN
        ALTER TABLE public.schedule ADD COLUMN archive BOOLEAN NOT NULL DEFAULT false;
    END IF;

    -- Ensure default is false (active)
    ALTER TABLE public.schedule ALTER COLUMN archive SET DEFAULT false;

    -- 2. Ensure school_year column exists on public.schedule
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'school_year'
    ) THEN
        ALTER TABLE public.schedule ADD COLUMN school_year VARCHAR(50);
    END IF;

    -- 3. Migrate existing records from schedule_archive (if table still exists) with archive = true
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'schedule_archive'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'schedule_archive' AND column_name = 'prof_course_id'
        ) THEN
            INSERT INTO public.schedule (
                prof_course_id,
                room_id,
                day,
                class_start,
                class_end,
                session_type,
                section,
                semester,
                major,
                program,
                archive
            )
            SELECT
                CASE 
                    WHEN sa.prof_course_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.prof_course pc WHERE pc.prof_course_id = sa.prof_course_id
                    ) THEN sa.prof_course_id
                    WHEN sa.prof_id IS NOT NULL AND sa.course_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.prof_course pc WHERE pc.prof_id = sa.prof_id AND pc.course_id = sa.course_id
                    ) THEN (SELECT pc.prof_course_id FROM public.prof_course pc WHERE pc.prof_id = sa.prof_id AND pc.course_id = sa.course_id LIMIT 1)
                    ELSE NULL
                END,
                CASE 
                    WHEN sa.room_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.room r WHERE r.room_id = sa.room_id
                    ) THEN sa.room_id
                    ELSE NULL
                END,
                sa.day,
                sa.class_start,
                sa.class_end,
                sa.session_type,
                sa.section,
                sa.semester,
                sa.major,
                sa.program,
                true
            FROM public.schedule_archive sa;
        ELSIF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'schedule_archive' AND column_name = 'prof_id'
        ) THEN
            INSERT INTO public.schedule (
                prof_course_id,
                room_id,
                day,
                class_start,
                class_end,
                session_type,
                section,
                semester,
                major,
                program,
                archive
            )
            SELECT
                CASE 
                    WHEN sa.prof_id IS NOT NULL AND sa.course_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.prof_course pc WHERE pc.prof_id = sa.prof_id AND pc.course_id = sa.course_id
                    ) THEN (SELECT pc.prof_course_id FROM public.prof_course pc WHERE pc.prof_id = sa.prof_id AND pc.course_id = sa.course_id LIMIT 1)
                    ELSE NULL
                END,
                CASE 
                    WHEN sa.room_id IS NOT NULL AND EXISTS (
                        SELECT 1 FROM public.room r WHERE r.room_id = sa.room_id
                    ) THEN sa.room_id
                    ELSE NULL
                END,
                sa.day,
                sa.class_start,
                sa.class_end,
                sa.session_type,
                sa.section,
                sa.semester,
                sa.major,
                sa.program,
                true
            FROM public.schedule_archive sa;
        END IF;

        -- 4. Drop schedule_archive table
        DROP TABLE IF EXISTS public.schedule_archive CASCADE;
    END IF;

    -- Drop legacy functions that relied exclusively on schedule_archive table
    DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text);
    DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(UUID, text);
END $$;

-- 4. Performance Indexes
DROP INDEX IF EXISTS idx_schedule_status;
DROP INDEX IF EXISTS idx_schedule_status_scope;
DROP INDEX IF EXISTS idx_schedule_is_archived;
DROP INDEX IF EXISTS idx_schedule_is_archived_scope;
DROP INDEX IF EXISTS idx_schedule_is_archived_sy;
DROP INDEX IF EXISTS idx_schedule_archive;
DROP INDEX IF EXISTS idx_schedule_archive_scope;
CREATE INDEX IF NOT EXISTS idx_schedule_archive ON public.schedule(archive);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_scope ON public.schedule(archive, semester, program);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_sy ON public.schedule(archive, semester, school_year);

-- ==============================================================================
-- 5. confirm_schedule_transaction RPC Function
--
-- Strict Rule:
--   - ONLY soft-archives active records (archive = false -> archive = true)
--     matching the EXACT target semester (2nd semester never archives 1st semester!)
--   - Inserts newly confirmed schedule records as ACTIVE (archive = false)
-- ==============================================================================
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, text);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text DEFAULT NULL,
    p_rows jsonb DEFAULT '[]'::jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT NULL,
    p_school_year text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    archived_count integer := 0;
    deleted_count integer := 0;
    inserted_count integer := 0;
    sem_clean text;
    prog_clean text;
    sy_clean text;
    sem_aliases text[];
BEGIN
    sem_clean := trim(COALESCE(p_semester, ''));
    prog_clean := trim(COALESCE(p_program, ''));
    sy_clean := trim(COALESCE(p_school_year, ''));

    IF sem_clean = '' THEN
        RAISE EXCEPTION 'Semester is strictly required to confirm schedule.';
    END IF;

    -- Normalize semester aliases strictly
    IF sem_clean ILIKE '1st%' OR sem_clean = '1' THEN
        sem_aliases := ARRAY['1st Semester', '1st', '1'];
    ELSIF sem_clean ILIKE '2nd%' OR sem_clean = '2' THEN
        sem_aliases := ARRAY['2nd Semester', '2nd', '2'];
    ELSE
        sem_aliases := ARRAY[sem_clean];
    END IF;

    -- 1. Soft-archive previous active records (set archive = true)
    -- STRICT: Only match currently active records (archive = false) in the specified semester
    IF p_clear_scope THEN
        IF prog_clean <> '' AND lower(prog_clean) NOT IN ('global / all programs', 'all', 'all programs', 'null') THEN
            -- Unbind irregular students if table exists
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'irregular_student_schedule') THEN
                DELETE FROM public.irregular_student_schedule
                WHERE schedule_id IN (
                    SELECT schedule_id FROM public.schedule
                    WHERE (program = prog_clean OR program IS NULL OR trim(program) = '')
                      AND semester = ANY(sem_aliases)
                      AND archive = false
                      AND (sy_clean = '' OR school_year = sy_clean OR school_year IS NULL OR trim(school_year) = '')
                );
            END IF;

            -- Update ONLY active schedules of matching semester & program to archive = true
            UPDATE public.schedule
            SET archive = true
            WHERE (program = prog_clean OR program IS NULL OR trim(program) = '')
              AND semester = ANY(sem_aliases)
              AND archive = false
              AND (sy_clean = '' OR school_year = sy_clean OR school_year IS NULL OR trim(school_year) = '');

            GET DIAGNOSTICS archived_count = ROW_COUNT;
        ELSE
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'irregular_student_schedule') THEN
                DELETE FROM public.irregular_student_schedule
                WHERE schedule_id IN (
                    SELECT schedule_id FROM public.schedule
                    WHERE semester = ANY(sem_aliases)
                      AND archive = false
                      AND (sy_clean = '' OR school_year = sy_clean OR school_year IS NULL OR trim(school_year) = '')
                );
            END IF;

            UPDATE public.schedule
            SET archive = true
            WHERE semester = ANY(sem_aliases)
              AND archive = false
              AND (sy_clean = '' OR school_year = sy_clean OR school_year IS NULL OR trim(school_year) = '');

            GET DIAGNOSTICS archived_count = ROW_COUNT;
        END IF;
    END IF;

    -- 2. Insert new confirmed schedule records with archive = false (active)
    IF p_rows IS NOT NULL AND jsonb_typeof(p_rows) = 'array' AND jsonb_array_length(p_rows) > 0 THEN
        INSERT INTO public.schedule (
            prof_course_id,
            room_id,
            day,
            class_start,
            class_end,
            session_type,
            section,
            semester,
            major,
            program,
            school_year,
            archive
        )
        SELECT
            NULLIF(trim(x->>'prof_course_id'), '')::integer,
            NULLIF(trim(x->>'room_id'), '')::integer,
            COALESCE(x->>'day', 'Monday'),
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            COALESCE(NULLIF(x->>'session_type', ''), 'Lecture'),
            x->>'section',
            COALESCE(NULLIF(x->>'semester', ''), sem_clean),
            NULLIF(x->>'major', ''),
            COALESCE(NULLIF(x->>'program', ''), NULLIF(prog_clean, '')),
            COALESCE(NULLIF(x->>'school_year', ''), NULLIF(sy_clean, '')),
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(p_rows) AS x;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
    END IF;

    -- Reset sequence
    PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;

    RETURN jsonb_build_object(
        'success', true,
        'archived_count', archived_count,
        'deleted_count', deleted_count,
        'inserted_count', inserted_count,
        'semester', sem_clean,
        'program', prog_clean,
        'school_year', sy_clean
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, text) TO authenticated, anon, service_role;

