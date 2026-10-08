-- ==============================================================================
-- Migration: 20261007_professor_program.sql
-- Description: Scopes professors per program via many-to-many junction table
--              professor_program, backfills from professor.program_id (home program),
--              and sets up RLS policies for Schedulers vs Admins.
-- ==============================================================================

BEGIN;

-- 1. Helper function for RLS user role & program_id
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT LOWER(COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'role'),
        (auth.jwt() -> 'user_metadata' ->> 'role'),
        (auth.jwt() ->> 'role'),
        (SELECT role FROM public.users WHERE id::text = auth.uid()::text LIMIT 1),
        'viewer'
    ));
$$;

CREATE OR REPLACE FUNCTION public.get_user_program_id()
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'program_id')::integer,
        (auth.jwt() -> 'user_metadata' ->> 'program_id')::integer,
        (SELECT program_id FROM public.users WHERE id::text = auth.uid()::text LIMIT 1)
    );
$$;

-- 2. Create professor_program table (idempotent)
CREATE TABLE IF NOT EXISTS public.professor_program (
    prof_id INTEGER REFERENCES public.professor(prof_id) ON DELETE CASCADE,
    program_id INTEGER REFERENCES public.program(id) ON DELETE CASCADE,
    PRIMARY KEY (prof_id, program_id)
);

-- Index for reverse lookups
CREATE INDEX IF NOT EXISTS idx_professor_program_prog_id ON public.professor_program(program_id);

-- 3. Backfill from existing professor.program_id (home program)
-- Professors with NULL program_id get no link (visible to admins only until assigned)
INSERT INTO public.professor_program (prof_id, program_id)
SELECT p.prof_id, p.program_id
FROM public.professor p
WHERE p.program_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM public.program prog WHERE prog.id = p.program_id)
ON CONFLICT (prof_id, program_id) DO NOTHING;

-- 4. Enable RLS on professor_program
ALTER TABLE public.professor_program ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "professor_program_admin_all" ON public.professor_program;
CREATE POLICY "professor_program_admin_all"
ON public.professor_program
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'super_admin'))
WITH CHECK (get_user_role() IN ('admin', 'super_admin'));

DROP POLICY IF EXISTS "professor_program_scheduler_select" ON public.professor_program;
CREATE POLICY "professor_program_scheduler_select"
ON public.professor_program
FOR SELECT
TO authenticated
USING (
    get_user_role() = 'scheduler'
    AND program_id = get_user_program_id()
);

DROP POLICY IF EXISTS "professor_program_scheduler_insert" ON public.professor_program;
CREATE POLICY "professor_program_scheduler_insert"
ON public.professor_program
FOR INSERT
TO authenticated
WITH CHECK (
    get_user_role() = 'scheduler'
    AND program_id = get_user_program_id()
);

-- 5. RLS on public.professor table
ALTER TABLE public.professor ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "professor_admin_scheduler_all" ON public.professor;
DROP POLICY IF EXISTS "professor_admin_all" ON public.professor;
CREATE POLICY "professor_admin_all"
ON public.professor
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'super_admin'))
WITH CHECK (get_user_role() IN ('admin', 'super_admin'));

DROP POLICY IF EXISTS "professor_scheduler_select" ON public.professor;
CREATE POLICY "professor_scheduler_select"
ON public.professor
FOR SELECT
TO authenticated
USING (
    get_user_role() = 'scheduler'
    AND EXISTS (
        SELECT 1 FROM public.professor_program pp
        WHERE pp.prof_id = professor.prof_id
          AND pp.program_id = get_user_program_id()
    )
);

DROP POLICY IF EXISTS "professor_scheduler_insert" ON public.professor;
CREATE POLICY "professor_scheduler_insert"
ON public.professor
FOR INSERT
TO authenticated
WITH CHECK (get_user_role() = 'scheduler');

DROP POLICY IF EXISTS "professor_scheduler_update" ON public.professor;
CREATE POLICY "professor_scheduler_update"
ON public.professor
FOR UPDATE
TO authenticated
USING (
    get_user_role() = 'scheduler'
    AND EXISTS (
        SELECT 1 FROM public.professor_program pp
        WHERE pp.prof_id = professor.prof_id
          AND pp.program_id = get_user_program_id()
    )
)
WITH CHECK (
    get_user_role() = 'scheduler'
    AND EXISTS (
        SELECT 1 FROM public.professor_program pp
        WHERE pp.prof_id = professor.prof_id
          AND pp.program_id = get_user_program_id()
    )
);

-- 6. Reload schema
NOTIFY pgrst, 'reload schema';

COMMIT;
