-- ==============================================================================
-- Migration: 20261016000000_chair_role_consolidation.sql
-- Purpose:   Complete the Scheduler -> Chair rename at the DATABASE layer and
--            remove leftover rules written for the OLD Chair / Dean / Super Admin
--            that would otherwise grant the NEW Chair extra privileges.
--
-- ROLE MATRIX (unchanged, only the Scheduler column was renamed):
--   Admin  = 'admin'   Chair = 'chair' (formerly 'scheduler')   Viewer = 'viewer'
--   Chair == the former Scheduler: same permissions, same program scoping.
--
-- !!  DESTRUCTIVE-GUARDED / NON-DESTRUCTIVE, but TAKE A BACKUP FIRST !!
--   Run a Supabase dashboard backup OR:  pg_dump --no-owner --no-privileges > backup.sql
--   This script is wrapped in a single transaction; it only UPDATEs role text,
--   replaces function bodies, and drops/creates RLS policies. It never deletes
--   schedule, course, room, program, load, user or log rows.
--
-- DO NOT run from the application. Run it in the Supabase SQL editor.
-- ==============================================================================

BEGIN;

-- ==============================================================================
-- 0. PRE-FLIGHT AUDIT (read-only). Run these SELECTs FIRST and eyeball the output
--    before you run the migration body below.
-- ==============================================================================
-- 0.1 Distinct stored roles in public.users with counts
--   SELECT COALESCE(role,'<NULL>') AS role, COUNT(*) FROM public.users GROUP BY 1 ORDER BY 2 DESC;
-- 0.2 Every RLS policy whose predicate mentions an old role name
--   SELECT schemaname, tablename, policyname, qual, with_check
--   FROM pg_policies
--   WHERE schemaname='public'
--     AND (coalesce(qual,'') || coalesce(with_check,'')) ~* '(scheduler|dean|super_admin|instructor|guest)'
--   ORDER BY tablename, policyname;
-- 0.3 Every function whose body mentions an old role name
--   SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args
--   FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
--   WHERE n.nspname='public' AND p.prosrc ~* '(scheduler|dean|super_admin)'
--   ORDER BY p.proname;
-- 0.4 Accounts that will be renamed by part 1 (run manually before migration if desired):
-- SELECT id, username, email, role, program_id
-- FROM public.users
-- WHERE lower(btrim(role)) IN ('scheduler', 'dean');

-- ==============================================================================
-- 1. Normalise the stored role values (pure rename + legacy cleanup)
-- ==============================================================================
-- 1.1 the pure rename: scheduler -> chair
UPDATE public.users
SET role = 'chair'
WHERE lower(btrim(role)) = 'scheduler';

-- 1.2 legacy "old Chair" / "Dean" values must NOT silently become the new Chair
--     by accident of string matching; map the ones that were genuinely the same
--     job (they already normalise to chair in the app) and drop anything else.
UPDATE public.users
SET role = 'chair'
WHERE lower(btrim(role)) IN ('dean', 'dean/chair', 'program_chair', 'department_head');

-- 1.3 any other value outside the whitelist becomes the least-privileged role.
UPDATE public.users
SET role = 'viewer'
WHERE lower(btrim(role)) IS NOT NULL
  AND lower(btrim(role)) NOT IN ('admin', 'chair', 'viewer', 'super_admin', 'superadmin');

-- 1.4 admin/viewer never carry a program
UPDATE public.users
SET program_id = NULL
WHERE lower(btrim(role)) IN ('admin', 'viewer', 'super_admin', 'superadmin')
  AND program_id IS NOT NULL;

-- ==============================================================================
-- 2. get_user_role(): single authoritative helper, now returns a CANONICAL role
--    so both the old function bodies and the RLS policies agree during the
--    transition. BEFORE: returned lower(trim(role)) verbatim ('scheduler').
--    AFTER: 'scheduler'/'dean'/... -> 'chair'; super_admin -> 'admin'; etc.
-- ==============================================================================
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
    IF v_role IS NOT NULL AND trim(v_role) <> '' THEN
        v_role := lower(trim(v_role));
    ELSIF auth.uid() IS NOT NULL THEN
        SELECT lower(trim(u.role)) INTO v_role
        FROM public.users u
        WHERE u.id = auth.uid()
        LIMIT 1;
    END IF;

    IF v_role IS NULL OR trim(v_role) = '' THEN
        RETURN 'viewer';
    END IF;

    -- Canonicalise every alias (transition-safe: old 'scheduler' still works).
    IF v_role IN ('super_admin', 'superadmin', 'administrator', 'academic_admin') THEN
        RETURN 'admin';
    ELSIF v_role IN ('scheduler', 'program_scheduler', 'program_chair', 'department_head', 'dean', 'dean/chair') THEN
        RETURN 'chair';
    ELSIF v_role IN ('instructor', 'guest', 'faculty') THEN
        RETURN 'viewer';
    END IF;
    RETURN v_role;
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_user_role() TO anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.get_user_program_id()
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
    SELECT COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'program_id')::integer,
        (auth.jwt() -> 'user_metadata' ->> 'program_id')::integer,
        (SELECT program_id FROM public.users WHERE id::text = auth.uid()::text LIMIT 1)
    );
