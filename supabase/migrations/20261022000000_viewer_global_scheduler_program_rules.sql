-- ==============================================================================
-- Migration: 20261022000000_viewer_global_scheduler_program_rules.sql
--
-- Role / program rules:
--   * New signups are ALWAYS viewers with program_id = NULL.
--   * viewer   -> program_id MUST be NULL (read-only across ALL programs).
--   * scheduler/chair -> program_id MUST NOT be NULL (scoped to that program).
--   * other roles (admin, ...) -> left as-is (unconstrained).
--
-- Also clears program_id on any existing viewer that still has one.
-- Idempotent.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. handle_new_auth_user(): new accounts are always viewers, never require or
--    receive a program. Role/program set later by an admin are preserved.
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_raw_full_name text;
    v_first_name text;
    v_middle_initial text;
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

        v_tokens := regexp_split_to_array(v_given_names, '\s+');
        IF array_length(v_tokens, 1) >= 2 THEN
            v_first_name := SUBSTRING(v_tokens[1] FROM 1 FOR 100);
            v_middle_initial := NULLIF(UPPER(LEFT(TRIM(array_to_string(v_tokens[2:array_length(v_tokens, 1)], ' ')), 1)), '');
        ELSE
            v_first_name := SUBSTRING(v_given_names FROM 1 FOR 100);
            v_middle_initial := NULL;
        END IF;
    ELSE
        v_first_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), ''), '') FROM 1 FOR 100);
        v_middle_initial := NULLIF(UPPER(LEFT(TRIM(COALESCE(new.raw_user_meta_data->>'middle_initial', '')), 1)), '');
        v_last_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), ''), '') FROM 1 FOR 100);
    END IF;

    v_middle_initial := CASE WHEN v_middle_initial ~ '^[A-Z]$' THEN v_middle_initial ELSE NULL END;
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));

    -- New accounts are ALWAYS viewers with NO program. No program is required.
    INSERT INTO public.users (
        id, email, username, first_name, middle_initial, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        COALESCE(v_first_name, ''),
        v_middle_initial,
        COALESCE(v_last_name, ''),
        NULL,
        'viewer',
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(NULLIF(public.users.username, ''), EXCLUDED.username),
        first_name = CASE WHEN public.users.first_name IS NOT NULL AND public.users.first_name <> '' THEN public.users.first_name ELSE EXCLUDED.first_name END,
        middle_initial = CASE WHEN public.users.middle_initial IS NOT NULL AND public.users.middle_initial <> '' THEN public.users.middle_initial ELSE EXCLUDED.middle_initial END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END,
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture);
        -- role and program_id are intentionally NOT changed on conflict.

    RETURN new;
END;
$$;

-- Ensure the trigger is strictly AFTER INSERT (logins must never rewrite profile).
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP TRIGGER IF EXISTS on_auth_user_signup ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();


-- ------------------------------------------------------------------------------
-- 2. List + clear program_id on existing VIEWER accounts.
--    (Run the standalone SELECT first if you want to review before applying.)
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    r record;
    affected integer := 0;
BEGIN
    FOR r IN
        SELECT id, username, email, program_id
        FROM public.users
        WHERE lower(trim(coalesce(role, ''))) = 'viewer' AND program_id IS NOT NULL
    LOOP
        RAISE NOTICE 'Clearing program_id=% for viewer % (id=%)',
            r.program_id, coalesce(r.username, r.email, '(no name)'), r.id;
        affected := affected + 1;
    END LOOP;
    RAISE NOTICE 'Viewer accounts with a program_id: % (now set to NULL)', affected;
END $$;

UPDATE public.users
SET program_id = NULL
WHERE lower(trim(coalesce(role, ''))) = 'viewer' AND program_id IS NOT NULL;


-- ------------------------------------------------------------------------------
-- 3. CHECK constraint: viewer => NULL, scheduler/chair => NOT NULL.
--    admin and any other role are left unconstrained.
-- ------------------------------------------------------------------------------
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_program_rule;
ALTER TABLE public.users ADD CONSTRAINT users_role_program_rule CHECK (
    (lower(trim(coalesce(role, ''))) = 'viewer' AND program_id IS NULL)
    OR (lower(trim(coalesce(role, ''))) IN ('chair', 'scheduler', 'dean') AND program_id IS NOT NULL)
    OR (lower(trim(coalesce(role, ''))) NOT IN ('viewer', 'chair', 'scheduler', 'dean'))
);

NOTIFY pgrst, 'reload schema';

COMMIT;


-- ==============================================================================
-- ROLLBACK
-- ==============================================================================
-- BEGIN;
-- ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_role_program_rule;
-- -- Restore the previous handle_new_auth_user by re-running:
-- --   supabase/migrations/20261018000000_users_drop_updated_at_and_rename_middle_initial.sql
-- -- (which requires a program at signup). Viewer program_id values cleared by
-- -- this migration cannot be restored automatically.
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- ==============================================================================


-- ==============================================================================
-- PRE-FLIGHT QUERY (run this manually BEFORE applying if you want to review):
--   SELECT id, username, email, program_id, role
--   FROM public.users
--   WHERE lower(trim(coalesce(role, ''))) = 'viewer' AND program_id IS NOT NULL
--   ORDER BY username, email;
-- ==============================================================================
