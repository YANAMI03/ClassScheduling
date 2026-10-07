-- ==============================================================================
-- Migration: 20261007_multi_program_support.sql
-- Description: Non-destructive schema preparation for multi-program support.
-- ==============================================================================

BEGIN;

-- 1. Ensure public.program table has required structure & case-insensitive uniqueness
CREATE TABLE IF NOT EXISTS public.program (
    id SERIAL PRIMARY KEY,
    program_name VARCHAR(100) NOT NULL UNIQUE,
    full_name VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.program ADD COLUMN IF NOT EXISTS full_name VARCHAR(255);

-- Case-insensitive unique index on program_name
CREATE UNIQUE INDEX IF NOT EXISTS idx_program_name_ci 
    ON public.program (LOWER(TRIM(program_name)));

-- Ensure canonical BSIT program exists
INSERT INTO public.program (program_name, full_name)
VALUES ('BSIT', 'Bachelor of Science in Information Technology')
ON CONFLICT (program_name) DO UPDATE 
SET full_name = EXCLUDED.full_name WHERE public.program.full_name IS NULL;

-- Compatibility view 'programs' (allows querying either table name)
CREATE OR REPLACE VIEW public.programs AS 
    SELECT id, program_name AS name, program_name, full_name, created_at, updated_at 
    FROM public.program;

-- 2. Backfill and enforce courses.program_id
ALTER TABLE public.course ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE RESTRICT;

-- Backfill any courses with NULL program_id to BSIT (id=1)
UPDATE public.course 
SET program_id = (SELECT id FROM public.program WHERE UPPER(TRIM(program_name)) = 'BSIT' LIMIT 1)
WHERE program_id IS NULL;

-- Enforce NOT NULL on course.program_id
ALTER TABLE public.course ALTER COLUMN program_id SET NOT NULL;

-- Clean up unused duplicate course IT-WS02 (id=48 is unused; id=20 is preserved)
DELETE FROM public.course 
WHERE course_id = 48 AND course_name = 'IT-WS02' 
  AND NOT EXISTS (SELECT 1 FROM public.professor_load WHERE course_id = 48);

-- Unique constraint on (program_id, lower(course_name))
CREATE UNIQUE INDEX IF NOT EXISTS idx_course_program_code_unique 
    ON public.course (program_id, LOWER(TRIM(course_name)));

-- 3. Users: Ensure Schedulers have program_id assigned
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE SET NULL;

UPDATE public.users 
SET program_id = (SELECT id FROM public.program WHERE UPPER(TRIM(program_name)) = 'BSIT' LIMIT 1)
WHERE LOWER(role) = 'scheduler' AND program_id IS NULL;

-- 4. Rooms: Make program_id nullable / unconstrained so rooms are shared
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'room' AND column_name = 'program_id'
    ) THEN
        ALTER TABLE public.room ALTER COLUMN program_id DROP NOT NULL;
    END IF;
END $$;

-- 5. Schedules: Ensure index on (program_id, semester, archive) for fast scoping
CREATE INDEX IF NOT EXISTS idx_schedule_active_prog_sem 
    ON public.schedule (program_id, semester) WHERE archive = false;

COMMIT;
