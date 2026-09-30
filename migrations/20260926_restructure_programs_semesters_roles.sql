-- ==============================================================================
-- Migration: 20260926_restructure_programs_semesters_roles.sql
-- Description:
--   1. Create program table and backfill program_id to professor, room, course,
--      users, schedule, academic_ranking, then drop old text columns.
--   2. Create semester table and convert course.semester and schedule.semester to semester_id FK.
--   3. Restructure timeslot to one row per day scoped to semester_id.
--   4. Create section_config table.
--   5. Add professor.time_designation.
--   6. Fix academic_ranking.max_units column type.
--   7. Delete existing viewer/instructor users.
--   8. Update RLS policies for super_admin, dean/chair, scheduler.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. PROGRAM TABLE
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.program (
    id SERIAL PRIMARY KEY,
    program_name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed confirmed canonical programs
INSERT INTO public.program (program_name)
VALUES ('BSIT'), ('BSBA')
ON CONFLICT (program_name) DO NOTHING;

-- Add program_id columns
ALTER TABLE public.professor ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE RESTRICT;
ALTER TABLE public.room ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE RESTRICT;
ALTER TABLE public.course ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE RESTRICT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE SET NULL;
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE CASCADE;
ALTER TABLE public.academic_ranking ADD COLUMN IF NOT EXISTS program_id INTEGER REFERENCES public.program(id) ON DELETE CASCADE;

-- Backfill program_id
DO $$
DECLARE
    v_bsit_id INTEGER;
    v_bsba_id INTEGER;
BEGIN
    SELECT id INTO v_bsit_id FROM public.program WHERE program_name = 'BSIT';
    SELECT id INTO v_bsba_id FROM public.program WHERE program_name = 'BSBA';

    -- Professor: map CICT to BSIT
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='professor' AND column_name='department') THEN
        UPDATE public.professor SET program_id = v_bsit_id WHERE department = 'CICT' OR program_id IS NULL;
    ELSE
        UPDATE public.professor SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;

    -- Room: map CICT to BSIT
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='room' AND column_name='department') THEN
        UPDATE public.room SET program_id = v_bsit_id WHERE department = 'CICT' OR program_id IS NULL;
    ELSE
        UPDATE public.room SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;

    -- Course
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='course' AND column_name='program') THEN
        UPDATE public.course SET program_id = v_bsit_id WHERE program = 'BSIT' OR program_id IS NULL;
    ELSE
        UPDATE public.course SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;

    -- Schedule
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='schedule' AND column_name='program') THEN
        UPDATE public.schedule SET program_id = v_bsit_id WHERE program = 'BSIT' OR program_id IS NULL;
    ELSE
        UPDATE public.schedule SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;

    -- Academic Ranking
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='academic_ranking' AND column_name='program') THEN
        UPDATE public.academic_ranking SET program_id = v_bsit_id WHERE program = 'BSIT' OR program_id IS NULL;
    ELSE
        UPDATE public.academic_ranking SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;

    -- Users
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='users' AND column_name='program') THEN
        UPDATE public.users SET program_id = v_bsit_id WHERE program = 'BSIT';
        UPDATE public.users SET program_id = v_bsba_id WHERE program = 'BSBA';
        UPDATE public.users SET program_id = v_bsit_id WHERE program_id IS NULL;
    ELSE
        UPDATE public.users SET program_id = v_bsit_id WHERE program_id IS NULL;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 2. SEMESTER TABLE
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.semester (
    id SERIAL PRIMARY KEY,
    program_id INTEGER NOT NULL REFERENCES public.program(id) ON DELETE CASCADE,
    school_year VARCHAR(20) NOT NULL,
    term VARCHAR(50) NOT NULL,
    created_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_semester_prog_sy_term UNIQUE(program_id, school_year, term)
);

-- Seed active semester for BSIT
DO $$
DECLARE
    v_bsit_id INTEGER;
    v_admin_id UUID;
BEGIN
    SELECT id INTO v_bsit_id FROM public.program WHERE program_name = 'BSIT';
    SELECT id INTO v_admin_id FROM public.users WHERE lower(role) = 'admin' LIMIT 1;

    INSERT INTO public.semester (program_id, school_year, term, created_by, is_active)
    VALUES (v_bsit_id, '2025-2026', '1st Semester', v_admin_id, true)
    ON CONFLICT (program_id, school_year, term) DO UPDATE SET is_active = true;

    INSERT INTO public.semester (program_id, school_year, term, created_by, is_active)
    VALUES (v_bsit_id, '2025-2026', '2nd Semester', v_admin_id, false)
    ON CONFLICT (program_id, school_year, term) DO NOTHING;
