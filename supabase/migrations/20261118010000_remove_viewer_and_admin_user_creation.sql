-- ==============================================================================
-- Script 2 (MAIN MIGRATION)
-- File: supabase/migrations/20261118010000_remove_viewer_and_admin_user_creation.sql
--
-- Run this AFTER reviewing the output of Script 1
-- (20261118000000_review_viewers_and_admins.sql).
--
-- What this migration does:
--   1. Disables all existing users with role = 'viewer' (does NOT delete them,
--      preserving activity_log history) and clears their program_id.
--   2. Normalizes any remaining legacy role aliases ('scheduler'/'dean' -> 'chair',
--      'super_admin'/'superadmin' -> 'admin') and ensures admins have program_id = NULL.
--   3. Removes the 'viewer' default on public.users.role and updates CHECK
--      constraints so only 'admin' (program_id IS NULL) and 'chair'
--      (program_id IS NOT NULL) are allowed on all new inserts and updates.
--   4. Updates public.get_user_role() to return only 'admin' or 'chair' for
--      active (non-disabled) users (no 'viewer' fallback).
--   5. Updates public.handle_new_auth_user() to reject public self-signups and
--      only sync admin-provisioned accounts ('admin' / 'chair').
--   6. Replaces public.admin_create_auth_user() so admins can create Admin and
--      Chair accounts with bcrypt-hashed passwords (no plaintext fallback),
--      middle_initial, username, and auth.identities rows.
--   7. Updates RLS policies on public.users and public.schedule to remove the
--      viewer role.
--
-- Idempotent: safe to run more than once.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Disable existing viewer accounts (preserve rows for activity_log history)
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    v_viewer_count integer;
    v_newly_disabled integer;
BEGIN
    SELECT COUNT(*)
    INTO v_viewer_count
    FROM public.users
    WHERE lower(trim(coalesce(role, ''))) = 'viewer';

    SELECT COUNT(*)
    INTO v_newly_disabled
    FROM public.users
    WHERE lower(trim(coalesce(role, ''))) = 'viewer'
      AND COALESCE(disabled, false) = false;

    RAISE NOTICE 'Existing viewer accounts: % total (% active will now be disabled).',
        v_viewer_count, v_newly_disabled;
END $$;

-- Drop existing role/program constraints temporarily so normalization never trips them
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_program_rule;

-- Disable all existing viewer accounts (and clear program_id); do NOT delete
UPDATE public.users
SET disabled   = true,
    program_id = NULL
WHERE lower(trim(coalesce(role, ''))) = 'viewer';

-- Normalize any lingering legacy role strings on active accounts
UPDATE public.users
SET role = 'admin',
    program_id = NULL
WHERE lower(trim(coalesce(role, ''))) IN ('admin', 'super_admin', 'superadmin', 'administrator', 'academic_admin');

UPDATE public.users
SET role = 'chair'
WHERE lower(trim(coalesce(role, ''))) IN ('chair', 'scheduler', 'program_scheduler', 'program_chair', 'department_head', 'dean', 'dean/chair');

-- Disable any other unrecognized legacy roles (without deleting)
UPDATE public.users
SET disabled   = true,
    program_id = NULL
WHERE role IS NULL
   OR role NOT IN ('admin', 'chair');

-- ------------------------------------------------------------------------------
-- 2. Drop 'viewer' column default & enforce admin/chair CHECK constraints
-- ------------------------------------------------------------------------------
ALTER TABLE public.users ALTER COLUMN role DROP DEFAULT;

-- Enforce only 'admin' and 'chair' on all future INSERTs and UPDATEs.
-- NOT VALID allows existing disabled viewer rows to remain in public.users for
-- activity_log integrity while immediately blocking any new or updated row from
-- having role = 'viewer' (or re-enabling a legacy viewer without changing role).
ALTER TABLE public.users
    ADD CONSTRAINT users_role_check
    CHECK (role IN ('admin', 'chair')) NOT VALID;

ALTER TABLE public.users
    ADD CONSTRAINT users_role_program_rule
    CHECK (
        (role = 'admin' AND program_id IS NULL)
        OR (role = 'chair' AND program_id IS NOT NULL)
    ) NOT VALID;

