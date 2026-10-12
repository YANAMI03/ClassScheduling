-- ==============================================================================
-- Migration: 20261012240000_user_middle_name_trigger.sql
-- Persist users.middle_name from sign-up/user metadata so faculty (Viewer)
-- accounts can be matched to their professor_load name (often middle-initial).
-- ==============================================================================

BEGIN;

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
    v_middle_name text;
    v_last_name text;
    v_role text;
BEGIN
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));
    v_first_name := NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), '');
    v_middle_name := NULLIF(TRIM(new.raw_user_meta_data->>'middle_name'), '');
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
        id, email, username, first_name, middle_name, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id, new.email, v_username,
        COALESCE(v_first_name, ''), COALESCE(v_middle_name, ''), COALESCE(v_last_name, ''),
        v_program_id, COALESCE(v_role, 'viewer'),
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE COALESCE(EXCLUDED.first_name, '') END,
        middle_name = CASE WHEN public.users.middle_name IS NOT NULL AND public.users.middle_name <> '' THEN public.users.middle_name ELSE COALESCE(EXCLUDED.middle_name, '') END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE COALESCE(EXCLUDED.last_name, '') END,
        program_id = CASE WHEN LOWER(COALESCE(public.users.role, 'viewer')) IN ('viewer', 'admin', 'super_admin') THEN NULL ELSE COALESCE(public.users.program_id, EXCLUDED.program_id) END,
        role = CASE WHEN public.users.role IS NOT NULL AND public.users.role <> '' THEN public.users.role ELSE COALESCE(EXCLUDED.role, 'viewer') END,
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture),
        updated_at = NOW();

    RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT OR UPDATE ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();

NOTIFY pgrst, 'reload schema';

COMMIT;
