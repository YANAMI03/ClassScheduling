-- ==============================================================================
-- Migration: 20261012200100_drop_course_specialization_text.sql
-- Phase 2 of the specialization refactor.
--
-- !! RUN ONLY AFTER APPLICATION CODE NO LONGER READS course.specialization. !!
--
-- Prerequisites (must all be true before running):
--   1. 20261012200000_add_specializations_table.sql has been applied.
--   2. Every course has been reviewed with the CHECK QUERY in that migration;
--      any unmatched legacy value has been either added as a specialization or
--      accepted as General.
--   3. The schedule_with_semester view already reads specialization from the FK
--      (done in phase 1), so dropping the column does not affect it.
--   4. public.restore_database_json has been updated to stop inserting/updating
--      course.specialization (map the incoming text value to specialization_id,
--      or ignore it). Otherwise database restore will fail at runtime.
-- ==============================================================================

BEGIN;

-- 1. Guard: refuse to drop if any legacy value would be silently lost --------
DO $$
DECLARE
    unmatched_count integer;
BEGIN
    SELECT count(*) INTO unmatched_count
    FROM public.course c
    WHERE c.specialization IS NOT NULL
      AND btrim(c.specialization) <> ''
      AND lower(btrim(c.specialization)) <> 'general'
      AND NOT EXISTS (
          SELECT 1 FROM public.specializations s
          WHERE s.program_id = c.program_id AND s.name = c.specialization
      );

    IF unmatched_count > 0 THEN
        RAISE EXCEPTION
            'Aborting: % course(s) have an unmatched legacy specialization. Resolve them first (see the CHECK QUERY in phase 1).',
            unmatched_count;
    END IF;
END $$;

-- 2. Drop the legacy CHECK constraint (if any) and the text column ------------
ALTER TABLE public.course DROP CONSTRAINT IF EXISTS chk_course_specialization;
ALTER TABLE public.course DROP COLUMN IF EXISTS specialization;

NOTIFY pgrst, 'reload schema';

COMMIT;
