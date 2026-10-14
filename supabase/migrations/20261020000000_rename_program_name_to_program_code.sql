-- ==============================================================================
-- Migration: 20261020000000_rename_program_name_to_program_code.sql
-- Phase 2: rename public.program.program_name -> program_code.
--
-- program_name actually held the program CODE (e.g. BSIT, BSDS); it is now
-- program_code. This migration ONLY renames -- it does NOT add the new
-- full-name column (that is Phase 3).
--
-- PostgreSQL automatically rewrites dependent indexes, views and RLS policy
-- expressions when a column is renamed (they are stored as parsed trees), so
-- only the function bodies (stored as text) are recreated here.
--
-- Views (auto-updated; listed for reference):
--   public.programs               (program_name AS name, program_name)
--   public.schedule_with_semester (p.program_name)
-- Functions (recreated below):
--   public.handle_new_auth_user
--   public.archive_active_schedule
--   public.restore_archived_schedule_batch
--   public.confirm_schedule_transaction
--   public.get_schedule_archive_batches
--   public.restore_database_json
-- ==============================================================================

BEGIN;

ALTER TABLE public.program RENAME COLUMN program_name TO program_code;

-- ------------------------------------------------------------------------------
-- Recreate the functions that referenced public.program.program_name.
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_raw_program_id text;
    v_raw_program_code text;
    v_program_id integer;
    v_raw_full_name text;
    v_first_name text;
    v_middle_initial text;
    v_last_name text;
    v_username text;
    v_given_names text;
    v_tokens text[];
BEGIN
    -- 1. Extract and sanitize full name (format: Last name, First name M.I.)
    v_raw_full_name := NULLIF(TRIM(new.raw_user_meta_data->>'full_name'), '');

    IF v_raw_full_name IS NOT NULL AND position(',' in v_raw_full_name) > 0 THEN
        v_last_name := SUBSTRING(TRIM(split_part(v_raw_full_name, ',', 1)) FROM 1 FOR 100);
        v_given_names := TRIM(split_part(v_raw_full_name, ',', 2));

        -- Split given names into first name and middle initial
        v_tokens := regexp_split_to_array(v_given_names, '\s+');
        IF array_length(v_tokens, 1) >= 2 THEN
            v_first_name := SUBSTRING(v_tokens[1] FROM 1 FOR 100);
            v_middle_initial := NULLIF(UPPER(LEFT(TRIM(array_to_string(v_tokens[2:array_length(v_tokens, 1)], ' ')), 1)), '');
        ELSE
            v_first_name := SUBSTRING(v_given_names FROM 1 FOR 100);
            v_middle_initial := NULL;
        END IF;
    ELSE
        -- Fallback to separate first/last names if provided
        v_first_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), ''), '') FROM 1 FOR 100);
        v_middle_initial := NULLIF(UPPER(LEFT(TRIM(COALESCE(new.raw_user_meta_data->>'middle_initial', '')), 1)), '');
        v_last_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), ''), '') FROM 1 FOR 100);
    END IF;

    -- Keep the middle initial a single A-Z letter or NULL.
    v_middle_initial := CASE WHEN v_middle_initial ~ '^[A-Z]$' THEN v_middle_initial ELSE NULL END;

    -- 2. Validate Program (REQUIRED for signup)
    v_raw_program_id := TRIM(COALESCE(new.raw_user_meta_data->>'program_id', ''));
    v_raw_program_code := TRIM(COALESCE(new.raw_user_meta_data->>'program', ''));

    IF v_raw_program_id ~ '^\d+$' THEN
        SELECT id INTO v_program_id
        FROM public.program
        WHERE id = v_raw_program_id::integer AND COALESCE(archive, false) = false
        LIMIT 1;
    ELSIF v_raw_program_code <> '' THEN
        SELECT id INTO v_program_id
        FROM public.program
        WHERE UPPER(TRIM(program_code)) = UPPER(TRIM(v_raw_program_code)) AND COALESCE(archive, false) = false
        LIMIT 1;
    END IF;

    -- If program is missing or invalid, fail the entire transaction (no half-created accounts)
    IF v_program_id IS NULL THEN
        RAISE EXCEPTION 'A valid, active program is required to create an account.';
    END IF;

    -- 3. Username resolution
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));

    -- 4. Insert or update public.users with FORCED role = 'viewer' and validated program_id
    INSERT INTO public.users (
        id, email, username, first_name, middle_initial, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        COALESCE(v_first_name, ''),
        v_middle_initial,
        COALESCE(v_last_name, ''),
        v_program_id,
        'viewer',  -- Always forced to viewer, ignoring any client role input
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE EXCLUDED.first_name END,
        middle_initial = CASE WHEN public.users.middle_initial IS NOT NULL AND public.users.middle_initial <> '' THEN public.users.middle_initial ELSE EXCLUDED.middle_initial END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END,
        program_id = COALESCE(public.users.program_id, EXCLUDED.program_id),
        role = COALESCE(public.users.role, 'viewer'),
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture);

    RETURN new;
