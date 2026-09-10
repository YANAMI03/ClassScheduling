-- ==============================================================================
-- Migration: Replace prof_id and course_id in schedule with prof_course_id
-- Date: 2026-09-10
--
-- 1. Adds prof_course_id to public.schedule and public.schedule_archive
-- 2. Migrates existing schedule records to reference prof_course(prof_course_id)
-- 3. Adds UNIQUE(prof_id, course_id) constraint to prof_course
-- 4. Enforces Foreign Key constraint on public.schedule(prof_course_id)
-- 5. Drops deprecated prof_id and course_id columns from public.schedule
-- 6. Updates confirm_schedule_transaction and restore_schedule_archive RPCs
-- ==============================================================================

DO $$
BEGIN
    -- 1. Ensure any missing (prof_id, course_id) pairs in schedule exist in prof_course
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'prof_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'course_id'
    ) THEN
        INSERT INTO public.prof_course (prof_id, course_id)
        SELECT DISTINCT s.prof_id, s.course_id
        FROM public.schedule s
        WHERE s.prof_id IS NOT NULL AND s.course_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM public.prof_course pc 
              WHERE pc.prof_id = s.prof_id AND pc.course_id = s.course_id
          )
        ON CONFLICT DO NOTHING;
    END IF;

    -- 2. Add prof_course_id column to public.schedule if not present
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'prof_course_id'
    ) THEN
        ALTER TABLE public.schedule ADD COLUMN prof_course_id INTEGER;
    END IF;

    -- 3. Migrate existing schedule rows to populate prof_course_id
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'prof_id'
    ) AND EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'course_id'
    ) THEN
        UPDATE public.schedule s
        SET prof_course_id = pc.prof_course_id
        FROM public.prof_course pc
        WHERE s.prof_id = pc.prof_id 
          AND s.course_id = pc.course_id
          AND s.prof_course_id IS NULL;
    END IF;

    -- 4. Add UNIQUE constraint to public.prof_course(prof_id, course_id)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'uq_prof_course_pair'
    ) THEN
        -- Remove any accidental duplicates before adding constraint
        DELETE FROM public.prof_course a
        USING public.prof_course b
        WHERE a.prof_course_id < b.prof_course_id
          AND a.prof_id = b.prof_id
          AND a.course_id = b.course_id;

        ALTER TABLE public.prof_course 
        ADD CONSTRAINT uq_prof_course_pair UNIQUE (prof_id, course_id);
    END IF;

    -- 5. Add Foreign Key constraint to public.schedule(prof_course_id)
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'fk_schedule_prof_course'
    ) THEN
        ALTER TABLE public.schedule
        ADD CONSTRAINT fk_schedule_prof_course 
        FOREIGN KEY (prof_course_id) 
        REFERENCES public.prof_course(prof_course_id) 
        ON DELETE CASCADE;
    END IF;

    -- 6. Add prof_course_id to public.schedule_archive if table exists
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'schedule_archive'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'schedule_archive' AND column_name = 'prof_course_id'
        ) THEN
            ALTER TABLE public.schedule_archive ADD COLUMN prof_course_id INTEGER;
        END IF;

        IF EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'schedule_archive' AND column_name = 'prof_id'
        ) AND EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_schema = 'public' AND table_name = 'schedule_archive' AND column_name = 'course_id'
        ) THEN
            UPDATE public.schedule_archive sa
            SET prof_course_id = pc.prof_course_id
            FROM public.prof_course pc
            WHERE sa.prof_id = pc.prof_id 
              AND sa.course_id = pc.course_id
              AND sa.prof_course_id IS NULL;
        END IF;
    END IF;

    -- 7. Drop obsolete prof_id and course_id columns from public.schedule
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'prof_id'
    ) THEN
        ALTER TABLE public.schedule DROP COLUMN prof_id CASCADE;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'schedule' AND column_name = 'course_id'
    ) THEN
        ALTER TABLE public.schedule DROP COLUMN course_id CASCADE;
    END IF;

    -- Indexes on prof_course_id for query performance
    CREATE INDEX IF NOT EXISTS idx_schedule_prof_course_id ON public.schedule(prof_course_id);
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schedule_archive') THEN
        CREATE INDEX IF NOT EXISTS idx_schedule_archive_prof_course_id ON public.schedule_archive(prof_course_id);
    END IF;
