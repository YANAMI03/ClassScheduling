-- ==============================================================================
-- Migration: 20260908_mutually_optional_email_username.sql
-- Description: Make email and username columns mutually optional in public.users.
--              Enforce database-level check constraint ensuring at least one
--              identifier is provided (email IS NOT NULL OR username IS NOT NULL).
--              Update get_email_by_username RPC to check both columns simultaneously.
--              Update handle_new_auth_user() trigger for null-safe user sync.
-- ==============================================================================

-- 1. Ensure email and username permit NULL values in public.users
ALTER TABLE public.users ALTER COLUMN email DROP NOT NULL;
ALTER TABLE public.users ALTER COLUMN username DROP NOT NULL;

-- 2. Add table-level check constraint ensuring at least one identifier is non-null
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_email_or_username_required;
ALTER TABLE public.users ADD CONSTRAINT users_email_or_username_required 
    CHECK (email IS NOT NULL OR username IS NOT NULL);

-- 3. Update get_email_by_username RPC to check both email and username simultaneously
-- Resolves an input identifier against either column in a single query
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

-- 4. Update user synchronization trigger to respect omitted email or username
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_username text;
    v_email text;
    v_provided_email boolean;
    v_provided_username boolean;
BEGIN
    v_provided_email := COALESCE((new.raw_user_meta_data->>'provided_email')::boolean, true);
    v_provided_username := COALESCE((new.raw_user_meta_data->>'provided_username')::boolean, true);

    IF v_provided_email THEN
        v_email := NULLIF(TRIM(new.email), '');
    ELSE
        v_email := NULL;
    END IF;

    IF v_provided_username THEN
        v_username := NULLIF(TRIM(new.raw_user_meta_data->>'username'), '');
        IF v_username IS NULL AND v_email IS NOT NULL AND position('@' in v_email) > 0 THEN
            v_username := split_part(v_email, '@', 1);
        END IF;
    ELSE
        v_username := NULL;
    END IF;

    -- Safety fallback if metadata omitted: ensure at least one identifier is populated
    IF v_email IS NULL AND v_username IS NULL THEN
        v_email := NULLIF(TRIM(new.email), '');
        v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(v_email, '@', 1));
    END IF;

    INSERT INTO public.users (
        id,
        email,
        username,
        first_name,
        last_name,
        program,
        role,
        profile_picture
    )
    VALUES (
        new.id,
        v_email,
        v_username,
        COALESCE(new.raw_user_meta_data->>'first_name', ''),
        COALESCE(new.raw_user_meta_data->>'last_name', ''),
        new.raw_user_meta_data->>'program',
        COALESCE(new.raw_user_meta_data->>'role', 'Viewer'),
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = EXCLUDED.username,
        first_name = COALESCE(EXCLUDED.first_name, public.users.first_name),
        last_name = COALESCE(EXCLUDED.last_name, public.users.last_name),
        program = COALESCE(EXCLUDED.program, public.users.program),
        role = COALESCE(EXCLUDED.role, public.users.role),
        profile_picture = COALESCE(EXCLUDED.profile_picture, public.users.profile_picture),
        updated_at = NOW();
    RETURN new;
END;
$$;
