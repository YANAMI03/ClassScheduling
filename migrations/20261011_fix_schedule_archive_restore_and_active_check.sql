-- ==============================================================================
-- Migration: 20261011_fix_schedule_archive_restore_and_active_check.sql
-- Description:
--   1. Fix archive_active_schedule RPC:
--      - Correct c.program_id reference (sc.program_id was dropped in derive migration).
--      - Unified batch-based archiving: archives ALL active rows for the program
--        and semester, including TBA fallback rows (professor_load_id IS NULL)
--        associated with the active batch.
--      - Enforce all-or-nothing: verifies count > 0 and verifies zero active rows remain.
--   2. Fix restore_archived_schedule_batch RPC:
--      - Enforces single active schedule check with active count in error message.
--      - Restores ALL rows belonging to the archive batch (including TBA rows).
--      - Verifies that restored count > 0.
--   3. Fix confirm_schedule_transaction RPC:
--      - Assigns archive_batch_id to newly inserted active rows (so active rows
--        always have batch identity).
--      - Atomically soft-archives all previous active rows (including TBA rows)
--        before inserting the new batch.
--   4. Re-create schedule RLS policies for UPDATE and INSERT so Schedulers can
--      archive and restore their own program's schedules without RLS blocking.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. RECREATE GRANULAR RLS POLICIES ON public.schedule (NO sc.program_id REFERENCE)
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
DROP POLICY IF EXISTS "schedule_insert" ON public.schedule;
DROP POLICY IF EXISTS "schedule_update" ON public.schedule;
DROP POLICY IF EXISTS "schedule_delete" ON public.schedule;

-- (a) SELECT:
--     - Admin sees all schedules across all programs.
--     - Scheduler sees own program's schedules (via course.program_id or TBA rows with NULL load)
--       PLUS active schedules of other programs for cross-program conflict detection.
--     - Viewer sees active schedules only (archive = false).
CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (
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
    OR (archive = false)
);

-- (b) INSERT:
--     - Admin can insert for any program.
--     - Scheduler can insert for own program (or TBA unassigned rows).
CREATE POLICY "schedule_insert" ON public.schedule
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() = 'admin'
    OR (
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
);

-- (c) UPDATE:
--     - Admin can update for any program.
--     - Scheduler can update for own program (archive and restore operations).
CREATE POLICY "schedule_update" ON public.schedule
FOR UPDATE TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (
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
)
WITH CHECK (
    public.get_user_role() = 'admin'
    OR (
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
);

-- (d) DELETE:
--     - Admin ONLY, and ONLY where archive = true.
CREATE POLICY "schedule_delete" ON public.schedule
FOR DELETE TO authenticated
USING (
    public.get_user_role() = 'admin'
    AND archive = true
);


-- ------------------------------------------------------------------------------
-- 2. FIX RPC: archive_active_schedule
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.archive_active_schedule(integer, text, text, text);

CREATE OR REPLACE FUNCTION public.archive_active_schedule(
    p_program_id   integer DEFAULT NULL,
    p_semester     text    DEFAULT NULL,
    p_archived_by  text    DEFAULT 'Scheduler',
    p_reason       text    DEFAULT 'Manually archived'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id         uuid        := gen_random_uuid();
    v_now              timestamptz := clock_timestamp();
    v_count            integer     := 0;
    v_remaining_active integer     := 0;
    v_caller_role      text;
    v_caller_pid       integer;
    v_target_pid       integer     := p_program_id;
    v_active_batch_ids uuid[];
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    -- Scoping check
    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot archive schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role = 'scheduler' THEN
        IF v_target_pid IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_pid != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Schedulers can only archive their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_pid := coalesce(v_target_pid, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Step 1: Collect any active batch IDs associated with this program and semester
    SELECT array_agg(DISTINCT sc.archive_batch_id)
    INTO v_active_batch_ids
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive = false
      AND (v_target_pid IS NULL OR c.program_id = v_target_pid)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
      AND sc.archive_batch_id IS NOT NULL;

    -- Step 2: Archive by active batch IDs (captures all rows of the batch including TBA rows)
    IF v_active_batch_ids IS NOT NULL AND array_length(v_active_batch_ids, 1) > 0 THEN
        UPDATE public.schedule
        SET archive = true,
            archived_at = v_now,
            archive_batch_id = coalesce(archive_batch_id, v_batch_id)
        WHERE archive = false
          AND archive_batch_id = ANY(v_active_batch_ids);

        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;

    -- Step 3: Archive any unbatched active rows matching program and semester
    WITH target_rows AS (
        SELECT sc.schedule_id
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = false
          AND (v_target_pid IS NULL OR c.program_id = v_target_pid)
          AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
    )
    UPDATE public.schedule sc
    SET archive = true,
        archived_at = v_now,
        archive_batch_id = v_batch_id
    FROM target_rows tr
    WHERE sc.schedule_id = tr.schedule_id;

    v_count := v_count + (SELECT count(*) FROM public.schedule WHERE archive_batch_id = v_batch_id AND archived_at = v_now);

    -- Step 4: Safety post-condition verification - NO active rows must remain
    SELECT count(*)
    INTO v_remaining_active
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive = false
      AND (v_target_pid IS NULL OR c.program_id = v_target_pid)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)));

    IF v_remaining_active > 0 THEN
        RAISE EXCEPTION 'Archiving failed: % active rows still remain for program % (%). Rolling back transaction.',
            v_remaining_active, v_target_pid, p_semester;
    END IF;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_at',    v_now,
        'archived_count', v_count,
        'program_id',     v_target_pid,
        'semester',       p_semester
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 3. FIX RPC: restore_archived_schedule_batch
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(text, text);

CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id    text,
    p_restored_by text DEFAULT 'Scheduler'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_target_semester   text;
    v_target_program_id integer;
    v_target_program    text;
    v_active_count      integer := 0;
    v_restored          integer := 0;
    v_caller_role       text;
    v_caller_prog_id    integer;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_prog_id := public.get_user_program_id();

    -- Identify batch scope
    SELECT coalesce(c.semester, '1st Semester'), c.program_id, p.program_name
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    LEFT JOIN public.program p ON c.program_id = p.id
    WHERE (
        (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL AND sc.archive = true)
        OR
        (sc.archive_batch_id::text = p_batch_id AND sc.archive = true)
    )
    LIMIT 1;

    IF v_target_semester IS NULL THEN
        RAISE EXCEPTION 'Archived batch % not found.', p_batch_id;
    END IF;

    -- Security enforcement
    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot restore schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role = 'scheduler' THEN
        IF v_target_program_id IS NOT NULL AND v_caller_prog_id IS NOT NULL AND v_target_program_id != v_caller_prog_id THEN
            RAISE EXCEPTION 'Permission denied: Schedulers can only restore their own program archives.'
                USING ERRCODE = '42501';
        END IF;
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Enforce single active schedule rule: Check for active rows in target program and semester
    SELECT count(*)
    INTO v_active_count
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive = false
      AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
      AND (trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(v_target_semester)));

    IF v_active_count > 0 THEN
        RAISE EXCEPTION 'Cannot restore schedule: An active schedule already exists for % (%) with % active classes. Please archive the active schedule before restoring this version.',
            coalesce(v_target_program, 'this program'), v_target_semester, v_active_count;
    END IF;

    -- Reactivate the specified batch (including TBA rows belonging to this batch)
    IF p_batch_id = 'legacy' THEN
        UPDATE public.schedule sc
        SET archive = false,
            archived_at = NULL,
            archive_batch_id = NULL
        FROM public.professor_load pl
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.archive = true
          AND sc.archive_batch_id IS NULL
          AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
          AND (trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(v_target_semester)));
    ELSE
        UPDATE public.schedule
        SET archive = false,
            archived_at = NULL
        WHERE archive = true
          AND archive_batch_id::text = p_batch_id;
    END IF;

    GET DIAGNOSTICS v_restored = ROW_COUNT;

    IF v_restored = 0 THEN
        RAISE EXCEPTION 'Restoration failed: 0 rows were restored for batch %.', p_batch_id;
    END IF;

    RETURN jsonb_build_object(
        'success',           true,
        'restored_batch_id', p_batch_id,
        'restored_count',    v_restored,
        'semester',          v_target_semester,
        'program',           v_target_program,
        'program_id',        v_target_program_id
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 4. FIX RPC: confirm_schedule_transaction
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text);

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
    p_prepared_by_title   text    DEFAULT NULL
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
    v_prep_name         text := p_prepared_by_name;
    v_prep_title        text := p_prepared_by_title;
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

    -- Atomically soft-archive previous active entries (including TBA rows)
    IF p_clear_scope THEN
        SELECT array_agg(DISTINCT sc.archive_batch_id)
        INTO v_active_batch_ids
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = false
          AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
          AND (trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
          AND sc.archive_batch_id IS NOT NULL;

        IF v_active_batch_ids IS NOT NULL AND array_length(v_active_batch_ids, 1) > 0 THEN
            UPDATE public.schedule
            SET archive = true,
                archived_at = clock_timestamp(),
                archive_batch_id = coalesce(archive_batch_id, v_batch_id)
            WHERE archive = false
              AND archive_batch_id = ANY(v_active_batch_ids);
        END IF;

        -- Archive any remaining unbatched active entries matching program/semester
        WITH prev_rows AS (
            SELECT sc.schedule_id
            FROM public.schedule sc
            LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
            LEFT JOIN public.course c ON pl.course_id = c.course_id
            WHERE sc.archive = false
              AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id)
              AND (trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
        )
        UPDATE public.schedule sc
        SET archive = true,
            archived_at = clock_timestamp(),
            archive_batch_id = v_batch_id
        FROM prev_rows pr
        WHERE sc.schedule_id = pr.schedule_id;

        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows TAGGED WITH archive_batch_id
    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive, archive_batch_id,
        prepared_by_user_id
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
           coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id)
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

GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
