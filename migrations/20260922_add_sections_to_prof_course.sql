-- Migration: Add sections column to public.prof_course
-- Generated on 2026-09-22

ALTER TABLE public.prof_course
ADD COLUMN IF NOT EXISTS sections integer DEFAULT 1;

COMMENT ON COLUMN public.prof_course.sections IS 'Number of sections assigned to the professor for this course';
