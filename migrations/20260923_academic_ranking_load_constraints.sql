-- Move teaching-load constraints to academic rankings.
-- Existing professors are assigned to a safe default ranking before the
-- foreign key is made mandatory.

ALTER TABLE public.academic_ranking
    ADD COLUMN IF NOT EXISTS min_units NUMERIC NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_units NUMERIC NOT NULL DEFAULT 24,
    ADD COLUMN IF NOT EXISTS min_hours NUMERIC NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_hours NUMERIC NOT NULL DEFAULT 40;

UPDATE public.academic_ranking
SET min_units = COALESCE(min_units, 0),
    max_units = COALESCE(max_units, 24),
    min_hours = COALESCE(min_hours, 0),
    max_hours = COALESCE(max_hours, 40);

DO $$
DECLARE
    default_ranking_id BIGINT;
BEGIN
    SELECT academic_ranking_id
    INTO default_ranking_id
    FROM public.academic_ranking
    WHERE lower(name) = 'general'
    ORDER BY academic_ranking_id
    LIMIT 1;

    IF default_ranking_id IS NULL THEN
        INSERT INTO public.academic_ranking (name, program, min_units, max_units, min_hours, max_hours)
        VALUES ('General', 'General', 0, 24, 0, 40)
        RETURNING academic_ranking_id INTO default_ranking_id;
    END IF;

    UPDATE public.professor
    SET academic_ranking_id = default_ranking_id
    WHERE academic_ranking_id IS NULL;
END $$;

ALTER TABLE public.professor
    ALTER COLUMN academic_ranking_id SET NOT NULL;

ALTER TABLE public.professor
    DROP CONSTRAINT IF EXISTS professor_academic_ranking_id_fkey;

ALTER TABLE public.professor
    ADD CONSTRAINT professor_academic_ranking_id_fkey
    FOREIGN KEY (academic_ranking_id)
    REFERENCES public.academic_ranking(academic_ranking_id)
    ON DELETE RESTRICT;

ALTER TABLE public.academic_ranking
    ADD CONSTRAINT academic_ranking_valid_load_limits
    CHECK (min_units >= 0 AND max_units > 0 AND min_units <= max_units
           AND min_hours >= 0 AND max_hours > 0 AND min_hours <= max_hours);