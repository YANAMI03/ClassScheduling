-- Rename the professor/course assignment table and make schedule references
-- point to professor_load.id.

DO $$
BEGIN
    IF to_regclass('public.prof_course') IS NOT NULL
       AND to_regclass('public.professor_load') IS NULL THEN
        ALTER TABLE public.prof_course RENAME TO professor_load;
    END IF;

    IF to_regclass('public.professor_load') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'professor_load'
             AND column_name = 'prof_course_id'
       )
       AND NOT EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'professor_load'
             AND column_name = 'id'
       ) THEN
        ALTER TABLE public.professor_load RENAME COLUMN prof_course_id TO id;
    END IF;

    IF to_regclass('public.professor_load') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conrelid = 'public.professor_load'::regclass
             AND contype = 'p'
       ) THEN
        ALTER TABLE public.professor_load ADD PRIMARY KEY (id);
    END IF;

    IF to_regclass('public.professor_load') IS NOT NULL THEN
        ALTER TABLE public.professor_load
            DROP COLUMN IF EXISTS professor_load_id;
    END IF;

    IF to_regclass('public.schedule') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'schedule'
             AND column_name = 'prof_course_id'
       )
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'schedule'
             AND column_name = 'professor_load_id'
       ) THEN
        ALTER TABLE public.schedule RENAME COLUMN prof_course_id TO professor_load_id;
    END IF;

    IF to_regclass('public.schedule_archive') IS NOT NULL
       AND EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'schedule_archive'
             AND column_name = 'prof_course_id'
       )
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'schedule_archive'
             AND column_name = 'professor_load_id'
       ) THEN
        ALTER TABLE public.schedule_archive RENAME COLUMN prof_course_id TO professor_load_id;
    END IF;
END $$;

ALTER TABLE public.schedule DROP CONSTRAINT IF EXISTS fk_schedule_prof_course;
ALTER TABLE public.schedule DROP CONSTRAINT IF EXISTS schedule_prof_course_id_fkey;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_schedule_professor_load'
    ) THEN
        ALTER TABLE public.schedule
            ADD CONSTRAINT fk_schedule_professor_load
            FOREIGN KEY (professor_load_id)
            REFERENCES public.professor_load(id)
            ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_schedule_professor_load_id
    ON public.schedule(professor_load_id);

DO $$
BEGIN
    IF to_regclass('public.schedule_archive') IS NOT NULL THEN
        ALTER TABLE public.schedule_archive
            DROP CONSTRAINT IF EXISTS schedule_archive_prof_course_id_fkey;
        ALTER TABLE public.schedule_archive
            DROP CONSTRAINT IF EXISTS fk_schedule_archive_prof_course;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_schedule_archive_professor_load'
        ) THEN
            ALTER TABLE public.schedule_archive
                ADD CONSTRAINT fk_schedule_archive_professor_load
                FOREIGN KEY (professor_load_id)
                REFERENCES public.professor_load(id)
                ON DELETE SET NULL;
        END IF;
        CREATE INDEX IF NOT EXISTS idx_schedule_archive_professor_load_id
            ON public.schedule_archive(professor_load_id);
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
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
    v_batch_id uuid := gen_random_uuid();
    v_archived integer := 0;
    v_deleted integer := 0;
    v_inserted integer := 0;
BEGIN
    IF nullif(trim(coalesce(p_semester, '')), '') IS NULL THEN
        RAISE EXCEPTION 'Semester is required to confirm schedule.';
    END IF;

    IF p_clear_scope THEN
        INSERT INTO public.schedule_archive (
            batch_id, original_schedule_id, professor_load_id, room_id, day,
            class_start, class_end, session_type, section, semester, major,
            program, archived_by, archive_reason
        )
        SELECT v_batch_id, s.schedule_id, s.professor_load_id, s.room_id, s.day,
               s.class_start, s.class_end, s.session_type, s.section, s.semester,
               s.major, s.program, coalesce(p_archived_by, 'Scheduler'),
               'Replaced on schedule confirmation'
        FROM public.schedule AS s
        WHERE s.semester = p_semester
          AND (nullif(trim(coalesce(p_program, '')), '') IS NULL
               OR s.program = p_program OR s.program IS NULL OR trim(s.program) = '');
        GET DIAGNOSTICS v_archived = ROW_COUNT;

        DELETE FROM public.schedule AS s
        WHERE s.semester = p_semester
          AND (nullif(trim(coalesce(p_program, '')), '') IS NULL
               OR s.program = p_program OR s.program IS NULL OR trim(s.program) = '');
        GET DIAGNOSTICS v_deleted = ROW_COUNT;
    END IF;

    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end, session_type,
        section, semester, major, program
    )
    SELECT nullif(x->>'professor_load_id', '')::integer,
           nullif(x->>'room_id', '')::integer,
           coalesce(x->>'day', 'Monday'),
           (x->>'class_start')::time, (x->>'class_end')::time,
           coalesce(nullif(x->>'session_type', ''), 'Lecture'),
           x->>'section', coalesce(nullif(x->>'semester', ''), p_semester),
           nullif(x->>'major', ''), nullif(x->>'program', '')
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', v_batch_id,
        'archived_count', v_archived,
        'deleted_count', v_deleted,
        'inserted_count', v_inserted
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text) TO service_role;

DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(uuid, text);
CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id uuid,
    p_restored_by text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_restored integer := 0;
BEGIN
    INSERT INTO public.schedule (
        professor_load_id, room_id, day, class_start, class_end, session_type,
        section, semester, major, program
    )
    SELECT professor_load_id, room_id, day, class_start, class_end,
           coalesce(session_type, 'Lecture'), section, semester, major, program
    FROM public.schedule_archive
    WHERE batch_id = p_batch_id;
    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'restored_batch_id', p_batch_id,
        'restored_count', v_restored,
        'restored_by', coalesce(p_restored_by, 'Scheduler')
    );
END;
$$;

GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(uuid, text) TO authenticated;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(uuid, text) TO service_role;