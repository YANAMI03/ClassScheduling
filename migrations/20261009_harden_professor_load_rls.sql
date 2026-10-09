-- ==============================================================================
-- Optional Proposal: 20261009_harden_professor_load_rls.sql
-- Description: Hardens RLS on public.professor_load by:
--   1. Removing obsolete 'dean' and 'chair' roles.
--   2. Restricting load write operations strictly to the 'scheduler' of that program.
--   3. Removing write permissions from 'admin' (Admin must not manage loads).
--   4. Eliminating the 'OR program_id IS NULL' leak across programs.
-- ==============================================================================

BEGIN;

DROP POLICY IF EXISTS "professor_load_dean_admin_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scoped_crud" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;
DROP POLICY IF EXISTS "professor_load_scheduler_program_scoped_crud" ON public.professor_load;

-- 1. Read: all authenticated users can view loads
CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated USING (true);

-- 2. Write: Only schedulers belonging to the same program can insert/update/delete loads
CREATE POLICY "professor_load_scheduler_program_scoped_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() = 'scheduler'
        AND program_id = public.get_user_program_id()
    )
    WITH CHECK (
        public.get_user_role() = 'scheduler'
        AND program_id = public.get_user_program_id()
    );

NOTIFY pgrst, 'reload schema';

COMMIT;
