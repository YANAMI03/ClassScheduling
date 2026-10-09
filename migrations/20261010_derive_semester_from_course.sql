-- ==============================================================================
-- Migration: 20261010_derive_semester_from_course.sql
-- Description:
--   1. Indexes on schedule and professor_load for fast join and archive lookups.
--   2. VIEW schedule_with_semester: exposes schedule columns + derived semester,
--      specialization/major, course_name, professor_name, room_name.
--      Uses WITH (security_invoker = on) to adhere to PostgREST / RLS policies.
--   3. RPC archive_active_schedule: atomically archives active schedule records
--      for a given program and derived semester.
--   4. RPC confirm_schedule_transaction: atomic archive + bulk insert without
--      referencing non-existent columns (schedule.semester, schedule.major, etc.).
--   5. RPC get_schedule_archive_batches: groups archived schedule records by batch,
--      deriving semester from course.semester.
--   6. RPC restore_archived_schedule_batch: restores batch, archiving current active
--      records of the same program & derived semester first.
--
-- BACKUP REMINDER:
--   Export a plain data backup (Supabase Dashboard -> Database -> Backups or pg_dump)
--   prior to running migrations.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Performance Indexes
-- ------------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_schedule_prof_load_id ON public.schedule (professor_load_id);
CREATE INDEX IF NOT EXISTS idx_schedule_program_archive ON public.schedule (program_id, archive);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_batch ON public.schedule (archive, archive_batch_id) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_schedule_archived_at ON public.schedule (archived_at DESC) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_prof_load_course_id ON public.professor_load (course_id);

-- ------------------------------------------------------------------------------
-- 2. View: schedule_with_semester
-- ------------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.schedule_with_semester WITH (security_invoker = on) AS
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
    sc.program_id,
    sc.archived_at,
    sc.archive_batch_id,
    sc.prepared_by_user_id,
    sc.prepared_by_name,
    sc.prepared_by_title,
    -- Derived attributes from course & professor_load
    coalesce(c.semester, '1st Semester') AS semester,
    c.specialization,
    c.specialization AS major,
    pl.course_id,
    c.course_name,
    c.year_level,
    pl.professor_name,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
LEFT JOIN public.course c ON pl.course_id = c.course_id
LEFT JOIN public.program p ON sc.program_id = p.id
LEFT JOIN public.room r ON sc.room_id = r.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 3. RPC: archive_active_schedule
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.archive_active_schedule(text, integer, text);
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
    v_batch_id  uuid        := gen_random_uuid();
    v_now       timestamptz := clock_timestamp();
    v_count     integer     := 0;
BEGIN
    UPDATE public.schedule sc
    SET archive = true,
        archive_batch_id = v_batch_id,
        archived_at = v_now
    FROM public.professor_load pl
    JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = false
      AND (p_program_id IS NULL OR sc.program_id = p_program_id)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(c.semester)) = trim(lower(p_semester)));

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_at',    v_now,
        'archived_count', v_count,
        'program_id',     p_program_id,
        'semester',       p_semester
    );
END;
$$;

