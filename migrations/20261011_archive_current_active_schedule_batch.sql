-- ==============================================================================
-- Script: 20261011_archive_current_active_schedule_batch.sql
-- Purpose:
--   Soft-archive the currently active 441 schedule entries for BSIT 2nd Semester
--   into a clean, dedicated archive batch.
--
-- Safety:
--   - Non-destructive (sets archive = true, assigns batch UUID and timestamp).
--   - Scoped strictly to BSIT (program_id = 1) and 2nd Semester.
--   - Leaves row data intact so it can be restored from the Schedule Archive tab.
-- ==============================================================================

BEGIN;

UPDATE public.schedule sc
SET archive = true,
    archived_at = clock_timestamp(),
    archive_batch_id = gen_random_uuid()
FROM public.professor_load pl
JOIN public.course c ON pl.course_id = c.course_id
WHERE sc.professor_load_id = pl.id
  AND sc.archive = false
  AND c.program_id = 1
  AND trim(lower(coalesce(c.semester, '1st Semester'))) = '2nd semester';

COMMIT;
