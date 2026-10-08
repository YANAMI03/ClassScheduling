-- ==============================================================================
-- Migration: Add prepared_by snapshot columns and update confirm RPC
-- File: migrations/20261007_schedule_prepared_by.sql
-- Description:
--   1. Adds prepared_by_user_id (UUID FK to users), prepared_by_name (TEXT),
--      and prepared_by_title (TEXT) to public.schedule.
--   2. Best-effort backfill of existing schedule rows from public.activity_log
--      confirm events.
--   3. Updates public.confirm_schedule_transaction RPC to accept and record
--      prepared_by snapshot fields on schedule rows.
-- ==============================================================================

BEGIN;

-- 1. Add snapshot columns to schedule if they do not exist
ALTER TABLE public.schedule 
    ADD COLUMN IF NOT EXISTS prepared_by_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL;

ALTER TABLE public.schedule 
    ADD COLUMN IF NOT EXISTS prepared_by_name text;

ALTER TABLE public.schedule 
    ADD COLUMN IF NOT EXISTS prepared_by_title text;

-- 2. Performance index on prepared_by_user_id
CREATE INDEX IF NOT EXISTS idx_schedule_prepared_by_user_id 
    ON public.schedule(prepared_by_user_id);

-- 3. Best-effort backfill for existing rows from activity_log
-- Backfill active schedules from the most recent confirm log matching semester and program
WITH active_confirms AS (
    SELECT DISTINCT ON (s.schedule_id)
        s.schedule_id,
        l.user_id,
        u.first_name,
        u.last_name,
        u.role,
        p.program_name
    FROM public.schedule s
    JOIN public.program p ON p.id = s.program_id
    JOIN public.activity_log l ON l.action = 'confirm'
        AND (l.target_detail ILIKE '%' || s.semester || '%' OR s.semester IS NULL)
    JOIN public.users u ON u.id = l.user_id
        AND (u.program_id = s.program_id OR u.program_id IS NULL)
    WHERE s.archive = false
    ORDER BY s.schedule_id, l.created_at DESC
)
UPDATE public.schedule s
SET prepared_by_user_id = ac.user_id,
    prepared_by_name = trim(upper(concat_ws(' ', nullif(trim(ac.first_name), ''), nullif(trim(ac.last_name), '')))),
    prepared_by_title = CASE
        WHEN lower(coalesce(ac.role, '')) IN ('super_admin', 'superadmin') THEN 'Super Admin - ' || ac.program_name
        WHEN lower(coalesce(ac.role, '')) IN ('admin', 'administrator', 'academic_admin') THEN 'Academic Admin - ' || ac.program_name
        ELSE 'Program Scheduler - ' || ac.program_name
    END
FROM active_confirms ac
WHERE s.schedule_id = ac.schedule_id
  AND s.prepared_by_name IS NULL;

-- Backfill archived schedules matching confirm logs within 5 minutes of archived_at
WITH archived_matches AS (
    SELECT DISTINCT ON (s.schedule_id)
        s.schedule_id,
        l.user_id,
        u.first_name,
        u.last_name,
        u.role,
        p.program_name
    FROM public.schedule s
    JOIN public.program p ON p.id = s.program_id
    JOIN public.activity_log l ON l.action = 'confirm'
        AND (l.target_detail ILIKE '%' || s.semester || '%' OR s.semester IS NULL)
        AND abs(extract(epoch from (l.created_at - s.archived_at))) < 300
    JOIN public.users u ON u.id = l.user_id
        AND (u.program_id = s.program_id OR u.program_id IS NULL)
    WHERE s.archive = true AND s.archived_at IS NOT NULL
    ORDER BY s.schedule_id, abs(extract(epoch from (l.created_at - s.archived_at))) ASC
)
UPDATE public.schedule s
SET prepared_by_user_id = am.user_id,
    prepared_by_name = trim(upper(concat_ws(' ', nullif(trim(am.first_name), ''), nullif(trim(am.last_name), '')))),
    prepared_by_title = CASE
        WHEN lower(coalesce(am.role, '')) IN ('super_admin', 'superadmin') THEN 'Super Admin - ' || am.program_name
        WHEN lower(coalesce(am.role, '')) IN ('admin', 'administrator', 'academic_admin') THEN 'Academic Admin - ' || am.program_name
        ELSE 'Program Scheduler - ' || am.program_name
    END
FROM archived_matches am
WHERE s.schedule_id = am.schedule_id
  AND s.prepared_by_name IS NULL;

-- 4. Update confirm_schedule_transaction RPC to insert prepared_by snapshot columns
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text,
    p_rows jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT 'Scheduler',
    p_program_id integer DEFAULT NULL,
    p_batch_id uuid DEFAULT NULL,
    p_prepared_by_user_id uuid DEFAULT NULL,
    p_prepared_by_name text DEFAULT NULL,
    p_prepared_by_title text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id uuid;
    v_archived integer := 0;
    v_inserted integer := 0;
    v_target_program_id integer := p_program_id;
    v_target_program_name text := p_program;
    v_prep_user_id uuid := p_prepared_by_user_id;
    v_prep_name text := nullif(trim(p_prepared_by_name), '');
    v_prep_title text := nullif(trim(p_prepared_by_title), '');
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

    IF v_target_program_name IS NULL THEN
        SELECT program_name INTO v_target_program_name
        FROM public.program
        WHERE id = v_target_program_id
        LIMIT 1;
    END IF;

    -- If preparer fields not passed in, attempt to derive from auth.uid()
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
        -- Soft archive previous active entries for this scope with batch_id and timestamp
        UPDATE public.schedule
        SET archive = true,
            archive_batch_id = v_batch_id,
            archived_at = clock_timestamp(),
            archived_by = coalesce(nullif(trim(p_archived_by), ''), 'Scheduler'),
            archive_reason = 'Archived on schedule confirmation'
        WHERE archive = false
          AND (p_semester IS NULL OR semester = p_semester)
          AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows referencing program_id and preparer snapshot
    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, semester, major, archive,
        prepared_by_user_id, prepared_by_name, prepared_by_title
    )
    SELECT coalesce(
               nullif(x->>'program_id', '')::integer,
               (SELECT p.id FROM public.program p WHERE upper(trim(p.program_name)) = upper(trim(x->>'program')) LIMIT 1),
               v_target_program_id
           ),
           nullif(x->>'professor_load_id', '')::integer,
           nullif(x->>'room_id', '')::integer,
           coalesce(x->>'day', 'Monday'),
           (x->>'class_start')::time, (x->>'class_end')::time,
           coalesce(nullif(x->>'session_type', ''), 'Lecture'),
           x->>'section', coalesce(nullif(x->>'semester', ''), p_semester),
           nullif(x->>'major', ''), false,
           coalesce(nullif(x->>'prepared_by_user_id', '')::uuid, v_prep_user_id),
           coalesce(nullif(trim(x->>'prepared_by_name'), ''), v_prep_name),
           coalesce(nullif(trim(x->>'prepared_by_title'), ''), v_prep_title)
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', v_batch_id,
        'archived_count', v_archived,
        'inserted_count', v_inserted,
        'semester', p_semester,
        'program_id', v_target_program_id,
        'prepared_by_name', v_prep_name,
        'prepared_by_title', v_prep_title
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid, uuid, text, text) TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
