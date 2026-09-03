-- ==============================================================================
-- Supabase Transactional Schedule Confirmation Function: confirm_schedule_transaction
-- Atomically deletes existing schedule entries within the target scope (semester/program)
-- and inserts the newly confirmed schedule records within a single PostgreSQL transaction.
-- Automatically rolls back if any error or constraint violation occurs.
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text DEFAULT NULL,
    p_rows jsonb DEFAULT '[]'::jsonb,
    p_clear_scope boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    deleted_count integer := 0;
    inserted_count integer := 0;
    sem_clean text;
    prog_clean text;
    sem_aliases text[];
BEGIN
    sem_clean := trim(COALESCE(p_semester, ''));
    prog_clean := trim(COALESCE(p_program, ''));

    IF sem_clean = '' THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    -- Normalize semester aliases
    IF sem_clean ILIKE '1st%' OR sem_clean = '1' THEN
        sem_aliases := ARRAY['1st Semester', '1st', '1'];
    ELSIF sem_clean ILIKE '2nd%' OR sem_clean = '2' THEN
        sem_aliases := ARRAY['2nd Semester', '2nd', '2'];
    ELSE
        sem_aliases := ARRAY[sem_clean];
    END IF;

    -- ==========================================================================
    -- 1. SCOPE-BASED DELETION
    -- ==========================================================================
    IF p_clear_scope THEN
        IF prog_clean <> '' AND lower(prog_clean) NOT IN ('global / all programs', 'all', 'all programs', 'null') THEN
            -- Delete linked irregular student schedules for the target scope
            DELETE FROM public.irregular_student_schedule
            WHERE schedule_id IN (
                SELECT schedule_id FROM public.schedule
                WHERE semester = ANY(sem_aliases)
                  AND (program = prog_clean OR program IS NULL OR trim(program) = '')
            );

            -- Delete all schedule records matching semester and program
            WITH del AS (
                DELETE FROM public.schedule
                WHERE semester = ANY(sem_aliases)
                  AND (program = prog_clean OR program IS NULL OR trim(program) = '')
                RETURNING schedule_id
            )
            SELECT count(*) INTO deleted_count FROM del;
        ELSE
            -- Global / All programs scope: delete all schedule records for the semester
            DELETE FROM public.irregular_student_schedule
            WHERE schedule_id IN (
                SELECT schedule_id FROM public.schedule
                WHERE semester = ANY(sem_aliases)
            );

            WITH del AS (
                DELETE FROM public.schedule
                WHERE semester = ANY(sem_aliases)
                RETURNING schedule_id
            )
            SELECT count(*) INTO deleted_count FROM del;
        END IF;
    END IF;

    -- ==========================================================================
    -- 2. INSERT NEW CONFIRMED SCHEDULE RECORDS
    -- ==========================================================================
    IF p_rows IS NOT NULL AND jsonb_typeof(p_rows) = 'array' AND jsonb_array_length(p_rows) > 0 THEN
        INSERT INTO public.schedule (
            course_id,
            prof_id,
            room_id,
            day,
            class_start,
            class_end,
            session_type,
            section,
            semester,
            major,
            program
        )
        SELECT
            NULLIF(trim(x->>'course_id'), '')::integer,
            NULLIF(trim(x->>'prof_id'), '')::integer,
            NULLIF(trim(x->>'room_id'), '')::integer,
            COALESCE(x->>'day', 'Monday'),
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            COALESCE(NULLIF(x->>'session_type', ''), 'Lecture'),
            x->>'section',
            COALESCE(NULLIF(x->>'semester', ''), sem_clean),
            NULLIF(x->>'major', ''),
            COALESCE(NULLIF(x->>'program', ''), NULLIF(prog_clean, ''))
        FROM jsonb_array_elements(p_rows) AS x;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
    END IF;

    -- Reset serial sequence to prevent duplicate key errors on future manual inserts
    PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;

    RETURN jsonb_build_object(
        'success', true,
        'deleted_count', deleted_count,
        'inserted_count', inserted_count,
        'semester', sem_clean,
        'program', prog_clean
    );
END;
$$;

-- Revoke default public execution & grant to authenticated and service_role
REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean) TO authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean) TO service_role;
