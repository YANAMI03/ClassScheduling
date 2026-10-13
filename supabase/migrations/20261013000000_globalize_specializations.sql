-- ==============================================================================
-- Migration: 20261013000000_globalize_specializations.sql
--
-- Removes the per-program scoping of specializations (the ERD loop
-- course -> program -> specialization -> course).
--
--   1. Detects duplicate specializations across programs (same name OR same code).
--   2. Merges each duplicate set into a single surviving row and repoints
--      course.specialization_id to the survivor.
--   3. Drops specializations.program_id (FK + indexes + column).
--   4. Enforces global uniqueness of specialization names.
--
-- Keeps:
--   * course.program_id (unchanged)
--   * course.specialization_id (nullable FK -> specializations; NULL = General)
--
-- Prerequisites:
--   * 20261012200000_add_specializations_table.sql has been applied.
--
-- This migration is idempotent and safe to re-run.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 0. GUARD: the table must exist (phase 1 applied)
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.specializations') IS NULL THEN
        RAISE EXCEPTION
            'public.specializations does not exist. Run 20261012200000_add_specializations_table.sql first.';
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 0b. DIAGNOSTIC: report duplicates before changing anything.
--     (Also available as an ad-hoc query — see the commented SELECT at the end.)
-- ------------------------------------------------------------------------------
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT lower(btrim(name)) AS key, count(*) AS n
        FROM public.specializations
        GROUP BY 1
        HAVING count(*) > 1
    LOOP
        RAISE NOTICE 'Duplicate specialization name "%": % rows will be merged.', r.key, r.n;
    END LOOP;

    FOR r IN
        SELECT lower(btrim(code)) AS key, count(*) AS n
        FROM public.specializations
        WHERE nullif(btrim(coalesce(code, '')), '') IS NOT NULL
        GROUP BY 1
        HAVING count(*) > 1
    LOOP
        RAISE NOTICE 'Duplicate specialization code "%": % rows will be merged.', r.key, r.n;
    END LOOP;
END $$;

-- ------------------------------------------------------------------------------
-- 1. Build connected components of duplicates.
--    Two rows are linked when they share a name or a non-empty code
--    (case- and whitespace-insensitive). The survivor is the smallest
--    specialization_id in the component.
-- ------------------------------------------------------------------------------
CREATE TEMP TABLE tmp_spec_survivor ON COMMIT DROP AS
WITH RECURSIVE
pair AS (
    SELECT s1.specialization_id AS a, s2.specialization_id AS b
    FROM public.specializations s1
    JOIN public.specializations s2
      ON s1.specialization_id < s2.specialization_id
     AND (
          lower(btrim(s1.name)) = lower(btrim(s2.name))
          OR (
              nullif(btrim(coalesce(s1.code, '')), '') IS NOT NULL
              AND lower(btrim(s1.code)) = lower(btrim(s2.code))
          )
     )
),
uedge AS (
    SELECT a, b FROM pair
    UNION
    SELECT b, a FROM pair
),
reach(node, root) AS (
    SELECT specialization_id, specialization_id FROM public.specializations
    UNION
    SELECT r.node, e.b
    FROM reach r
    JOIN uedge e ON e.a = r.node
)
SELECT node AS specialization_id, min(root) AS keep_id
FROM reach
GROUP BY node;

-- ------------------------------------------------------------------------------
-- 2. Fold attributes into the survivor.
--    The survivor stays ACTIVE if any member was active; it inherits the first
--    non-empty code if it has none of its own. The survivor keeps its own name.
-- ------------------------------------------------------------------------------
UPDATE public.specializations s
SET archive = g.all_archived,
    code    = coalesce(nullif(btrim(s.code), ''), g.first_code)
FROM (
    SELECT sv.keep_id,
           bool_and(m.archive)                                   AS all_archived,
           min(nullif(btrim(coalesce(m.code, '')), ''))          AS first_code
    FROM tmp_spec_survivor sv
    JOIN public.specializations m ON m.specialization_id = sv.specialization_id
    GROUP BY sv.keep_id
) g
WHERE s.specialization_id = g.keep_id;

-- ------------------------------------------------------------------------------
-- 3. Repoint courses to the surviving specialization (must run before DELETE).
-- ------------------------------------------------------------------------------
UPDATE public.course c
SET specialization_id = sv.keep_id
FROM tmp_spec_survivor sv
WHERE c.specialization_id = sv.specialization_id
  AND sv.specialization_id <> sv.keep_id;

-- ------------------------------------------------------------------------------
-- 4. Delete the duplicate rows.
-- ------------------------------------------------------------------------------
DELETE FROM public.specializations s
USING tmp_spec_survivor sv
WHERE s.specialization_id = sv.specialization_id
  AND sv.specialization_id <> sv.keep_id;

-- ------------------------------------------------------------------------------
-- 5. Drop specializations.program_id (FK, indexes, then the column).
-- ------------------------------------------------------------------------------

-- 5a. Drop any FK / unique / PK constraint that references program_id.
DO $$
DECLARE
    con RECORD;
BEGIN
    FOR con IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.specializations'::regclass
          AND contype IN ('f', 'u', 'p')
          AND pg_get_constraintdef(oid) ILIKE '%program_id%'
    LOOP
        EXECUTE format('ALTER TABLE public.specializations DROP CONSTRAINT IF EXISTS %I', con.conname);
    END LOOP;
END $$;

-- 5b. Drop any index that references program_id (incl. specializations_program_id_name_key).
DO $$
DECLARE
    idx RECORD;
BEGIN
    FOR idx IN
        SELECT indexrelid::regclass AS idxname
        FROM pg_index
        WHERE indrelid = 'public.specializations'::regclass
          AND pg_get_indexdef(indexrelid) ILIKE '%program_id%'
    LOOP
        EXECUTE format('DROP INDEX IF EXISTS %s', idx.idxname);
    END LOOP;
END $$;

-- 5c. Drop the column itself.
ALTER TABLE public.specializations DROP COLUMN IF EXISTS program_id;

-- ------------------------------------------------------------------------------
-- 6. Global uniqueness of specialization names (one institution-wide list).
-- ------------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS specializations_name_key
    ON public.specializations (lower(btrim(name)));

CREATE INDEX IF NOT EXISTS idx_specializations_archive
    ON public.specializations (archive);

NOTIFY pgrst, 'reload schema';

COMMIT;

-- ==============================================================================
-- OPTIONAL pre-flight query (run this manually BEFORE applying, to review what
-- will be merged):
--
--   SELECT specialization_id, name, code, program_id, archive
--   FROM public.specializations
--   WHERE lower(btrim(name)) IN (
--           SELECT lower(btrim(name)) FROM public.specializations
--           GROUP BY 1 HAVING count(*) > 1
--         )
--      OR (
--           nullif(btrim(coalesce(code, '')), '') IS NOT NULL
--           AND lower(btrim(code)) IN (
--               SELECT lower(btrim(code)) FROM public.specializations
--               WHERE nullif(btrim(coalesce(code, '')), '') IS NOT NULL
--               GROUP BY 1 HAVING count(*) > 1
--           )
--         )
--   ORDER BY lower(btrim(name)), specialization_id;
-- ==============================================================================