-- If no legacy viewer rows exist (all rows already satisfy the constraints),
-- validate both constraints completely.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.users
        WHERE role IS NULL
           OR role NOT IN ('admin', 'chair')
           OR (role = 'admin' AND program_id IS NOT NULL)
           OR (role = 'chair' AND program_id IS NULL)
    ) THEN
        ALTER TABLE public.users VALIDATE CONSTRAINT users_role_check;
        ALTER TABLE public.users VALIDATE CONSTRAINT users_role_program_rule;
        RAISE NOTICE 'Constraints users_role_check and users_role_program_rule fully validated.';
    ELSE
        RAISE NOTICE 'Constraints added as NOT VALID (active for all future writes) to preserve disabled legacy viewer rows.';
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 3. Update public.get_user_role(): only 'admin' or 'chair' for active users
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_role     text;
    v_disabled boolean := false;
BEGIN
    IF auth.uid() IS NOT NULL THEN
        SELECT lower(trim(u.role)), COALESCE(u.disabled, false)
        INTO v_role, v_disabled
        FROM public.users u
        WHERE u.id = auth.uid()
        LIMIT 1;

        IF v_disabled THEN
            RETURN NULL;
        END IF;
    END IF;

    IF v_role IS NULL OR trim(v_role) = '' THEN
        v_role := lower(trim(COALESCE(auth.jwt() -> 'app_metadata' ->> 'role', '')));
    END IF;

    IF v_role IN ('admin', 'super_admin', 'superadmin', 'administrator', 'academic_admin') THEN
        RETURN 'admin';
    ELSIF v_role IN ('chair', 'scheduler', 'program_scheduler', 'program_chair', 'department_head', 'dean', 'dean/chair') THEN
        RETURN 'chair';
    END IF;

    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION public.get_user_role() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_user_role() TO authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 4. Update public.handle_new_auth_user() trigger on auth.users
--    Blocks public self-signups (which cannot set raw_app_meta_data.role).
--    Only allows admin-created accounts with app_metadata role IN ('admin','chair').
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_role           text;
    v_raw_program_id text;
    v_program_id     integer;
    v_first_name     text;
    v_middle_initial text;
    v_last_name      text;
    v_username       text;
BEGIN
    -- Role MUST come from server-controlled raw_app_meta_data (never user_metadata).
    v_role := lower(trim(COALESCE(new.raw_app_meta_data->>'role', '')));

    IF v_role NOT IN ('admin', 'chair') THEN
        RAISE EXCEPTION 'Public sign-up is disabled. Accounts must be created by an administrator.'
            USING ERRCODE = '42501';
    END IF;

    v_first_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), ''), '') FROM 1 FOR 100);
    v_middle_initial := NULLIF(UPPER(LEFT(TRIM(COALESCE(new.raw_user_meta_data->>'middle_initial', '')), 1)), '');
    v_middle_initial := CASE WHEN v_middle_initial ~ '^[A-Z]$' THEN v_middle_initial ELSE NULL END;
    v_last_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), ''), '') FROM 1 FOR 100);
    v_username := lower(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1)));

    IF v_role = 'chair' THEN
        v_raw_program_id := TRIM(COALESCE(new.raw_app_meta_data->>'program_id', ''));
        IF v_raw_program_id ~ '^\d+$' THEN
            SELECT id INTO v_program_id
            FROM public.program
            WHERE id = v_raw_program_id::integer
              AND COALESCE(archive, false) = false
            LIMIT 1;
        END IF;
        IF v_program_id IS NULL THEN
            RAISE EXCEPTION 'A valid, active program_id is required for Chair accounts.'
                USING ERRCODE = '22023';
        END IF;
    ELSE
        v_program_id := NULL;
    END IF;

    INSERT INTO public.users (
        id, email, username, first_name, middle_initial, last_name, program_id, role, profile_picture, disabled
    )
    VALUES (
        new.id,
        lower(trim(new.email)),
        v_username,
        COALESCE(v_first_name, ''),
        v_middle_initial,
        COALESCE(v_last_name, ''),
        v_program_id,
        v_role,
        new.raw_user_meta_data->>'profile_picture',
        false
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email          = EXCLUDED.email,
        username       = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name     = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE EXCLUDED.first_name END,
        middle_initial = COALESCE(public.users.middle_initial, EXCLUDED.middle_initial),
        last_name      = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END,
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture);

    RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();

-- ------------------------------------------------------------------------------
-- 5. Replace public.admin_create_auth_user() RPC
--    Admin-only account provisioning with bcrypt password hashing (pgcrypto).
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER);

