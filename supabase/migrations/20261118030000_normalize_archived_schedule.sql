-- ==============================================================================
-- Migration: 20261118030000_normalize_archived_schedule.sql
-- Purpose:
--   Normalize schedule archiving into its own entity `public.archived_schedule`
--   and remove the legacy `schedule.archive` (boolean) and `schedule.archived_at`
--   columns.
--
-- Target Design:
--   - public.archived_schedule(archive_batch_id uuid PK, archived_at timestamptz, archived_by varchar)
--   - public.schedule.archive_batch_id uuid FK -> public.archived_schedule(archive_batch_id)
--   - Active schedule   : schedule.archive_batch_id IS NULL
--   - Archived schedule : schedule.archive_batch_id IS NOT NULL
--   - Archive booleans on course, program, room, specializations, working_hours stay unchanged.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Create public.archived_schedule + RLS policies
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.archived_schedule (
    archive_batch_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    archived_at      timestamptz NOT NULL DEFAULT now(),
    archived_by      varchar(255)
);

ALTER TABLE public.archived_schedule ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.archived_schedule FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.archived_schedule TO authenticated, service_role;

DROP POLICY IF EXISTS "archived_schedule_select" ON public.archived_schedule;
DROP POLICY IF EXISTS "archived_schedule_insert" ON public.archived_schedule;
DROP POLICY IF EXISTS "archived_schedule_update" ON public.archived_schedule;
DROP POLICY IF EXISTS "archived_schedule_delete" ON public.archived_schedule;

CREATE POLICY "archived_schedule_select" ON public.archived_schedule
    FOR SELECT TO authenticated
    USING ((SELECT public.get_user_role()) IN ('admin', 'chair'));

CREATE POLICY "archived_schedule_insert" ON public.archived_schedule
    FOR INSERT TO authenticated
    WITH CHECK ((SELECT public.get_user_role()) IN ('admin', 'chair'));

CREATE POLICY "archived_schedule_update" ON public.archived_schedule
    FOR UPDATE TO authenticated
    USING ((SELECT public.get_user_role()) IN ('admin', 'chair'))
    WITH CHECK ((SELECT public.get_user_role()) IN ('admin', 'chair'));

CREATE POLICY "archived_schedule_delete" ON public.archived_schedule
    FOR DELETE TO authenticated
    USING ((SELECT public.get_user_role()) IN ('admin', 'chair'));

-- ------------------------------------------------------------------------------
-- 2. Ensure public.schedule.archive_batch_id exists and is type uuid
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    v_udt_name text;
BEGIN
    SELECT c.udt_name
    INTO v_udt_name
    FROM information_schema.columns c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'schedule'
      AND c.column_name = 'archive_batch_id';

    IF v_udt_name IS NULL THEN
        ALTER TABLE public.schedule ADD COLUMN archive_batch_id uuid;
    ELSIF v_udt_name <> 'uuid' THEN
        -- Clear any non-UUID garbage strings before casting to uuid
        EXECUTE $q$
            UPDATE public.schedule
            SET archive_batch_id = NULL
            WHERE archive_batch_id IS NOT NULL
              AND btrim(archive_batch_id::text) !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        $q$;
        ALTER TABLE public.schedule
            ALTER COLUMN archive_batch_id TYPE uuid
            USING NULLIF(btrim(archive_batch_id::text), '')::uuid;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 3. Migrate existing schedule data into public.archived_schedule
