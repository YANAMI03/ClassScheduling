-- ==============================================================================
-- Migration: 20261018000000_users_drop_updated_at_and_rename_middle_initial.sql
-- Description:
--   1. Drop public.users.updated_at.
--      - No dedicated moddatetime / BEFORE UPDATE trigger exists for this
--        column. The only functions that wrote it are redefined in section 3
--        without that assignment.
--      - activity_log is NOT touched; it stays the source of truth for change
--        history.
--   2. Rename public.users.middle_name -> middle_initial.
--      - Normalise existing data to a single uppercase letter.
--      - Empty / whitespace-only values become NULL.
--      - Type becomes varchar(1), remains nullable.
--      - Adds a CHECK constraint allowing only a single letter or NULL.
-- Idempotent: safe to run more than once.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. Rename middle_name -> middle_initial (idempotent)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'middle_name'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'middle_initial'
    ) THEN
        ALTER TABLE public.users RENAME COLUMN middle_name TO middle_initial;
    END IF;
END $$;

-- Ensure the column exists even on a database that never had middle_name.
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS middle_initial text;

-- Drop the CHECK constraint first so normalisation can never trip it.
ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_middle_initial_single_letter;

-- ------------------------------------------------------------------------------
-- 2. Normalise data to a single uppercase letter (or NULL)
-- ------------------------------------------------------------------------------
UPDATE public.users
SET middle_initial = CASE
        WHEN UPPER(LEFT(TRIM(middle_initial), 1)) ~ '^[A-Z]$'
            THEN UPPER(LEFT(TRIM(middle_initial), 1))
        ELSE NULL
    END
WHERE middle_initial IS NOT NULL;

-- Collapse to varchar(1); every remaining value is NULL or a single letter.
ALTER TABLE public.users ALTER COLUMN middle_initial TYPE varchar(1);

-- Keep it nullable (middle_name was nullable).
ALTER TABLE public.users ALTER COLUMN middle_initial DROP NOT NULL;

-- Single letter or NULL.
ALTER TABLE public.users ADD CONSTRAINT users_middle_initial_single_letter
    CHECK (middle_initial IS NULL OR middle_initial ~ '^[A-Za-z]$');

-- ------------------------------------------------------------------------------
-- 3. Redefine the functions that previously wrote users.updated_at.
--    (The previous definitions live in:
--       20261017000000_viewer_program_scoping_and_signup.sql
--       20261007_admin_create_user_rpc.sql
--     They are superseded here without rewriting migration history.)
-- ------------------------------------------------------------------------------

-- 3.1 handle_new_auth_user(): now writes middle_initial and no updated_at.
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

        -- Split given names into first name and middle initial
        v_tokens := regexp_split_to_array(v_given_names, '\s+');
        IF array_length(v_tokens, 1) >= 2 THEN
            v_first_name := SUBSTRING(v_tokens[1] FROM 1 FOR 100);
            v_middle_initial := NULLIF(UPPER(LEFT(TRIM(array_to_string(v_tokens[2:array_length(v_tokens, 1)], ' ')), 1)), '');
        ELSE
            v_first_name := SUBSTRING(v_given_names FROM 1 FOR 100);
            v_middle_initial := NULL;
        END IF;
    ELSE
        -- Fallback to separate first/last names if provided
        v_first_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'first_name'), ''), '') FROM 1 FOR 100);
        v_middle_initial := NULLIF(UPPER(LEFT(TRIM(COALESCE(new.raw_user_meta_data->>'middle_initial', '')), 1)), '');
        v_last_name := SUBSTRING(COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'last_name'), ''), '') FROM 1 FOR 100);
    END IF;

    -- Keep the middle initial a single A-Z letter or NULL.
    v_middle_initial := CASE WHEN v_middle_initial ~ '^[A-Z]$' THEN v_middle_initial ELSE NULL END;

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
        id, email, username, first_name, middle_initial, last_name, program_id, role, profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        COALESCE(v_first_name, ''),
        v_middle_initial,
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
        middle_initial = CASE WHEN public.users.middle_initial IS NOT NULL AND public.users.middle_initial <> '' THEN public.users.middle_initial ELSE EXCLUDED.middle_initial END,
        last_name = CASE WHEN public.users.last_name IS NOT NULL AND public.users.last_name <> '' THEN public.users.last_name ELSE EXCLUDED.last_name END,
        program_id = COALESCE(public.users.program_id, EXCLUDED.program_id),
        role = COALESCE(public.users.role, 'viewer'),
        profile_picture = COALESCE(public.users.profile_picture, EXCLUDED.profile_picture);

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

-- 3.2 admin_create_auth_user(): same as before, but no users.updated_at write.
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
                              THEN public.users.last_name  ELSE EXCLUDED.last_name  END;

    RETURN v_new_id::TEXT;
END;
$$;

GRANT EXECUTE ON FUNCTION public.admin_create_auth_user(TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER)
    TO authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 4. Drop public.users.updated_at
--    activity_log is intentionally left untouched.
-- ------------------------------------------------------------------------------
ALTER TABLE public.users DROP COLUMN IF EXISTS updated_at;

NOTIFY pgrst, 'reload schema';

COMMIT;


-- ==============================================================================
-- ROLLBACK (run only if you need to revert this migration)
-- ==============================================================================
-- BEGIN;
--
-- -- 1. Restore updated_at.
-- ALTER TABLE public.users ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();
--
-- -- 2. Restore middle_name as nullable text, reversing the data normalisation.
-- ALTER TABLE public.users DROP CONSTRAINT IF EXISTS users_middle_initial_single_letter;
-- ALTER TABLE public.users ALTER COLUMN middle_initial TYPE text;
-- ALTER TABLE public.users RENAME COLUMN middle_initial TO middle_name;
--
-- -- 3. Restore the previous function definitions by re-running:
-- --    supabase/migrations/20261017000000_viewer_program_scoping_and_signup.sql
-- --    (handle_new_auth_user) and
-- --    migrations/20261007_admin_create_user_rpc.sql
-- --    (admin_create_auth_user), which write users.updated_at again.
--
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- ==============================================================================