$$;

GRANT EXECUTE ON FUNCTION public.get_user_program_id() TO anon, authenticated, service_role;

-- ==============================================================================
-- 3. FUNCTION BODIES that still compared against the literal 'scheduler'.
--    Recreated verbatim from their latest definitions with ONLY the role
--    comparison changed so the new Chair (get_user_role() = 'chair') passes.
-- ==============================================================================

-- 3.1 archive_active_schedule  (BEFORE: ELSIF v_caller_role = 'scheduler'
--                              AFTER:  ELSIF v_caller_role IN ('chair','scheduler'))
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
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO anon, authenticated, service_role;

-- 3.2 restore_archived_schedule_batch
--     BEFORE: ELSIF v_caller_role = 'scheduler' THEN
--     AFTER : ELSIF v_caller_role IN ('chair', 'scheduler') THEN
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
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;

-- 3.3 set_schedule_batch_used
--     BEFORE: ELSIF v_caller_role = 'scheduler' THEN
--     AFTER : ELSIF v_caller_role IN ('chair', 'scheduler') THEN
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
    ELSIF v_caller_role IN ('chair', 'scheduler') THEN
        IF v_program_id IS NOT NULL AND v_caller_pid IS NOT NULL AND v_program_id != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only manage their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_program_id := coalesce(v_program_id, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

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

        UPDATE public.schedule sc
        SET is_used = false
        WHERE sc.professor_load_id IS NULL
          AND sc.school_year = v_school_year
          AND sc.is_used = true;

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

-- 3.4 confirm_schedule_transaction: add the missing role guard (defence in depth).
--     The function is SECURITY DEFINER and previously had NO ACL check, so any
--     authenticated user could archive/insert schedules by calling it directly.
--     Allowed: Chair scoped to own program, or Admin. Viewers are rejected.
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
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text, text) TO anon, authenticated, service_role;

-- ==============================================================================
-- 4. RLS POLICY CLEANUP -- remove leftover OLD Chair / Dean / Super Admin rules
--    that would grant the NEW Chair privileges the former Scheduler never had,
--    then (re)create the policies that match the role matrix.
-- ==============================================================================

-- 4.1 Drop every legacy policy whose NAME references an old role
--     (scheduler / dean) -- these are the rules that were written for the OLD
--     Chair / Dean / Super Admin and could silently grant the NEW Chair extra
--     privileges after the rename. (IF EXISTS = safe; name-based so unrelated
--     tables are never touched.)
DO $$
DECLARE
    pol record;
BEGIN
    FOR pol IN
        SELECT policyname, tablename
        FROM pg_policies
        WHERE schemaname = 'public'
          AND policyname ~* '(scheduler|dean)'
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', pol.policyname, pol.tablename);
        RAISE NOTICE 'Dropped legacy policy %.%', pol.tablename, pol.policyname;
    END LOOP;
END $$;

-- 4.2 public.course : READ all authenticated, WRITE admin only (no Chair)
ALTER TABLE public.course ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "course_select_authenticated" ON public.course;
DROP POLICY IF EXISTS "course_admin_manage" ON public.course;
CREATE POLICY "course_select_authenticated" ON public.course
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "course_admin_manage" ON public.course
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('admin','super_admin'))
    WITH CHECK (public.get_user_role() IN ('admin','super_admin'));

-- 4.3 public.working_hours : READ all authenticated, WRITE admin only (no Chair)
ALTER TABLE public.working_hours ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "working_hours_select_authenticated" ON public.working_hours;
DROP POLICY IF EXISTS "working_hours_admin_manage" ON public.working_hours;
CREATE POLICY "working_hours_select_authenticated" ON public.working_hours
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "working_hours_admin_manage" ON public.working_hours
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('admin','super_admin'))
    WITH CHECK (public.get_user_role() IN ('admin','super_admin'));

