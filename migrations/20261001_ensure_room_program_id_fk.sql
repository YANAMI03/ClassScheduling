-- ==============================================================================
-- Migration: 20261001_ensure_room_program_id_fk.sql
-- Description:
--   Ensure room table has program_id referencing public.program(id) as a Foreign Key.
--   Drop obsolete department column from room if present.
--   Ensure index on room(program_id) and update RLS policies.
-- ==============================================================================

BEGIN;

-- 1. Ensure program table exists
CREATE TABLE IF NOT EXISTS public.program (
    id SERIAL PRIMARY KEY,
    program_name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed confirmed canonical programs if not present
INSERT INTO public.program (program_name)
VALUES ('BSIT'), ('BSBA')
ON CONFLICT (program_name) DO NOTHING;

-- 2. Ensure program_id column exists on room with Foreign Key constraint
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'room' AND column_name = 'program_id'
    ) THEN
        ALTER TABLE public.room ADD COLUMN program_id INTEGER REFERENCES public.program(id) ON DELETE RESTRICT;
    END IF;
END $$;

-- 3. Backfill any room rows with NULL program_id to the first program (BSIT)
DO $$
DECLARE
    v_prog_id INTEGER;
BEGIN
    SELECT id INTO v_prog_id FROM public.program ORDER BY id LIMIT 1;
    IF v_prog_id IS NOT NULL THEN
        UPDATE public.room SET program_id = v_prog_id WHERE program_id IS NULL;
    END IF;
END $$;

-- 4. Create index on room.program_id for fast FK joins and filtering
CREATE INDEX IF NOT EXISTS idx_room_program_id ON public.room(program_id);

-- 5. Drop obsolete department column from room if it exists
ALTER TABLE public.room DROP COLUMN IF EXISTS department;

-- 6. Ensure RLS policies on public.room enforce program_id scoping
DROP POLICY IF EXISTS "room_admin_scheduler_all" ON public.room;
DROP POLICY IF EXISTS "room_select_authenticated" ON public.room;
CREATE POLICY "room_select_authenticated" ON public.room
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "room_dean_admin_crud" ON public.room;
CREATE POLICY "room_dean_admin_crud" ON public.room
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    );

COMMIT;
