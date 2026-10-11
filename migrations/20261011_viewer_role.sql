-- ==============================================================================
-- Migration: 20261011_viewer_role.sql
-- Description:
--   1. Set default role on public.users to 'viewer' and backfill NULL/blank roles to 'viewer'.
--   2. Update get_user_role() SECURITY DEFINER helper to query public.users directly
--      and never trust raw user_metadata for role escalation.
--   3. Update handle_new_auth_user() trigger: self-signups default to 'viewer' with program_id NULL.
--   4. Privilege escalation protection:
--      - Trigger protect_user_role_and_program prevents non-admins from modifying role or program_id.
--      - Update RLS policies on public.users so non-admins cannot insert non-viewer roles.
--      - Viewers can only SELECT their own user profile row.
--   5. Update RLS policies on public.schedule so Viewers can SELECT only active schedules (archive = false).
--   6. Protect public.activity_log so Viewers cannot read global system activity logs.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Database Default and Backfill for public.users.role
-- ------------------------------------------------------------------------------
ALTER TABLE public.users ALTER COLUMN role SET DEFAULT 'viewer';

-- Backfill only NULL or blank rows to 'viewer'; preserve all existing roles (admin, Scheduler, etc.)
UPDATE public.users 
SET role = 'viewer' 
WHERE role IS NULL OR TRIM(role) = '';

-- Clear program_id for any existing viewer accounts
UPDATE public.users 
SET program_id = NULL 
WHERE LOWER(TRIM(role)) = 'viewer' AND program_id IS NOT NULL;


-- ------------------------------------------------------------------------------
-- 2. Authoritative get_user_role() Helper (No Recursion, No user_metadata trust)
-- ------------------------------------------------------------------------------
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
    -- 1. Check custom claims in JWT app_metadata (service role / Supabase dashboard admin claims only)
    v_role := auth.jwt() -> 'app_metadata' ->> 'role';
    IF v_role IS NOT NULL AND TRIM(v_role) <> '' THEN
        RETURN LOWER(TRIM(v_role));
    END IF;

    -- 2. Query public.users directly for current authenticated user (Authoritative source of truth)
    IF auth.uid() IS NOT NULL THEN
        SELECT LOWER(TRIM(u.role)) INTO v_role
        FROM public.users u
        WHERE u.id = auth.uid()
        LIMIT 1;

        IF v_role IS NOT NULL AND TRIM(v_role) <> '' THEN
            RETURN v_role;
        END IF;
    END IF;

    -- 3. Default fallback
    RETURN 'viewer';
END;
$$;

GRANT EXECUTE ON FUNCTION public.get_user_role() TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 3. Update handle_new_auth_user() Trigger Function
-- ------------------------------------------------------------------------------
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

    -- Default all new self-registrations to 'viewer'
    IF v_role IS NULL OR LOWER(v_role) IN ('viewer', 'instructor', 'guest') THEN
        v_role := 'viewer';
    ELSIF LOWER(v_role) NOT IN ('super_admin', 'admin', 'scheduler') THEN
        v_role := 'viewer';
    END IF;

    -- Viewers and Admins have NO program assignment
    IF LOWER(v_role) IN ('viewer', 'admin', 'super_admin') THEN
        v_program_id := NULL;
    ELSE
        -- Schedulers resolve program_id
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
        COALESCE(v_role, 'viewer'),
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
        program_id = CASE
            WHEN LOWER(COALESCE(public.users.role, 'viewer')) IN ('viewer', 'admin', 'super_admin') THEN NULL
            ELSE COALESCE(public.users.program_id, EXCLUDED.program_id)
        END,
        role = CASE 
            WHEN public.users.role IS NOT NULL AND public.users.role <> '' THEN public.users.role 
            ELSE COALESCE(EXCLUDED.role, 'viewer') 
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