END $$;

-- ==============================================================================
-- 8. Updated confirm_schedule_transaction RPC Function
-- ==============================================================================
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean);
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

    -- Archive and clear previous active records
    IF p_clear_scope THEN
        IF prog_clean <> '' AND lower(prog_clean) NOT IN ('global / all programs', 'all', 'all programs', 'null') THEN
            -- Archive
            INSERT INTO public.schedule_archive (
                batch_id,
                original_schedule_id,
                prof_course_id,
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
                prof_course_id,
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

            -- Delete linked irregular student schedules
            DELETE FROM public.irregular_student_schedule
            WHERE schedule_id IN (
                SELECT schedule_id FROM public.schedule
                WHERE (program = prog_clean OR program IS NULL OR trim(program) = '')
            );

            -- Delete active schedule records
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
                prof_course_id,
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
                prof_course_id,
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

    -- Insert new schedule records using prof_course_id
    IF p_rows IS NOT NULL AND jsonb_typeof(p_rows) = 'array' AND jsonb_array_length(p_rows) > 0 THEN
        INSERT INTO public.schedule (
            prof_course_id,
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
            NULLIF(trim(x->>'prof_course_id'), '')::integer,
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

    -- Reset sequence
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

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO service_role;

-- ==============================================================================
-- 9. Updated restore_archived_schedule_batch RPC Function
-- ==============================================================================
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
    SELECT semester, program
    INTO v_semester, v_program
    FROM public.schedule_archive
    WHERE batch_id = p_batch_id
    LIMIT 1;

    IF v_semester IS NULL THEN
        RAISE EXCEPTION 'Archived schedule batch % not found.', p_batch_id;
    END IF;

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
            prof_course_id,
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
            prof_course_id,
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

        DELETE FROM public.irregular_student_schedule
        WHERE schedule_id IN (
            SELECT schedule_id FROM public.schedule
            WHERE semester = ANY(sem_aliases)
              AND (program = v_program OR program IS NULL OR trim(program) = '')
        );

        DELETE FROM public.schedule
        WHERE semester = ANY(sem_aliases)
          AND (program = v_program OR program IS NULL OR trim(program) = '');

        GET DIAGNOSTICS deleted_current_count = ROW_COUNT;
    ELSE
        INSERT INTO public.schedule_archive (
            batch_id,
            original_schedule_id,
            prof_course_id,
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
            prof_course_id,
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

        DELETE FROM public.irregular_student_schedule
        WHERE schedule_id IN (
            SELECT schedule_id FROM public.schedule
            WHERE semester = ANY(sem_aliases)
        );

        DELETE FROM public.schedule
        WHERE semester = ANY(sem_aliases);

        GET DIAGNOSTICS deleted_current_count = ROW_COUNT;
    END IF;

    -- 2. Restore records from schedule_archive into public.schedule using prof_course_id
    INSERT INTO public.schedule (
        prof_course_id,
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
        COALESCE(
            sa.prof_course_id,
            (SELECT pc.prof_course_id FROM public.prof_course pc 
             WHERE pc.prof_id = sa.prof_id AND pc.course_id = sa.course_id LIMIT 1)
        ),
        sa.room_id,
        sa.day,
        sa.class_start,
        sa.class_end,
        sa.session_type,
        sa.section,
        sa.semester,
        sa.major,
        sa.program
    FROM public.schedule_archive sa
    WHERE sa.batch_id = p_batch_id;

    GET DIAGNOSTICS restored_count = ROW_COUNT;

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

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(UUID, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(UUID, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(UUID, text) TO service_role;

