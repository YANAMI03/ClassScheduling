-- ==============================================================================
-- Migration: 20261014_schedule_school_year_and_used.sql
-- Description:
--   1. Lists any foreign key constraints pointing to public.schedule.
--   2. Truncates public.schedule table (without CASCADE).
--   3. Adds school_year (text, NOT NULL, format YYYY-YYYY) and is_used (boolean, DEFAULT false).
--   4. Adds CHECK constraint verifying school_year ~ '^\d{4}-\d{4}$' and second year = first year + 1.
--   5. Creates RPC set_schedule_batch_used to atomically mark/unmark a schedule batch as used,
--      clearing any other schedule in the same school_year + semester + program in a single transaction.
--   6. Updates confirm_schedule_transaction RPC to insert school_year and is_used.
--   7. Updates get_schedule_archive_batches RPC to return school_year and is_used.
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- 1. Query to inspect foreign keys pointing to public.schedule before truncate
-- ------------------------------------------------------------------------------
SELECT
    tc.table_schema, 
    tc.table_name, 
    kcu.column_name, 
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name,
    rc.delete_rule
FROM 
    information_schema.table_constraints AS tc 
    JOIN information_schema.key_column_usage AS kcu
      ON tc.constraint_name = kcu.constraint_name
      AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage AS ccu
      ON ccu.constraint_name = tc.constraint_name
      AND ccu.table_schema = tc.table_schema
    JOIN information_schema.referential_constraints AS rc
      ON rc.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' 
  AND ccu.table_name = 'schedule';

-- ------------------------------------------------------------------------------
-- 2. Truncate public.schedule (without CASCADE)
-- ------------------------------------------------------------------------------
TRUNCATE TABLE public.schedule;

-- ------------------------------------------------------------------------------
-- 3. Add school_year and is_used columns
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule 
    ADD COLUMN IF NOT EXISTS school_year text,
    ADD COLUMN IF NOT EXISTS is_used boolean NOT NULL DEFAULT false;

-- Table is empty after truncation, so NOT NULL can be set immediately
ALTER TABLE public.schedule 
    ALTER COLUMN school_year SET NOT NULL;

-- ------------------------------------------------------------------------------
-- 4. CHECK constraint for school_year format YYYY-YYYY with second year = first + 1
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule 
    DROP CONSTRAINT IF EXISTS chk_schedule_school_year;

ALTER TABLE public.schedule 
    ADD CONSTRAINT chk_schedule_school_year 
    CHECK (
        school_year ~ '^\d{4}-\d{4}$' 
        AND substring(school_year from 6 for 4)::integer = substring(school_year from 1 for 4)::integer + 1
    );

CREATE INDEX IF NOT EXISTS idx_schedule_sy_sem ON public.schedule (school_year, semester);
CREATE INDEX IF NOT EXISTS idx_schedule_is_used ON public.schedule (is_used) WHERE is_used = true;

-- ------------------------------------------------------------------------------
-- 5. RPC: set_schedule_batch_used
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.set_schedule_batch_used(text, boolean, integer);

