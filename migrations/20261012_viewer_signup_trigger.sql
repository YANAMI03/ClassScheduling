-- ==============================================================================
-- Migration: 20261012_viewer_signup_trigger.sql
-- Description:
--   1. Ensures SECURITY DEFINER trigger handle_new_user() on auth.users:
--      - Runs with fixed search_path = public.
--      - Extracts full_name (or first_name, last_name) from user metadata,
--        sanitizing and truncating to safe column lengths (100 chars).
--      - Derives unique username from email prefix with numeric suffix on conflict.
--      - ALWAYS assigns role = 'viewer' and program_id = NULL (hardcoded).
--      - Completely ignores any role or program specified in user_metadata.
--      - Inserts into public.users.
--      - Writes a signup audit record into public.activity_log.
--      - If inserting into public.users fails, the error is NOT suppressed,
--        causing Supabase Auth to roll back the auth.users transaction,
--        guaranteeing NO half-created/orphaned accounts.
--   2. Ensures RLS policies and trigger guards on public.users prevent
--      any privilege escalation (users cannot insert or update their own role/program).
-- ==============================================================================

-- REMINDER: Please take a database backup before applying this migration in Supabase SQL Editor.

BEGIN;

-- 1. Ensure created_at exists on public.users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();

-- 2. Define or replace the trigger function on auth.users
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_raw_full_name text;
    v_first_name text;
    v_last_name text;
    v_base_username text;
    v_username text;
    v_suffix integer := 1;
BEGIN
    -- Extract full_name / first_name / last_name from raw_user_meta_data
    v_raw_full_name := TRIM(COALESCE(new.raw_user_meta_data->>'full_name', ''));
    
    IF v_raw_full_name <> '' THEN
        v_first_name := split_part(v_raw_full_name, ' ', 1);
        v_last_name  := TRIM(SUBSTRING(v_raw_full_name FROM LENGTH(v_first_name) + 1));
    ELSE
        v_first_name := TRIM(COALESCE(new.raw_user_meta_data->>'first_name', ''));
        v_last_name  := TRIM(COALESCE(new.raw_user_meta_data->>'last_name',  ''));
    END IF;

    -- If first_name is empty, fallback to email prefix
    IF v_first_name IS NULL OR v_first_name = '' THEN
        v_first_name := split_part(COALESCE(new.email, 'User'), '@', 1);
    END IF;

    -- Truncate to safe column length (100 characters)
    v_first_name := SUBSTRING(v_first_name FROM 1 FOR 100);
    v_last_name  := SUBSTRING(COALESCE(v_last_name, '') FROM 1 FOR 100);

    -- Derive unique username from email local part
    v_base_username := LOWER(TRIM(split_part(COALESCE(new.email, 'user'), '@', 1)));
    IF v_base_username = '' THEN
        v_base_username := 'user';
    END IF;
    -- Clean non-alphanumeric characters for username
    v_base_username := regexp_replace(v_base_username, '[^a-z0-9_]', '', 'g');
    IF v_base_username = '' THEN
        v_base_username := 'user';
    END IF;
    v_base_username := SUBSTRING(v_base_username FROM 1 FOR 40);

    v_username := v_base_username;
    WHILE EXISTS (SELECT 1 FROM public.users WHERE LOWER(username) = LOWER(v_username) AND id <> new.id) LOOP
        v_username := v_base_username || v_suffix;
        v_suffix := v_suffix + 1;
    END LOOP;

    -- Insert matching profile row:
    -- HARDCODED role = 'viewer', program_id = NULL.
    -- Any role or program in metadata is strictly ignored.
    INSERT INTO public.users (
        id,
        email,
        username,
        first_name,
        last_name,
        role,
        program_id,
        created_at
    ) VALUES (
        new.id,
        new.email,
        v_username,
        v_first_name,
        v_last_name,
        'viewer',
        NULL,
        COALESCE(new.created_at, now())
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE EXCLUDED.first_name END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END;

    -- Record signup in public.activity_log if possible
    BEGIN
        INSERT INTO public.activity_log (
            user_id,
            username,
            action,
            target_type,
            target_detail
        ) VALUES (
            new.id,
            v_username,
            'signup',
            'user',
            'Self-registered: ' || COALESCE(new.email, v_username) || ' (role: viewer)'
        );
    EXCEPTION WHEN OTHERS THEN
        -- Non-critical activity log entry failure does not abort the profile creation
        NULL;
    END;

    -- Return new to complete transaction successfully
    RETURN new;
    -- Note: No top-level EXCEPTION block. If public.users insertion fails,
    -- the exception is raised and Supabase Auth rolls back the auth.users creation!
END;
$$;

-- Grant execution
GRANT EXECUTE ON FUNCTION public.handle_new_user() TO authenticated, service_role, anon;

-- Recreate trigger on auth.users
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- 3. Verify / Reinforce RLS on public.users
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Ensure privilege escalation guard triggers on public.users
CREATE OR REPLACE FUNCTION public.protect_user_role_and_program()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_caller_role text;
BEGIN
    v_caller_role := public.get_user_role();

    -- Super Admin and Admin can insert/update any role and program
    IF v_caller_role IN ('super_admin', 'admin') THEN
        RETURN NEW;
    END IF;

    -- Non-admin cannot change their own or another user's role
    IF TG_OP = 'UPDATE' AND NEW.role IS DISTINCT FROM OLD.role THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify roles.';
    END IF;

    -- Non-admin cannot change their own or another user's program_id
    IF TG_OP = 'UPDATE' AND NEW.program_id IS DISTINCT FROM OLD.program_id THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify program assignment.';
    END IF;

    -- Non-admin cannot insert a role other than 'viewer' or program_id other than NULL
    IF TG_OP = 'INSERT' AND (LOWER(TRIM(COALESCE(NEW.role, 'viewer'))) <> 'viewer' OR NEW.program_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Self-registration only permits viewer role with NULL program.';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_user_role_and_program ON public.users;
CREATE TRIGGER trg_protect_user_role_and_program
    BEFORE INSERT OR UPDATE ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.protect_user_role_and_program();

-- 4. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
