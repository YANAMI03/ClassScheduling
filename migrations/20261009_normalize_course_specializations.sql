-- Migration: Normalize course specializations and enforce valid track choices
-- Description:
--   Updates existing courses with non-standard track names to the canonical choices:
--   'Database Systems', 'Web Systems', 'Networking', 'General'.
--   Specifically:
--   - IT-CAP01 (DST): 'Database' -> 'Database Systems'
--   - IT-CAP01 (WST): 'Web' -> 'Web Systems'
--   - IT-WS03, IT-WS04, IT-WS05: 'Web Development' -> 'Web Systems'
--   - IT-WS06, IT-WS07: 'Web Development' -> 'Web Systems'

BEGIN;

-- 1. 3rd Year 2nd Semester: Capstone course tracks
UPDATE public.course
SET specialization = 'Database Systems'
WHERE course_id = 73 AND course_name = 'IT-CAP01 (DST)';

UPDATE public.course
SET specialization = 'Web Systems'
WHERE course_id = 74 AND course_name = 'IT-CAP01 (WST)';

-- 2. 3rd Year 2nd Semester: Web Systems track courses
UPDATE public.course
SET specialization = 'Web Systems'
WHERE course_id IN (52, 53, 54)
  AND course_name IN ('IT-WS03', 'IT-WS04', 'IT-WS05');

-- 3. 4th Year 1st Semester: Web Systems track courses
UPDATE public.course
SET specialization = 'Web Systems'
WHERE course_id IN (65, 66)
  AND course_name IN ('IT-WS06', 'IT-WS07');

COMMIT;