CREATE OR REPLACE FUNCTION public.set_schedule_batch_used(
    p_batch_id text,
    p_is_used boolean,
    p_program_id integer DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_school_year text;
    v_semester text;
    v_program_id integer := p_program_id;
    v_caller_role text;
    v_caller_pid integer;
    v_updated_count integer := 0;
    v_cleared_count integer := 0;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot mark schedules as used.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role = 'scheduler' THEN
        IF v_program_id IS NOT NULL AND v_caller_pid IS NOT NULL AND v_program_id != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Schedulers can only manage their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_program_id := coalesce(v_program_id, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Locate the batch and determine school_year, semester, and program_id
    IF p_batch_id = 'active' THEN
        SELECT sc.school_year, coalesce(c.semester, '1st Semester'), c.program_id
        INTO v_school_year, v_semester, v_program_id
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = false
          AND (v_program_id IS NULL OR c.program_id = v_program_id)
        LIMIT 1;
    ELSE
        SELECT sc.school_year, coalesce(c.semester, '1st Semester'), c.program_id
        INTO v_school_year, v_semester, v_program_id
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE (sc.archive_batch_id::text = p_batch_id OR (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL AND sc.archive = true))
          AND (v_program_id IS NULL OR c.program_id = v_program_id)
        LIMIT 1;
    END IF;

    IF v_school_year IS NULL THEN
        RAISE EXCEPTION 'Schedule batch % not found.', p_batch_id;
    END IF;

    IF p_is_used THEN
        -- Clear is_used on ANY other schedule in the same school_year + semester + program
        UPDATE public.schedule sc
        SET is_used = false
        FROM public.professor_load pl
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.school_year = v_school_year
          AND trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(v_semester))
          AND (v_program_id IS NULL OR c.program_id = v_program_id)
          AND sc.is_used = true;
        GET DIAGNOSTICS v_cleared_count = ROW_COUNT;

        -- Also clear for any TBA rows in the same school_year
        UPDATE public.schedule sc
        SET is_used = false
        WHERE sc.professor_load_id IS NULL
          AND sc.school_year = v_school_year
          AND sc.is_used = true;

        -- Mark the target batch as used
        IF p_batch_id = 'active' THEN
            UPDATE public.schedule sc
            SET is_used = true
            WHERE sc.archive = false
              AND (
                EXISTS (
                    SELECT 1 FROM public.professor_load pl
                    JOIN public.course c ON pl.course_id = c.course_id
                    WHERE pl.id = sc.professor_load_id
                      AND (v_program_id IS NULL OR c.program_id = v_program_id)
                )
                OR sc.professor_load_id IS NULL
              );
        ELSIF p_batch_id = 'legacy' THEN
            UPDATE public.schedule sc
            SET is_used = true
            WHERE sc.archive = true
              AND sc.archive_batch_id IS NULL
              AND sc.school_year = v_school_year;
        ELSE
            UPDATE public.schedule sc
            SET is_used = true
            WHERE sc.archive_batch_id::text = p_batch_id;
        END IF;

        GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    ELSE
        -- Unmark the target batch as used
        IF p_batch_id = 'active' THEN
            UPDATE public.schedule sc
            SET is_used = false
            WHERE sc.archive = false
              AND (
                EXISTS (
                    SELECT 1 FROM public.professor_load pl
                    JOIN public.course c ON pl.course_id = c.course_id
                    WHERE pl.id = sc.professor_load_id
                      AND (v_program_id IS NULL OR c.program_id = v_program_id)
                )
                OR sc.professor_load_id IS NULL
              );
        ELSIF p_batch_id = 'legacy' THEN
            UPDATE public.schedule sc
            SET is_used = false
            WHERE sc.archive = true
              AND sc.archive_batch_id IS NULL
              AND sc.school_year = v_school_year;
        ELSE
            UPDATE public.schedule sc
            SET is_used = false
            WHERE sc.archive_batch_id::text = p_batch_id;
        END IF;

        GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', p_batch_id,
        'school_year', v_school_year,
        'semester', v_semester,
        'program_id', v_program_id,
        'is_used', p_is_used,
        'updated_count', v_updated_count,
        'cleared_count', v_cleared_count
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.set_schedule_batch_used(text, boolean, integer) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 6. Update confirm_schedule_transaction RPC to insert school_year and is_used
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester            text,
    p_program             text,
    p_rows                jsonb,
    p_clear_scope         boolean DEFAULT true,
    p_archived_by         text    DEFAULT 'Scheduler',
    p_program_id          integer DEFAULT NULL,
    p_batch_id            uuid    DEFAULT NULL,
    p_prepared_by_user_id uuid    DEFAULT NULL,
    p_prepared_by_name    text    DEFAULT NULL,
    p_prepared_by_title   text    DEFAULT NULL,
    p_school_year         text    DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id          uuid;
    v_archived          integer := 0;
    v_inserted          integer := 0;
    v_target_program_id integer := p_program_id;
    v_prep_user_id      uuid := p_prepared_by_user_id;
    v_target_program_name text;
    v_active_batch_ids  uuid[];
BEGIN
    IF p_batch_id IS NOT NULL THEN
        v_batch_id := p_batch_id;
    ELSE
        v_batch_id := gen_random_uuid();
    END IF;

    IF p_semester IS NULL OR trim(p_semester) = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) != '' THEN
        SELECT id, program_name INTO v_target_program_id, v_target_program_name
        FROM public.program
        WHERE upper(trim(program_name)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF v_target_program_id IS NULL THEN
        SELECT id, program_name INTO v_target_program_id, v_target_program_name
        FROM public.program
        WHERE upper(program_name) = 'BSIT'
        LIMIT 1;
    END IF;

    -- Atomically soft-archive ANY currently active entries for this program
    IF p_clear_scope THEN
        SELECT array_agg(DISTINCT sc.archive_batch_id)
        INTO v_active_batch_ids
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = false
          AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
          AND sc.archive_batch_id IS NOT NULL;

        IF v_active_batch_ids IS NOT NULL AND array_length(v_active_batch_ids, 1) > 0 THEN
            UPDATE public.schedule
            SET archive = true,
                archived_at = clock_timestamp(),
                archive_batch_id = coalesce(archive_batch_id, v_batch_id)
            WHERE archive = false
              AND archive_batch_id = ANY(v_active_batch_ids);
        END IF;

        -- Archive any remaining unbatched active entries matching program
        WITH prev_rows AS (
            SELECT sc.schedule_id
            FROM public.schedule sc
            LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
            LEFT JOIN public.course c ON pl.course_id = c.course_id
            WHERE sc.archive = false
              AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
        )
        UPDATE public.schedule sc
        SET archive = true,
            archived_at = clock_timestamp(),
            archive_batch_id = v_batch_id
        FROM prev_rows pr
        WHERE sc.schedule_id = pr.schedule_id;

        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows TAGGED WITH archive_batch_id, school_year, and is_used
    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive, archive_batch_id,
        prepared_by_user_id, school_year, is_used
    )
    SELECT nullif(x->>'professor_load_id', '')::bigint,
           nullif(x->>'room_id', '')::bigint,
           coalesce(x->>'day', 'Monday'),
           (x->>'class_start')::time,
           (x->>'class_end')::time,
           coalesce(nullif(x->>'session_type', ''), 'Lecture'),
           x->>'section',
           false,
           v_batch_id,
           coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id),
           coalesce(x->>'school_year', p_school_year),
           coalesce((x->>'is_used')::boolean, false)
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_count', v_archived,
        'inserted_count', v_inserted,
        'semester',       p_semester,
        'program_id',     v_target_program_id
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text, text) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 7. Update get_schedule_archive_batches RPC to return school_year and is_used
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, timestamptz, timestamptz);
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, text, timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.get_schedule_archive_batches(
    p_program text DEFAULT NULL,
    p_semester text DEFAULT NULL,
    p_school_year text DEFAULT NULL,
    p_date_from timestamptz DEFAULT NULL,
    p_date_to timestamptz DEFAULT NULL
)
RETURNS TABLE (
    batch_id text,
    semester text,
    school_year text,
    program text,
    archived_at timestamptz,
    archived_by text,
    archive_reason text,
    entry_count bigint,
    section_count bigint,
    sections text[],
    is_used boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH batch_data AS (
        SELECT
            coalesce(
                sc.archive_batch_id::text,
                replace(coalesce(c.semester, '1st Semester'), ' ', '_')
                    || '__' || coalesce(p.program_name, c.program_id::text, 'unknown')
            ) AS b_id,
            coalesce(c.semester, '1st Semester')::text AS b_semester,
            coalesce(sc.school_year, '')::text AS b_school_year,
            coalesce(p.program_name, '')::text AS b_program,
            sc.archived_at AS b_archived_at,
            coalesce(
                nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                nullif(trim(u.username), ''),
                'Preparer account unavailable'
            )::text AS b_archived_by,
            'Archived schedule'::text AS b_archive_reason,
            sc.section::text AS b_section,
            bool_or(sc.is_used) OVER (PARTITION BY coalesce(sc.archive_batch_id::text, 'legacy')) AS b_is_used
        FROM public.schedule sc
        LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id
        LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
        LEFT JOIN public.course c ON c.course_id = pl.course_id
        LEFT JOIN public.program p ON p.id = c.program_id
        WHERE sc.archive = true
          AND (p_program IS NULL OR nullif(trim(p_program), '') IS NULL
               OR trim(lower(coalesce(p.program_name, ''))) = trim(lower(p_program)))
          AND (p_semester IS NULL OR nullif(trim(p_semester), '') IS NULL
               OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
          AND (p_school_year IS NULL OR nullif(trim(p_school_year), '') IS NULL
               OR trim(sc.school_year) = trim(p_school_year))
    )
    SELECT
        bd.b_id,
        (array_agg(bd.b_semester ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        (array_agg(bd.b_school_year ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        (array_agg(bd.b_program ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        max(bd.b_archived_at),
        coalesce((array_agg(bd.b_archived_by ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Preparer account unavailable'),
        coalesce((array_agg(bd.b_archive_reason ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Archived schedule'),
        count(*),
        count(DISTINCT bd.b_section),
        array_agg(DISTINCT bd.b_section ORDER BY bd.b_section),
        bool_or(bd.b_is_used)
    FROM batch_data bd
    WHERE (p_date_from IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at >= p_date_from))
      AND (p_date_to IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at <= p_date_to))
    GROUP BY bd.b_id
    ORDER BY max(bd.b_archived_at) DESC NULLS LAST;
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_schedule_archive_batches(text, text, text, timestamptz, timestamptz) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