CREATE OR REPLACE FUNCTION public.admin_create_auth_user(
    p_email          TEXT,
    p_password       TEXT,
    p_first_name     TEXT    DEFAULT '',
    p_middle_initial TEXT    DEFAULT NULL,
    p_last_name      TEXT    DEFAULT '',
    p_username       TEXT    DEFAULT NULL,
    p_role           TEXT    DEFAULT 'chair',
    p_program_id     INTEGER DEFAULT NULL
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, extensions, pg_temp
AS $$
DECLARE
    v_caller_role    TEXT;
    v_new_id         UUID;
    v_email          TEXT;
    v_username       TEXT;
    v_first_name     TEXT;
    v_middle_initial TEXT;
    v_last_name      TEXT;
    v_role           TEXT;
    v_program_id     INTEGER;
    v_encrypted      TEXT;
BEGIN
    -- 1. ACL check: only active Admin users may call this RPC
    v_caller_role := public.get_user_role();
    IF v_caller_role IS NULL OR v_caller_role <> 'admin' THEN
        RAISE EXCEPTION 'Permission denied: only admin users can create accounts.'
            USING ERRCODE = '42501';
    END IF;

    -- 2. Validate required inputs
    v_email      := lower(trim(coalesce(p_email, '')));
    v_first_name := trim(coalesce(p_first_name, ''));
    v_last_name  := trim(coalesce(p_last_name, ''));
    v_role       := lower(trim(coalesce(p_role, '')));

    IF v_email = '' OR v_first_name = '' OR v_last_name = '' OR coalesce(p_password, '') = '' THEN
        RAISE EXCEPTION 'Email, password, first name, and last name are required.'
            USING ERRCODE = '22023';
    END IF;

    IF length(p_password) < 8 THEN
        RAISE EXCEPTION 'Password must be at least 8 characters long.'
            USING ERRCODE = '22023';
    END IF;

    IF v_role NOT IN ('admin', 'chair') THEN
        RAISE EXCEPTION 'Invalid role %. Allowed roles are: admin, chair.', v_role
            USING ERRCODE = '22023';
    END IF;

    -- Middle initial: optional single uppercase letter A-Z
    v_middle_initial := NULLIF(trim(coalesce(p_middle_initial, '')), '');
    IF v_middle_initial IS NOT NULL THEN
        IF length(v_middle_initial) > 1 OR upper(v_middle_initial) !~ '^[A-Z]$' THEN
            RAISE EXCEPTION 'Middle initial must be a single letter.'
                USING ERRCODE = '22023';
        END IF;
        v_middle_initial := upper(v_middle_initial);
    END IF;

    -- Program rule: required active program for chair, NULL for admin
    IF v_role = 'chair' THEN
        IF p_program_id IS NULL THEN
            RAISE EXCEPTION 'Program is required for the Chair role.'
                USING ERRCODE = '22023';
        END IF;
        SELECT id INTO v_program_id
        FROM public.program
        WHERE id = p_program_id
          AND COALESCE(archive, false) = false
        LIMIT 1;
        IF v_program_id IS NULL THEN
            RAISE EXCEPTION 'Invalid or archived program_id: %', p_program_id
                USING ERRCODE = '22023';
        END IF;
    ELSE
        v_program_id := NULL;
    END IF;

    -- Username resolution
    v_username := lower(COALESCE(NULLIF(trim(coalesce(p_username, '')), ''), split_part(v_email, '@', 1)));

    -- 3. Reject duplicate email or username
    IF EXISTS (SELECT 1 FROM auth.users WHERE lower(email) = v_email)
       OR EXISTS (SELECT 1 FROM public.users WHERE lower(email) = v_email) THEN
        RAISE EXCEPTION 'Email already exists: %', v_email
            USING ERRCODE = '23505';
    END IF;

    IF EXISTS (SELECT 1 FROM public.users WHERE lower(username) = v_username) THEN
        RAISE EXCEPTION 'Username already exists: %', v_username
            USING ERRCODE = '23505';
    END IF;

    -- 4. Hash password with bcrypt (exact format expected by Supabase GoTrue login).
    --    Never store plain text.
    v_encrypted := extensions.crypt(p_password, extensions.gen_salt('bf'));
    IF v_encrypted IS NULL OR v_encrypted = p_password THEN
        RAISE EXCEPTION 'Password hashing failed (pgcrypto unavailable).'
            USING ERRCODE = '50000';
    END IF;

    v_new_id := gen_random_uuid();

    -- 5. Insert into auth.users (pre-confirmed so user can log in immediately)
    INSERT INTO auth.users (
        id,
        instance_id,
        email,
        encrypted_password,
        email_confirmed_at,
        confirmation_token,
        recovery_token,
        email_change_token_new,
        email_change,
        raw_app_meta_data,
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
        v_email,
        v_encrypted,
        NOW(),
        '',
        '',
        '',
        '',
        jsonb_build_object(
            'provider',   'email',
            'providers',  ARRAY['email'],
            'role',       v_role,
            'program_id', v_program_id
        ),
        jsonb_build_object(
            'first_name',     v_first_name,
            'middle_initial', v_middle_initial,
            'last_name',      v_last_name,
            'username',       v_username
        ),
        'authenticated',
        'authenticated',
        NOW(),
        NOW(),
        NOW(),
        FALSE,
        FALSE
    );

    -- 6. Insert matching email identity row for Supabase Auth GoTrue
    INSERT INTO auth.identities (
        id,
        user_id,
        provider_id,
        identity_data,
        provider,
        last_sign_in_at,
        created_at,
        updated_at
    ) VALUES (
        gen_random_uuid(),
        v_new_id,
        v_new_id::text,
        jsonb_build_object(
            'sub',            v_new_id::text,
            'email',          v_email,
            'email_verified', true,
            'phone_verified', false
        ),
        'email',
        NOW(),
        NOW(),
        NOW()
    )
    ON CONFLICT DO NOTHING;

    -- 7. Ensure public.users row has the exact fields (trigger also runs on insert)
    INSERT INTO public.users (
        id, email, username, first_name, middle_initial, last_name, role, program_id, disabled
    ) VALUES (
        v_new_id,
        v_email,
        v_username,
        v_first_name,
        v_middle_initial,
        v_last_name,
        v_role,
        v_program_id,
        false
    )
    ON CONFLICT (id) DO UPDATE
    SET email          = EXCLUDED.email,
        username       = EXCLUDED.username,
        first_name     = EXCLUDED.first_name,
        middle_initial = EXCLUDED.middle_initial,
        last_name      = EXCLUDED.last_name,
        role           = EXCLUDED.role,
        program_id     = EXCLUDED.program_id,
        disabled       = false;

    RETURN v_new_id::TEXT;
END;
$$;

REVOKE ALL ON FUNCTION public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    TO authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 6. Update RLS policies to remove 'viewer' and block non-admin inserts
-- ------------------------------------------------------------------------------
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_select" ON public.users;
DROP POLICY IF EXISTS "users_insert" ON public.users;
DROP POLICY IF EXISTS "users_update" ON public.users;

CREATE POLICY "users_select" ON public.users
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('admin', 'chair')
    OR (SELECT auth.uid()) = id
);

