-- ==============================================================================
-- PHASE 1: Additive & Non-Destructive Migration
-- Safely adds professor_name, professor_key, and program_id to professor_load,
-- backfills from professor and course tables, verifies data, and adds constraints.
-- Leaves professor table and prof_id intact so existing code keeps functioning.
-- ==============================================================================

BEGIN;

-- 1. Add new columns as nullable first
ALTER TABLE public.professor_load
    ADD COLUMN IF NOT EXISTS professor_name TEXT,
    ADD COLUMN IF NOT EXISTS professor_key TEXT,
    ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id);

-- 2. Backfill professor_name, professor_key, and program_id
-- Normalization formula: lower(regexp_replace(btrim(replace(name, E'\u00a0', ' ')), '\s+', ' ', 'g'))
UPDATE public.professor_load pl
SET
    professor_name = regexp_replace(
        btrim(replace(
            concat_ws(' ', nullif(btrim(p.first_name), ''), nullif(btrim(p.last_name), ''))
        , E'\u00a0', ' ')),
        '\s+', ' ', 'g'
    ),
    professor_key = lower(regexp_replace(
        btrim(replace(
            concat_ws(' ', nullif(btrim(p.first_name), ''), nullif(btrim(p.last_name), ''))
        , E'\u00a0', ' ')),
        '\s+', ' ', 'g'
    )),
    program_id = c.program_id
FROM public.professor p, public.course c
WHERE pl.prof_id = p.prof_id
  AND pl.course_id = c.course_id;

-- 3. Verification checks (fails transaction if any row is unpopulated)
DO $$
DECLARE
    v_null_names INTEGER;
    v_null_keys INTEGER;
    v_null_progs INTEGER;
    v_total_rows INTEGER;
BEGIN
    SELECT count(*) INTO v_total_rows FROM public.professor_load;
    SELECT count(*) INTO v_null_names FROM public.professor_load WHERE professor_name IS NULL OR professor_name = '';
    SELECT count(*) INTO v_null_keys FROM public.professor_load WHERE professor_key IS NULL OR professor_key = '';
    SELECT count(*) INTO v_null_progs FROM public.professor_load WHERE program_id IS NULL;

    IF v_null_names > 0 OR v_null_keys > 0 OR v_null_progs > 0 THEN
        RAISE EXCEPTION 'Backfill failed verification: % rows total, % null names, % null keys, % null programs',
            v_total_rows, v_null_names, v_null_keys, v_null_progs;
    END IF;
    RAISE NOTICE 'Backfill verified successfully: % rows backfilled with 0 NULLs.', v_total_rows;
END $$;

-- 4. Enforce NOT NULL constraints
ALTER TABLE public.professor_load
    ALTER COLUMN professor_name SET NOT NULL,
    ALTER COLUMN professor_key SET NOT NULL,
    ALTER COLUMN program_id SET NOT NULL;

-- 5. Add index on professor_key for fast queries, joins, and conflict detection
CREATE INDEX IF NOT EXISTS idx_professor_load_professor_key
    ON public.professor_load(professor_key);

CREATE INDEX IF NOT EXISTS idx_professor_load_prog_key
    ON public.professor_load(program_id, professor_key);

-- 6. Add new Unique Constraint on (program_id, professor_key, course_id)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_professor_load_prog_key_course'
    ) THEN
        ALTER TABLE public.professor_load
            ADD CONSTRAINT uq_professor_load_prog_key_course
            UNIQUE (program_id, professor_key, course_id);
    END IF;
END $$;

COMMIT;