END $$;

-- Add semester_id FK to course and schedule
ALTER TABLE public.course ADD COLUMN IF NOT EXISTS semester_id INTEGER REFERENCES public.semester(id) ON DELETE SET NULL;
ALTER TABLE public.schedule ADD COLUMN IF NOT EXISTS semester_id INTEGER REFERENCES public.semester(id) ON DELETE SET NULL;

-- Backfill semester_id
DO $$
DECLARE
    v_active_sem_id INTEGER;
    v_second_sem_id INTEGER;
    v_bsit_id INTEGER;
BEGIN
    SELECT id INTO v_bsit_id FROM public.program WHERE program_name = 'BSIT';
    SELECT id INTO v_active_sem_id FROM public.semester WHERE program_id = v_bsit_id AND is_active = true LIMIT 1;
    SELECT id INTO v_second_sem_id FROM public.semester WHERE program_id = v_bsit_id AND term = '2nd Semester' LIMIT 1;

    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='course' AND column_name='semester') THEN
        UPDATE public.course SET semester_id = v_second_sem_id WHERE semester ILIKE '%2nd%' AND v_second_sem_id IS NOT NULL;
        UPDATE public.course SET semester_id = v_active_sem_id WHERE semester_id IS NULL;
    ELSE
        UPDATE public.course SET semester_id = v_active_sem_id WHERE semester_id IS NULL;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='schedule' AND column_name='semester') THEN
        UPDATE public.schedule SET semester_id = v_second_sem_id WHERE semester ILIKE '%2nd%' AND v_second_sem_id IS NOT NULL;
        UPDATE public.schedule SET semester_id = v_active_sem_id WHERE semester_id IS NULL;
    ELSE
        UPDATE public.schedule SET semester_id = v_active_sem_id WHERE semester_id IS NULL;
    END IF;
END $$;

-- ------------------------------------------------------------------------------
-- 3. RESTRUCTURE TIMESLOT TABLE
-- ------------------------------------------------------------------------------
ALTER TABLE public.timeslot ADD COLUMN IF NOT EXISTS semester_id INTEGER REFERENCES public.semester(id) ON DELETE CASCADE;
ALTER TABLE public.timeslot ADD COLUMN IF NOT EXISTS day VARCHAR(20);

DO $$
DECLARE
    v_active_sem_id INTEGER;
    ts RECORD;
    d TEXT;
