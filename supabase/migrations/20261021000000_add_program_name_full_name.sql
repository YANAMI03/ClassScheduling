-- ==============================================================================
-- Migration: 20261021000000_add_program_name_full_name.sql
-- Phase 3: add the real full name to public.program.
--
--   program.program_code = short code (BSIT, BSDS, ...)  [renamed in Phase 2]
--   program.program_name = official full degree name      [added here]
-- ==============================================================================

BEGIN;

ALTER TABLE public.program ADD COLUMN IF NOT EXISTS program_name varchar;

-- ------------------------------------------------------------------------------
-- Backfill the known programs.
-- ------------------------------------------------------------------------------
UPDATE public.program
SET program_name = 'Bachelor of Science in Information Technology'
WHERE upper(btrim(program_code)) = 'BSIT'
  AND (program_name IS NULL OR btrim(program_name) = '');

UPDATE public.program
SET program_name = 'Bachelor of Science in Data Science'
WHERE upper(btrim(program_code)) = 'BSDS'
  AND (program_name IS NULL OR btrim(program_name) = '');

-- ------------------------------------------------------------------------------
-- Other existing program codes: FILL IN EACH FULL NAME BELOW.
-- Add one UPDATE per code (uncomment / duplicate as needed). Any code left
-- unmapped is reported by the block below and temporarily uses its own code.
-- ------------------------------------------------------------------------------
-- UPDATE public.program SET program_name = 'Bachelor of Science in Business Administration'
--   WHERE upper(btrim(program_code)) = 'BSBA' AND (program_name IS NULL OR btrim(program_name) = '');
-- UPDATE public.program SET program_name = 'Bachelor of Science in Computer Science'
--   WHERE upper(btrim(program_code)) = 'BSCS' AND (program_name IS NULL OR btrim(program_name) = '');
-- UPDATE public.program SET program_name = 'Bachelor of Science in Information Systems'
--   WHERE upper(btrim(program_code)) = 'BSIS' AND (program_name IS NULL OR btrim(program_name) = '');

-- ------------------------------------------------------------------------------
-- Report anything still unmapped, then place the code as a temporary
-- placeholder.  ==> REVIEW AND CORRECT THESE ON THE PROGRAMS SCREEN. <==
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT program_code
        FROM public.program
        WHERE program_name IS NULL OR btrim(program_name) = ''
    LOOP
        RAISE WARNING 'Program % has no full name mapped; using the code as a temporary placeholder.', r.program_code;
    END LOOP;
END $$;

UPDATE public.program
SET program_name = program_code
WHERE program_name IS NULL OR btrim(program_name) = '';

ALTER TABLE public.program ALTER COLUMN program_name SET NOT NULL;

-- ------------------------------------------------------------------------------
-- program_code is unique (inherited from the original program_name UNIQUE
-- constraint; PostgreSQL keeps the constraint when the column is renamed).
-- Add a plain UNIQUE only if one is somehow missing.
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        WHERE t.relname = 'program'
          AND i.indisunique
          AND i.indexprs IS NULL
          AND pg_get_indexdef(i.indexrelid) ILIKE '%(program_code)%'
    ) THEN
        ALTER TABLE public.program ADD CONSTRAINT program_program_code_key UNIQUE (program_code);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;


-- ==============================================================================
-- ROLLBACK (run only if you need to revert this migration)
-- ==============================================================================
-- BEGIN;
-- ALTER TABLE public.program DROP COLUMN IF EXISTS program_name;
-- NOTIFY pgrst, 'reload schema';
-- COMMIT;
-- ==============================================================================