END;
$$;

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

    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot archive schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role IN ('chair', 'scheduler') THEN
        IF v_target_pid IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_pid != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only archive their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_pid := coalesce(v_target_pid, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    SELECT array_agg(DISTINCT sc.archive_batch_id)
    INTO v_active_batch_ids
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive = false
      AND (v_target_pid IS NULL OR c.program_id = v_target_pid)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
      AND sc.archive_batch_id IS NOT NULL;

    IF v_active_batch_ids IS NOT NULL AND array_length(v_active_batch_ids, 1) > 0 THEN
        UPDATE public.schedule
        SET archive = true,
            archived_at = v_now,
            archive_batch_id = coalesce(archive_batch_id, v_batch_id)
        WHERE archive = false
          AND archive_batch_id = ANY(v_active_batch_ids);
        GET DIAGNOSTICS v_count = ROW_COUNT;
    END IF;

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

    SELECT coalesce(c.semester, '1st Semester'), c.program_id, p.program_code
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

    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot restore schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role IN ('chair', 'scheduler') THEN
        IF v_target_program_id IS NOT NULL AND v_caller_prog_id IS NOT NULL AND v_target_program_id != v_caller_prog_id THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only restore their own program archives.'
                USING ERRCODE = '42501';
        END IF;
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

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
    v_target_program_code text;
    v_active_batch_ids  uuid[];
    v_caller_role       text;
    v_caller_pid        integer;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot confirm schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role IN ('chair', 'scheduler') THEN
        IF v_target_program_id IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_program_id != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only confirm their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_program_id := coalesce(v_target_program_id, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    IF p_batch_id IS NOT NULL THEN
        v_batch_id := p_batch_id;
    ELSE
        v_batch_id := gen_random_uuid();
    END IF;

    IF p_semester IS NULL OR trim(p_semester) = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) != '' THEN
        SELECT id, program_code INTO v_target_program_id, v_target_program_code
        FROM public.program
        WHERE upper(trim(program_code)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF v_target_program_id IS NULL THEN
        SELECT id, program_code INTO v_target_program_id, v_target_program_code
        FROM public.program
        WHERE upper(program_code) = 'BSIT'
        LIMIT 1;
    END IF;

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
                    || '__' || coalesce(p.program_code, c.program_id::text, 'unknown')
            ) AS b_id,
            coalesce(c.semester, '1st Semester')::text AS b_semester,
            coalesce(sc.school_year, '')::text AS b_school_year,
            coalesce(p.program_code, '')::text AS b_program,
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
               OR trim(lower(coalesce(p.program_code, ''))) = trim(lower(p_program)))
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