BEGIN
    SELECT id INTO v_active_sem_id FROM public.semester WHERE is_active = true LIMIT 1;
    IF v_active_sem_id IS NOT NULL THEN
        SELECT * INTO ts FROM public.timeslot LIMIT 1;
        -- Create standard 6-day slots for active semester
        FOREACH d IN ARRAY ARRAY['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        LOOP
            INSERT INTO public.timeslot (semester_id, day, start_time, end_time, lunch_time)
            VALUES (
                v_active_sem_id,
                d,
                COALESCE(ts.start_time, '07:00:00'::time),
                COALESCE(ts.end_time, '20:00:00'::time),
                COALESCE(ts.lunch_time, '12:00:00'::time)
            )
            ON CONFLICT DO NOTHING;
        END LOOP;
    END IF;
END $$;

-- Delete old rows that have NULL day
DELETE FROM public.timeslot WHERE day IS NULL;

-- Drop obsolete day range columns
ALTER TABLE public.timeslot DROP COLUMN IF EXISTS start_day;
ALTER TABLE public.timeslot DROP COLUMN IF EXISTS end_day;

-- Add unique constraint on (semester_id, day)
ALTER TABLE public.timeslot DROP CONSTRAINT IF EXISTS uq_timeslot_semester_day;
ALTER TABLE public.timeslot ADD CONSTRAINT uq_timeslot_semester_day UNIQUE (semester_id, day);

-- ------------------------------------------------------------------------------
-- 4. SECTION_CONFIG TABLE
-- ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.section_config (
    id SERIAL PRIMARY KEY,
    semester_id INTEGER NOT NULL REFERENCES public.semester(id) ON DELETE CASCADE,
    year_level INTEGER NOT NULL CHECK (year_level >= 1 AND year_level <= 6),
    number_of_sections INTEGER NOT NULL DEFAULT 1 CHECK (number_of_sections >= 0),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_section_config_sem_year UNIQUE(semester_id, year_level)
);

-- Seed default section_config (4 sections for 1st-4th year) for active semester
INSERT INTO public.section_config (semester_id, year_level, number_of_sections)
SELECT s.id, y.lvl, 4
FROM public.semester s
CROSS JOIN (VALUES (1), (2), (3), (4)) AS y(lvl)
WHERE s.is_active = true
ON CONFLICT (semester_id, year_level) DO NOTHING;

-- ------------------------------------------------------------------------------
-- 5. PROFESSOR.TIME_DESIGNATION & ACADEMIC RANKING DECIMAL FIX
-- ------------------------------------------------------------------------------
ALTER TABLE public.professor ADD COLUMN IF NOT EXISTS time_designation INTEGER DEFAULT 5 CHECK (time_designation IN (4, 5));
UPDATE public.professor SET time_designation = 5 WHERE time_designation IS NULL;

-- Fix academic_ranking decimal columns
ALTER TABLE public.academic_ranking
    ALTER COLUMN max_units TYPE DECIMAL(5,2) USING max_units::numeric(5,2);
ALTER TABLE public.academic_ranking
    ALTER COLUMN min_units TYPE DECIMAL(5,2) USING min_units::numeric(5,2);

-- ------------------------------------------------------------------------------
-- 6. DROP OLD FREE-TEXT COLUMNS
-- ------------------------------------------------------------------------------
ALTER TABLE public.professor DROP COLUMN IF EXISTS department;
ALTER TABLE public.room DROP COLUMN IF EXISTS department;
ALTER TABLE public.course DROP COLUMN IF EXISTS program;
ALTER TABLE public.course DROP COLUMN IF EXISTS semester;
ALTER TABLE public.users DROP COLUMN IF EXISTS program;
ALTER TABLE public.schedule DROP COLUMN IF EXISTS program;
ALTER TABLE public.schedule DROP COLUMN IF EXISTS semester;
ALTER TABLE public.academic_ranking DROP COLUMN IF EXISTS program;

-- ------------------------------------------------------------------------------
-- 7. REMOVE INSTRUCTOR / VIEWER USERS SAFELY
-- ------------------------------------------------------------------------------
DO $$
BEGIN
    DELETE FROM public.delete_requests WHERE user_id IN (SELECT id FROM public.users WHERE lower(coalesce(role, '')) IN ('viewer', 'instructor'));
    DELETE FROM public.activity_log WHERE user_id IN (SELECT id FROM public.users WHERE lower(coalesce(role, '')) IN ('viewer', 'instructor'));
    
    BEGIN
        DELETE FROM auth.users WHERE id IN (SELECT id FROM public.users WHERE lower(coalesce(role, '')) IN ('viewer', 'instructor'));
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
    
    DELETE FROM public.users WHERE lower(coalesce(role, '')) IN ('viewer', 'instructor');
END $$;

-- ------------------------------------------------------------------------------
-- 8. HELPER FUNCTION & ROW LEVEL SECURITY (RLS) POLICIES
-- ------------------------------------------------------------------------------

-- Helper: Get authenticated user's program_id
CREATE OR REPLACE FUNCTION public.get_user_program_id()
RETURNS integer
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_pid integer;
BEGIN
    SELECT program_id INTO v_pid FROM public.users WHERE id = auth.uid() LIMIT 1;
    RETURN v_pid;
END;
$$;

-- Fix handle_new_auth_user() trigger to use program_id instead of dropped program column
CREATE OR REPLACE FUNCTION public.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_program_id integer;
    v_program_name text;
    v_username text;
    v_first_name text;
    v_last_name text;
    v_role text;
BEGIN
    v_program_name := COALESCE(new.raw_user_meta_data->>'program', '');
    v_username := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'username'), ''), split_part(new.email, '@', 1));
    v_first_name := COALESCE(new.raw_user_meta_data->>'first_name', '');
    v_last_name := COALESCE(new.raw_user_meta_data->>'last_name', '');
    v_role := COALESCE(NULLIF(TRIM(new.raw_user_meta_data->>'role'), ''), 'scheduler');
    IF LOWER(v_role) IN ('viewer', 'instructor') THEN
        v_role := 'scheduler';
    END IF;

    -- Resolve program_id from metadata
    IF (new.raw_user_meta_data->>'program_id') IS NOT NULL AND (new.raw_user_meta_data->>'program_id') ~ '^\d+$' THEN
        v_program_id := (new.raw_user_meta_data->>'program_id')::integer;
    ELSIF v_program_name ~ '^\d+$' THEN
        v_program_id := v_program_name::integer;
    ELSIF v_program_name <> '' THEN
        SELECT id INTO v_program_id FROM public.program
        WHERE UPPER(program_name) = UPPER(TRIM(v_program_name))
           OR UPPER(program_name) = (CASE WHEN UPPER(TRIM(v_program_name)) = 'CICT' THEN 'BSIT' WHEN UPPER(TRIM(v_program_name)) = 'CMBT' THEN 'BSBA' ELSE UPPER(TRIM(v_program_name)) END)
        LIMIT 1;
    END IF;

    IF v_program_id IS NULL THEN
        SELECT id INTO v_program_id FROM public.program WHERE UPPER(program_name) = 'BSIT' LIMIT 1;
    END IF;

    INSERT INTO public.users (
        id,
        email,
        username,
        first_name,
        last_name,
        program_id,
        role,
        profile_picture
    )
    VALUES (
        new.id,
        new.email,
        v_username,
        v_first_name,
        v_last_name,
        v_program_id,
        v_role,
        new.raw_user_meta_data->>'profile_picture'
    )
    ON CONFLICT (id) DO UPDATE
    SET
        email = EXCLUDED.email,
        username = COALESCE(EXCLUDED.username, public.users.username),
        first_name = COALESCE(EXCLUDED.first_name, public.users.first_name),
        last_name = COALESCE(EXCLUDED.last_name, public.users.last_name),
        program_id = COALESCE(EXCLUDED.program_id, public.users.program_id),
        role = COALESCE(EXCLUDED.role, public.users.role),
        profile_picture = COALESCE(EXCLUDED.profile_picture, public.users.profile_picture),
        updated_at = NOW();

    RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT OR UPDATE ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_auth_user();

