-- Apply after 20261010052106_remove_prepared_by_snapshot_columns.sql if that
-- separate migration is being used. This migration does not alter preparer fields.
BEGIN;

-- Remove database objects that directly depend on schedule.program_id before
-- dropping the column. The active view/RPC/policy definitions are recreated below.
DROP VIEW IF EXISTS public.schedule_with_semester;
DO $$
DECLARE
    v_policy record;
BEGIN
    FOR v_policy IN
        SELECT policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'schedule'
    LOOP
        EXECUTE format('DROP POLICY %I ON public.schedule', v_policy.policyname);
    END LOOP;
END;
$$;

DROP FUNCTION IF EXISTS public.archive_active_schedule(text, integer, text);
DROP FUNCTION IF EXISTS public.archive_active_schedule(integer, text, text, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text);
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text);
DROP FUNCTION IF EXISTS public.get_schedule_archive_batches(text, text, timestamptz, timestamptz);
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(uuid, text);
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(text, text);

DO $$
DECLARE
    v_constraint record;
    v_index record;
    v_program_attnum smallint;
BEGIN
    SELECT attnum INTO v_program_attnum
    FROM pg_attribute
    WHERE attrelid = 'public.schedule'::regclass
      AND attname = 'program_id'
      AND NOT attisdropped;

    IF v_program_attnum IS NULL THEN
        RAISE EXCEPTION 'public.schedule.program_id was not found';
    END IF;

    FOR v_constraint IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.schedule'::regclass
          AND v_program_attnum = ANY(conkey)
    LOOP
        EXECUTE format('ALTER TABLE public.schedule DROP CONSTRAINT %I', v_constraint.conname);
    END LOOP;

    FOR v_index IN
        SELECT index_class.relname
        FROM pg_index i
        JOIN pg_class index_class ON index_class.oid = i.indexrelid
        WHERE i.indrelid = 'public.schedule'::regclass
          AND v_program_attnum = ANY(i.indkey)
    LOOP
        EXECUTE format('DROP INDEX %I.%I', 'public', v_index.relname);
    END LOOP;
END;
$$;

ALTER TABLE public.schedule DROP COLUMN program_id;

-- Preserve the established API shape: program_id in this view is now derived
-- from the schedule row's one linked professor_load/course.
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
    sc.archived_at,
    sc.archive_batch_id,
    sc.prepared_by_user_id,
    CASE
        WHEN u.id IS NULL THEN 'Preparer account unavailable'
        ELSE coalesce(
            nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
            nullif(trim(u.username), ''),
            'Unknown user'
        )
    END AS preparer_name,
    CASE
        WHEN u.id IS NULL THEN 'Role/title unavailable'
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('super_admin', 'superadmin')
            THEN 'Super Admin - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('admin', 'administrator', 'academic_admin')
            THEN 'Academic Admin - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('scheduler', 'dean', 'chair', 'dean/chair', 'program_chair', 'department_head')
            THEN 'Program Scheduler - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN nullif(trim(u.role), '') IS NOT NULL
            THEN initcap(replace(u.role, '_', ' ')) || ' - ' || coalesce(p.program_name, 'Unknown Program')
        ELSE 'Schedule Preparer - ' || coalesce(p.program_name, 'Unknown Program')
    END AS preparer_title,
    coalesce(c.semester, '1st Semester') AS semester,
    c.specialization,
    c.specialization AS major,
    pl.course_id,
    c.program_id,
    c.course_code,
    c.year_level,
    pl.professor_name,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.program p ON p.id = c.program_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

CREATE INDEX IF NOT EXISTS idx_schedule_prof_load_id ON public.schedule (professor_load_id);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_batch ON public.schedule (archive, archive_batch_id) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_schedule_archived_at ON public.schedule (archived_at DESC) WHERE archive = true;
CREATE INDEX IF NOT EXISTS idx_prof_load_course_id ON public.professor_load (course_id);

CREATE OR REPLACE FUNCTION public.archive_active_schedule(
    p_program_id integer DEFAULT NULL,
    p_semester text DEFAULT NULL,
    p_archived_by text DEFAULT 'Scheduler',
    p_reason text DEFAULT 'Manually archived'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id uuid := gen_random_uuid();
    v_now timestamptz := clock_timestamp();
    v_count integer := 0;
BEGIN
    UPDATE public.schedule sc
    SET archive = true,
        archive_batch_id = v_batch_id,
        archived_at = v_now
    FROM public.professor_load pl
    JOIN public.course c ON c.course_id = pl.course_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = false
      AND (p_program_id IS NULL OR c.program_id = p_program_id)
      AND (p_semester IS NULL OR trim(p_semester) = ''
           OR trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester)));

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', v_batch_id,
        'archived_at', v_now,
        'archived_count', v_count,
        'program_id', p_program_id,
        'semester', p_semester
    );
END;
$$;

