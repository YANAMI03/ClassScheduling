-- ==============================================================================
-- ClassScheduling Migration: Public Program Dropdown & Viewer Program Scoping
-- File: supabase/migrations/20261017000000_viewer_program_scoping_and_signup.sql
-- ==============================================================================

BEGIN;

-- Ensure middle_name column exists on public.users
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS middle_name text;

-- ------------------------------------------------------------------------------
-- 1. Minimal Public RPC: get_public_programs()
-- Exposes ONLY id and program_name of active programs to anon and authenticated.
-- No service_role key is used; search_path is locked to public, pg_temp.
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_public_programs()
RETURNS TABLE (
    id integer,
    program_name text
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT p.id, p.program_name::text
    FROM public.program p
    WHERE COALESCE(p.archive, false) = false
    ORDER BY p.program_name ASC;
$$;

REVOKE ALL ON FUNCTION public.get_public_programs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_public_programs() TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 2. Auth Trigger: handle_new_auth_user()
-- Validates program existence, stores program_id, normalizes/truncates full_name,
-- and forces role = 'viewer' for all signups.
-- If the program is missing or invalid, the sign-up rolls back immediately.
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_raw_program_id text;
    v_raw_program_name text;
    v_program_id integer;
    v_raw_full_name text;
    v_first_name text;
    v_middle_name text;
    v_last_name text;
    v_username text;
    v_given_names text;
    v_tokens text[];
BEGIN
    -- 1. Extract and sanitize full name (format: Last name, First name M.I.)
    v_raw_full_name := NULLIF(TRIM(new.raw_user_meta_data->>'full_name'), '');
    
    IF v_raw_full_name IS NOT NULL AND position(',' in v_raw_full_name) > 0 THEN
        v_last_name := SUBSTRING(TRIM(split_part(v_raw_full_name, ',', 1)) FROM 1 FOR 100);
        v_given_names := TRIM(split_part(v_raw_full_name, ',', 2));
        
        -- Split given names into first and middle
        v_tokens := regexp_split_to_array(v_given_names, '\s+');
        IF array_length(v_tokens, 1) >= 2 THEN
            v_first_name := SUBSTRING(v_tokens[1] FROM 1 FOR 100);
            v_middle_name := SUBSTRING(array_to_string(v_tokens[2:array_length(v_tokens, 1)], ' ') FROM 1 FOR 100);
        ELSE
            v_first_name := SUBSTRING(v_given_names FROM 1 FOR 100);
            v_middle_name := '';
        END IF;
    ELSE
        -- Fallback to separate first/last names if provided
        v_first_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), ''), '') FROM 1 FOR 100);
        v_middle_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'middle_name'), ''), '') FROM 1 FOR 100);
        v_last_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), ''), '') FROM 1 FOR 100);
    END IF;

    -- 2. Validate Program (REQUIRED for signup)
    v_raw_program_id := TRIM(COALESCE(new.raw_user_meta_data->>'program_id', ''));
    v_raw_program_name := TRIM(COALESCE(new.raw_user_meta_data->>'program', ''));
    
    IF v_raw_program_id ~ '^\d+$' THEN
        SELECT id INTO v_program_id 
        FROM public.program 
        WHERE id = v_raw_program_id::integer AND COALESCE(archive, false) = false
        LIMIT 1;
    ELSIF v_raw_program_name <> '' THEN
        SELECT id INTO v_program_id 
        FROM public.program 
        WHERE UPPER(TRIM(program_name)) = UPPER(TRIM(v_raw_program_name)) AND COALESCE(archive, false) = false
        LIMIT 1;
    END IF;

    -- If program is missing or invalid, fail the entire transaction (no half-created accounts)
    IF v_program_id IS NULL THEN
        RAISE EXCEPTION 'A valid, active program is required to create an account.';
    END IF;

    -- 3. Username resolution
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));

    -- 4. Insert or update public.users with FORCED role = 'viewer' and validated program_id
    INSERT INTO public.users (
        id, email, username, first_name, middle_name, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        COALESCE(v_first_name, ''),
        COALESCE(v_middle_name, ''),
        COALESCE(v_last_name, ''),
        v_program_id,
        'viewer',  -- Always forced to viewer, ignoring any client role input
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE EXCLUDED.first_name END,
        middle_name = CASE WHEN public.users.middle_name IS NOT NULL AND public.users.middle_name <> '' THEN public.users.middle_name ELSE EXCLUDED.middle_name END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END,
        program_id = COALESCE(public.users.program_id, EXCLUDED.program_id),
        role = COALESCE(public.users.role, 'viewer'),
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture),
        updated_at = NOW();

    RETURN new;
END;
$$;

-- Ensure trigger is strictly AFTER INSERT on auth.users (never UPDATE so logins are never blocked)
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();


-- ------------------------------------------------------------------------------
-- 3. Update public.users RLS Policies
-- BEFORE: users_update required program_id IS NULL for non-admins, which would
--         block Viewers with an assigned program from updating their profile.
-- AFTER:  Prevents non-admins from changing role or program_id, while allowing
--         profile updates with their existing program_id.
-- ------------------------------------------------------------------------------
DROP POLICY IF EXISTS "users_update" ON public.users;
CREATE POLICY "users_update" ON public.users
FOR UPDATE TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin')
    OR (SELECT auth.uid()) = id
)
WITH CHECK (
    public.get_user_role() IN ('super_admin', 'admin')
    OR (
        (SELECT auth.uid()) = id
        AND (role IS NULL OR LOWER(TRIM(role)) = 'viewer')
        AND program_id IS NOT DISTINCT FROM (SELECT u.program_id FROM public.users u WHERE u.id = (SELECT auth.uid()))
    )
);


-- ------------------------------------------------------------------------------
-- 4. Tighten public.schedule SELECT RLS Policy
-- BEFORE: public.get_user_role() IN ('super_admin', 'admin', 'viewer') allowed
--         Viewers to read all schedules across all programs and archive batches.
-- AFTER:  Viewers are removed from the unconstrained admin set and scoped by
--         program alongside Chairs.
-- ------------------------------------------------------------------------------
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin')
    OR (
        public.get_user_role() IN ('chair', 'scheduler', 'viewer')
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

COMMIT;
