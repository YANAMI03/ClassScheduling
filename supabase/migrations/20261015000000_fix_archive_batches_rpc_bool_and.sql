-- ==============================================================================
-- Migration: 20261015000000_fix_archive_batches_rpc_bool_and.sql
-- Description:
--   Fixes two defects that were left behind by the two 20261014 migrations,
--   whose file-name ordering applied "remove ... snapshot" BEFORE
--   "schedule_school_year_and_used", so the LATER file re-created the archive
--   RPC with references to the already-dropped schedule.prepared_by_user_id
--   column (and a broken public.users join).
--
--   1. Ensures the snapshot / school_year / is_used columns exist.
--   2. Re-creates public.schedule_with_semester WITHOUT any users join.
--   3. Re-creates public.get_schedule_archive_batches:
--        - no public.users join and no prepared_by_user_id reference,
--        - batch "used" status uses bool_and (all rows), not bool_or (any row).
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Ensure the required columns exist (idempotent, additive only)
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule
    ADD COLUMN IF NOT EXISTS school_year text,
    ADD COLUMN IF NOT EXISTS is_used boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS prepared_by_name text,
    ADD COLUMN IF NOT EXISTS prepared_by_title text;

-- ------------------------------------------------------------------------------
-- 2. Re-create public.schedule_with_semester decoupled from public.users
-- ------------------------------------------------------------------------------
DROP VIEW IF EXISTS public.schedule_with_semester;

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
-- 3. Re-create the archive batches RPC (no users join, bool_and semantics)
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
            sc.is_used AS b_is_used
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
        -- A batch is "used" only when EVERY row of the batch has is_used = true.
        bool_and(bd.b_is_used)
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
