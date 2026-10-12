-- ==============================================================================
-- Migration: 20261012210000_rename_scheduler_role_to_chair.sql
-- Rename the user role value 'scheduler' -> 'chair' (permissions unchanged).
-- ==============================================================================

BEGIN;

-- 1. Rename the stored role value --------------------------------------------
UPDATE public.users
SET role = 'chair'
WHERE LOWER(BTRIM(role)) = 'scheduler';

-- 2. Fix previously generated schedule titles (only if the column still exists;
--    it was dropped by 20261010052106_remove_prepared_by_snapshot_columns.sql,
--    in which case titles are derived from the linked user and need no update).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'schedule'
          AND column_name = 'prepared_by_title'
    ) THEN
        EXECUTE 'UPDATE public.schedule SET prepared_by_title = ''Chair''
                 WHERE LOWER(BTRIM(prepared_by_title)) = ''scheduler''';
    END IF;
END $$;

-- 3. Rebuild schedule_with_semester so the derived preparer title says 'Chair' --
DROP VIEW IF EXISTS public.schedule_with_semester;
CREATE VIEW public.schedule_with_semester
WITH (security_invoker = on)
AS
SELECT
    sc.schedule_id, sc.room_id, sc.day, sc.class_start, sc.class_end, sc.session_type,
    sc.section, sc.professor_load_id, sc.archive, sc.archived_at, sc.archive_batch_id,
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
        WHEN lower(replace(replace(coalesce(u.role, ''), '-', '_'), ' ', '_')) IN ('chair', 'scheduler', 'dean', 'dean/chair', 'program_chair', 'department_head')
            THEN 'Chair - ' || coalesce(p.program_name, 'Unknown Program')
        WHEN nullif(trim(u.role), '') IS NOT NULL
            THEN initcap(replace(u.role, '_', ' ')) || ' - ' || coalesce(p.program_name, 'Unknown Program')
        ELSE 'Schedule Preparer - ' || coalesce(p.program_name, 'Unknown Program')
    END AS preparer_title,
    coalesce(c.semester, '1st Semester') AS semester,
    s.name AS specialization,
    s.name AS major,
    c.specialization_id,
    pl.course_id, c.program_id, c.course_code, c.course_name, c.year_level,
    pl.professor_name, p.program_name, r.room_name
FROM public.schedule sc
LEFT JOIN public.users u ON u.id = sc.prepared_by_user_id
LEFT JOIN public.professor_load pl ON pl.id = sc.professor_load_id
LEFT JOIN public.course c ON c.course_id = pl.course_id
LEFT JOIN public.program p ON p.id = c.program_id
LEFT JOIN public.specializations s ON s.specialization_id = c.specialization_id
LEFT JOIN public.room r ON r.room_id = sc.room_id;

GRANT SELECT ON public.schedule_with_semester TO anon, authenticated, service_role;

-- 4. Rewrite every RLS policy that references 'scheduler' so the chair keeps
--    exactly the same access (pure string substitution in the policy predicate).
DO $$
DECLARE
    pol record;
    new_using text;
    new_check text;
BEGIN
    FOR pol IN
        SELECT schemaname, tablename, policyname, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND (coalesce(qual, '') ILIKE '%scheduler%' OR coalesce(with_check, '') ILIKE '%scheduler%')
    LOOP
        new_using := replace(pol.qual, 'scheduler', 'chair');
        new_check := replace(pol.with_check, 'scheduler', 'chair');
        IF new_using IS NOT NULL AND new_check IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %I.%I USING (%s) WITH CHECK (%s)',
                           pol.policyname, pol.schemaname, pol.tablename, new_using, new_check);
        ELSIF new_using IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %I.%I USING (%s)',
                           pol.policyname, pol.schemaname, pol.tablename, new_using);
        ELSIF new_check IS NOT NULL THEN
            EXECUTE format('ALTER POLICY %I ON %I.%I WITH CHECK (%s)',
                           pol.policyname, pol.schemaname, pol.tablename, new_check);
        END IF;
    END LOOP;
END $$;

-- 5. Allow 'chair' in the auth trigger's role whitelist (was 'scheduler') ------
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_program_id integer;
    v_program_name text;
    v_username text;
    v_first_name text;
    v_last_name text;
    v_role text;
BEGIN
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));
    v_first_name := NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), '');
    v_last_name := NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), '');
    v_role := NULLIF(TRIM(new.raw_user_meta_data->>'role'), '');

    IF v_role IS NULL OR LOWER(v_role) IN ('viewer', 'instructor', 'guest') THEN
        v_role := 'viewer';
    ELSIF LOWER(v_role) NOT IN ('super_admin', 'admin', 'chair', 'scheduler', 'dean') THEN
        v_role := 'viewer';
    END IF;

    IF LOWER(v_role) IN ('viewer', 'admin', 'super_admin') THEN
        v_program_id := NULL;
    ELSE
        v_program_name := COALESCE(new.raw_user_meta_data->>'program', '');
        IF (new.raw_user_meta_data->>'program_id') IS NOT NULL AND (new.raw_user_meta_data->>'program_id') ~ '^\d+$' THEN
            v_program_id := (new.raw_user_meta_data->>'program_id')::integer;
        ELSIF v_program_name ~ '^\d+$' THEN
            v_program_id := v_program_name::integer;
        ELSIF v_program_name <> '' THEN
            SELECT id INTO v_program_id FROM public.program
            WHERE UPPER(program_name) = UPPER(TRIM(v_program_name))
               OR UPPER(program_name) = (CASE WHEN UPPER(TRIM(v_program_name)) = 'CICT' THEN 'BSIT' WHEN UPPER(TRIM(v_program_name)) = 'CMBT' THEN 'BSBA' ELSE UPPER(TRIM(v_program_name)) END)
            LIMIT 1;
        END IF;

        IF v_program_id IS NULL THEN
            SELECT id INTO v_program_id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1;
        END IF;
    END IF;

    INSERT INTO public.users (
        id, email, username, first_name, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id, new.email, v_username,
        COALESCE(v_first_name, ''), COALESCE(v_last_name, ''),
        v_program_id, COALESCE(v_role, 'viewer'),
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE COALESCE(EXCLUDED.first_name, '') END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE COALESCE(EXCLUDED.last_name, '') END,
        program_id = CASE WHEN LOWER(COALESCE(public.users.role, 'viewer')) IN ('viewer', 'admin', 'super_admin') THEN NULL ELSE COALESCE(public.users.program_id, EXCLUDED.program_id) END,
        role = CASE WHEN public.users.role IS NOT NULL AND public.users.role <> '' THEN public.users.role ELSE COALESCE(EXCLUDED.role, 'viewer') END,
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture),
        updated_at = NOW();

    RETURN new;
END;
$$;

-- 6. NOTE: public.admin_create_auth_user(...) has p_role DEFAULT 'scheduler'.
--    Postgres cannot ALTER a parameter default, so that function must be
--    recreated from its current definition with the default changed to 'chair'.
--    The app always passes p_role explicitly, so this is cosmetic, not required
--    for correct behavior.

NOTIFY pgrst, 'reload schema';

COMMIT;
