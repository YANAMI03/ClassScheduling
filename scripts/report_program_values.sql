-- ==============================================================================
-- Distinct program/department text values & role report. Read-only.
-- Run in Supabase SQL Editor: https://supabase.com/dashboard/project/maaeqnmziwhocziwgjpo/sql/new
-- ==============================================================================

-- 1. ALL-IN-ONE JSON QUERY (Recommended: Click "Run" and copy the single result cell)
SELECT jsonb_pretty(jsonb_build_object(
    'distinct_values', (
        SELECT jsonb_agg(jsonb_build_object('source', source, 'value', value, 'count', n))
        FROM (
            SELECT 'professor.department' AS source, department AS value, COUNT(*) AS n FROM public.professor GROUP BY department
            UNION ALL
            SELECT 'room.department', department, COUNT(*) FROM public.room GROUP BY department
            UNION ALL
            SELECT 'course.program', program, COUNT(*) FROM public.course GROUP BY program
            UNION ALL
            SELECT 'users.program', program, COUNT(*) FROM public.users GROUP BY program
            UNION ALL
            SELECT 'schedule.program', program, COUNT(*) FROM public.schedule GROUP BY program
            UNION ALL
            SELECT 'academic_ranking.program', program, COUNT(*) FROM public.academic_ranking GROUP BY program
            ORDER BY source, n DESC, value NULLS FIRST
        ) s1
    ),
    'roles_summary', (
        SELECT jsonb_agg(jsonb_build_object('role', role, 'count', n))
        FROM (SELECT role, COUNT(*) AS n FROM public.users GROUP BY role ORDER BY n DESC, role NULLS FIRST) s2
    ),
    'instructor_viewer_users', (
        SELECT coalesce(jsonb_agg(jsonb_build_object('id', id, 'email', email, 'username', username, 'first_name', first_name, 'last_name', last_name, 'program', program, 'role', role)), '[]'::jsonb)
        FROM (SELECT id, email, username, first_name, last_name, program, role FROM public.users WHERE lower(coalesce(role, '')) IN ('instructor', 'viewer') ORDER BY lower(role), email NULLS LAST) s3
    ),
    'program_department_legacy', (
        SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'program_department')
            THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('program_name', program_name, 'department_name', department_name)), '[]'::jsonb) FROM public.program_department)
            ELSE '[]'::jsonb
        END
    )
)) AS reconciliation_data;

-- ==============================================================================
-- INDIVIDUAL QUERIES (Optional: Run individually if preferred)
-- ==============================================================================
-- SELECT 'professor.department' AS source, department AS value, COUNT(*) AS n FROM public.professor GROUP BY department
-- UNION ALL SELECT 'room.department', department, COUNT(*) FROM public.room GROUP BY department
-- UNION ALL SELECT 'course.program', program, COUNT(*) FROM public.course GROUP BY program
-- UNION ALL SELECT 'users.program', program, COUNT(*) FROM public.users GROUP BY program
-- UNION ALL SELECT 'schedule.program', program, COUNT(*) FROM public.schedule GROUP BY program
-- UNION ALL SELECT 'academic_ranking.program', program, COUNT(*) FROM public.academic_ranking GROUP BY program
-- ORDER BY source, n DESC, value NULLS FIRST;