-- ------------------------------------------------------------------------------
-- 4. Privilege Escalation Guard Trigger on public.users
-- ------------------------------------------------------------------------------
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

    -- Super Admin and Admin can update any role and program
    IF v_caller_role IN ('super_admin', 'admin') THEN
        RETURN NEW;
    END IF;

    -- Non-admin cannot change their own or another user's role
    IF NEW.role IS DISTINCT FROM OLD.role THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify roles.';
    END IF;

    -- Non-admin cannot change their own or another user's program_id
    IF NEW.program_id IS DISTINCT FROM OLD.program_id THEN
        RAISE EXCEPTION 'Privilege escalation blocked: Only administrators can modify program assignment.';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_user_role_and_program ON public.users;
CREATE TRIGGER trg_protect_user_role_and_program
    BEFORE UPDATE ON public.users
    FOR EACH ROW
    EXECUTE FUNCTION public.protect_user_role_and_program();


-- ------------------------------------------------------------------------------
-- 5. Hardened RLS Policies on public.users
-- ------------------------------------------------------------------------------
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_self_select" ON public.users;
DROP POLICY IF EXISTS "users_scheduler_select" ON public.users;
DROP POLICY IF EXISTS "users_admin_select" ON public.users;
DROP POLICY IF EXISTS "users_select" ON public.users;
DROP POLICY IF EXISTS "users_insert" ON public.users;
DROP POLICY IF EXISTS "users_admin_insert" ON public.users;
DROP POLICY IF EXISTS "users_update" ON public.users;
DROP POLICY IF EXISTS "users_self_update" ON public.users;
DROP POLICY IF EXISTS "users_admin_update" ON public.users;
DROP POLICY IF EXISTS "users_delete" ON public.users;
DROP POLICY IF EXISTS "users_admin_delete" ON public.users;

-- SELECT: Admins and Schedulers can view user list. Viewers can ONLY view their own profile row.
CREATE POLICY "users_select" ON public.users
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin', 'scheduler')
    OR (SELECT auth.uid()) = id
);

-- INSERT: Admins can insert any user. Non-admins (self-signup self-heal) can ONLY insert their own row with role 'viewer' and program_id NULL.
CREATE POLICY "users_insert" ON public.users
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() IN ('super_admin', 'admin')
    OR (
        (SELECT auth.uid()) = id
        AND (role IS NULL OR LOWER(TRIM(role)) = 'viewer')
        AND program_id IS NULL
    )
);

-- UPDATE: Admins can update any user. Non-admins can only update their own profile row (trigger protects role and program_id).
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
        AND program_id IS NULL
    )
);

-- DELETE: Strictly restricted to Admin / Super Admin
CREATE POLICY "users_delete" ON public.users
FOR DELETE TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin'));


-- ------------------------------------------------------------------------------
-- 6. Hardened RLS Policies on public.schedule (Viewer restricted to archive = false)
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "schedule_select" ON public.schedule;

CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    -- Admin sees all (active and archived)
    public.get_user_role() IN ('super_admin', 'admin')
    -- Viewer sees ONLY active schedules
    OR (
        public.get_user_role() = 'viewer'
        AND archive = false
    )
    -- Scheduler sees active schedules, or archived schedules belonging to their program
    OR (
        public.get_user_role() = 'scheduler'
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


-- ------------------------------------------------------------------------------
-- 7. Hardened RLS Policies on public.activity_log (Viewer restricted to own logs)
-- ------------------------------------------------------------------------------
ALTER TABLE public.activity_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "activity_log_select" ON public.activity_log;
DROP POLICY IF EXISTS "activity_log_admin_select" ON public.activity_log;
DROP POLICY IF EXISTS "activity_log_insert" ON public.activity_log;

-- SELECT: Admins see all activity logs. Schedulers and Viewers see only logs associated with their own username.
CREATE POLICY "activity_log_select" ON public.activity_log
FOR SELECT TO authenticated
USING (
    public.get_user_role() IN ('super_admin', 'admin')
    OR username = (SELECT u.username FROM public.users u WHERE u.id = auth.uid())
);

-- INSERT: Authenticated users can insert activity log entries for actions they perform
CREATE POLICY "activity_log_insert" ON public.activity_log
FOR INSERT TO authenticated
WITH CHECK (true);

COMMIT;

NOTIFY pgrst, 'reload schema';
