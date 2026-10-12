-- ==============================================================================
-- Migration: 20261012200000_add_specializations_table.sql
-- Phase 1 of the specialization refactor (safe / additive).
--
--   * Creates public.specializations (admin-managed, archive-based no-delete).
--   * Adds course.specialization_id (nullable FK). NULL == "General".
--   * Backfills specialization_id from the legacy course.specialization text.
--   * Rebuilds schedule_with_semester so its `specialization`/`major` columns come
--     from the FK join (works before AND after the legacy column is dropped).
--
-- The legacy course.specialization text column is intentionally NOT dropped here.
-- It is dropped by 20261012200100_drop_course_specialization_text.sql, which must
-- only be run AFTER the application code has been switched to specialization_id.
-- ==============================================================================

BEGIN;

-- 1. Specializations table -----------------------------------------------------
CREATE TABLE IF NOT EXISTS public.specializations (
    specialization_id serial PRIMARY KEY,
    name    text    NOT NULL,
    code    text,
    program_id integer NOT NULL REFERENCES public.program(id),
    archive boolean NOT NULL DEFAULT false
);

-- Same name may not be added twice within one program.
CREATE UNIQUE INDEX IF NOT EXISTS specializations_program_id_name_key
    ON public.specializations (program_id, name);

CREATE INDEX IF NOT EXISTS idx_specializations_program ON public.specializations (program_id);
CREATE INDEX IF NOT EXISTS idx_specializations_archive ON public.specializations (archive);

-- 2. RLS (matches the admin-managed reference-table pattern) --------------------
ALTER TABLE public.specializations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "specializations_select_authenticated" ON public.specializations;
CREATE POLICY "specializations_select_authenticated" ON public.specializations
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "specializations_admin_all" ON public.specializations;
CREATE POLICY "specializations_admin_all" ON public.specializations
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('super_admin', 'admin'))
    WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

-- 3. Seed the specializations that programs currently use ----------------------
--    "General" is deliberately NOT inserted (it is a code rule: specialization_id
--    IS NULL). Names/codes are derived from existing course data.
INSERT INTO public.specializations (name, code, program_id)
SELECT
    c.specialization,
    CASE c.specialization
        WHEN 'Database Systems' THEN 'DST'
        WHEN 'Web Systems'      THEN 'WST'
        WHEN 'Networking'       THEN 'NET'
        ELSE NULL
    END,
    c.program_id
FROM public.course c
WHERE c.program_id IS NOT NULL
  AND c.specialization IS NOT NULL
  AND btrim(c.specialization) <> ''
  AND lower(btrim(c.specialization)) <> 'general'
GROUP BY c.specialization, c.program_id
ON CONFLICT (program_id, name) DO NOTHING;

-- 4. Add the FK column on course ----------------------------------------------
ALTER TABLE public.course
    ADD COLUMN IF NOT EXISTS specialization_id integer
    REFERENCES public.specializations(specialization_id);

CREATE INDEX IF NOT EXISTS idx_course_specialization_id ON public.course (specialization_id);

-- 5. Backfill specialization_id from the legacy text value ---------------------
--    "General" and empty/blank become NULL (== General).
UPDATE public.course c
SET specialization_id = s.specialization_id
FROM public.specializations s
WHERE s.program_id = c.program_id
  AND s.name = c.specialization
  AND c.specialization IS NOT NULL
  AND lower(btrim(c.specialization)) <> 'general';

-- 6. CHECK QUERY: list any course whose legacy text did not map to a
--    specialization, so nothing is lost silently. (Run and review this before
--    dropping the column. Courses here keep specialization_id = NULL, i.e.
--    they will become "General".)
-- ------------------------------------------------------------------------------
-- SELECT c.course_id, c.course_code, c.program_id, c.specialization
-- FROM public.course c
-- WHERE c.specialization IS NOT NULL
--   AND btrim(c.specialization) <> ''
--   AND lower(btrim(c.specialization)) <> 'general'
--   AND NOT EXISTS (
--       SELECT 1 FROM public.specializations s
--       WHERE s.program_id = c.program_id AND s.name = c.specialization
--   )
-- ORDER BY c.program_id, c.specialization, c.course_code;
-- ------------------------------------------------------------------------------

-- 7. Rebuild schedule_with_semester to source specialization from the FK --------
DROP VIEW IF EXISTS public.schedule_with_semester;
CREATE VIEW public.schedule_with_semester
WITH (security_invoker = on)
AS
SELECT
    sc.schedule_id,
    sc.room_id,
    sc.day,
    sc.class_start,
    sc.class_end,
    sc.session_type,
    sc.section,
    sc.professor_load_id,
    sc.archive,
    sc.archived_at,
    sc.archive_batch_id,
    sc.prepared_by_user_id,
    CASE
        WHEN u.id IS NULL THEN 'Preparer account unavailable'
        ELSE coalesce(
            nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
            nullif(trim(u.username), ''),
            'Unknown user'
        )
    END AS preparer_name,
    CASE
        WHEN u.id IS NULL THEN 'Role/title unavailable'
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('super_admin', 'superadmin')
            THEN 'Super Admin - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('admin', 'administrator', 'academic_admin')
            THEN 'Academic Admin - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('scheduler', 'dean', 'chair', 'dean/chair', 'program_chair', 'department_head')
            THEN 'Program Scheduler - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN nullif(trim(u.role), '') IS NOT NULL
            THEN initcap(replace(u.role, '_', ' ')) || ' - ' || coalesce(p.program_name, 'Unknown Program')
        ELSE 'Schedule Preparer - ' || coalesce(p.program_name, 'Unknown Program')
    END AS preparer_title,
    coalesce(c.semester, '1st Semester') AS semester,
    s.name AS specialization,
    s.name AS major,
    c.specialization_id,
    pl.course_id,
    c.program_id,
    c.course_code,
    c.course_name,
    c.year_level,
    pl.professor_name,
    p.program_name,
    r.room_name
FROM public.schedule sc
LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.program p ON p.id = c.program_id
LEFT JOIN public.specializations s ON s.specialization_id = c.specialization_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
