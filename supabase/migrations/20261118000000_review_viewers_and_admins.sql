-- ==============================================================================
-- Script 1 (READ-ONLY PRE-FLIGHT REVIEW)
-- File: supabase/migrations/20261118000000_review_viewers_and_admins.sql
--
-- Run this FIRST in the Supabase SQL Editor to review:
--   1. Count and list of existing users with role = 'viewer' (before disabling)
--   2. Count and list of existing admin accounts (to verify at least one active
--      admin exists before removing self sign-up)
--   3. Current constraints and role defaults on public.users
--
-- This script makes NO changes to the database.
-- ==============================================================================

-- 1. Summary count of existing viewer accounts
SELECT
    COUNT(*)                                                   AS total_viewer_accounts,
    COUNT(*) FILTER (WHERE COALESCE(disabled, false) = false)  AS active_viewers_to_disable,
    COUNT(*) FILTER (WHERE COALESCE(disabled, false) = true)   AS already_disabled_viewers
FROM public.users
WHERE lower(trim(coalesce(role, ''))) = 'viewer';

-- 2. Detailed list of existing viewer accounts (to be disabled, NOT deleted)
SELECT
    id,
    first_name,
    middle_initial,
    last_name,
    email,
    username,
    role,
    program_id,
    COALESCE(disabled, false) AS disabled
FROM public.users
WHERE lower(trim(coalesce(role, ''))) = 'viewer'
ORDER BY last_name, first_name, email;

-- 3. Summary count of all users by role and disabled status
SELECT
    COALESCE(NULLIF(lower(trim(role)), ''), '<NULL>') AS role,
    COALESCE(disabled, false)                         AS disabled,
    COUNT(*)                                          AS user_count
FROM public.users
GROUP BY 1, 2
ORDER BY 1, 2;

-- 4. Active Admin account check (MUST be >= 1; if 0, run Script 3 to bootstrap an admin)
SELECT
    COUNT(*) AS active_admin_count
FROM public.users
WHERE lower(trim(coalesce(role, ''))) IN ('admin', 'super_admin', 'superadmin')
  AND COALESCE(disabled, false) = false;

-- 5. Current CHECK constraints and role column default on public.users
SELECT
    conname                         AS constraint_name,
    pg_get_constraintdef(oid)       AS constraint_definition,
    convalidated                    AS is_validated
FROM pg_constraint
WHERE conrelid = 'public.users'::regclass
  AND contype = 'c'
ORDER BY conname;