CREATE POLICY "users_insert" ON public.users
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() = 'admin'
);

CREATE POLICY "users_update" ON public.users
FOR UPDATE TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (SELECT auth.uid()) = id
)
WITH CHECK (
    public.get_user_role() = 'admin'
    OR (
        (SELECT auth.uid()) = id
        AND role IS NOT DISTINCT FROM (SELECT u.role FROM public.users u WHERE u.id = (SELECT auth.uid()))
        AND program_id IS NOT DISTINCT FROM (SELECT u.program_id FROM public.users u WHERE u.id = (SELECT auth.uid()))
        AND disabled IS NOT DISTINCT FROM (SELECT u.disabled FROM public.users u WHERE u.id = (SELECT auth.uid()))
    )
);

-- Tighten public.schedule SELECT policy (Admin and Chair only; no viewer)
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;

CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (
        public.get_user_role() = 'chair'
        AND (
            archive = false
            OR professor_load_id IS NULL
            OR EXISTS (
                SELECT 1
                FROM public.professor_load pl
                JOIN public.course c ON pl.course_id = c.course_id
                WHERE pl.id = schedule.professor_load_id
                  AND (c.program_id = public.get_user_program_id() OR c.program_id IS NULL)
            )
        )
    )
);

-- Drop public signup helper RPC (no longer used)
DROP FUNCTION IF EXISTS public.get_public_programs();

NOTIFY pgrst, 'reload schema';

COMMIT;