-- Re-create and grant get_email_by_username() RPC
CREATE OR REPLACE FUNCTION public.get_email_by_username(p_username text)
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT COALESCE(email, LOWER(username) || '@example.com')
    FROM public.users
    WHERE (LOWER(email) = LOWER(TRIM(p_username)) OR LOWER(username) = LOWER(TRIM(p_username)))
    LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_email_by_username(text) TO anon, authenticated, service_role;

-- Update public.users RLS policies
DROP POLICY IF EXISTS "users_admin_select" ON public.users;
CREATE POLICY "users_admin_select"
ON public.users
FOR SELECT
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin', 'dean', 'chair', 'scheduler'));

DROP POLICY IF EXISTS "users_admin_insert" ON public.users;
CREATE POLICY "users_admin_insert"
ON public.users
FOR INSERT
TO authenticated
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

DROP POLICY IF EXISTS "users_admin_update" ON public.users;
CREATE POLICY "users_admin_update"
ON public.users
FOR UPDATE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id)
WITH CHECK (public.get_user_role() IN ('super_admin', 'admin') OR (SELECT auth.uid()) = id);

DROP POLICY IF EXISTS "users_admin_delete" ON public.users;
CREATE POLICY "users_admin_delete"
ON public.users
FOR DELETE
TO authenticated
USING (public.get_user_role() IN ('super_admin', 'admin'));

-- Enable RLS on newly created tables
ALTER TABLE public.program ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.semester ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.section_config ENABLE ROW LEVEL SECURITY;

-- PROGRAM: Read for authenticated users, CRUD for super_admin
DROP POLICY IF EXISTS "program_select_authenticated" ON public.program;
CREATE POLICY "program_select_authenticated" ON public.program
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "program_admin_crud" ON public.program;
CREATE POLICY "program_admin_crud" ON public.program
    FOR ALL TO authenticated
    USING (public.get_user_role() IN ('super_admin', 'admin'))
    WITH CHECK (public.get_user_role() IN ('super_admin', 'admin'));

-- SEMESTER: Read for authenticated users, Dean/SuperAdmin can create/edit for their program
DROP POLICY IF EXISTS "semester_select_authenticated" ON public.semester;
CREATE POLICY "semester_select_authenticated" ON public.semester
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "semester_dean_admin_crud" ON public.semester;
CREATE POLICY "semester_dean_admin_crud" ON public.semester
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    );

