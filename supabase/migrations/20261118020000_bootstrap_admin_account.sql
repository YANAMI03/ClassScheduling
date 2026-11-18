-- ==============================================================================
-- Script 3 (BOOTSTRAP ADMIN ACCOUNT — run ONLY if Script 1 shows 0 active admins)
-- File: supabase/migrations/20261118020000_bootstrap_admin_account.sql
--
-- Creates an initial Admin account if no active admin exists in public.users.
-- Password is hashed with bcrypt (pgcrypto) to match Supabase Auth login.
--
-- BEFORE RUNNING:
--   Edit the credentials in the DECLARE block below (v_email, v_password,
--   v_username, v_first_name, v_middle_initial, v_last_name).
-- ==============================================================================

BEGIN;

DO $$
DECLARE
    -- >>> CHANGE THESE VALUES BEFORE RUNNING <<<
    v_email          TEXT := 'admin@neust.edu.ph';
    v_password       TEXT := 'ChangeMe123!';
    v_username       TEXT := 'admin';
    v_first_name     TEXT := 'System';
    v_middle_initial TEXT := NULL;          -- Optional single uppercase letter (e.g. 'A') or NULL
    v_last_name      TEXT := 'Administrator';
    -- >>> END CONFIGURATION <<<

    v_existing_admins integer;
    v_new_id          uuid;
    v_encrypted       text;
BEGIN
    SELECT COUNT(*)
    INTO v_existing_admins
    FROM public.users
    WHERE lower(trim(coalesce(role, ''))) = 'admin'
      AND COALESCE(disabled, false) = false;

    IF v_existing_admins > 0 THEN
        RAISE NOTICE 'Skipped: % active admin account(s) already exist.', v_existing_admins;
        RETURN;
    END IF;

    v_email    := lower(trim(v_email));
    v_username := lower(trim(v_username));

    IF length(v_password) < 8 THEN
        RAISE EXCEPTION 'Bootstrap password must be at least 8 characters long.';
    END IF;

    IF EXISTS (SELECT 1 FROM auth.users WHERE lower(email) = v_email) THEN
        RAISE EXCEPTION 'An auth.users record with email % already exists. Promote it to admin instead or choose another email.', v_email;
    END IF;

    v_encrypted := extensions.crypt(v_password, extensions.gen_salt('bf'));
    IF v_encrypted IS NULL OR v_encrypted = v_password THEN
        RAISE EXCEPTION 'Password hashing failed (pgcrypto unavailable).';
    END IF;

    v_new_id := gen_random_uuid();

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
            'role',       'admin',
            'program_id', NULL
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

    INSERT INTO public.users (
        id, email, username, first_name, middle_initial, last_name, role, program_id, disabled
    ) VALUES (
        v_new_id,
        v_email,
        v_username,
        v_first_name,
        v_middle_initial,
        v_last_name,
        'admin',
        NULL,
        false
    )
    ON CONFLICT (id) DO UPDATE
    SET email          = EXCLUDED.email,
        username       = EXCLUDED.username,
        first_name     = EXCLUDED.first_name,
        middle_initial = EXCLUDED.middle_initial,
        last_name      = EXCLUDED.last_name,
        role           = 'admin',
        program_id     = NULL,
        disabled       = false;

    RAISE NOTICE 'Created bootstrap Admin account: % (id=%)', v_email, v_new_id;
END $$;

COMMIT;

