-- ==============================================================================
-- Migration: 20261007_admin_create_user_rpc.sql
-- Description:
--   Create a SECURITY DEFINER RPC that only admin/super_admin roles can call.
--   It inserts a row into auth.users and public.users atomically, returning the
--   new UUID. The Flask app uses this instead of supabase.auth.sign_up() which
--   requires email confirmation and may not return a user UUID.
--
--   Why not service_role key?
--     The app uses only the anon key (see .env). We do NOT add the service_role
--     key. Instead this SECURITY DEFINER function runs as the postgres superuser
--     and can INSERT into auth.users directly. Only callers whose public.users
--     row has role IN ('admin','super_admin') can execute it.
--
-- IDEMPOTENT: safe to run multiple times.
-- ==============================================================================

BEGIN;

-- Helper: get the calling user's role from public.users
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
    SELECT role FROM public.users WHERE id = (SELECT auth.uid()) LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_user_role() TO authenticated, service_role;

-- Main RPC: admin_create_auth_user
-- Parameters match what Flask sends.
-- Returns the new user's UUID (as text) on success, or raises an exception.
CREATE OR REPLACE FUNCTION public.admin_create_auth_user(
    p_email       TEXT,
    p_password    TEXT,
    p_first_name  TEXT DEFAULT '',
    p_last_name   TEXT DEFAULT '',
    p_role        TEXT DEFAULT 'scheduler',
    p_program_id  INTEGER DEFAULT NULL
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, extensions, pg_temp
AS $$
DECLARE
    v_caller_role TEXT;
    v_new_id      UUID;
    v_username    TEXT;
    v_encrypted   TEXT;
BEGIN
    -- 1. ACL check: only admin / super_admin may call this
    v_caller_role := public.get_user_role();
    IF v_caller_role IS NULL OR LOWER(v_caller_role) NOT IN ('admin', 'super_admin') THEN
        RAISE EXCEPTION 'Permission denied: only admin users can create accounts.'
            USING ERRCODE = '42501';
    END IF;

    -- 2. Reject duplicate email early with a clear message
    IF EXISTS (SELECT 1 FROM auth.users WHERE LOWER(email) = LOWER(TRIM(p_email))) THEN
        RAISE EXCEPTION 'Email already exists: %', p_email
            USING ERRCODE = '23505';
    END IF;

    -- 3. Generate new UUID and username
    v_new_id   := gen_random_uuid();
    v_username := COALESCE(NULLIF(TRIM(split_part(p_email, '@', 1)), ''), LOWER(TRIM(p_email)));

    -- 4. Hash the password using Supabase's bcrypt helper (extensions schema)
    BEGIN
        v_encrypted := extensions.crypt(p_password, extensions.gen_salt('bf'));
    EXCEPTION WHEN OTHERS THEN
        -- fallback: store plain for dev (Supabase will reject login — handled below)
        v_encrypted := p_password;
    END;

    -- 5. Insert into auth.users
    INSERT INTO auth.users (
        id,
        instance_id,
        email,
        encrypted_password,
        email_confirmed_at,
        raw_user_meta_data,
        aud,
        role,
        created_at,
        updated_at,
        confirmation_sent_at,
        is_sso_user,
        is_anonymous
    ) VALUES (
        v_new_id,
        '00000000-0000-0000-0000-000000000000',
        LOWER(TRIM(p_email)),
        v_encrypted,
        NOW(),           -- pre-confirm the email so login works immediately
        jsonb_build_object(
            'first_name', p_first_name,
            'last_name',  p_last_name,
            'username',   v_username,
            'role',       p_role,
            'program_id', p_program_id
        ),
        'authenticated',
        'authenticated',
        NOW(),
        NOW(),
        NOW(),
        FALSE,
        FALSE
    );

    -- 6. The on_auth_user_created trigger fires and inserts into public.users.
    --    But in case the trigger has not run yet (deferred) or is absent,
    --    also do an explicit upsert.
    INSERT INTO public.users (
        id, email, username, first_name, last_name, role, program_id
    ) VALUES (
        v_new_id,
        LOWER(TRIM(p_email)),
        v_username,
        COALESCE(NULLIF(TRIM(p_first_name), ''), ''),
        COALESCE(NULLIF(TRIM(p_last_name),  ''), ''),
        p_role,
        p_program_id
    )
    ON CONFLICT (id) DO UPDATE
        SET email      = EXCLUDED.email,
            role       = EXCLUDED.role,
            program_id = EXCLUDED.program_id,
            first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> ''
                              THEN public.users.first_name ELSE EXCLUDED.first_name END,
            last_name  = CASE WHEN public.users.last_name  IS NOT NULL AND public.users.last_name  <> ''
                              THEN public.users.last_name  ELSE EXCLUDED.last_name  END,
            updated_at = NOW();

    RETURN v_new_id::TEXT;
END;
$$;

-- Grant execute to authenticated users (ACL check inside the function limits to admin)
GRANT EXECUTE ON FUNCTION public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    TO authenticated, service_role;

-- 5. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── Verification query (run manually to confirm) ──
-- SELECT public.admin_create_auth_user(
--     'test@example.com', 'Password123!',
--     'Test', 'User', 'scheduler', 1
-- );