REVOKE ALL ON FUNCTION public.archive_active_schedule(integer, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 4. RPC: confirm_schedule_transaction
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);
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

    IF v_target_program_name IS NULL AND v_target_program_id IS NOT NULL THEN
        SELECT program_name INTO v_target_program_name
        FROM public.program
        WHERE id = v_target_program_id
        LIMIT 1;
    END IF;

    -- Auto-resolve preparer identity from auth.uid() if not passed
    IF v_prep_user_id IS NULL AND auth.uid() IS NOT NULL THEN
        v_prep_user_id := auth.uid();
    END IF;

    IF v_prep_name IS NULL AND v_prep_user_id IS NOT NULL THEN
        SELECT trim(upper(concat_ws(' ', nullif(trim(first_name), ''), nullif(trim(last_name), '')))),
               CASE
                   WHEN lower(coalesce(role, '')) IN ('super_admin', 'superadmin') THEN 'Super Admin - ' || coalesce(v_target_program_name, 'BSIT')
                   WHEN lower(coalesce(role, '')) IN ('admin', 'administrator', 'academic_admin') THEN 'Academic Admin - ' || coalesce(v_target_program_name, 'BSIT')
                   ELSE 'Program Scheduler - ' || coalesce(v_target_program_name, 'BSIT')
               END
        INTO v_prep_name, v_prep_title
        FROM public.users
        WHERE id = v_prep_user_id
        LIMIT 1;
    END IF;

    IF p_clear_scope THEN
        -- Atomically soft-archive previous active entries for the SAME program and derived semester
        UPDATE public.schedule sc
        SET archive = true,
            archive_batch_id = v_batch_id,
            archived_at = clock_timestamp()
        FROM public.professor_load pl
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.archive = false
          AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id)
          AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(c.semester)) = trim(lower(p_semester)));

        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows (STRICTLY real columns only: semester and major omitted)
    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive,
        prepared_by_user_id, prepared_by_name, prepared_by_title
    )
    SELECT coalesce(
               nullif(x->>'program_id', '')::integer,
               (SELECT p.id FROM public.program p WHERE upper(trim(p.program_name)) = upper(trim(x->>'program')) LIMIT 1),
               v_target_program_id
           ),
           nullif(x->>'professor_load_id', '')::bigint,
           nullif(x->>'room_id', '')::bigint,
           coalesce(x->>'day', 'Monday'),
           (x->>'class_start')::time,
           (x->>'class_end')::time,
           coalesce(nullif(x->>'session_type', ''), 'Lecture'),
           x->>'section',
           false,
           coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id),
           coalesce(nullif(trim(x->>'prepared_by_name'), ''), v_prep_name),
           coalesce(nullif(trim(x->>'prepared_by_title'), ''), v_prep_title)
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_count', v_archived,
        'inserted_count', v_inserted,
        'semester',       p_semester,
        'program_id',     v_target_program_id,
        'prepared_by_name', v_prep_name,
        'prepared_by_title', v_prep_title
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 5. RPC: get_schedule_archive_batches
-- ------------------------------------------------------------------------------
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
            coalesce(c.semester, '1st Semester')::text    AS b_semester,
            coalesce(p.program_name, '')::text            AS b_program,
            sc.archived_at                                AS b_archived_at,
            coalesce(sc.prepared_by_name, 'Scheduler')::text AS b_archived_by,
            'Archived schedule'::text                     AS b_archive_reason,
            sc.section::text                              AS b_section
        FROM public.schedule sc
        LEFT JOIN public.program p ON sc.program_id = p.id
        LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
        LEFT JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.archive = true
          AND (p_program IS NULL OR nullif(trim(p_program), '') IS NULL OR trim(lower(coalesce(p.program_name, ''))) = trim(lower(p_program)))
          AND (p_semester IS NULL OR nullif(trim(p_semester), '') IS NULL OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)))
    )
    SELECT
        bd.b_id                                                           AS batch_id,
        (array_agg(bd.b_semester ORDER BY bd.b_archived_at DESC NULLS LAST))[1] AS semester,
        (array_agg(bd.b_program ORDER BY bd.b_archived_at DESC NULLS LAST))[1]  AS program,
        MAX(bd.b_archived_at)                                             AS archived_at,
        coalesce((array_agg(bd.b_archived_by ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Scheduler') AS archived_by,
        coalesce((array_agg(bd.b_archive_reason ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Archived schedule') AS archive_reason,
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

-- ------------------------------------------------------------------------------
-- 6. RPC: restore_archived_schedule_batch
-- ------------------------------------------------------------------------------
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
    -- Identify the batch scope via course join
    SELECT coalesce(c.semester, '1st Semester'), sc.program_id, p.program_name
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    LEFT JOIN public.program p ON sc.program_id = p.id
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
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
    UPDATE public.schedule sc
    SET archive = true,
        archive_batch_id = v_new_archive_batch,
        archived_at = clock_timestamp()
    FROM public.professor_load pl
    JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = false
      AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id)
      AND (trim(lower(c.semester)) = trim(lower(v_target_semester)));

    GET DIAGNOSTICS v_archived_current = ROW_COUNT;

    -- 2. Reactivate the specified batch (archive = false)
    IF p_batch_id = 'legacy' THEN
        UPDATE public.schedule sc
        SET archive = false
        FROM public.professor_load pl
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.archive = true
          AND sc.archive_batch_id IS NULL
          AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id)
          AND (trim(lower(c.semester)) = trim(lower(v_target_semester)));
    ELSE
        UPDATE public.schedule
        SET archive = false
        WHERE archive = true
          AND archive_batch_id::text = p_batch_id;
    END IF;

    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',                 true,
        'restored_batch_id',       p_batch_id,
        'restored_count',          v_restored,
        'semester',                v_target_semester,
        'program',                 v_target_program,
        'previous_archived_count', v_archived_current
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