--    - Active rows (archive = false, including previously restored rows) -> archive_batch_id = NULL
--    - Archived rows with an existing archive_batch_id -> 1 archived_schedule row per batch
--    - Archived rows with NULL archive_batch_id -> grouped into 1 legacy batch
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    v_has_archive      boolean;
    v_has_archived_at  boolean;
    v_has_prep_name    boolean;
    v_archived_at_expr text;
    v_archived_by_expr text;
    v_legacy_count     bigint;
    v_legacy_batch_id  uuid;
    v_legacy_at        timestamptz;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'archive'
    ) INTO v_has_archive;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'archived_at'
    ) INTO v_has_archived_at;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'prepared_by_name'
    ) INTO v_has_prep_name;

    v_archived_at_expr := CASE
        WHEN v_has_archived_at THEN 'COALESCE(MAX(archived_at), now())'
        ELSE 'now()'
    END;

    v_archived_by_expr := CASE
        WHEN v_has_prep_name THEN 'COALESCE(MAX(NULLIF(btrim(prepared_by_name), '''')), ''Legacy Archive'')'
        ELSE '''Legacy Archive'''
    END;

    IF v_has_archive THEN
        -- 3a. Active rows (including previously restored rows) MUST have archive_batch_id = NULL
        EXECUTE $q$
            UPDATE public.schedule
            SET archive_batch_id = NULL
            WHERE COALESCE(archive, false) = false
              AND archive_batch_id IS NOT NULL
        $q$;

        -- 3b. One archived_schedule row per existing archived batch
        EXECUTE format($q$
            INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
            SELECT
                archive_batch_id,
                %s,
                %s
            FROM public.schedule
            WHERE archive = true
              AND archive_batch_id IS NOT NULL
            GROUP BY archive_batch_id
            ON CONFLICT (archive_batch_id) DO NOTHING
        $q$, v_archived_at_expr, v_archived_by_expr);

        -- 3c. Group archived rows with no archive_batch_id into one legacy batch
        EXECUTE 'SELECT count(*) FROM public.schedule WHERE archive = true AND archive_batch_id IS NULL'
            INTO v_legacy_count;

        IF v_legacy_count > 0 THEN
            v_legacy_batch_id := gen_random_uuid();
            EXECUTE format(
                'SELECT %s FROM public.schedule WHERE archive = true AND archive_batch_id IS NULL',
                v_archived_at_expr
            ) INTO v_legacy_at;

            INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
            VALUES (v_legacy_batch_id, COALESCE(v_legacy_at, now()), 'Legacy Archive');

            EXECUTE 'UPDATE public.schedule SET archive_batch_id = $1 WHERE archive = true AND archive_batch_id IS NULL'
                USING v_legacy_batch_id;
        END IF;
    ELSE
        -- Re-run safety if schedule.archive was already dropped: backfill any missing parent rows
        INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
        SELECT DISTINCT archive_batch_id, now(), 'Legacy Archive'
        FROM public.schedule
        WHERE archive_batch_id IS NOT NULL
        ON CONFLICT (archive_batch_id) DO NOTHING;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 4. Add Foreign Key (schedule.archive_batch_id -> archived_schedule) + Index
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule
    DROP CONSTRAINT IF EXISTS schedule_archive_batch_id_fkey;

ALTER TABLE public.schedule
    ADD CONSTRAINT schedule_archive_batch_id_fkey
    FOREIGN KEY (archive_batch_id)
    REFERENCES public.archived_schedule (archive_batch_id)
    ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_schedule_archive_batch_id
    ON public.schedule (archive_batch_id);

-- ------------------------------------------------------------------------------
-- 5. Drop/replace dependent views, indexes, and RLS policies referencing
--    schedule.archive or schedule.archived_at BEFORE dropping those columns
-- ------------------------------------------------------------------------------
DROP VIEW IF EXISTS public.schedule_with_semester;

-- Drop known legacy indexes that depend on schedule.archive / schedule.archived_at
DROP INDEX IF EXISTS public.idx_schedule_room_active;
DROP INDEX IF EXISTS public.idx_schedule_section_active;
DROP INDEX IF EXISTS public.idx_schedule_archive_day;
DROP INDEX IF EXISTS public.idx_schedule_program_archive;

-- Dynamically drop any other non-PK index on public.schedule referencing exact column
-- names `archive` or `archived_at` (without touching `archive_batch_id`)
DO $$
DECLARE
    v_idx record;
BEGIN
    FOR v_idx IN
        SELECT schemaname, indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'schedule'
          AND indexname <> 'schedule_pkey'
          AND (
              indexdef ~ '\marchive\M'
              OR indexdef ~ '\marchived_at\M'
          )
    LOOP
        EXECUTE format('DROP INDEX IF EXISTS %I.%I', v_idx.schemaname, v_idx.indexname);
    END LOOP;
END $$;

-- Update RLS policy on public.schedule so it uses archive_batch_id IS NULL (no schedule.archive)
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
CREATE POLICY "schedule_select" ON public.schedule
    FOR SELECT TO authenticated
    USING (
        (SELECT public.get_user_role()) = 'admin'
        OR (
            (SELECT public.get_user_role()) = 'chair'
            AND (
                archive_batch_id IS NULL
                OR professor_load_id IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM public.professor_load pl
                    JOIN public.course c ON pl.course_id = c.course_id
                    WHERE pl.id = schedule.professor_load_id
                      AND (c.program_id = (SELECT public.get_user_program_id()) OR c.program_id IS NULL)
                )
            )
        )
    );

-- ------------------------------------------------------------------------------
-- 6. Drop schedule.archive and schedule.archived_at
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule
    DROP COLUMN IF EXISTS archive,
    DROP COLUMN IF EXISTS archived_at;

-- Recreate active-schedule partial indexes using archive_batch_id IS NULL
CREATE INDEX IF NOT EXISTS idx_schedule_room_active
    ON public.schedule (room_id)
    WHERE archive_batch_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_schedule_section_active
    ON public.schedule (section)
    WHERE archive_batch_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_schedule_active_day
    ON public.schedule (day)
    WHERE archive_batch_id IS NULL;

-- ------------------------------------------------------------------------------
-- 7. Recreate public.schedule_with_semester (SECURITY INVOKER, joins archived_schedule & specializations)
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
    c.program_id,
    sc.archive_batch_id,
    arch.archived_at,
    arch.archived_by,
    sc.school_year,
    sc.is_used,
    sc.prepared_by_name,
    sc.prepared_by_title,
    coalesce(nullif(trim(sc.prepared_by_name), ''), 'Preparer account unavailable') AS preparer_name,
    coalesce(nullif(trim(sc.prepared_by_title), ''), 'Role/title unavailable') AS preparer_title,
    coalesce(c.semester, '1st Semester') AS semester,
    c.specialization_id,
    sp.name AS specialization,
    sp.name AS major,
    pl.course_id,
    c.course_code,
    c.year_level,
    pl.professor_name,
    p.program_code,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.archived_schedule arch ON arch.archive_batch_id = sc.archive_batch_id
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.specializations sp ON sp.specialization_id = c.specialization_id
LEFT JOIN public.program p ON p.id = c.program_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

REVOKE ALL ON public.schedule_with_semester FROM anon;
GRANT SELECT ON public.schedule_with_semester TO authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 8. Update RPCs to use archived_schedule and archive_batch_id IS NULL / IS NOT NULL
-- ------------------------------------------------------------------------------

-- 8.1 archive_active_schedule: atomically creates 1 archived_schedule row and
--     sets schedule.archive_batch_id on all matching active schedule rows.
CREATE OR REPLACE FUNCTION public.archive_active_schedule(
    p_program_id   integer DEFAULT NULL,
    p_semester     text    DEFAULT NULL,
    p_archived_by  text    DEFAULT 'Chair',
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
    v_target_ids       bigint[];
    v_archived_by      text        := coalesce(nullif(trim(p_archived_by), ''), 'Chair');
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    IF v_caller_role = 'chair' THEN
        IF v_target_pid IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_pid != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only archive their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_pid := coalesce(v_target_pid, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    SELECT array_agg(sc.schedule_id)
    INTO v_target_ids
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive_batch_id IS NULL
      AND (v_target_pid IS NULL OR c.program_id = v_target_pid)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)));

    IF v_target_ids IS NULL OR array_length(v_target_ids, 1) IS NULL THEN
        RETURN jsonb_build_object(
            'success',        true,
            'batch_id',       NULL,
            'archived_at',    NULL,
            'archived_count', 0,
            'program_id',     v_target_pid,
            'semester',       p_semester
        );
    END IF;

    INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
    VALUES (v_batch_id, v_now, v_archived_by);

    UPDATE public.schedule
    SET archive_batch_id = v_batch_id
    WHERE schedule_id = ANY(v_target_ids)
      AND archive_batch_id IS NULL;

    GET DIAGNOSTICS v_count = ROW_COUNT;

    SELECT count(*)
    INTO v_remaining_active
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive_batch_id IS NULL
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

REVOKE ALL ON FUNCTION public.archive_active_schedule(integer, text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO authenticated, service_role;

-- 8.2 confirm_schedule_transaction: atomically archives any existing active
--     schedule for the program into a new archived_schedule row, then inserts
--     the new active schedule rows with archive_batch_id = NULL.
CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester            text,
    p_program             text,
    p_rows                jsonb,
    p_clear_scope         boolean DEFAULT true,
    p_archived_by         text    DEFAULT 'Chair',
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
    v_archive_batch_id  uuid;
    v_now               timestamptz := clock_timestamp();
    v_archived          integer := 0;
    v_inserted          integer := 0;
    v_target_program_id integer := p_program_id;
    v_target_program_code text;
    v_prev_ids          bigint[];
    v_caller_role       text;
    v_caller_pid        integer;
    v_archived_by       text;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    IF v_caller_role = 'chair' THEN
        IF v_target_program_id IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_program_id != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only confirm their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_program_id := coalesce(v_target_program_id, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    IF p_semester IS NULL OR trim(p_semester) = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) != '' THEN
        SELECT id, program_code INTO v_target_program_id, v_target_program_code
        FROM public.program
        WHERE upper(trim(program_code)) = upper(trim(p_program))
           OR upper(trim(coalesce(program_name, ''))) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    v_archived_by := coalesce(nullif(trim(p_archived_by), ''), nullif(trim(p_prepared_by_name), ''), 'Chair');

    IF p_clear_scope THEN
        SELECT array_agg(sc.schedule_id)
        INTO v_prev_ids
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive_batch_id IS NULL
          AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id);

        IF v_prev_ids IS NOT NULL AND array_length(v_prev_ids, 1) > 0 THEN
            v_archive_batch_id := coalesce(p_batch_id, gen_random_uuid());

            INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
            VALUES (v_archive_batch_id, v_now, v_archived_by)
            ON CONFLICT (archive_batch_id) DO NOTHING;

            UPDATE public.schedule
            SET archive_batch_id = v_archive_batch_id
            WHERE schedule_id = ANY(v_prev_ids)
              AND archive_batch_id IS NULL;

            GET DIAGNOSTICS v_archived = ROW_COUNT;
        END IF;
    END IF;

    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive_batch_id,
        prepared_by_name, prepared_by_title, school_year, is_used
    )
    SELECT
        nullif(x->>'professor_load_id', '')::bigint,
        nullif(x->>'room_id', '')::bigint,
        coalesce(x->>'day', 'Monday'),
        (x->>'class_start')::time,
        (x->>'class_end')::time,
        coalesce(nullif(x->>'session_type', ''), 'Lecture'),
        x->>'section',
        NULL,
        coalesce(nullif(trim(x->>'prepared_by_name'), ''), nullif(trim(p_prepared_by_name), ''), 'Chair'),
        coalesce(nullif(trim(x->>'prepared_by_title'), ''), nullif(trim(p_prepared_by_title), ''), 'Chair'),
        coalesce(x->>'school_year', p_school_year),
        coalesce((x->>'is_used')::boolean, false)
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',           true,
        'archived_batch_id', v_archive_batch_id,
        'archived_count',    v_archived,
        'inserted_count',    v_inserted,
        'semester',          p_semester,
        'program_id',        v_target_program_id
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text, text) TO authenticated, service_role;

-- 8.3 restore_archived_schedule_batch:
--     1. Archive the current active schedule for the program first (new archived_schedule batch).
--     2. Set archive_batch_id = NULL on all rows of the restored batch.
--     3. Delete the restored batch's now-empty archived_schedule row.
CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id    text,
    p_restored_by text DEFAULT 'Chair'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_restore_batch_id     uuid;
    v_target_semester      text;
    v_target_program_id    integer;
    v_target_program       text;
    v_active_ids           bigint[];
    v_new_archive_batch_id uuid;
    v_archived_prev_count  integer := 0;
    v_restored             integer := 0;
    v_now                  timestamptz := clock_timestamp();
    v_caller_role          text;
    v_caller_prog_id       integer;
    v_actor                text := coalesce(nullif(trim(p_restored_by), ''), 'Chair');
BEGIN
    v_caller_role    := public.get_user_role();
    v_caller_prog_id := public.get_user_program_id();

    BEGIN
        v_restore_batch_id := btrim(p_batch_id)::uuid;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'Invalid archive batch ID: %', p_batch_id;
    END;

    SELECT coalesce(c.semester, '1st Semester'), c.program_id, p.program_code
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    LEFT JOIN public.program p ON c.program_id = p.id
    WHERE sc.archive_batch_id = v_restore_batch_id
    LIMIT 1;

    IF v_target_semester IS NULL THEN
        RAISE EXCEPTION 'Archived batch % not found.', p_batch_id;
    END IF;

    IF v_caller_role = 'chair' THEN
        IF v_target_program_id IS NOT NULL AND v_caller_prog_id IS NOT NULL AND v_target_program_id != v_caller_prog_id THEN
            RAISE EXCEPTION 'Permission denied: Chairs can only restore their own program archives.'
                USING ERRCODE = '42501';
        END IF;
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Step 1: Archive any currently active schedule for this program first (new batch)
    SELECT array_agg(sc.schedule_id)
    INTO v_active_ids
    FROM public.schedule sc
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive_batch_id IS NULL
      AND (v_target_program_id IS NULL OR c.program_id = v_target_program_id);

    IF v_active_ids IS NOT NULL AND array_length(v_active_ids, 1) > 0 THEN
        v_new_archive_batch_id := gen_random_uuid();

        INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
        VALUES (v_new_archive_batch_id, v_now, v_actor);

        UPDATE public.schedule
        SET archive_batch_id = v_new_archive_batch_id
        WHERE schedule_id = ANY(v_active_ids)
          AND archive_batch_id IS NULL;

        GET DIAGNOSTICS v_archived_prev_count = ROW_COUNT;
    END IF;

    -- Step 2: Set archive_batch_id = NULL on all rows of the restored batch
    UPDATE public.schedule
    SET archive_batch_id = NULL
    WHERE archive_batch_id = v_restore_batch_id;

    GET DIAGNOSTICS v_restored = ROW_COUNT;

    IF v_restored = 0 THEN
        RAISE EXCEPTION 'Restoration failed: 0 rows were restored for batch %.', p_batch_id;
    END IF;

    -- Step 3: Delete the restored batch's now-empty archived_schedule row
    DELETE FROM public.archived_schedule
    WHERE archive_batch_id = v_restore_batch_id;

    RETURN jsonb_build_object(
        'success',                    true,
        'restored_batch_id',          v_restore_batch_id,
        'restored_count',             v_restored,
        'archived_previous_batch_id', v_new_archive_batch_id,
        'archived_previous_count',    v_archived_prev_count,
        'semester',                   v_target_semester,
        'program',                    v_target_program,
        'program_id',                 v_target_program_id
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(text, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO authenticated, service_role;

-- 8.4 get_schedule_archive_batches: groups archived schedule rows by archive_batch_id
--     and reads archived_at and archived_by from public.archived_schedule.
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, timestamptz, timestamptz);
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, text, timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.get_schedule_archive_batches(
    p_program     text DEFAULT NULL,
    p_semester    text DEFAULT NULL,
    p_school_year text DEFAULT NULL,
    p_date_from   timestamptz DEFAULT NULL,
    p_date_to     timestamptz DEFAULT NULL
)
RETURNS TABLE (
    batch_id          text,
    semester          text,
    school_year       text,
    program           text,
    archived_at       timestamptz,
    archived_by       text,
    archived_by_title text,
    archive_reason    text,
    entry_count       bigint,
    section_count     bigint,
    sections          text[],
    is_used           boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH batch_data AS (
        SELECT
            sc.archive_batch_id::text AS b_id,
            coalesce(c.semester, '1st Semester')::text AS b_semester,
            coalesce(sc.school_year, '')::text AS b_school_year,
            coalesce(p.program_code, '')::text AS b_program,
            arch.archived_at AS b_archived_at,
            coalesce(nullif(trim(arch.archived_by), ''), 'System')::text AS b_archived_by,
            coalesce(nullif(trim(sc.prepared_by_title), ''), '')::text AS b_archived_by_title,
            'Archived schedule'::text AS b_archive_reason,
            sc.section::text AS b_section,
            sc.is_used AS b_is_used
        FROM public.schedule sc
        INNER JOIN public.archived_schedule arch
            ON arch.archive_batch_id = sc.archive_batch_id
        LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
        LEFT JOIN public.course c ON c.course_id = pl.course_id
        LEFT JOIN public.program p ON p.id = c.program_id
        WHERE sc.archive_batch_id IS NOT NULL
          AND (
              p_program IS NULL OR nullif(trim(p_program), '') IS NULL
              OR trim(lower(coalesce(p.program_code, ''))) = trim(lower(p_program))
              OR trim(lower(coalesce(p.program_name, ''))) = trim(lower(p_program))
          )
          AND (
              p_semester IS NULL OR nullif(trim(p_semester), '') IS NULL
              OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester))
          )
          AND (
              p_school_year IS NULL OR nullif(trim(p_school_year), '') IS NULL
              OR trim(coalesce(sc.school_year, '')) = trim(p_school_year)
          )
    )
    SELECT
        bd.b_id,
        (array_agg(bd.b_semester ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        (array_agg(bd.b_school_year ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        (array_agg(bd.b_program ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        max(bd.b_archived_at),
        coalesce((array_agg(bd.b_archived_by ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'System'),
        coalesce((array_agg(bd.b_archived_by_title ORDER BY bd.b_archived_at DESC NULLS LAST))[1], ''),
        coalesce((array_agg(bd.b_archive_reason ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Archived schedule'),
        count(*),
        count(DISTINCT bd.b_section),
        array_agg(DISTINCT bd.b_section ORDER BY bd.b_section),
        bool_and(bd.b_is_used)
    FROM batch_data bd
    WHERE (p_date_from IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at >= p_date_from))
      AND (p_date_to IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at <= p_date_to))
    GROUP BY bd.b_id
    ORDER BY max(bd.b_archived_at) DESC NULLS LAST;
END;
$$;

REVOKE ALL ON FUNCTION public.get_schedule_archive_batches(text, text, text, timestamptz, timestamptz) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_schedule_archive_batches(text, text, text, timestamptz, timestamptz) TO authenticated, service_role;

-- 8.5 set_schedule_batch_used: uses archive_batch_id IS NULL for active schedule
--     and archive_batch_id::text = p_batch_id for archived batches.
CREATE OR REPLACE FUNCTION public.set_schedule_batch_used(
    p_batch_id   text,
    p_is_used    boolean,
    p_program_id integer DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_school_year   text;
    v_semester      text;
    v_program_id    integer := p_program_id;
    v_caller_role   text;
    v_caller_pid    integer;
    v_updated_count integer := 0;
    v_cleared_count integer := 0;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    IF v_caller_role = 'chair' THEN
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
        WHERE sc.archive_batch_id IS NULL
          AND (v_program_id IS NULL OR c.program_id = v_program_id)
        LIMIT 1;
    ELSE
        SELECT sc.school_year, coalesce(c.semester, '1st Semester'), c.program_id
        INTO v_school_year, v_semester, v_program_id
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive_batch_id::text = p_batch_id
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
            WHERE sc.archive_batch_id IS NULL
              AND (
                  EXISTS (
                      SELECT 1 FROM public.professor_load pl
                      JOIN public.course c ON pl.course_id = c.course_id
                      WHERE pl.id = sc.professor_load_id
                        AND (v_program_id IS NULL OR c.program_id = v_program_id)
                  )
                  OR sc.professor_load_id IS NULL
              );
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
            WHERE sc.archive_batch_id IS NULL
              AND (
                  EXISTS (
                      SELECT 1 FROM public.professor_load pl
                      JOIN public.course c ON pl.course_id = c.course_id
                      WHERE pl.id = sc.professor_load_id
                        AND (v_program_id IS NULL OR c.program_id = v_program_id)
                  )
                  OR sc.professor_load_id IS NULL
              );
        ELSE
            UPDATE public.schedule sc
            SET is_used = false
            WHERE sc.archive_batch_id::text = p_batch_id;
        END IF;

        GET DIAGNOSTICS v_updated_count = ROW_COUNT;
    END IF;

    RETURN jsonb_build_object(
        'success',       true,
        'batch_id',      p_batch_id,
        'school_year',   v_school_year,
        'semester',      v_semester,
        'program_id',    v_program_id,
        'is_used',       p_is_used,
        'updated_count', v_updated_count,
        'cleared_count', v_cleared_count
    );
END;
$$;

REVOKE ALL ON FUNCTION public.set_schedule_batch_used(text, boolean, integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.set_schedule_batch_used(text, boolean, integer) TO authenticated, service_role;

-- 8.6 restore_database_json: updated to restore specializations, archived_schedule,
--     and schedule without dropped schedule.archive / schedule.archived_at columns.
CREATE OR REPLACE FUNCTION public.restore_database_json(
    payload        jsonb,
    clear_existing boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    result          jsonb := '{}'::jsonb;
    inserted_count  integer := 0;
    total_restored  integer := 0;
    t_data          jsonb;
    has_valid_table boolean := false;
    chk_table       text;
    v_legacy_id     uuid;
    valid_tables    text[] := ARRAY[
        'program', 'program_department', 'specializations', 'users', 'academic_ranking',
        'room', 'working_hours', 'timeslot', 'course', 'professor_load', 'prof_course',
        'archived_schedule', 'schedule', 'irregular_students', 'irregular_student_schedule',
        'delete_requests', 'activity_log'
    ];
BEGIN
    IF public.get_user_role() != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Only administrators can restore backups.'
            USING ERRCODE = '42501';
    END IF;

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
        RAISE EXCEPTION 'Invalid backup payload: no recognized tables found. Aborting restore.';
    END IF;

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
        IF (t_data ? 'archived_schedule' OR t_data ? 'schedule') AND to_regclass('public.archived_schedule') IS NOT NULL THEN
            DELETE FROM public.archived_schedule;
        END IF;
        IF (t_data ? 'professor_load' OR t_data ? 'prof_course') AND to_regclass('public.professor_load') IS NOT NULL THEN
            DELETE FROM public.professor_load;
        END IF;
        IF t_data ? 'course' AND to_regclass('public.course') IS NOT NULL THEN
            DELETE FROM public.course;
        END IF;
        IF t_data ? 'specializations' AND to_regclass('public.specializations') IS NOT NULL THEN
            DELETE FROM public.specializations;
        END IF;
        IF (t_data ? 'working_hours' OR t_data ? 'timeslot') AND to_regclass('public.working_hours') IS NOT NULL THEN
            DELETE FROM public.working_hours;
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

    -- program
    IF t_data ? 'program' AND jsonb_array_length(t_data->'program') > 0 AND to_regclass('public.program') IS NOT NULL THEN
        INSERT INTO public.program (id, program_code, program_name, archive)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            x->>'program_code',
            x->>'program_name',
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(t_data->'program') AS x
        ON CONFLICT (id) DO UPDATE
        SET program_code = EXCLUDED.program_code,
            program_name = EXCLUDED.program_name,
            archive      = EXCLUDED.archive;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('program', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- specializations
    IF t_data ? 'specializations' AND jsonb_array_length(t_data->'specializations') > 0 AND to_regclass('public.specializations') IS NOT NULL THEN
        INSERT INTO public.specializations (specialization_id, program_id, name, code, archive)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'specialization_id')::integer,
            NULLIF(x->>'program_id', '')::integer,
            x->>'name',
            x->>'code',
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(t_data->'specializations') AS x
        ON CONFLICT (specialization_id) DO UPDATE
        SET program_id = EXCLUDED.program_id,
            name       = EXCLUDED.name,
            code       = EXCLUDED.code,
            archive    = EXCLUDED.archive;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('specializations', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- room
    IF t_data ? 'room' AND jsonb_array_length(t_data->'room') > 0 AND to_regclass('public.room') IS NOT NULL THEN
        INSERT INTO public.room (room_id, room_name, room_type, archive)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'room_id')::integer,
            (x->>'room_name')::varchar,
            (x->>'room_type')::varchar,
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(t_data->'room') AS x
        ON CONFLICT (room_id) DO UPDATE
        SET room_name = EXCLUDED.room_name,
            room_type = EXCLUDED.room_type,
            archive   = EXCLUDED.archive;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('room', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- working_hours
    IF (t_data ? 'working_hours' OR t_data ? 'timeslot') AND to_regclass('public.working_hours') IS NOT NULL THEN
        INSERT INTO public.working_hours (working_hours_id, day, start_time, end_time, lunch_time, archive)
        OVERRIDING SYSTEM VALUE
        SELECT
            COALESCE((x->>'working_hours_id')::integer, (x->>'timeslot_id')::integer),
            x->>'day',
            (x->>'start_time')::time,
            (x->>'end_time')::time,
            NULLIF(x->>'lunch_time', '')::time,
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(COALESCE(t_data->'working_hours', t_data->'timeslot')) AS x
        ON CONFLICT (working_hours_id) DO UPDATE
        SET day        = EXCLUDED.day,
            start_time = EXCLUDED.start_time,
            end_time   = EXCLUDED.end_time,
            lunch_time = EXCLUDED.lunch_time,
            archive    = EXCLUDED.archive;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('working_hours', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- course
    IF t_data ? 'course' AND jsonb_array_length(t_data->'course') > 0 AND to_regclass('public.course') IS NOT NULL THEN
        INSERT INTO public.course (
            course_id, course_code, course_name, lecture_hours, lab_hours, ilp_hours,
            units, year_level, semester, specialization_id, program_id, archive
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
            NULLIF(x->>'specialization_id', '')::integer,
            COALESCE(NULLIF(x->>'program_id', '')::integer, (SELECT id FROM public.program ORDER BY id LIMIT 1)),
            COALESCE((x->>'archive')::boolean, false)
        FROM jsonb_array_elements(t_data->'course') AS x
        ON CONFLICT (course_id) DO UPDATE
        SET course_code       = EXCLUDED.course_code,
            course_name       = EXCLUDED.course_name,
            lecture_hours     = EXCLUDED.lecture_hours,
            lab_hours         = EXCLUDED.lab_hours,
            ilp_hours         = EXCLUDED.ilp_hours,
            units             = EXCLUDED.units,
            year_level        = EXCLUDED.year_level,
            semester          = EXCLUDED.semester,
            specialization_id = EXCLUDED.specialization_id,
            program_id        = EXCLUDED.program_id,
            archive           = EXCLUDED.archive;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('course', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- professor_load
    IF (t_data ? 'professor_load' AND jsonb_array_length(t_data->'professor_load') > 0)
       OR (t_data ? 'prof_course' AND jsonb_array_length(t_data->'prof_course') > 0) THEN
        INSERT INTO public.professor_load (id, course_id, sections, professor_name)
        OVERRIDING SYSTEM VALUE
        SELECT
            COALESCE((x->>'id')::integer, (x->>'prof_course_id')::integer, (x->>'professor_load_id')::integer),
            (x->>'course_id')::integer,
            COALESCE(NULLIF(x->>'sections', '')::integer, 1),
            COALESCE(NULLIF(trim(x->>'professor_name'), ''), 'Unknown Professor')
        FROM jsonb_array_elements(COALESCE(t_data->'professor_load', t_data->'prof_course')) AS x
        ON CONFLICT (id) DO UPDATE
        SET course_id      = EXCLUDED.course_id,
            sections       = EXCLUDED.sections,
            professor_name = EXCLUDED.professor_name;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('professor_load', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- archived_schedule (must be inserted before schedule due to FK)
    IF t_data ? 'archived_schedule' AND jsonb_array_length(t_data->'archived_schedule') > 0 AND to_regclass('public.archived_schedule') IS NOT NULL THEN
        INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
        SELECT
            (x->>'archive_batch_id')::uuid,
            COALESCE(NULLIF(x->>'archived_at', '')::timestamptz, now()),
            NULLIF(x->>'archived_by', '')::varchar
        FROM jsonb_array_elements(t_data->'archived_schedule') AS x
        WHERE NULLIF(x->>'archive_batch_id', '') IS NOT NULL
        ON CONFLICT (archive_batch_id) DO UPDATE
        SET archived_at = EXCLUDED.archived_at,
            archived_by = EXCLUDED.archived_by;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('archived_schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- schedule (supports both normalized backups and legacy backups with schedule.archive)
    IF t_data ? 'schedule' AND jsonb_array_length(t_data->'schedule') > 0 AND to_regclass('public.schedule') IS NOT NULL THEN
        -- Backfill parent archived_schedule rows for any legacy backup rows
        INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
        SELECT
            (x->>'archive_batch_id')::uuid,
            COALESCE(MAX(NULLIF(x->>'archived_at', '')::timestamptz), now()),
            COALESCE(MAX(NULLIF(trim(x->>'archived_by'), '')), MAX(NULLIF(trim(x->>'prepared_by_name'), '')), 'Legacy Archive')
        FROM jsonb_array_elements(t_data->'schedule') AS x
        WHERE NULLIF(x->>'archive_batch_id', '') IS NOT NULL
          AND (NOT (x ? 'archive') OR COALESCE((x->>'archive')::boolean, false) = true)
        GROUP BY (x->>'archive_batch_id')::uuid
        ON CONFLICT (archive_batch_id) DO NOTHING;

        -- If legacy backup has archived rows without archive_batch_id, synthesize one legacy batch
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(t_data->'schedule') AS x
            WHERE (x ? 'archive') AND COALESCE((x->>'archive')::boolean, false) = true
              AND NULLIF(x->>'archive_batch_id', '') IS NULL
        ) THEN
            v_legacy_id := gen_random_uuid();
            INSERT INTO public.archived_schedule (archive_batch_id, archived_at, archived_by)
            VALUES (v_legacy_id, now(), 'Legacy Archive');
        END IF;

        INSERT INTO public.schedule (
            schedule_id, professor_load_id, room_id, day,
            class_start, class_end, session_type, section,
            archive_batch_id, school_year, is_used,
            prepared_by_name, prepared_by_title
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'schedule_id')::integer,
            COALESCE(NULLIF(x->>'professor_load_id', '')::integer, NULLIF(x->>'prof_course_id', '')::integer),
            NULLIF(x->>'room_id', '')::integer,
            (x->>'day')::varchar,
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            x->>'session_type',
            x->>'section',
            CASE
                WHEN (x ? 'archive') AND COALESCE((x->>'archive')::boolean, false) = false THEN NULL
                WHEN NULLIF(x->>'archive_batch_id', '') IS NOT NULL THEN (x->>'archive_batch_id')::uuid
                WHEN (x ? 'archive') AND COALESCE((x->>'archive')::boolean, false) = true THEN v_legacy_id
                ELSE NULL
            END,
            x->>'school_year',
            COALESCE((x->>'is_used')::boolean, false),
            COALESCE(NULLIF(trim(x->>'prepared_by_name'), ''), 'Unknown Preparer'),
            COALESCE(NULLIF(trim(x->>'prepared_by_title'), ''), 'Chair')
        FROM jsonb_array_elements(t_data->'schedule') AS x
        ON CONFLICT (schedule_id) DO UPDATE
        SET professor_load_id = EXCLUDED.professor_load_id,
            room_id           = EXCLUDED.room_id,
            day               = EXCLUDED.day,
            class_start       = EXCLUDED.class_start,
            class_end         = EXCLUDED.class_end,
            session_type      = EXCLUDED.session_type,
            section           = EXCLUDED.section,
            archive_batch_id  = EXCLUDED.archive_batch_id,
            school_year       = EXCLUDED.school_year,
            is_used           = EXCLUDED.is_used,
            prepared_by_name  = EXCLUDED.prepared_by_name,
            prepared_by_title = EXCLUDED.prepared_by_title;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    RETURN jsonb_build_object(
        'success',        true,
        'total_restored', total_restored,
        'details',        result
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_database_json(jsonb, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.restore_database_json(jsonb, boolean) TO authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 9. Reload PostgREST schema cache so the new FK relationship
--    schedule -> archived_schedule is immediately discoverable (no PGRST200)
-- ------------------------------------------------------------------------------
NOTIFY pgrst, 'reload schema';

COMMIT;

-- ==============================================================================
-- VERIFICATION QUERIES (run immediately after COMMIT to verify migration)
-- ==============================================================================

-- 1. Confirm schedule.archive and schedule.archived_at are dropped, and archive_batch_id is uuid
SELECT column_name, data_type, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'schedule'
  AND column_name IN ('archive', 'archived_at', 'archive_batch_id')
ORDER BY column_name;

-- 2. Confirm FK schedule.archive_batch_id -> archived_schedule.archive_batch_id and index exist
SELECT conname AS fk_name, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.schedule'::regclass
  AND contype = 'f'
  AND conname = 'schedule_archive_batch_id_fkey';

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'schedule'
ORDER BY indexname;

-- 3. Confirm row counts and data integrity (0 orphans, 0 empty batches)
SELECT
    (SELECT count(*) FROM public.schedule WHERE archive_batch_id IS NULL) AS active_schedule_rows,
    (SELECT count(*) FROM public.schedule WHERE archive_batch_id IS NOT NULL) AS archived_schedule_rows,
    (SELECT count(*) FROM public.archived_schedule) AS archived_batch_count,
    (SELECT count(*) FROM public.schedule s
     LEFT JOIN public.archived_schedule a ON a.archive_batch_id = s.archive_batch_id
     WHERE s.archive_batch_id IS NOT NULL AND a.archive_batch_id IS NULL) AS orphaned_schedule_rows,
    (SELECT count(*) FROM public.archived_schedule a
     WHERE NOT EXISTS (SELECT 1 FROM public.schedule s WHERE s.archive_batch_id = a.archive_batch_id)) AS empty_archive_batches;

-- 4. Confirm archived_schedule batches and schedule_with_semester view work cleanly
SELECT a.archive_batch_id, a.archived_at, a.archived_by, count(s.schedule_id) AS row_count
FROM public.archived_schedule a
LEFT JOIN public.schedule s ON s.archive_batch_id = a.archive_batch_id
GROUP BY a.archive_batch_id, a.archived_at, a.archived_by
ORDER BY a.archived_at DESC;

SELECT count(*) AS view_row_count FROM public.schedule_with_semester;

-- 5. Confirm specializations.program_id FK exists
SELECT conname AS fk_name, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.specializations'::regclass
  AND contype = 'f';

