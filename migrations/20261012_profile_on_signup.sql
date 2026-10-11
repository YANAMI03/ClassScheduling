-- ==============================================================================
-- Migration: 20261012_profile_on_signup.sql
-- Description:
--   1. Ensure created_at exists on public.users with DEFAULT now().
--   2. Define public.handle_new_user() SECURITY DEFINER trigger function:
--      - Inserts profile row in public.users on auth.users AFTER INSERT.
--      - id = new.id, email = new.email.
--      - first_name and last_name trimmed from raw_user_meta_data only.
--      - role = 'viewer' HARD-CODED (never read from metadata).
--      - program_id = NULL.
--      - username derived from email local part with numeric suffix if collides.
--      - created_at from auth user or now().
--      - ON CONFLICT (id) DO NOTHING.
--      - Exception handled with RAISE WARNING so auth signup is never blocked.
--   3. Create AFTER INSERT trigger on auth.users.
--   4. Backfill all existing orphaned auth.users rows without a public.users row
--      (both confirmed and unconfirmed).
--   5. Reload PostgREST schema cache.
-- ==============================================================================

BEGIN;

-- 1. Ensure created_at column exists on public.users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
UPDATE public.users SET created_at = now() WHERE created_at IS NULL;

-- 2. Define public.handle_new_user() SECURITY DEFINER trigger function
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_base_username text;
    v_username text;
    v_suffix integer := 1;
    v_first_name text;
    v_last_name text;
BEGIN
    -- Extract first_name and last_name from raw_user_meta_data only, trimmed
    v_first_name := TRIM(COALESCE(new.raw_user_meta_data->>'first_name', ''));
    v_last_name  := TRIM(COALESCE(new.raw_user_meta_data->>'last_name',  ''));

    -- Derive username from email's local part
    v_base_username := LOWER(TRIM(split_part(COALESCE(new.email, 'user'), '@', 1)));
    IF v_base_username = '' THEN
        v_base_username := 'user';
    END IF;

    -- Avoid username collisions by appending a numeric suffix if already taken
    v_username := v_base_username;
    WHILE EXISTS (SELECT 1 FROM public.users WHERE LOWER(username) = LOWER(v_username) AND id <> new.id) LOOP
        v_username := v_base_username || v_suffix;
        v_suffix := v_suffix + 1;
    END LOOP;

    -- Insert profile row: hard-coded role = 'viewer', program_id = NULL
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
    ON CONFLICT (id) DO NOTHING;

    RETURN new;
EXCEPTION WHEN OTHERS THEN
    -- Never let a profile creation failure block the auth signup
    RAISE WARNING 'handle_new_user failed for auth user % (%): %', new.id, new.email, SQLERRM;
    RETURN new;
END;
$$;

-- Grant execution
GRANT EXECUTE ON FUNCTION public.handle_new_user() TO authenticated, service_role;

-- 3. Trigger on auth.users
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- 4. Backfill existing orphaned auth.users rows (confirmed and unconfirmed)
DO $$
DECLARE
    r RECORD;
    v_base_username text;
    v_username text;
    v_suffix integer;
    v_first_name text;
    v_last_name text;
    v_count integer := 0;
BEGIN
    FOR r IN
        SELECT au.id, au.email, au.raw_user_meta_data, au.created_at
        FROM auth.users au
        LEFT JOIN public.users pu ON au.id = pu.id
        WHERE pu.id IS NULL
    LOOP
        v_first_name := TRIM(COALESCE(r.raw_user_meta_data->>'first_name', ''));
        v_last_name  := TRIM(COALESCE(r.raw_user_meta_data->>'last_name',  ''));

        v_base_username := LOWER(TRIM(split_part(COALESCE(r.email, 'user'), '@', 1)));
        IF v_base_username = '' THEN
            v_base_username := 'user';
        END IF;

        v_username := v_base_username;
        v_suffix := 1;
        WHILE EXISTS (SELECT 1 FROM public.users WHERE LOWER(username) = LOWER(v_username) AND id <> r.id) LOOP
            v_username := v_base_username || v_suffix;
            v_suffix := v_suffix + 1;
        END LOOP;

        BEGIN
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
                r.id,
                r.email,
                v_username,
                v_first_name,
                v_last_name,
                'viewer',
                NULL,
                COALESCE(r.created_at, now())
            )
            ON CONFLICT (id) DO NOTHING;

            v_count := v_count + 1;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Backfill failed for auth user % (%): %', r.id, r.email, SQLERRM;
        END;
    END LOOP;

    RAISE NOTICE 'Backfilled % orphaned auth.users into public.users', v_count;
END;
$$;

-- 5. Helper RPC to check orphan users count if needed
CREATE OR REPLACE FUNCTION public.get_orphan_users_count()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_count integer;
BEGIN
    SELECT count(*) INTO v_count
    FROM auth.users au
    LEFT JOIN public.users pu ON au.id = pu.id
    WHERE pu.id IS NULL;
    RETURN v_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_orphan_users_count() TO authenticated, service_role;

-- 6. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
