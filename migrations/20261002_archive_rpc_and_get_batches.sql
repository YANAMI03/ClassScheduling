-- ==============================================================================
-- Migration: 20261002_archive_rpc_and_get_batches.sql
-- Description:
--   1. Non-destructive schema addition:
--      Adds archive_batch_id, archived_at, archived_by, archive_reason to public.schedule.
--   2. Performance indexes:
--      Indexes on (archive, archive_batch_id) and (archived_at DESC).
--   3. RPC: archive_active_schedule
--      Atomically archives active schedule records in public.schedule
--      with a shared batch_id and archived_at timestamp.
--   4. RPC: get_schedule_archive_batches
--      Aggregates public.schedule by archive_batch_id, returns one row per batch
--      action with entry & section counts, sorted newest first by archive timestamp.
--      Supports filtering by program, semester, and date range.
--      Preserves legacy archived rows (NULL batch_id) as 'legacy' batch.
--   5. RPC: confirm_schedule_transaction
--      Updated to tag previous active schedules with archive_batch_id and timestamp
--      whenever a newly confirmed schedule replaces them.
--   6. RPC: restore_archived_schedule_batch
--      Restores only the selected batch back to active schedule, enforcing the
--      one-active-schedule-per-semester rule by soft-archiving active schedules first.
-- ==============================================================================

BEGIN;

-- ============================================================================
-- 1. Schema Additions to public.schedule (Non-destructive)
-- ============================================================================
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS archive_batch_id UUID;
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS archived_by VARCHAR(150);
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS archive_reason VARCHAR(255);

-- Indexes for fast aggregation and queries
CREATE INDEX IF NOT EXISTS idx_schedule_archive_batch ON public.schedule (archive, archive_batch_id) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_schedule_archived_at ON public.schedule (archived_at DESC) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_schedule_archived_prog_sem ON public.schedule (archive, program_id, semester) WHERE archive = true;


-- ============================================================================
-- 2. RPC: archive_active_schedule
--    Archives currently active schedule rows for a program in public.schedule
--    with a shared batch_id and archived_at timestamp.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.archive_active_schedule(
    p_archived_by  text    DEFAULT 'Scheduler',
    p_program_id   integer DEFAULT NULL,
    p_reason       text    DEFAULT 'Manually archived'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id  uuid        := gen_random_uuid();
    v_now       timestamptz := clock_timestamp();
    v_count     integer     := 0;
BEGIN
    UPDATE public.schedule
    SET archive = true,
        archive_batch_id = v_batch_id,
        archived_at = v_now,
        archived_by = coalesce(nullif(trim(p_archived_by), ''), 'Scheduler'),
        archive_reason = coalesce(nullif(trim(p_reason), ''), 'Manually archived')
    WHERE archive = false
      AND (p_program_id IS NULL OR program_id = p_program_id);

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_at',    v_now,
        'archived_count', v_count
    );
END;
$$;

