-- ==============================================================================
-- Migration: 20261024000000_schedule_view_indexes.sql
-- Purpose:   Indexes for the schedule-view read paths (Section/Room/My Schedules
--            and the archive tab). Read-only performance work; no column, table,
--            FK, or data changes.
--
-- Which query each index serves
--   * idx_schedule_room_active     -> /room_schedule/<room_id>:
--                                     schedule WHERE room_id = ? AND archive = false
--   * idx_schedule_section_active  -> /schedule/<section_name>:
--                                     schedule WHERE section = ? AND archive = false
--                                     (also the LEFT(section,...) year filter path)
--   * idx_schedule_archive_day     -> edit conflict scan:
--                                     schedule WHERE archive = false AND day = ?
--   * idx_course_program_id        -> program scoping used by every view:
--                                     join schedule -> professor_load -> course
--                                     WHERE course.program_id = ?
--   * idx_professor_load_prog_id   -> program scoping on the load side:
--                                     professor_load WHERE program_id = ?
--
-- NOTE: schedule has NO semester column (semester is derived from course.semester
--       via the schedule_with_semester view), so an index on
--       (archive, semester, school_year) is not possible on schedule. The
--       existing idx_schedule_program_archive (program_id, archive) and
--       idx_schedule_prof_load_id / idx_prof_load_course_id already cover much of
--       the join; the ones below fill the gaps that the view predicates hit.
--
-- CREATE INDEX IF NOT EXISTS is used so re-running is safe and pre-existing
-- indexes are left untouched.
-- ==============================================================================

BEGIN;

-- Active rows of a given room (room schedule detail).
CREATE INDEX IF NOT EXISTS idx_schedule_room_active
    ON public.schedule (room_id)
    WHERE archive = false;

-- Active rows of a given section (section detail).
CREATE INDEX IF NOT EXISTS idx_schedule_section_active
    ON public.schedule (section)
    WHERE archive = false;

-- Active rows by day (edit-modal conflict scan).
CREATE INDEX IF NOT EXISTS idx_schedule_archive_day
    ON public.schedule (archive, day);

-- Program scoping: schedule -> professor_load -> course.program_id.
CREATE INDEX IF NOT EXISTS idx_course_program_id
    ON public.course (program_id);

CREATE INDEX IF NOT EXISTS idx_professor_load_prog_id
    ON public.professor_load (program_id);

NOTIFY pgrst, 'reload schema';

COMMIT;

-- ==============================================================================
-- ROLLBACK  (run only if you decide these indexes are not useful)
-- ==============================================================================
-- BEGIN;
-- DROP INDEX IF EXISTS public.idx_schedule_room_active;
-- DROP INDEX IF EXISTS public.idx_schedule_section_active;
-- DROP INDEX IF EXISTS public.idx_schedule_archive_day;
-- DROP INDEX IF EXISTS public.idx_course_program_id;
-- DROP INDEX IF EXISTS public.idx_professor_load_prog_id;
-- COMMIT;
