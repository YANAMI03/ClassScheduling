-- ==============================================================================
-- Migration: Create schedule_archive table and transactional archive/restore RPCs
-- ==============================================================================

-- 1. Create schedule_archive table
CREATE TABLE IF NOT EXISTS public.schedule_archive (
    archive_id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL DEFAULT gen_random_uuid(),
    original_schedule_id INTEGER,
    course_id INTEGER REFERENCES public.course(course_id) ON DELETE SET NULL,
    prof_id INTEGER REFERENCES public.professor(prof_id) ON DELETE SET NULL,
    room_id INTEGER REFERENCES public.room(room_id) ON DELETE SET NULL,
    day VARCHAR(50) NOT NULL,
    class_start TIME WITHOUT TIME ZONE NOT NULL,
    class_end TIME WITHOUT TIME ZONE NOT NULL,
    session_type VARCHAR(50) DEFAULT 'Lecture',
    section VARCHAR(50) NOT NULL,
    semester VARCHAR(50) NOT NULL,
    major VARCHAR(100),
    program VARCHAR(100),
    archived_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    archived_by VARCHAR(150),
    archive_reason VARCHAR(255) DEFAULT 'Schedule confirmed and replaced'
);

-- 2. Indexes for fast retrieval
CREATE INDEX IF NOT EXISTS idx_schedule_archive_batch_id ON public.schedule_archive(batch_id);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_scope ON public.schedule_archive(semester, program);
CREATE INDEX IF NOT EXISTS idx_schedule_archive_archived_at ON public.schedule_archive(archived_at DESC);

-- 3. Enable RLS and create security policies
ALTER TABLE public.schedule_archive ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "schedule_archive_authenticated_all" ON public.schedule_archive;
CREATE POLICY "schedule_archive_authenticated_all"
ON public.schedule_archive
FOR ALL
TO authenticated
USING (true)
WITH CHECK (true);

-- 4. Transactional Schedule Confirmation Function (Archive -> Delete -> Insert)
CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text DEFAULT NULL,
    p_rows jsonb DEFAULT '[]'::jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id UUID := gen_random_uuid();
    archived_count integer := 0;
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
    -- 1. ARCHIVE AND CLEAR ALL PREVIOUS ACTIVE SCHEDULE RECORDS FOR THE PROGRAM
    -- ==========================================================================
    IF p_clear_scope THEN
        IF prog_clean <> '' AND lower(prog_clean) NOT IN ('global / all programs', 'all', 'all programs', 'null') THEN
            -- Archive all existing records for this program into schedule_archive
            INSERT INTO public.schedule_archive (
                batch_id,
                original_schedule_id,
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
                program,
                archived_at,
                archived_by,
                archive_reason
            )
            SELECT
                v_batch_id,
                schedule_id,
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
                program,
                clock_timestamp(),
                COALESCE(p_archived_by, 'Scheduler'),
                'Replaced on confirmation of ' || sem_clean
            FROM public.schedule
            WHERE (program = prog_clean OR program IS NULL OR trim(program) = '');

            GET DIAGNOSTICS archived_count = ROW_COUNT;

            -- Delete linked irregular student schedules for target program
            DELETE FROM public.irregular_student_schedule
            WHERE schedule_id IN (
                SELECT schedule_id FROM public.schedule
                WHERE (program = prog_clean OR program IS NULL OR trim(program) = '')
            );

            -- Delete active schedule records for target program
            WITH del AS (
                DELETE FROM public.schedule
                WHERE (program = prog_clean OR program IS NULL OR trim(program) = '')
                RETURNING schedule_id
            )
            SELECT count(*) INTO deleted_count FROM del;
        ELSE
            -- Global scope archive
            INSERT INTO public.schedule_archive (
                batch_id,
                original_schedule_id,
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
                program,
                archived_at,
                archived_by,
                archive_reason
            )
            SELECT
                v_batch_id,
                schedule_id,
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
                program,
                clock_timestamp(),
                COALESCE(p_archived_by, 'Scheduler'),
                'Replaced on schedule confirmation'
            FROM public.schedule
            WHERE semester = ANY(sem_aliases);

            GET DIAGNOSTICS archived_count = ROW_COUNT;

            -- Delete linked irregular student schedules
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
        'batch_id', v_batch_id,
        'archived_count', archived_count,
        'deleted_count', deleted_count,
        'inserted_count', inserted_count,
        'semester', sem_clean,
        'program', prog_clean
    );
END;
$$;