REVOKE ALL ON FUNCTION public.archive_active_schedule(text, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(text, integer, text) TO anon, authenticated, service_role;


-- ============================================================================
-- 3. RPC: get_schedule_archive_batches
--    Returns one row per batch from public.schedule, with counts and
--    optional filtering by program name, semester, and date range.
--    Sorted newest first by archived_at timestamp.
--    Legacy rows (archive = true and archive_batch_id IS NULL) are grouped into 'legacy'.
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text);
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.get_schedule_archive_batches(
    p_program   text        DEFAULT NULL,
    p_semester  text        DEFAULT NULL,
    p_date_from timestamptz DEFAULT NULL,
    p_date_to   timestamptz DEFAULT NULL
)
RETURNS TABLE (
    batch_id       text,
    semester       text,
    program        text,
    archived_at    timestamptz,
    archived_by    text,
    archive_reason text,
    entry_count    bigint,
    section_count  bigint,
    sections       text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH batch_data AS (
        SELECT
            coalesce(sc.archive_batch_id::text, 'legacy') AS b_id,
            sc.semester::text                              AS b_semester,
            coalesce(p.program_name, '')::text            AS b_program,
            sc.archived_at                                AS b_archived_at,
            sc.archived_by::text                          AS b_archived_by,
            sc.archive_reason::text                        AS b_archive_reason,
            sc.section::text                              AS b_section
        FROM public.schedule sc
        LEFT JOIN public.program p ON sc.program_id = p.id
        WHERE sc.archive = true
          AND (p_program IS NULL OR nullif(trim(p_program), '') IS NULL OR trim(lower(coalesce(p.program_name, ''))) = trim(lower(p_program)))
          AND (p_semester IS NULL OR nullif(trim(p_semester), '') IS NULL OR trim(lower(sc.semester)) = trim(lower(p_semester)))
    )
    SELECT
        bd.b_id                                                           AS batch_id,
        (array_agg(bd.b_semester ORDER BY bd.b_archived_at DESC NULLS LAST))[1] AS semester,
        (array_agg(bd.b_program ORDER BY bd.b_archived_at DESC NULLS LAST))[1]  AS program,
        MAX(bd.b_archived_at)                                             AS archived_at,
        coalesce((array_agg(bd.b_archived_by ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'System (Legacy)') AS archived_by,
        coalesce((array_agg(bd.b_archive_reason ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Legacy archive') AS archive_reason,
        COUNT(*)                                                          AS entry_count,
        COUNT(DISTINCT bd.b_section)                                      AS section_count,
        array_agg(DISTINCT bd.b_section ORDER BY bd.b_section)            AS sections
    FROM batch_data bd
    WHERE (
        (p_date_from IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at >= p_date_from))
        AND
        (p_date_to IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at <= p_date_to))
    )
    GROUP BY bd.b_id
    ORDER BY MAX(bd.b_archived_at) DESC NULLS LAST;
END;
$$;

REVOKE ALL ON FUNCTION public.get_schedule_archive_batches(text, text, timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_schedule_archive_batches(text, text, timestamptz, timestamptz) TO anon, authenticated, service_role;


-- ============================================================================
-- 4. RPC: confirm_schedule_transaction
--    Sets archive_batch_id, archived_at, archived_by, archive_reason when
--    soft-archiving old records during confirmation of a new schedule.
-- ============================================================================
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text,
    p_rows jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT 'Scheduler',
    p_program_id integer DEFAULT NULL,
    p_batch_id uuid DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id uuid;
    v_archived integer := 0;
    v_inserted integer := 0;
    v_target_program_id integer := p_program_id;
BEGIN
    IF p_batch_id IS NOT NULL THEN
        v_batch_id := p_batch_id;
    ELSE
        v_batch_id := gen_random_uuid();
    END IF;

    IF p_semester IS NULL OR trim(p_semester) = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    -- Resolve program_id if not explicitly provided
    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) != '' THEN
        SELECT id INTO v_target_program_id
        FROM public.program
        WHERE upper(trim(program_name)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF v_target_program_id IS NULL THEN
        SELECT id INTO v_target_program_id
        FROM public.program
        WHERE upper(program_name) = 'BSIT'
        LIMIT 1;
    END IF;

    IF p_clear_scope THEN
        -- Soft archive previous active entries for this scope with batch_id and timestamp
        UPDATE public.schedule
        SET archive = true,
            archive_batch_id = v_batch_id,
            archived_at = clock_timestamp(),
            archived_by = coalesce(nullif(trim(p_archived_by), ''), 'Scheduler'),
            archive_reason = 'Archived on schedule confirmation'
        WHERE archive = false
          AND (p_semester IS NULL OR semester = p_semester)
          AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows referencing program_id
    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, semester, major, archive
    )
    SELECT coalesce(
               nullif(x->>'program_id', '')::integer,
               (SELECT p.id FROM public.program p WHERE upper(trim(p.program_name)) = upper(trim(x->>'program')) LIMIT 1),
               v_target_program_id
           ),
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
        'inserted_count', v_inserted,
        'semester', p_semester,
        'program_id', v_target_program_id
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) TO anon, authenticated, service_role;


-- ============================================================================
-- 5. RPC: restore_archived_schedule_batch
--    Restores only the specified batch to active schedule in public.schedule.
--    Maintains the "one active schedule per semester" rule by archiving
--    any active schedule for the target semester & program first.
-- ============================================================================
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(uuid, text);
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(text, text);

CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id    text,
    p_restored_by text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_target_semester    text;
    v_target_program_id  integer;
    v_target_program     text;
    v_restored           integer := 0;
    v_archived_current   integer := 0;
    v_new_archive_batch  uuid    := gen_random_uuid();
BEGIN
    -- Identify the batch scope
    SELECT sc.semester, sc.program_id, p.program_name
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    LEFT JOIN public.program p ON sc.program_id = p.id
    WHERE (
        (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL AND sc.archive = true)
        OR
        (sc.archive_batch_id::text = p_batch_id AND sc.archive = true)
    )
    LIMIT 1;

    IF v_target_semester IS NULL THEN
        RAISE EXCEPTION 'Archived batch % not found.', p_batch_id;
    END IF;

    -- 1. Soft-archive ANY currently active schedule for the same semester and program
    UPDATE public.schedule
    SET archive = true,
        archive_batch_id = v_new_archive_batch,
        archived_at = clock_timestamp(),
        archived_by = coalesce(nullif(trim(p_restored_by), ''), 'Scheduler'),
        archive_reason = 'Archived prior to restoring schedule'
    WHERE archive = false
      AND (v_target_semester IS NULL OR semester = v_target_semester)
      AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
    GET DIAGNOSTICS v_archived_current = ROW_COUNT;

    -- 2. Reactivate the specified batch (archive = false)
    IF p_batch_id = 'legacy' THEN
        UPDATE public.schedule
        SET archive = false
        WHERE archive = true
          AND archive_batch_id IS NULL
          AND (v_target_semester IS NULL OR semester = v_target_semester)
          AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
    ELSE
        UPDATE public.schedule
        SET archive = false
        WHERE archive = true
          AND archive_batch_id::text = p_batch_id;
    END IF;
    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',                true,
        'restored_batch_id',      p_batch_id,
        'semester',               v_target_semester,
        'program',                v_target_program,
        'archived_current_count', v_archived_current,
        'restored_count',         v_restored,
        'restored_by',            coalesce(p_restored_by, 'Scheduler')
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;

COMMIT;