REVOKE ALL ON FUNCTION public.archive_active_schedule(integer, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO anon, authenticated, service_role;

CREATE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text,
    p_rows jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT 'Scheduler',
    p_program_id integer DEFAULT NULL,
    p_batch_id uuid DEFAULT NULL,
    p_prepared_by_user_id uuid DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id uuid := coalesce(p_batch_id, gen_random_uuid());
    v_archived integer := 0;
    v_inserted integer := 0;
    v_target_program_id integer := p_program_id;
    v_prep_user_id uuid := p_prepared_by_user_id;
BEGIN
    IF p_semester IS NULL OR trim(p_semester) = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    IF v_target_program_id IS NULL AND p_program IS NOT NULL AND trim(p_program) <> '' THEN
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

    IF v_target_program_id IS NULL THEN
        RAISE EXCEPTION 'A valid program is required to confirm schedule.';
    END IF;

    IF v_prep_user_id IS NULL AND auth.uid() IS NOT NULL THEN
        v_prep_user_id := auth.uid();
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x
        LEFT JOIN public.professor_load pl
            ON pl.id = nullif(x->>'professor_load_id', '')::bigint
        LEFT JOIN public.course c ON c.course_id = pl.course_id
        WHERE pl.id IS NULL
           OR c.course_id IS NULL
           OR c.program_id IS DISTINCT FROM v_target_program_id
           OR trim(lower(coalesce(c.semester, '1st Semester'))) <> trim(lower(p_semester))
    ) THEN
        RAISE EXCEPTION 'Schedule rows must reference professor loads from the selected program and semester.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x
        LEFT JOIN public.room r ON r.room_id = nullif(x->>'room_id', '')::bigint
        WHERE nullif(x->>'room_id', '') IS NOT NULL
          AND r.room_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Schedule rows must reference valid rooms.';
    END IF;

    IF p_clear_scope THEN
        UPDATE public.schedule sc
        SET archive = true,
            archive_batch_id = v_batch_id,
            archived_at = clock_timestamp()
        FROM public.professor_load pl
        JOIN public.course c ON c.course_id = pl.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.archive = false
          AND c.program_id = v_target_program_id
          AND trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(p_semester));
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive, prepared_by_user_id
    )
    SELECT
        pl.id,
        nullif(x->>'room_id', '')::bigint,
        coalesce(x->>'day', 'Monday'),
        (x->>'class_start')::time,
        (x->>'class_end')::time,
        coalesce(nullif(x->>'session_type', ''), 'Lecture'),
        x->>'section',
        false,
        coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id)
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x
    JOIN public.professor_load pl
      ON pl.id = nullif(x->>'professor_load_id', '')::bigint;
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

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid) TO anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.get_schedule_archive_batches(
    p_program text DEFAULT NULL,
    p_semester text DEFAULT NULL,
    p_date_from timestamptz DEFAULT NULL,
    p_date_to timestamptz DEFAULT NULL
)
RETURNS TABLE (
    batch_id text,
    semester text,
    program text,
    archived_at timestamptz,
    archived_by text,
    archive_reason text,
    entry_count bigint,
    section_count bigint,
    sections text[]
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
            coalesce(p.program_name, '')::text AS b_program,
            sc.archived_at AS b_archived_at,
            coalesce(
                nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                nullif(trim(u.username), ''),
                'Preparer account unavailable'
            )::text AS b_archived_by,
            'Archived schedule'::text AS b_archive_reason,
            sc.section::text AS b_section
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
    )
    SELECT
        bd.b_id,
        (array_agg(bd.b_semester ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        (array_agg(bd.b_program ORDER BY bd.b_archived_at DESC NULLS LAST))[1],
        max(bd.b_archived_at),
        coalesce((array_agg(bd.b_archived_by ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Preparer account unavailable'),
        coalesce((array_agg(bd.b_archive_reason ORDER BY bd.b_archived_at DESC NULLS LAST))[1], 'Archived schedule'),
        count(*),
        count(DISTINCT bd.b_section),
        array_agg(DISTINCT bd.b_section ORDER BY bd.b_section)
    FROM batch_data bd
    WHERE (p_date_from IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at >= p_date_from))
      AND (p_date_to IS NULL OR (bd.b_archived_at IS NOT NULL AND bd.b_archived_at <= p_date_to))
    GROUP BY bd.b_id
    ORDER BY max(bd.b_archived_at) DESC NULLS LAST;
END;
$$;

REVOKE ALL ON FUNCTION public.get_schedule_archive_batches(text, text, timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_schedule_archive_batches(text, text, timestamptz, timestamptz) TO anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id text,
    p_restored_by text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_target_semester text;
    v_target_program_id integer;
    v_target_program text;
    v_restored integer := 0;
    v_archived_current integer := 0;
    v_new_archive_batch uuid := gen_random_uuid();
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.schedule sc
        LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
        LEFT JOIN public.course c ON c.course_id = pl.course_id
        LEFT JOIN public.program p ON p.id = c.program_id
        WHERE sc.archive = true
          AND (
              (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL)
              OR (
                  position('__' in p_batch_id) > 0
                  AND sc.archive_batch_id IS NULL
                  AND replace(coalesce(c.semester, '1st Semester'), ' ', '_') = split_part(p_batch_id, '__', 1)
                  AND coalesce(p.program_name, c.program_id::text, 'unknown') = split_part(p_batch_id, '__', 2)
              )
              OR (position('__' in p_batch_id) = 0 AND sc.archive_batch_id::text = p_batch_id)
          )
          AND (c.course_id IS NULL OR c.program_id IS NULL)
    ) THEN
        RAISE EXCEPTION 'Archived batch % contains rows with no resolvable linked course/program.', p_batch_id;
    END IF;

    SELECT coalesce(c.semester, '1st Semester'), c.program_id, p.program_name
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    JOIN public.professor_load pl ON pl.id = sc.professor_load_id
    JOIN public.course c ON c.course_id = pl.course_id
    LEFT JOIN public.program p ON p.id = c.program_id
    LEFT JOIN public.program p ON p.id = c.program_id
    WHERE sc.archive = true
      AND (
          (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL)
          OR (
              position('__' in p_batch_id) > 0
              AND sc.archive_batch_id IS NULL
              AND replace(coalesce(c.semester, '1st Semester'), ' ', '_') = split_part(p_batch_id, '__', 1)
              AND coalesce(p.program_name, c.program_id::text, 'unknown') = split_part(p_batch_id, '__', 2)
          )
          OR (position('__' in p_batch_id) = 0 AND sc.archive_batch_id::text = p_batch_id)
      )
    LIMIT 1;

    IF v_target_semester IS NULL OR v_target_program_id IS NULL THEN
        RAISE EXCEPTION 'Archived batch % has no resolvable program and semester.', p_batch_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.schedule sc
        JOIN public.professor_load pl ON pl.id = sc.professor_load_id
        JOIN public.course c ON c.course_id = pl.course_id
        WHERE sc.archive = true
          AND (
              (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL)
              OR (
                  position('__' in p_batch_id) > 0
                  AND sc.archive_batch_id IS NULL
                  AND replace(coalesce(c.semester, '1st Semester'), ' ', '_') = split_part(p_batch_id, '__', 1)
                  AND coalesce(p.program_name, c.program_id::text, 'unknown') = split_part(p_batch_id, '__', 2)
              )
              OR (position('__' in p_batch_id) = 0 AND sc.archive_batch_id::text = p_batch_id)
          )
          AND (c.program_id <> v_target_program_id
               OR trim(lower(coalesce(c.semester, '1st Semester'))) <> trim(lower(v_target_semester)))
    ) THEN
        RAISE EXCEPTION 'Archived batch % contains multiple program/semester scopes.', p_batch_id;
    END IF;

    UPDATE public.schedule sc
    SET archive = true,
        archive_batch_id = v_new_archive_batch,
        archived_at = clock_timestamp()
    FROM public.professor_load pl
    JOIN public.course c ON c.course_id = pl.course_id
    LEFT JOIN public.program p ON p.id = c.program_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = false
      AND c.program_id = v_target_program_id
      AND trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(v_target_semester));
    GET DIAGNOSTICS v_archived_current = ROW_COUNT;

    UPDATE public.schedule sc
    SET archive = false
    FROM public.professor_load pl
    JOIN public.course c ON c.course_id = pl.course_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = true
      AND c.program_id = v_target_program_id
      AND trim(lower(coalesce(c.semester, '1st Semester'))) = trim(lower(v_target_semester))
      AND (
          (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL)
          OR (
              position('__' in p_batch_id) > 0
              AND sc.archive_batch_id IS NULL
              AND replace(coalesce(c.semester, '1st Semester'), ' ', '_') = split_part(p_batch_id, '__', 1)
              AND coalesce(p.program_name, c.program_id::text, 'unknown') = split_part(p_batch_id, '__', 2)
          )
          OR (position('__' in p_batch_id) = 0 AND sc.archive_batch_id::text = p_batch_id)
      );
    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'restored_batch_id', p_batch_id,
        'restored_count', v_restored,
        'semester', v_target_semester,
        'program', v_target_program,
        'previous_archived_count', v_archived_current
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;

ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

CREATE POLICY "schedule_scoped_all" ON public.schedule
    FOR SELECT TO authenticated
    USING (
        public.get_user_role() IN ('admin', 'super_admin')
        OR archive = false
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON c.course_id = pl.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND c.program_id = public.get_user_program_id()
            )
        )
    );

CREATE POLICY "schedule_insert" ON public.schedule
    FOR INSERT TO authenticated
    WITH CHECK (
        public.get_user_role() IN ('admin', 'super_admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON c.course_id = pl.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND c.program_id = public.get_user_program_id()
            )
        )
    );

CREATE POLICY "schedule_update" ON public.schedule
    FOR UPDATE TO authenticated
    USING (
        public.get_user_role() IN ('admin', 'super_admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON c.course_id = pl.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND c.program_id = public.get_user_program_id()
            )
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('admin', 'super_admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON c.course_id = pl.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND c.program_id = public.get_user_program_id()
            )
        )
    );

CREATE POLICY "schedule_delete" ON public.schedule
    FOR DELETE TO authenticated
    USING (public.get_user_role() IN ('admin', 'super_admin') AND archive = true);
NOTIFY pgrst, 'reload schema';

COMMIT;