-- 5. Transactional Archive Restore Function (Archive Current -> Delete Current -> Restore from Archive)
CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id UUID,
    p_restored_by text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_new_archive_batch_id UUID := gen_random_uuid();
    v_semester text;
    v_program text;
    sem_aliases text[];
    archived_current_count integer := 0;
    deleted_current_count integer := 0;
    restored_count integer := 0;
BEGIN
    -- Check that archived batch exists and get scope
    SELECT semester, program
    INTO v_semester, v_program
    FROM public.schedule_archive
    WHERE batch_id = p_batch_id
    LIMIT 1;

    IF v_semester IS NULL THEN
        RAISE EXCEPTION 'Archived schedule batch % not found.', p_batch_id;
    END IF;

    -- Normalize semester aliases
    IF v_semester ILIKE '1st%' OR v_semester = '1' THEN
        sem_aliases := ARRAY['1st Semester', '1st', '1'];
    ELSIF v_semester ILIKE '2nd%' OR v_semester = '2' THEN
        sem_aliases := ARRAY['2nd Semester', '2nd', '2'];
    ELSE
        sem_aliases := ARRAY[v_semester];
    END IF;

    -- 1. Archive current active schedule before overwriting
    IF v_program IS NOT NULL AND trim(v_program) <> '' AND lower(v_program) NOT IN ('global / all programs', 'all', 'all programs', 'null') THEN
        INSERT INTO public.schedule_archive (
            batch_id,
            original_schedule_id,
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
            program,
            archived_at,
            archived_by,
            archive_reason
        )
        SELECT
            v_new_archive_batch_id,
            schedule_id,
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
            program,
            clock_timestamp(),
            COALESCE(p_restored_by, 'Scheduler'),
            'Archived prior to restoring batch ' || p_batch_id::text
        FROM public.schedule
        WHERE semester = ANY(sem_aliases)
          AND (program = v_program OR program IS NULL OR trim(program) = '');

        GET DIAGNOSTICS archived_current_count = ROW_COUNT;

        -- Clean up linked irregular student schedules
        DELETE FROM public.irregular_student_schedule
        WHERE schedule_id IN (
            SELECT schedule_id FROM public.schedule
            WHERE semester = ANY(sem_aliases)
              AND (program = v_program OR program IS NULL OR trim(program) = '')
        );

        -- Delete current active schedule
        DELETE FROM public.schedule
        WHERE semester = ANY(sem_aliases)
          AND (program = v_program OR program IS NULL OR trim(program) = '');

        GET DIAGNOSTICS deleted_current_count = ROW_COUNT;
    ELSE
        INSERT INTO public.schedule_archive (
            batch_id,
            original_schedule_id,
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
            program,
            archived_at,
            archived_by,
            archive_reason
        )
        SELECT
            v_new_archive_batch_id,
            schedule_id,
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
            program,
            clock_timestamp(),
            COALESCE(p_restored_by, 'Scheduler'),
            'Archived prior to restoring batch ' || p_batch_id::text
        FROM public.schedule
        WHERE semester = ANY(sem_aliases);

        GET DIAGNOSTICS archived_current_count = ROW_COUNT;

        -- Clean up linked irregular student schedules
        DELETE FROM public.irregular_student_schedule
        WHERE schedule_id IN (
            SELECT schedule_id FROM public.schedule
            WHERE semester = ANY(sem_aliases)
        );

        -- Delete current active schedule
        DELETE FROM public.schedule
        WHERE semester = ANY(sem_aliases);

        GET DIAGNOSTICS deleted_current_count = ROW_COUNT;
    END IF;

    -- 2. Restore records from schedule_archive into public.schedule
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
    FROM public.schedule_archive
    WHERE batch_id = p_batch_id;

    GET DIAGNOSTICS restored_count = ROW_COUNT;

    -- Reset sequence
    PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;

    RETURN jsonb_build_object(
        'success', true,
        'restored_batch_id', p_batch_id,
        'archived_current_batch_id', v_new_archive_batch_id,
        'archived_current_count', archived_current_count,
        'restored_count', restored_count,
        'semester', v_semester,
        'program', v_program
    );
END;
$$;

-- 6. Permissions management
REVOKE ALL ON TABLE public.schedule_archive FROM PUBLIC;
REVOKE ALL ON TABLE public.schedule_archive FROM anon;
GRANT ALL ON TABLE public.schedule_archive TO authenticated;
GRANT ALL ON TABLE public.schedule_archive TO service_role;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) FROM anon;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO service_role;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(UUID, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(UUID, text) FROM anon;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(UUID, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(UUID, text) TO service_role;
