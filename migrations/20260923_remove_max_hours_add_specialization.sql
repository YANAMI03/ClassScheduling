-- Migration: Add specialization column and remove max_hours from public.professor
-- Generated on 2026-09-23

ALTER TABLE public.professor
ADD COLUMN IF NOT EXISTS specialization text DEFAULT '';

ALTER TABLE public.professor
DROP COLUMN IF EXISTS max_hours;

COMMENT ON COLUMN public.professor.specialization IS 'Area of specialization or primary teaching expertise for the professor';