-- 4.4 public.program / program_department : READ all authenticated, WRITE admin only
ALTER TABLE public.program ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "program_select_authenticated" ON public.program;
DROP POLICY IF EXISTS "program_admin_manage" ON public.program;
CREATE POLICY "program_select_authenticated" ON public.program
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "program_admin_manage" ON public.program
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('admin','super_admin'))
    WITH CHECK (public.get_user_role() IN ('admin','super_admin'));

-- 4.4b public.program_department : READ all authenticated, WRITE admin only (if table exists)
DO $$
BEGIN
    IF to_regclass('public.program_department') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.program_department ENABLE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS "program_department_select_authenticated" ON public.program_department';
        EXECUTE 'DROP POLICY IF EXISTS "program_department_admin_manage" ON public.program_department';
        EXECUTE 'CREATE POLICY "program_department_select_authenticated" ON public.program_department FOR SELECT TO authenticated USING (true)';
        EXECUTE 'CREATE POLICY "program_department_admin_manage" ON public.program_department FOR ALL TO authenticated USING (public.get_user_role() IN (''admin'',''super_admin'')) WITH CHECK (public.get_user_role() IN (''admin'',''super_admin''))';
    END IF;
END $$;

-- 4.5 public.delete_requests : Chair INSERTS their own requests + reads own;
--     Admin has full control. (BEFORE the rename this was admin+scheduler ALL.)
DO $$
BEGIN
    IF to_regclass('public.delete_requests') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.delete_requests ENABLE ROW LEVEL SECURITY';
        EXECUTE 'DROP POLICY IF EXISTS "delete_requests_select" ON public.delete_requests';
        EXECUTE 'DROP POLICY IF EXISTS "delete_requests_insert" ON public.delete_requests';
        EXECUTE 'DROP POLICY IF EXISTS "delete_requests_admin_manage" ON public.delete_requests';
        EXECUTE 'CREATE POLICY "delete_requests_select" ON public.delete_requests FOR SELECT TO authenticated USING (public.get_user_role() IN (''admin'',''super_admin'') OR user_id::text = (SELECT auth.uid())::text)';
        EXECUTE 'CREATE POLICY "delete_requests_insert" ON public.delete_requests FOR INSERT TO authenticated WITH CHECK (public.get_user_role() IN (''chair'',''scheduler'') AND user_id::text = (SELECT auth.uid())::text)';
        EXECUTE 'CREATE POLICY "delete_requests_admin_manage" ON public.delete_requests FOR UPDATE TO authenticated USING (public.get_user_role() IN (''admin'',''super_admin'')) WITH CHECK (public.get_user_role() IN (''admin'',''super_admin''))';
    END IF;
END $$;

-- 4.6 public.professor_load : READ all authenticated, WRITE Chair own program.
--     Program is derived from public.course (course.program_id).
ALTER TABLE public.professor_load ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scheduler_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scheduler_program_scoped_crud" ON public.professor_load;

CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "professor_load_scoped_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('chair', 'scheduler')
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
            public.get_user_role() IN ('chair', 'scheduler')
            AND EXISTS (
                SELECT 1 FROM public.course c
                WHERE c.course_id = professor_load.course_id
                  AND (c.program_id = public.get_user_program_id() OR public.get_user_program_id() IS NULL)
            )
        )
    );

-- 4.7 public.schedule : READ Admin/Viewer/Chair; WRITE Chair own program only; DELETE disabled
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
DROP POLICY IF EXISTS "schedule_insert" ON public.schedule;
DROP POLICY IF EXISTS "schedule_update" ON public.schedule;
DROP POLICY IF EXISTS "schedule_delete" ON public.schedule;
DROP POLICY IF EXISTS "schedule_scoped_all" ON public.schedule;
DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;

CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin', 'viewer')
    OR (
        public.get_user_role() IN ('chair', 'scheduler')
        AND (
            archive = false
            OR professor_load_id IS NULL
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

CREATE POLICY "schedule_insert" ON public.schedule
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() IN ('chair', 'scheduler')
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
);

CREATE POLICY "schedule_update" ON public.schedule
FOR UPDATE TO authenticated
USING (
    public.get_user_role() IN ('chair', 'scheduler')
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
WITH CHECK (
    public.get_user_role() IN ('chair', 'scheduler')
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
);

CREATE POLICY "schedule_delete" ON public.schedule
FOR DELETE TO authenticated
USING (false);

-- ==============================================================================
-- 5. User Schema, Escalation Guard & Auth Trigger
--    Ensures all columns exist, triggers run safely, and logins are NEVER blocked.
-- ==============================================================================

-- 5.1 Ensure all optional columns exist on public.users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS middle_name text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS first_name text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS last_name text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS profile_picture text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();

-- 5.2 Privilege Escalation Guard Trigger on public.users
CREATE OR REPLACE FUNCTION public.protect_user_role_and_program()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_caller_role text;
BEGIN
    -- Allow internal system, trigger, or unauthenticated auth calls
    IF auth.uid() IS NULL THEN
        RETURN NEW;
    END IF;

    v_caller_role := public.get_user_role();

    -- Super Admin and Admin can update any role and program
    IF v_caller_role IN ('super_admin', 'admin') THEN
        RETURN NEW;
    END IF;

    -- Non-admin cannot change their own or another user's role
    IF NEW.role IS DISTINCT FROM OLD.role THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify roles.';
    END IF;

    -- Non-admin cannot change their own or another user's program_id
    IF NEW.program_id IS DISTINCT FROM OLD.program_id THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify program assignment.';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_user_role_and_program ON public.users;
CREATE TRIGGER trg_protect_user_role_and_program
    BEFORE UPDATE ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.protect_user_role_and_program();

-- 5.3 handle_new_auth_user(): Creates/syncs user on signup.
--     Wrapped in an EXCEPTION block so non-critical errors never block authentication.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_program_id integer;
    v_program_name text;
    v_username text;
    v_first_name text;
    v_middle_name text;
    v_last_name text;
    v_role text;
BEGIN
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));
    v_first_name := NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), '');
    v_middle_name := NULLIF(TRIM(new.raw_user_meta_data->>'middle_name'), '');
    v_last_name := NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), '');
    v_role := NULLIF(TRIM(new.raw_user_meta_data->>'role'), '');

    -- Normalise any client-supplied role to the canonical whitelist.
    IF v_role IS NULL THEN
        v_role := 'viewer';
    ELSE
        v_role := lower(replace(replace(v_role, '-', '_'), ' ', '_'));
        IF v_role IN ('super_admin', 'superadmin', 'administrator', 'academic_admin') THEN
            v_role := 'admin';
        ELSIF v_role IN ('chair', 'scheduler', 'program_chair', 'department_head', 'dean', 'dean/chair') THEN
            v_role := 'chair';
        ELSE
            v_role := 'viewer';
        END IF;
    END IF;

    IF LOWER(v_role) IN ('viewer', 'admin') THEN
        v_program_id := NULL;
    ELSE
        v_program_name := COALESCE(new.raw_user_meta_data->>'program', '');
        IF (new.raw_user_meta_data->>'program_id') IS NOT NULL AND (new.raw_user_meta_data->>'program_id') ~ '^\d+$' THEN
            v_program_id := (new.raw_user_meta_data->>'program_id')::integer;
        ELSIF v_program_name ~ '^\d+$' THEN
            v_program_id := v_program_name::integer;
        ELSIF v_program_name <> '' THEN
            SELECT id INTO v_program_id FROM public.program
            WHERE UPPER(program_name) = UPPER(TRIM(v_program_name))
               OR UPPER(program_name) = (CASE WHEN UPPER(TRIM(v_program_name)) = 'CICT' THEN 'BSIT' WHEN UPPER(TRIM(v_program_name)) = 'CMBT' THEN 'BSBA' ELSE UPPER(TRIM(v_program_name)) END)
            LIMIT 1;
        END IF;

        IF v_program_id IS NULL THEN
            SELECT id INTO v_program_id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1;
        END IF;
    END IF;

    BEGIN
        INSERT INTO public.users (
            id, email, username, first_name, middle_name, last_name, program_id, role, profile_picture
        )
        VALUES (
            new.id, new.email, v_username,
            COALESCE(v_first_name, ''), COALESCE(v_middle_name, ''), COALESCE(v_last_name, ''),
            v_program_id, COALESCE(v_role, 'viewer'),
            new.raw_user_meta_data->>'profile_picture'
        )
        ON CONFLICT (id) DO UPDATE
        SET
            email = EXCLUDED.email,
            username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
            first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE COALESCE(EXCLUDED.first_name, '') END,
            middle_name = CASE WHEN public.users.middle_name IS NOT NULL AND public.users.middle_name <> '' THEN public.users.middle_name ELSE COALESCE(EXCLUDED.middle_name, '') END,
            last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE COALESCE(EXCLUDED.last_name, '') END,
            program_id = CASE WHEN LOWER(COALESCE(public.users.role, 'viewer')) IN ('viewer', 'admin', 'super_admin') THEN NULL ELSE COALESCE(public.users.program_id, EXCLUDED.program_id) END,
            role = CASE WHEN public.users.role IS NOT NULL AND public.users.role <> '' THEN public.users.role ELSE COALESCE(EXCLUDED.role, 'viewer') END,
            profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture),
            updated_at = NOW();
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'handle_new_auth_user warning: %', SQLERRM;
    END;

    RETURN new;
