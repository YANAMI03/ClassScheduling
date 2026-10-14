-- ==============================================================================
-- Migration: Remove schedule-users link and restore prepared_by snapshot columns
-- File: supabase/migrations/20261014_remove_schedule_users_fk_and_restore_snapshot.sql
-- Description:
--   1. Dynamically finds and drops foreign key constraints and performance indexes
--      on schedule.prepared_by_user_id pointing to public.users (without CASCADE).
--   2. Updates public.schedule_with_semester view beforehand so dropping the
--      column does not violate view dependency.
--   3. Drops column public.schedule.prepared_by_user_id.
--   4. Re-creates public.schedule.prepared_by_name and prepared_by_title as snapshot
--      text columns (NOT NULL if table is empty; nullable if table has rows).
--   5. Updates confirm_schedule_transaction and get_schedule_archive_batches RPCs
--      to use the snapshot columns with zero joins or references to public.users.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Update/Drop view dependency BEFORE dropping column (strictly no CASCADE)
-- ------------------------------------------------------------------------------
DROP VIEW IF EXISTS public.schedule_with_semester;

-- ------------------------------------------------------------------------------
-- 2. Dynamically find and drop any foreign key constraints on schedule.prepared_by_user_id
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
            ON tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = 'schedule'
          AND kcu.column_name = 'prepared_by_user_id'
    ) LOOP
        EXECUTE 'ALTER TABLE public.schedule DROP CONSTRAINT IF EXISTS ' || quote_ident(r.constraint_name);
        RAISE NOTICE 'Dropped foreign key constraint: %', r.constraint_name;
    END LOOP;
END $$;

-- Drop any performance index on prepared_by_user_id
DROP INDEX IF EXISTS public.idx_schedule_prepared_by_user_id;

-- ------------------------------------------------------------------------------
-- 3. Drop column prepared_by_user_id from schedule (without CASCADE)
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule
    DROP COLUMN IF EXISTS prepared_by_user_id;

-- ------------------------------------------------------------------------------
-- 4. Add snapshot text columns (NOT NULL if table empty; nullable if rows exist)
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    v_row_count bigint;
BEGIN
    SELECT count(*) INTO v_row_count FROM public.schedule;

    IF v_row_count = 0 THEN
        ALTER TABLE public.schedule
            ADD COLUMN IF NOT EXISTS prepared_by_name text NOT NULL,
            ADD COLUMN IF NOT EXISTS prepared_by_title text NOT NULL;
        RAISE NOTICE 'Table public.schedule is empty: added prepared_by_name and prepared_by_title as NOT NULL.';
    ELSE
        ALTER TABLE public.schedule
            ADD COLUMN IF NOT EXISTS prepared_by_name text,
            ADD COLUMN IF NOT EXISTS prepared_by_title text;
        RAISE NOTICE 'Table public.schedule has % rows: added prepared_by_name and prepared_by_title as NULLABLE.', v_row_count;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 5. Re-create public.schedule_with_semester view (purely decoupled from users)
-- ------------------------------------------------------------------------------
CREATE VIEW public.schedule_with_semester
WITH (security_invoker = on)
AS
SELECT
    sc.schedule_id,
    sc.room_id,
    sc.day,
    sc.class_start,
    sc.class_end,
    sc.session_type,
    sc.section,
    sc.professor_load_id,
    sc.archive,
    c.program_id,
    sc.archived_at,
    sc.archive_batch_id,
    sc.school_year,
    sc.is_used,
    sc.prepared_by_name,
    sc.prepared_by_title,
    coalesce(nullif(trim(sc.prepared_by_name), ''), 'Preparer account unavailable') AS preparer_name,
    coalesce(nullif(trim(sc.prepared_by_title), ''), 'Role/title unavailable') AS preparer_title,
    coalesce(c.semester, '1st Semester') AS semester,
    c.specialization,
    c.specialization AS major,
    pl.course_id,
    c.course_code,
    c.year_level,
    pl.professor_name,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.program p ON p.id = c.program_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 6. Update confirm_schedule_transaction RPC (reads plain snapshot values)
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, text, text, text);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester            text,
    p_program             text,
    p_rows                jsonb,
    p_clear_scope         boolean DEFAULT true,
    p_archived_by         text    DEFAULT 'Chair',
    p_program_id          integer DEFAULT NULL,
    p_batch_id            uuid    DEFAULT NULL,
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
    v_prep_name         text    := nullif(trim(p_prepared_by_name), '');
    v_prep_title        text    := nullif(trim(p_prepared_by_title), '');
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

    -- Resolve program ID from program name if not provided
    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) <> '' THEN
        SELECT id, program_name
        INTO v_target_program_id, v_target_program_name
        FROM public.program
        WHERE upper(trim(program_name)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF v_target_program_id IS NULL THEN
        SELECT id, program_name
        INTO v_target_program_id, v_target_program_name
        FROM public.program
        WHERE upper(program_name) = 'BSIT'
        LIMIT 1;
    END IF;

    -- Fallback preparer values if missing
    IF v_prep_name IS NULL THEN
        v_prep_name := coalesce(nullif(trim(p_archived_by), ''), 'Unknown Preparer');
    END IF;
    IF v_prep_title IS NULL THEN
        v_prep_title := 'Chair';
    END IF;

    IF p_clear_scope THEN
        -- Find distinct batch IDs of active rows for this program
        SELECT array_agg(DISTINCT sc.archive_batch_id)
        INTO v_active_batch_ids
        FROM public.schedule sc
        JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = false
          AND sc.archive_batch_id IS NOT NULL
          AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id);

        IF v_active_batch_ids IS NOT NULL AND array_length(v_active_batch_ids, 1) > 0 THEN
            UPDATE public.schedule sc
            SET archive = true,
                archived_at = clock_timestamp()
            WHERE sc.archive_batch_id = ANY(v_active_batch_ids)
              AND sc.archive = false;
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

    -- Insert new schedule rows with snapshot columns, school_year, and is_used
    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive, archive_batch_id,
        prepared_by_name, prepared_by_title, school_year, is_used
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
           coalesce(nullif(trim(x->>'prepared_by_name'), ''), v_prep_name),
           coalesce(nullif(trim(x->>'prepared_by_title'), ''), v_prep_title),
           coalesce(x->>'school_year', p_school_year),
           false
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

GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, text, text, text) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 7. Update get_schedule_archive_batches RPC (no users join)
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
    archived_by_title text,
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
                nullif(trim(sc.prepared_by_name), ''),
                'Preparer account unavailable'
            )::text AS b_archived_by,
            coalesce(
                nullif(trim(sc.prepared_by_title), ''),
                'Role/title unavailable'
            )::text AS b_archived_by_title,
            'Archived schedule'::text AS b_archive_reason,
            sc.section::text AS b_section,
            bool_or(sc.is_used) OVER (PARTITION BY coalesce(sc.archive_batch_id::text, 'legacy')) AS b_is_used
        FROM public.schedule sc
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
        coalesce((array_agg(bd.b_archived_by_title ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Role/title unavailable'),
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

COMMIT;
