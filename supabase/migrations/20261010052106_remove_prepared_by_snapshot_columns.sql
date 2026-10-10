BEGIN;

DROP VIEW IF EXISTS public.schedule_with_semester;

DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text);

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
        SELECT id
        INTO v_target_program_id
        FROM public.program
        WHERE upper(trim(program_name)) = upper(trim(p_program))
        LIMIT 1;
    END IF;

    IF v_target_program_id IS NULL THEN
        SELECT id
        INTO v_target_program_id
        FROM public.program
        WHERE upper(program_name) = 'BSIT'
        LIMIT 1;
    END IF;

    IF v_prep_user_id IS NULL AND auth.uid() IS NOT NULL THEN
        v_prep_user_id := auth.uid();
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
          AND trim(lower(c.semester)) = trim(lower(p_semester))
          AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id);
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, archive, prepared_by_user_id
    )
    SELECT coalesce(
               nullif(x->>'program_id', '')::integer,
               (SELECT p.id
                FROM public.program p
                WHERE upper(trim(p.program_name)) = upper(trim(x->>'program'))
                LIMIT 1),
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
           coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id)
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
            coalesce(sc.archive_batch_id::text, 'legacy') AS b_id,
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
        LEFT JOIN public.program p ON p.id = sc.program_id
        LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
        LEFT JOIN public.course c ON c.course_id = pl.course_id
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

ALTER TABLE public.schedule
    DROP COLUMN IF EXISTS prepared_by_name,
    DROP COLUMN IF EXISTS prepared_by_title;

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
    sc.program_id,
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
    c.course_code,
    c.year_level,
    pl.professor_name,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.program p ON p.id = sc.program_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;