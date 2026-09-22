-- Migration: Add min_units and max_units to public.professor
-- Generated on 2026-09-22

ALTER TABLE public.professor
ADD COLUMN IF NOT EXISTS min_units numeric DEFAULT 0,
ADD COLUMN IF NOT EXISTS max_units numeric DEFAULT 24;

COMMENT ON COLUMN public.professor.min_units IS 'Minimum units required or preferred for the professor';
COMMENT ON COLUMN public.professor.max_units IS 'Maximum units allowed for the professor';