CREATE OR REPLACE FUNCTION public.restore_database_json(
    payload jsonb,
    clear_existing boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    result jsonb := '{}'::jsonb;
    inserted_count integer := 0;
    total_restored integer := 0;
    t_data jsonb;
    has_valid_table boolean := false;
    chk_table text;
    valid_tables text[] := ARRAY[
        'program', 'program_department', 'users', 'academic_ranking',
        'room', 'working_hours', 'timeslot', 'course', 'professor_load', 'prof_course',
        'schedule', 'irregular_students', 'irregular_student_schedule',
        'delete_requests', 'activity_log'
    ];
BEGIN
    -- 1. Validate payload
    IF payload IS NULL THEN
        RAISE EXCEPTION 'Payload cannot be null';
    END IF;

    IF payload ? 'data' AND jsonb_typeof(payload->'data') = 'object' THEN
        t_data := payload->'data';
    ELSIF payload ? 'tables' AND jsonb_typeof(payload->'tables') = 'object' THEN
        t_data := payload->'tables';
    ELSIF jsonb_typeof(payload) = 'object' THEN
        t_data := payload;
    ELSE
        RAISE EXCEPTION 'Invalid backup payload: root must be a JSON object';
    END IF;

    IF NOT (t_data ? 'working_hours') AND (t_data ? 'timeslot') THEN
        t_data := t_data || jsonb_build_object('working_hours', t_data->'timeslot');
    END IF;

    IF NOT (t_data ? 'professor_load') AND (t_data ? 'prof_course') THEN
        t_data := t_data || jsonb_build_object('professor_load', t_data->'prof_course');
    END IF;

    FOREACH chk_table IN ARRAY valid_tables LOOP
        IF t_data ? chk_table AND jsonb_typeof(t_data->chk_table) = 'array' THEN
            has_valid_table := true;
            EXIT;
        END IF;
    END LOOP;

    IF NOT has_valid_table THEN
        RAISE EXCEPTION 'Invalid backup payload: no recognized tables found. Aborting restore to prevent data loss.';
    END IF;

    -- 2. CLEAR EXISTING DATA (Child -> Parent Order)
    IF clear_existing THEN
        IF t_data ? 'activity_log' AND to_regclass('public.activity_log') IS NOT NULL THEN
            DELETE FROM public.activity_log;
        END IF;
        IF t_data ? 'delete_requests' AND to_regclass('public.delete_requests') IS NOT NULL THEN
            DELETE FROM public.delete_requests;
        END IF;
        IF t_data ? 'irregular_student_schedule' AND to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
            DELETE FROM public.irregular_student_schedule;
        END IF;
        IF t_data ? 'irregular_students' AND to_regclass('public.irregular_students') IS NOT NULL THEN
            DELETE FROM public.irregular_students;
        END IF;
        IF t_data ? 'schedule' AND to_regclass('public.schedule') IS NOT NULL THEN
            DELETE FROM public.schedule;
        END IF;
        IF (t_data ? 'professor_load' OR t_data ? 'prof_course') AND to_regclass('public.professor_load') IS NOT NULL THEN
            DELETE FROM public.professor_load;
        END IF;
        IF t_data ? 'course' AND to_regclass('public.course') IS NOT NULL THEN
            DELETE FROM public.course;
        END IF;
        IF (t_data ? 'working_hours' OR t_data ? 'timeslot') THEN
            IF to_regclass('public.working_hours') IS NOT NULL THEN
                DELETE FROM public.working_hours;
            ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
                DELETE FROM public.timeslot;
            END IF;
        END IF;
        IF t_data ? 'room' AND to_regclass('public.room') IS NOT NULL THEN
            DELETE FROM public.room;
        END IF;
        IF t_data ? 'academic_ranking' AND to_regclass('public.academic_ranking') IS NOT NULL THEN
            DELETE FROM public.academic_ranking;
        END IF;
        IF t_data ? 'users' AND to_regclass('public.users') IS NOT NULL THEN
            DELETE FROM public.users;
        END IF;
        IF t_data ? 'program' AND to_regclass('public.program') IS NOT NULL THEN
            DELETE FROM public.program;
        END IF;
        IF t_data ? 'program_department' AND to_regclass('public.program_department') IS NOT NULL THEN
            DELETE FROM public.program_department;
        END IF;
    END IF;

    -- 3. INSERT DATA (Parent -> Child Order)

    -- 3.1 program_department
    IF t_data ? 'program_department' AND jsonb_array_length(t_data->'program_department') > 0 AND to_regclass('public.program_department') IS NOT NULL THEN
        INSERT INTO public.program_department (program_code, department_name)
        SELECT
            (x->>'program_code')::varchar,
            (x->>'department_name')::varchar
        FROM jsonb_array_elements(t_data->'program_department') AS x
        ON CONFLICT (program_code) DO UPDATE
        SET department_name = EXCLUDED.department_name;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('program_department', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.2 program
    IF t_data ? 'program' AND jsonb_array_length(t_data->'program') > 0 AND to_regclass('public.program') IS NOT NULL THEN
        INSERT INTO public.program (id, program_code)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            x->>'program_code'
        FROM jsonb_array_elements(t_data->'program') AS x
        ON CONFLICT (id) DO UPDATE
        SET program_code = EXCLUDED.program_code;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('program', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.3 users
    IF t_data ? 'users' AND jsonb_array_length(t_data->'users') > 0 AND to_regclass('public.users') IS NOT NULL THEN
        INSERT INTO public.users (
            id, username, role, first_name, last_name, email, profile_picture, program_id
        )
        SELECT
            (x->>'id')::uuid,
            x->>'username',
            x->>'role',
            x->>'first_name',
            x->>'last_name',
            x->>'email',
            x->>'profile_picture',
            NULLIF(x->>'program_id', '')::integer
        FROM jsonb_array_elements(t_data->'users') AS x
        ON CONFLICT (id) DO UPDATE
        SET username = EXCLUDED.username,
            role = EXCLUDED.role,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            email = EXCLUDED.email,
            profile_picture = EXCLUDED.profile_picture,
            program_id = EXCLUDED.program_id;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('users', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.4 academic_ranking
    IF t_data ? 'academic_ranking' AND jsonb_array_length(t_data->'academic_ranking') > 0 AND to_regclass('public.academic_ranking') IS NOT NULL THEN
        INSERT INTO public.academic_ranking (academic_ranking_id, name, units_required, program)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'academic_ranking_id')::bigint,
            x->>'name',
            COALESCE(NULLIF(x->>'units_required', '')::numeric, 0),
            x->>'program'
        FROM jsonb_array_elements(t_data->'academic_ranking') AS x
        ON CONFLICT (academic_ranking_id) DO UPDATE
        SET name = EXCLUDED.name,
            units_required = EXCLUDED.units_required,
            program = EXCLUDED.program;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('academic_ranking', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.5 room
    IF t_data ? 'room' AND jsonb_array_length(t_data->'room') > 0 AND to_regclass('public.room') IS NOT NULL THEN
        INSERT INTO public.room (room_id, room_name, room_type)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'room_id')::integer,
            (x->>'room_name')::varchar,
            (x->>'room_type')::varchar
        FROM jsonb_array_elements(t_data->'room') AS x
        ON CONFLICT (room_id) DO UPDATE
        SET room_name = EXCLUDED.room_name,
            room_type = EXCLUDED.room_type;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('room', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.6 working_hours / timeslot
    IF (t_data ? 'working_hours' OR t_data ? 'timeslot') THEN
        IF to_regclass('public.working_hours') IS NOT NULL THEN
            INSERT INTO public.working_hours (
                working_hours_id, day, start_time, end_time, lunch_time
            )
            OVERRIDING SYSTEM VALUE
            SELECT
                COALESCE((x->>'working_hours_id')::integer, (x->>'timeslot_id')::integer),
                x->>'day',
                (x->>'start_time')::time,
                (x->>'end_time')::time,
                NULLIF(x->>'lunch_time', '')::time
            FROM jsonb_array_elements(COALESCE(t_data->'working_hours', t_data->'timeslot')) AS x
            ON CONFLICT (working_hours_id) DO UPDATE
            SET day = EXCLUDED.day,
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                lunch_time = EXCLUDED.lunch_time;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;
            result := result || jsonb_build_object('working_hours', inserted_count);
            total_restored := total_restored + inserted_count;
        ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
            INSERT INTO public.timeslot (
                timeslot_id, day, start_time, end_time, lunch_time
            )
            OVERRIDING SYSTEM VALUE
            SELECT
                (x->>'timeslot_id')::integer,
                x->>'day',
                (x->>'start_time')::time,
                (x->>'end_time')::time,
                NULLIF(x->>'lunch_time', '')::time
            FROM jsonb_array_elements(COALESCE(t_data->'working_hours', t_data->'timeslot')) AS x
            ON CONFLICT (timeslot_id) DO UPDATE
            SET day = EXCLUDED.day,
                start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                lunch_time = EXCLUDED.lunch_time;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;
            result := result || jsonb_build_object('timeslot', inserted_count);
            total_restored := total_restored + inserted_count;
        END IF;
    END IF;

    -- 3.7 course (supports both course_code and distinct course_name)
    IF t_data ? 'course' AND jsonb_array_length(t_data->'course') > 0 AND to_regclass('public.course') IS NOT NULL THEN
        INSERT INTO public.course (
            course_id, course_code, course_name, lecture_hours, lab_hours, ilp_hours, units, year_level, semester, specialization, program_id
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'course_id')::integer,
            COALESCE(NULLIF(x->>'course_code', ''), x->>'course_name')::varchar,
            NULLIF(x->>'course_name', '')::text,
            COALESCE(NULLIF(x->>'lecture_hours', '')::integer, 0),
            COALESCE(NULLIF(x->>'lab_hours', '')::integer, 0),
            COALESCE(NULLIF(x->>'ilp_hours', '')::integer, 0),
            COALESCE(NULLIF(x->>'units', '')::numeric, 0),
            NULLIF(x->>'year_level', '')::integer,
            x->>'semester',
            COALESCE(NULLIF(x->>'specialization', ''), x->>'major'),
            COALESCE(NULLIF(x->>'program_id', '')::integer, (SELECT id FROM public.program ORDER BY id LIMIT 1))
        FROM jsonb_array_elements(t_data->'course') AS x
        ON CONFLICT (course_id) DO UPDATE
        SET course_code = EXCLUDED.course_code,
            course_name = EXCLUDED.course_name,
            lecture_hours = EXCLUDED.lecture_hours,
            lab_hours = EXCLUDED.lab_hours,
            ilp_hours = EXCLUDED.ilp_hours,
            units = EXCLUDED.units,
            year_level = EXCLUDED.year_level,
            semester = EXCLUDED.semester,
            specialization = EXCLUDED.specialization,
            program_id = EXCLUDED.program_id;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('course', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.8 professor_load
    IF (t_data ? 'professor_load' AND jsonb_array_length(t_data->'professor_load') > 0)
       OR (t_data ? 'prof_course' AND jsonb_array_length(t_data->'prof_course') > 0) THEN

        WITH legacy_profs AS (
            SELECT
                (p->>'prof_id')::integer AS p_id,
                trim(regexp_replace(replace(trim(p->>'first_name') || ' ' || trim(p->>'last_name'), chr(160), ' '), '\s+', ' ', 'g')) AS p_name
            FROM jsonb_array_elements(COALESCE(t_data->'professor', '[]'::jsonb)) AS p
            WHERE NULLIF(p->>'prof_id', '') IS NOT NULL
        )
        INSERT INTO public.professor_load (id, course_id, sections, professor_name)
        OVERRIDING SYSTEM VALUE
        SELECT
            COALESCE((x->>'id')::integer, (x->>'prof_course_id')::integer, (x->>'professor_load_id')::integer),
            (x->>'course_id')::integer,
            COALESCE(NULLIF(x->>'sections', '')::integer, 1),
            COALESCE(
                NULLIF(trim(regexp_replace(replace(x->>'professor_name', chr(160), ' '), '\s+', ' ', 'g')), ''),
                lp.p_name,
                'Unknown Professor'
            )
        FROM jsonb_array_elements(COALESCE(t_data->'professor_load', t_data->'prof_course')) AS x
        LEFT JOIN legacy_profs lp ON lp.p_id = NULLIF(x->>'prof_id', '')::integer
        ON CONFLICT (id) DO UPDATE
        SET course_id = EXCLUDED.course_id,
            sections = EXCLUDED.sections,
            professor_name = EXCLUDED.professor_name;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('professor_load', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.9 schedule
    IF t_data ? 'schedule' AND jsonb_array_length(t_data->'schedule') > 0 AND to_regclass('public.schedule') IS NOT NULL THEN
        INSERT INTO public.schedule (
            schedule_id, professor_load_id, room_id, day,
            class_start, class_end, session_type, section,
            archive, archive_batch_id, archived_at, prepared_by_user_id
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'schedule_id')::integer,
            COALESCE((x->>'professor_load_id')::integer, (x->>'prof_course_id')::integer),
            NULLIF(x->>'room_id', '')::integer,
            (x->>'day')::varchar,
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            x->>'session_type',
            x->>'section',
            COALESCE((x->>'archive')::boolean, false),
            NULLIF(x->>'archive_batch_id', '')::uuid,
            NULLIF(x->>'archived_at', '')::timestamptz,
            NULLIF(x->>'prepared_by_user_id', '')::uuid
        FROM jsonb_array_elements(t_data->'schedule') AS x
        ON CONFLICT (schedule_id) DO UPDATE
        SET professor_load_id = EXCLUDED.professor_load_id,
            room_id = EXCLUDED.room_id,
            day = EXCLUDED.day,
            class_start = EXCLUDED.class_start,
            class_end = EXCLUDED.class_end,
            session_type = EXCLUDED.session_type,
            section = EXCLUDED.section,
            archive = EXCLUDED.archive,
            archive_batch_id = EXCLUDED.archive_batch_id,
            archived_at = EXCLUDED.archived_at,
            prepared_by_user_id = EXCLUDED.prepared_by_user_id;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.10 irregular_students
    IF t_data ? 'irregular_students' AND jsonb_array_length(t_data->'irregular_students') > 0 AND to_regclass('public.irregular_students') IS NOT NULL THEN
        INSERT INTO public.irregular_students (student_id, student_id_number, first_name, last_name, program, year_level, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'student_id')::integer,
            (x->>'student_id_number')::varchar,
            (x->>'first_name')::varchar,
            (x->>'last_name')::varchar,
            (x->>'program')::varchar,
            (x->>'year_level')::varchar,
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'irregular_students') AS x
        ON CONFLICT (student_id) DO UPDATE
        SET student_id_number = EXCLUDED.student_id_number,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            program = EXCLUDED.program,
            year_level = EXCLUDED.year_level;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_students', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.11 irregular_student_schedule
    IF t_data ? 'irregular_student_schedule' AND jsonb_array_length(t_data->'irregular_student_schedule') > 0 AND to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
        INSERT INTO public.irregular_student_schedule (id, student_id, schedule_id, course_id, section, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'student_id')::integer,
            (x->>'schedule_id')::integer,
            (x->>'course_id')::integer,
            (x->>'section')::varchar,
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'irregular_student_schedule') AS x
        ON CONFLICT (id) DO UPDATE
        SET student_id = EXCLUDED.student_id,
            schedule_id = EXCLUDED.schedule_id,
            course_id = EXCLUDED.course_id,
            section = EXCLUDED.section;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_student_schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.12 delete_requests
    IF t_data ? 'delete_requests' AND jsonb_array_length(t_data->'delete_requests') > 0 AND to_regclass('public.delete_requests') IS NOT NULL THEN
        INSERT INTO public.delete_requests (
            id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, is_read, created_at, updated_at
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            (x->>'username')::varchar,
            (x->>'first_name')::varchar,
            (x->>'last_name')::varchar,
            (x->>'item_type')::varchar,
            (x->>'item_id')::varchar,
            (x->>'item_details')::varchar,
            COALESCE(x->>'status', 'pending')::varchar,
            COALESCE((x->>'is_read')::boolean, false),
            COALESCE((x->>'created_at')::timestamptz, now()),
            COALESCE((x->>'updated_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'delete_requests') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            item_type = EXCLUDED.item_type,
            item_id = EXCLUDED.item_id,
            item_details = EXCLUDED.item_details,
            status = EXCLUDED.status,
            is_read = EXCLUDED.is_read,
            updated_at = EXCLUDED.updated_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('delete_requests', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.13 activity_log
    IF t_data ? 'activity_log' AND jsonb_array_length(t_data->'activity_log') > 0 AND to_regclass('public.activity_log') IS NOT NULL THEN
        INSERT INTO public.activity_log (id, user_id, username, action, target_type, target_detail, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            x->>'username',
            x->>'action',
            x->>'target_type',
            x->>'target_detail',
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'activity_log') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            action = EXCLUDED.action,
            target_type = EXCLUDED.target_type,
            target_detail = EXCLUDED.target_detail;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('activity_log', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 4. SYNCHRONIZE IDENTITY SEQUENCES
    IF to_regclass('public.program') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.program', 'id'), COALESCE(MAX(id), 1)) FROM public.program;
    END IF;
    IF to_regclass('public.academic_ranking') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.academic_ranking', 'academic_ranking_id'), COALESCE(MAX(academic_ranking_id), 1)) FROM public.academic_ranking;
    END IF;
    IF to_regclass('public.room') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.room', 'room_id'), COALESCE(MAX(room_id), 1)) FROM public.room;
    END IF;
    IF to_regclass('public.working_hours') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.working_hours', 'working_hours_id'), COALESCE(MAX(working_hours_id), 1)) FROM public.working_hours;
    ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.timeslot', 'timeslot_id'), COALESCE(MAX(timeslot_id), 1)) FROM public.timeslot;
    END IF;
    IF to_regclass('public.course') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.course', 'course_id'), COALESCE(MAX(course_id), 1)) FROM public.course;
    END IF;
    IF to_regclass('public.professor_load') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.professor_load', 'id'), COALESCE(MAX(id), 1)) FROM public.professor_load;
    END IF;
    IF to_regclass('public.schedule') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;
    END IF;
    IF to_regclass('public.irregular_students') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.irregular_students', 'student_id'), COALESCE(MAX(student_id), 1)) FROM public.irregular_students;
    END IF;
    IF to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.irregular_student_schedule', 'id'), COALESCE(MAX(id), 1)) FROM public.irregular_student_schedule;
    END IF;
    IF to_regclass('public.delete_requests') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.delete_requests', 'id'), COALESCE(MAX(id), 1)) FROM public.delete_requests;
    END IF;
    IF to_regclass('public.activity_log') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.activity_log', 'id'), COALESCE(MAX(id), 1)) FROM public.activity_log;
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'total_restored', total_restored,
        'details', result
    );
END;
$$;

NOTIFY pgrst, 'reload schema';

COMMIT;


-- ==============================================================================
-- ROLLBACK (run only if you need to revert this migration)
-- ==============================================================================
-- BEGIN;
-- ALTER TABLE public.program RENAME COLUMN program_code TO program_name;
-- -- Then restore the pre-rename function bodies by re-running:
-- --   supabase/migrations/20261016000000_chair_role_consolidation.sql
-- --   supabase/migrations/20261015000000_fix_archive_batches_rpc_bool_and.sql
-- --   supabase/migrations/20261011130000_add_course_name_to_course_table.sql
-- --   supabase/migrations/20261018000000_users_drop_updated_at_and_rename_middle_initial.sql
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- ==============================================================================
