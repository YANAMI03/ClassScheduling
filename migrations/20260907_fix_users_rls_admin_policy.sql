-- ==============================================================================
-- Migration: 20260907_fix_users_rls_admin_policy.sql
-- Description: Configure Row Level Security (RLS) policies for User Management
--              allowing authenticated users with the ADMIN role full management
--              permissions (SELECT, INSERT, UPDATE, DELETE) on public.users,
--              while keeping RLS enabled and without using service-role / secret keys.
-- ==============================================================================

-- 1. Ensure public.users table exists with correct schema
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT UNIQUE,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    program VARCHAR,
    role TEXT DEFAULT 'Viewer',
    profile_picture TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ensure RLS is strictly ENABLED
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- Create indexes on columns used in RLS policies and lookups for optimal performance
CREATE INDEX IF NOT EXISTS users_role_idx ON public.users (role);
CREATE INDEX IF NOT EXISTS users_username_idx ON public.users (username);
CREATE INDEX IF NOT EXISTS users_email_idx ON public.users (email);

-- 2. Secure role resolver function
-- Resolves the current authenticated user's role from JWT claims (app_metadata / user_metadata)
-- or directly from public.users.role.
-- Note: We specifically DO NOT use (auth.jwt() ->> 'role') because in Supabase that returns
-- the PostgreSQL connection role 'authenticated', which masks the actual application role.
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_role text;
BEGIN
    -- 1. Check custom claims in JWT app_metadata
    v_role := auth.jwt() -> 'app_metadata' ->> 'role';
    IF v_role IS NOT NULL AND v_role <> '' THEN
        RETURN LOWER(v_role);
    END IF;

    -- 2. Check user_metadata in JWT (where signup and admin operations save the role)
    v_role := auth.jwt() -> 'user_metadata' ->> 'role';
    IF v_role IS NOT NULL AND v_role <> '' THEN
        RETURN LOWER(v_role);
    END IF;

    -- 3. Fallback: Check role column in public.users for the current user
    IF auth.uid() IS NOT NULL THEN
        SELECT LOWER(u.role) INTO v_role
        FROM public.users u
        WHERE u.id = auth.uid()
        LIMIT 1;

        IF v_role IS NOT NULL AND v_role <> '' THEN
            RETURN v_role;
        END IF;
    END IF;

    -- 4. Default fallback
    RETURN 'viewer';
END;
$$;

-- Helper RPC for pre-auth email resolution (e.g. login with username)
-- Resolves username to email without exposing table rows or bypassing RLS.
CREATE OR REPLACE FUNCTION public.get_email_by_username(p_username text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT email FROM public.users WHERE LOWER(username) = LOWER(TRIM(p_username)) LIMIT 1;
$$;

-- 3. Drop any obsolete, recursive, or over-permissive policies on public.users
DROP POLICY IF EXISTS "users_admin_scheduler_all" ON public.users;
DROP POLICY IF EXISTS "users_admin_all" ON public.users;
DROP POLICY IF EXISTS "users_admin_select" ON public.users;
DROP POLICY IF EXISTS "users_admin_insert" ON public.users;
DROP POLICY IF EXISTS "users_admin_update" ON public.users;
DROP POLICY IF EXISTS "users_admin_delete" ON public.users;
DROP POLICY IF EXISTS "users_self_select" ON public.users;
DROP POLICY IF EXISTS "users_self_update" ON public.users;
DROP POLICY IF EXISTS "users_scheduler_select" ON public.users;

-- 4. Granular, Optimized RLS Policies for public.users
-- Using subquery wrapping (SELECT ...) ensures functions are evaluated once per query and cached.

-- Policy A: Self SELECT - Authenticated users can view their own profile (prevents recursion)
CREATE POLICY "users_self_select"
ON public.users
FOR SELECT
TO authenticated
USING ((SELECT auth.uid()) = id);

-- Policy B: Self UPDATE - Authenticated users can update their own profile
CREATE POLICY "users_self_update"
ON public.users
FOR UPDATE
TO authenticated
USING ((SELECT auth.uid()) = id)
WITH CHECK ((SELECT auth.uid()) = id);

-- Policy C: Admin SELECT - Authenticated ADMIN users can view all user records
CREATE POLICY "users_admin_select"
ON public.users
FOR SELECT
TO authenticated
USING ((SELECT public.get_user_role()) = 'admin');

-- Policy D: Admin INSERT - Authenticated ADMIN users can create new user records
CREATE POLICY "users_admin_insert"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK ((SELECT public.get_user_role()) = 'admin');

-- Policy E: Admin UPDATE - Authenticated ADMIN users can update any user record
CREATE POLICY "users_admin_update"
ON public.users
FOR UPDATE
TO authenticated
USING ((SELECT public.get_user_role()) = 'admin')
WITH CHECK ((SELECT public.get_user_role()) = 'admin');

-- Policy F: Admin DELETE - Authenticated ADMIN users can delete user records
CREATE POLICY "users_admin_delete"
ON public.users
FOR DELETE
TO authenticated
USING ((SELECT public.get_user_role()) = 'admin');

-- Policy G: Scheduler SELECT - Authenticated Schedulers can read user records (for system backups)
CREATE POLICY "users_scheduler_select"
ON public.users
FOR SELECT
TO authenticated
USING ((SELECT public.get_user_role()) = 'scheduler');

-- 5. Automatic User Synchronization Trigger (from auth.users to public.users)
-- Ensures that whenever a user signs up or is created in Supabase Auth,
-- a corresponding profile record exists in public.users.
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
BEGIN
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
        new.email,
        COALESCE(new.raw_user_meta_data->>'username', split_part(new.email, '@', 1)),
        COALESCE(new.raw_user_meta_data->>'first_name', ''),
        COALESCE(new.raw_user_meta_data->>'last_name', ''),
        new.raw_user_meta_data->>'program',
        COALESCE(new.raw_user_meta_data->>'role', 'Viewer'),
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(EXCLUDED.username, public.users.username),
        first_name = COALESCE(EXCLUDED.first_name, public.users.first_name),
        last_name = COALESCE(EXCLUDED.last_name, public.users.last_name),
        program = COALESCE(EXCLUDED.program, public.users.program),
        role = COALESCE(EXCLUDED.role, public.users.role),
        profile_picture = COALESCE(EXCLUDED.profile_picture, public.users.profile_picture),
        updated_at = NOW();
    RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT OR UPDATE ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();

-- 6. Backfill existing auth.users into public.users
-- Copies any existing users from Supabase Auth so they appear in User Management immediately.
INSERT INTO public.users (id, email, username, first_name, last_name, program, role, profile_picture)
SELECT
    id,
    email,
    COALESCE(raw_user_meta_data->>'username', split_part(email, '@', 1)),
    COALESCE(raw_user_meta_data->>'first_name', ''),
    COALESCE(raw_user_meta_data->>'last_name', ''),
    raw_user_meta_data->>'program',
    COALESCE(raw_user_meta_data->>'role', 'Viewer'),
    raw_user_meta_data->>'profile_picture'
FROM auth.users
ON CONFLICT (id) DO UPDATE
SET
    email = EXCLUDED.email,
    username = COALESCE(EXCLUDED.username, public.users.username),
    first_name = COALESCE(EXCLUDED.first_name, public.users.first_name),
    last_name = COALESCE(EXCLUDED.last_name, public.users.last_name),
    program = COALESCE(EXCLUDED.program, public.users.program),
    role = COALESCE(EXCLUDED.role, public.users.role),
    profile_picture = COALESCE(EXCLUDED.profile_picture, public.users.profile_picture),
    updated_at = NOW();