-- SECTION_CONFIG: Read for authenticated users, Dean/SuperAdmin can edit
DROP POLICY IF EXISTS "section_config_select_authenticated" ON public.section_config;
CREATE POLICY "section_config_select_authenticated" ON public.section_config
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "section_config_dean_admin_crud" ON public.section_config;
CREATE POLICY "section_config_dean_admin_crud" ON public.section_config
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND semester_id IN (SELECT id FROM public.semester WHERE program_id = public.get_user_program_id())
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND semester_id IN (SELECT id FROM public.semester WHERE program_id = public.get_user_program_id())
        )
    );

-- COURSE: Dean full CRUD for own program; Scheduler READ ONLY; SuperAdmin full CRUD
DROP POLICY IF EXISTS "course_admin_scheduler_all" ON public.course;
DROP POLICY IF EXISTS "course_select_authenticated" ON public.course;
CREATE POLICY "course_select_authenticated" ON public.course
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "course_dean_admin_crud" ON public.course;
CREATE POLICY "course_dean_admin_crud" ON public.course
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    );

-- PROFESSOR: Dean full CRUD for own program; Scheduler READ ONLY; SuperAdmin full CRUD
DROP POLICY IF EXISTS "professor_admin_scheduler_all" ON public.professor;
DROP POLICY IF EXISTS "professor_select_authenticated" ON public.professor;
CREATE POLICY "professor_select_authenticated" ON public.professor
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "professor_dean_admin_crud" ON public.professor;
CREATE POLICY "professor_dean_admin_crud" ON public.professor
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    );

-- ROOM: Dean full CRUD for own program; Scheduler READ ONLY; SuperAdmin full CRUD
DROP POLICY IF EXISTS "room_admin_scheduler_all" ON public.room;
DROP POLICY IF EXISTS "room_select_authenticated" ON public.room;
CREATE POLICY "room_select_authenticated" ON public.room
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "room_dean_admin_crud" ON public.room;
CREATE POLICY "room_dean_admin_crud" ON public.room
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (public.get_user_role() IN ('dean', 'chair') AND program_id = public.get_user_program_id())
    );

-- TIMESLOT: Dean full CRUD for own program's semesters; Scheduler READ ONLY; SuperAdmin full CRUD
DROP POLICY IF EXISTS "timeslot_admin_scheduler_all" ON public.timeslot;
DROP POLICY IF EXISTS "timeslot_select_authenticated" ON public.timeslot;
CREATE POLICY "timeslot_select_authenticated" ON public.timeslot
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "timeslot_dean_admin_crud" ON public.timeslot;
CREATE POLICY "timeslot_dean_admin_crud" ON public.timeslot
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND semester_id IN (SELECT id FROM public.semester WHERE program_id = public.get_user_program_id())
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND semester_id IN (SELECT id FROM public.semester WHERE program_id = public.get_user_program_id())
        )
    );

-- PROFESSOR_LOAD: Dean full CRUD for own program; Scheduler READ ONLY; SuperAdmin full CRUD
DROP POLICY IF EXISTS "professor_load_select_authenticated" ON public.professor_load;
CREATE POLICY "professor_load_select_authenticated" ON public.professor_load
    FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS "professor_load_dean_admin_crud" ON public.professor_load;
CREATE POLICY "professor_load_dean_admin_crud" ON public.professor_load
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND prof_id IN (SELECT prof_id FROM public.professor WHERE program_id = public.get_user_program_id())
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair')
            AND prof_id IN (SELECT prof_id FROM public.professor WHERE program_id = public.get_user_program_id())
        )
    );

-- SCHEDULE: Scheduler & Dean full CRUD for own program; SuperAdmin full CRUD
DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;
CREATE POLICY "schedule_scoped_all" ON public.schedule
    FOR ALL TO authenticated
    USING (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (program_id = public.get_user_program_id() OR program_id IS NULL)
        )
    )
    WITH CHECK (
        public.get_user_role() IN ('super_admin', 'admin')
        OR (
            public.get_user_role() IN ('dean', 'chair', 'scheduler')
            AND (program_id = public.get_user_program_id() OR program_id IS NULL)
        )
    );

COMMIT;
