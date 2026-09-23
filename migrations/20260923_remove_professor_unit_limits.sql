-- Remove workload-limit fields from the professor and academic-ranking models.
-- Course.units remains the source for calculating assigned teaching load.

ALTER TABLE public.professor
    DROP COLUMN IF EXISTS min_units,
    DROP COLUMN IF EXISTS max_units;

ALTER TABLE public.academic_ranking
    DROP COLUMN IF EXISTS units_required;