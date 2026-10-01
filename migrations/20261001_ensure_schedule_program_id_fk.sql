-- ==============================================================================
-- Migration: 20261001_ensure_schedule_program_id_fk.sql
-- Description:
--   Convert schedule table to use program_id as a Foreign Key referencing
--   public.program(id) ON DELETE CASCADE, and drop the obsolete 'program' text column.
--
-- Steps:
--   1. Ensure public.program table exists with standard programs ('BSIT', 'BSBA').
--   2. Add column program_id to public.schedule if not already present.
--   3. Backfill schedule.program_id from existing schedule.program text values.
--   4. Backfill any fallback NULL program_id values to canonical 'BSIT'.
--   5. Add/verify Foreign Key constraint fk_schedule_program ON DELETE CASCADE.
--   6. Enforce NOT NULL on schedule.program_id.
--   7. Create indexes on schedule(program_id) and schedule(program_id, archive).
--   8. Drop the obsolete 'program' text column from public.schedule.
--   9. Re-define confirm_schedule_transaction RPC function to use program_id.
--  10. Update RLS policies on public.schedule.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. ENSURE PROGRAM TABLE EXISTS AND CONTAINS CANONICAL PROGRAMS
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.program (
    id SERIAL PRIMARY KEY,
    program_name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO public.program (program_name)
VALUES ('BSIT'), ('BSBA')
ON CONFLICT (program_name) DO NOTHING;

-- ------------------------------------------------------------------------------
-- 2. ENSURE program_id COLUMN EXISTS ON SCHEDULE
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule 
    ADD COLUMN IF NOT EXISTS program_id INTEGER;

-- ------------------------------------------------------------------------------
-- 3. BACKFILL program_id FROM schedule.program (IF program COLUMN EXISTS)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' 
          AND table_name = 'schedule' 
          AND column_name = 'program'
    ) THEN
        -- (a) Match text by program_name (case-insensitive, trimmed)
        UPDATE public.schedule sc
        SET program_id = p.id
        FROM public.program p
        WHERE sc.program_id IS NULL 
          AND sc.program IS NOT NULL
          AND UPPER(TRIM(sc.program)) = UPPER(TRIM(p.program_name));

        -- (b) Match if program stored numeric string ID
        UPDATE public.schedule sc
        SET program_id = p.id
        FROM public.program p
        WHERE sc.program_id IS NULL
          AND sc.program IS NOT NULL
          AND sc.program ~ '^[0-9]+$'
          AND sc.program::integer = p.id;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 4. BACKFILL ANY REMAINING NULL program_id TO CANONICAL 'BSIT' (OR FIRST PROGRAM)
-- ------------------------------------------------------------------------------
UPDATE public.schedule sc
SET program_id = (SELECT id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1)
WHERE sc.program_id IS NULL;

UPDATE public.schedule sc
SET program_id = (SELECT id FROM public.program ORDER BY id ASC LIMIT 1)
WHERE sc.program_id IS NULL;

-- ------------------------------------------------------------------------------
-- 5. ENSURE FOREIGN KEY CONSTRAINT ON schedule(program_id)
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    v_fk_name text;
BEGIN
    -- Check if an FK constraint referencing public.program already exists on schedule
    SELECT conname INTO v_fk_name
    FROM pg_constraint
    WHERE conrelid = 'public.schedule'::regclass
      AND confrelid = 'public.program'::regclass
      AND contype = 'f'
    LIMIT 1;

    IF v_fk_name IS NULL THEN
        ALTER TABLE public.schedule
            ADD CONSTRAINT fk_schedule_program
            FOREIGN KEY (program_id)
            REFERENCES public.program(id)
            ON DELETE CASCADE;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 6. ENFORCE NOT NULL ON schedule.program_id
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule 
    ALTER COLUMN program_id SET NOT NULL;

-- ------------------------------------------------------------------------------
-- 7. INDEXES ON schedule.program_id (Supabase Postgres Best Practice)
-- ------------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_schedule_program_id 
    ON public.schedule(program_id);

CREATE INDEX IF NOT EXISTS idx_schedule_program_archive 
    ON public.schedule(program_id, archive);

-- ------------------------------------------------------------------------------
-- 8. DROP THE OBSOLETE 'program' TEXT COLUMN FROM public.schedule
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule 
    DROP COLUMN IF EXISTS program CASCADE;

-- ------------------------------------------------------------------------------
-- 9. RE-DEFINE confirm_schedule_transaction RPC FUNCTION
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text);
DROP FUNCTION IF EXISTS public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid);

CREATE OR REPLACE FUNCTION public.confirm_schedule_transaction(
    p_semester text,
    p_program text,
    p_rows jsonb,
    p_clear_scope boolean DEFAULT true,
    p_archived_by text DEFAULT 'System',
    p_program_id integer DEFAULT NULL,
    p_batch_id uuid DEFAULT NULL
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

    IF p_clear_scope THEN
        -- Soft archive previous active entries for this scope
        UPDATE public.schedule
        SET archive = true
        WHERE archive = false
          AND (p_semester IS NULL OR semester = p_semester)
          AND (v_target_program_id IS NULL OR program_id = v_target_program_id);
        GET DIAGNOSTICS v_archived = ROW_COUNT;
    END IF;

    -- Insert new schedule rows referencing program_id
    INSERT INTO public.schedule (
        program_id, professor_load_id, room_id, day, class_start, class_end,
        session_type, section, semester, major, archive
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
           nullif(x->>'major', ''), false
    FROM jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) AS x;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN jsonb_build_object(
        'success', true,
        'batch_id', v_batch_id,
        'archived_count', v_archived,
        'inserted_count', v_inserted
    );
END;
$$;

REVOKE ALL ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.confirm_schedule_transaction(text, text, jsonb, boolean, text, integer, uuid) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 10. REFRESH RLS POLICIES ON public.schedule
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;
DROP POLICY IF EXISTS "schedule_scoped_all" ON public.schedule;

CREATE POLICY "schedule_scoped_all" ON public.schedule
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (program_id = public.get_user_program_id() OR program_id IS NULL)
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (program_id = public.get_user_program_id() OR program_id IS NULL)
        )
    );

COMMIT;
