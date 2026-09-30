-- ==============================================================================
-- Migration: 20260926_fix_auth_trigger_and_login.sql
-- Description:
--   1. Fix handle_new_auth_user() trigger to use program_id instead of dropped program column.
--      (The dropped column caused "column program does not exist" on every auth event, blocking logins and signups).
--   2. Re-create and grant get_email_by_username() RPC to anon/authenticated for username login.
--   3. Update public.users RLS policies to support super_admin, dean/chair, scheduler.
--   4. Sync any existing auth.users rows into public.users.
-- ==============================================================================

BEGIN;

-- 0. Ensure default programs exist
INSERT INTO public.program (program_name)
VALUES ('BSIT'), ('BSBA')
ON CONFLICT (program_name) DO NOTHING;

-- 1. Fix handle_new_auth_user() to reference program_id
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
    v_program_name := COALESCE(new.raw_user_meta_data->>'program', '');
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));
    v_first_name := NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), '');
    v_last_name := NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), '');
    v_role := NULLIF(TRIM(new.raw_user_meta_data->>'role'), '');
    IF LOWER(COALESCE(v_role, '')) IN ('viewer', 'instructor') THEN
        v_role := 'scheduler';
    END IF;

    -- Resolve program_id from metadata
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

    -- Fallback default to BSIT
    IF v_program_id IS NULL THEN
        SELECT id INTO v_program_id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1;
    END IF;

    INSERT INTO public.users (
        id,
        email,
        username,
        first_name,
        last_name,
        program_id,
        role,
        profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        COALESCE(v_first_name, ''),
        COALESCE(v_last_name, ''),
        v_program_id,
        COALESCE(v_role, 'scheduler'),
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE 
            WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name 
            ELSE COALESCE(EXCLUDED.first_name, '') 
        END,
        last_name = CASE 
            WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name 
            ELSE COALESCE(EXCLUDED.last_name, '') 
        END,
        program_id = COALESCE(public.users.program_id, EXCLUDED.program_id),
        role = CASE 
            WHEN public.users.role IS NOT NULL AND public.users.role <> '' THEN public.users.role 
            ELSE COALESCE(EXCLUDED.role, 'scheduler') 
        END,
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

-- 2. Re-create and grant get_email_by_username() RPC
CREATE OR REPLACE FUNCTION public.get_email_by_username(p_username text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT COALESCE(email, LOWER(username) || '@example.com')
    FROM public.users
    WHERE (LOWER(email) = LOWER(TRIM(p_username)) OR LOWER(username) = LOWER(TRIM(p_username)))
    LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_email_by_username(text) TO anon, authenticated, service_role;

-- 3. Update public.users RLS policies for super_admin, dean/chair, scheduler
DROP POLICY IF EXISTS "users_self_select" ON public.users;
DROP POLICY IF EXISTS "users_scheduler_select" ON public.users;
DROP POLICY IF EXISTS "users_admin_select" ON public.users;
CREATE POLICY "users_admin_select"
ON public.users
FOR SELECT
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin', 'dean', 'chair', 'scheduler'));

DROP POLICY IF EXISTS "users_admin_insert" ON public.users;
CREATE POLICY "users_admin_insert"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

DROP POLICY IF EXISTS "users_admin_update" ON public.users;
CREATE POLICY "users_admin_update"
ON public.users
FOR UPDATE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id)
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id);

DROP POLICY IF EXISTS "users_admin_delete" ON public.users;
CREATE POLICY "users_admin_delete"
ON public.users
FOR DELETE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin'));

-- 4. Sync any existing auth.users into public.users if missing
DO $$
DECLARE
    r RECORD;
    v_bsit_id integer;
BEGIN
    SELECT id INTO v_bsit_id FROM public.program WHERE program_name = 'BSIT' LIMIT 1;
    FOR r IN SELECT * FROM auth.users LOOP
        INSERT INTO public.users (
            id,
            email,
            username,
            first_name,
            last_name,
            program_id,
            role
        )
        VALUES (
            r.id,
            r.email,
            COALESCE(r.raw_user_meta_data->>'username', split_part(r.email, '@', 1)),
            COALESCE(r.raw_user_meta_data->>'first_name', ''),
            COALESCE(r.raw_user_meta_data->>'last_name', ''),
            v_bsit_id,
            COALESCE(r.raw_user_meta_data->>'role', 'scheduler')
        )
        ON CONFLICT (id) DO UPDATE
        SET email = EXCLUDED.email,
            program_id = COALESCE(public.users.program_id, EXCLUDED.program_id);
    END LOOP;

    -- Normalize any legacy viewer or instructor roles to scheduler
    UPDATE public.users SET role = 'scheduler' WHERE LOWER(role) IN ('viewer', 'instructor');
END $$;

COMMIT;

-- 5. Diagnostic Query: View all accounts currently in auth and public
SELECT 
    au.id,
    au.email,
    au.confirmed_at,
    pu.username,
    pu.role,
    pu.program_id,
    p.program_name
FROM auth.users au
LEFT JOIN public.users pu ON au.id = pu.id
LEFT JOIN public.program p ON pu.program_id = p.id
ORDER BY pu.role, au.email;
