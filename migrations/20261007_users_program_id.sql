-- ==============================================================================
-- Migration: 20261007_users_program_id.sql
-- Description: Idempotent migration to ensure users.program_id foreign key,
--              backfill from legacy text program column if present,
--              drop legacy program column if present,
--              clear program_id for administrators,
--              and notify PostgREST to reload the schema cache.
-- ==============================================================================

BEGIN;

-- 1. Ensure public.program table exists
CREATE TABLE IF NOT EXISTS public.program (
    id SERIAL PRIMARY KEY,
    program_name VARCHAR(100) NOT NULL UNIQUE,
    full_name VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Add users.program_id if missing (FK -> program.id ON DELETE SET NULL)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'program_id'
    ) THEN
        ALTER TABLE public.users ADD COLUMN program_id INTEGER REFERENCES public.program(id) ON DELETE SET NULL;
    END IF;
END $$;

-- 3. If legacy text column 'program' exists in public.users, backfill program_id
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'program'
    ) THEN
        -- Backfill program_id from program table by matching program_name
        UPDATE public.users u
        SET program_id = p.id
        FROM public.program p
        WHERE u.program_id IS NULL
          AND u.program IS NOT NULL
          AND (
            UPPER(TRIM(u.program)) = UPPER(TRIM(p.program_name))
            OR UPPER(TRIM(u.program)) = (CASE WHEN UPPER(TRIM(p.program_name)) = 'BSIT' THEN 'CICT' WHEN UPPER(TRIM(p.program_name)) = 'BSBA' THEN 'CMBT' ELSE '' END)
          );

        -- Drop legacy text column 'program' to prevent schema cache ambiguity
        ALTER TABLE public.users DROP COLUMN IF EXISTS program;
    END IF;
END $$;

-- 4. Clear program_id for Admin and Super Admin roles
UPDATE public.users
SET program_id = NULL
WHERE LOWER(role) IN ('admin', 'super_admin');

-- 5. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

COMMIT;