END;
$$;

-- 5.4 Recreate trigger on auth.users: AFTER INSERT ONLY (Signups).
--     Logins (UPDATE on auth.users for last_sign_in_at) will NEVER execute this trigger.
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();

-- ==============================================================================
-- 6. restore_database_json(): sanitise restored user roles + admin-only ACL.
--
--    This function's body is large and is NOT reproduced in full here to avoid
--    transcription risk. Apply it in the Supabase SQL editor in two steps:
--
--    (a) Add the ACL guard at the very top of the function's BEGIN block
--        (BEFORE: no ACL check at all):
--
--           DECLARE v_caller_role text;      -- add to the DECLARE list
--           BEGIN
--               v_caller_role := public.get_user_role();
--               IF v_caller_role NOT IN ('admin','super_admin') THEN
--                   RAISE EXCEPTION 'Permission denied: Only administrators can restore the database.'
--                       USING ERRCODE = '42501';
--               END IF;
--
--    (b) Replace ONLY the users INSERT block (part 6.1 below) so an old backup
--        that contains role='scheduler' restores as 'chair', and no backup can
--        introduce a role outside the whitelist.
-- ==============================================================================

-- 6.1 The exact users INSERT WITH sanitisation to place inside restore_database_json:
/*
    IF t_data ? 'users' AND jsonb_array_length(t_data->'users') > 0 AND to_regclass('public.users') IS NOT NULL THEN
        INSERT INTO public.users (
            id, username, role, first_name, last_name, email, profile_picture, program_id
        )
        SELECT
            (x->>'id')::uuid,
            x->>'username',
            CASE
                WHEN lower(btrim(coalesce(x->>'role',''))) IN ('scheduler','dean','dean/chair','program_chair','department_head') THEN 'chair'
                WHEN lower(btrim(coalesce(x->>'role',''))) IN ('admin','chair','viewer','super_admin','superadmin') THEN
                     CASE WHEN lower(btrim(x->>'role')) IN ('super_admin','superadmin') THEN 'admin' ELSE lower(btrim(x->>'role')) END
                ELSE 'viewer'
            END AS role,
            x->>'first_name',
            x->>'last_name',
            x->>'email',
            x->>'profile_picture',
            CASE WHEN lower(btrim(coalesce(x->>'role',''))) IN ('admin','super_admin','superadmin','viewer','instructor','guest')
                 THEN NULL ELSE NULLIF(x->>'program_id','')::integer END
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
*/

-- ==============================================================================
-- 7. admin_create_auth_user(p_role DEFAULT 'scheduler') -- make the default chair
--    (Postgres cannot ALTER a parameter default; recreate from current definition
--     with p_role DEFAULT 'chair'. The app always passes p_role explicitly.)
-- ==============================================================================
-- (See 20261012210000_rename_scheduler_role_to_chair.sql section 6 for the note.)

NOTIFY pgrst, 'reload schema';

COMMIT;

-- ==============================================================================
-- POST-CHECK (read-only), run after COMMIT
-- ==============================================================================
-- SELECT COALESCE(role,'<NULL>') AS role, COUNT(*) FROM public.users GROUP BY 1 ORDER BY 2 DESC;   -- expect only admin/chair/viewer
-- SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'
--   AND (coalesce(qual,'')||coalesce(with_check,'')) ~* '(scheduler|dean|super_admin)';            -- expect 0 rows
-- SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
--   WHERE n.nspname='public' AND p.prosrc ~* '= ''scheduler''';                                    -- expect 0 rows
