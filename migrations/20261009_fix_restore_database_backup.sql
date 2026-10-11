-- ==============================================================================
-- Migration: 20261009_fix_restore_database_backup.sql
-- Description: Rebuilds public.restore_database_json and restore_database_backup
--              to strictly match the latest consolidated schema:
--              - Supports working_hours (and legacy timeslot payloads)
--              - Supports course_code on public.course (with fallback from course_name)
--              - Removes dropped program_id from public.professor_load
--              - Removes dropped program_id and department from public.room
--              - Removes dropped created_at and updated_at from public.program
--              - Removes dropped course_id, program_id, program, and prepared_by from public.schedule
--              - Performs atomic cleanup in child->parent order and restores in parent->child order
--              - Synchronizes all identity sequences accurately
-- ==============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.restore_database_json(
    payload jsonb,
    clear_existing boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    result jsonb := '{}'::jsonb;
    inserted_count integer := 0;
    total_restored integer := 0;
    t_data jsonb;
    has_valid_table boolean := false;
    chk_table text;
    valid_tables text[] := ARRAY[
        'program', 'program_department', 'users', 'academic_ranking',
        'room', 'working_hours', 'timeslot', 'course', 'professor_load', 'prof_course',
        'schedule', 'irregular_students', 'irregular_student_schedule',
        'delete_requests', 'activity_log'
    ];
BEGIN
    -- 1. Validate payload
    IF payload IS NULL THEN
        RAISE EXCEPTION 'Payload cannot be null';
    END IF;

    -- Extract data object whether root is { "data": {...} } or { "tables": {...} } or direct
    IF payload ? 'data' AND jsonb_typeof(payload->'data') = 'object' THEN
        t_data := payload->'data';
    ELSIF payload ? 'tables' AND jsonb_typeof(payload->'tables') = 'object' THEN
        t_data := payload->'tables';
    ELSIF jsonb_typeof(payload) = 'object' THEN
        t_data := payload;
    ELSE
        RAISE EXCEPTION 'Invalid backup payload: root must be a JSON object';
    END IF;

    -- Map legacy timeslot key if working_hours is absent
    IF NOT (t_data ? 'working_hours') AND (t_data ? 'timeslot') THEN
        t_data := t_data || jsonb_build_object('working_hours', t_data->'timeslot');
    END IF;

    -- Map legacy prof_course key if professor_load is absent
    IF NOT (t_data ? 'professor_load') AND (t_data ? 'prof_course') THEN
        t_data := t_data || jsonb_build_object('professor_load', t_data->'prof_course');
    END IF;

    -- Verify that at least one recognizable table exists before doing anything
    FOREACH chk_table IN ARRAY valid_tables LOOP
        IF t_data ? chk_table AND jsonb_typeof(t_data->chk_table) = 'array' THEN
            has_valid_table := true;
            EXIT;
        END IF;
    END LOOP;

    IF NOT has_valid_table THEN
        RAISE EXCEPTION 'Invalid backup payload: no recognized tables found. Aborting restore to prevent data loss.';
    END IF;

    -- 2. CLEAR EXISTING DATA (Child -> Parent Order to avoid FK violations)
    IF clear_existing THEN
        IF t_data ? 'activity_log' AND to_regclass('public.activity_log') IS NOT NULL THEN
            DELETE FROM public.activity_log;
        END IF;
        IF t_data ? 'delete_requests' AND to_regclass('public.delete_requests') IS NOT NULL THEN
            DELETE FROM public.delete_requests;
        END IF;
        IF t_data ? 'irregular_student_schedule' AND to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
            DELETE FROM public.irregular_student_schedule;
        END IF;
        IF t_data ? 'irregular_students' AND to_regclass('public.irregular_students') IS NOT NULL THEN
            DELETE FROM public.irregular_students;
        END IF;
        IF t_data ? 'schedule' AND to_regclass('public.schedule') IS NOT NULL THEN
            DELETE FROM public.schedule;
        END IF;
        IF (t_data ? 'professor_load' OR t_data ? 'prof_course') AND to_regclass('public.professor_load') IS NOT NULL THEN
            DELETE FROM public.professor_load;
        END IF;
        IF t_data ? 'course' AND to_regclass('public.course') IS NOT NULL THEN
            DELETE FROM public.course;
        END IF;
        IF (t_data ? 'working_hours' OR t_data ? 'timeslot') THEN
            IF to_regclass('public.working_hours') IS NOT NULL THEN
                DELETE FROM public.working_hours;
            ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
                DELETE FROM public.timeslot;
            END IF;
        END IF;
        IF t_data ? 'room' AND to_regclass('public.room') IS NOT NULL THEN
            DELETE FROM public.room;
        END IF;
        IF t_data ? 'academic_ranking' AND to_regclass('public.academic_ranking') IS NOT NULL THEN
            DELETE FROM public.academic_ranking;
        END IF;
        IF t_data ? 'users' AND to_regclass('public.users') IS NOT NULL THEN
            DELETE FROM public.users;
        END IF;
        IF t_data ? 'program' AND to_regclass('public.program') IS NOT NULL THEN
            DELETE FROM public.program;
        END IF;
        IF t_data ? 'program_department' AND to_regclass('public.program_department') IS NOT NULL THEN
            DELETE FROM public.program_department;
        END IF;
    END IF;

    -- 3. INSERT DATA (Parent -> Child Order to respect Foreign Keys)

    -- 3.1 program_department
    IF t_data ? 'program_department' AND jsonb_array_length(t_data->'program_department') > 0 AND to_regclass('public.program_department') IS NOT NULL THEN
        INSERT INTO public.program_department (program_name, department_name)
        SELECT
            (x->>'program_name')::varchar,
            (x->>'department_name')::varchar
        FROM jsonb_array_elements(t_data->'program_department') AS x
        ON CONFLICT (program_name) DO UPDATE
        SET department_name = EXCLUDED.department_name;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('program_department', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.2 program (without dropped created_at, updated_at)
    IF t_data ? 'program' AND jsonb_array_length(t_data->'program') > 0 AND to_regclass('public.program') IS NOT NULL THEN
        INSERT INTO public.program (id, program_name, full_name)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            x->>'program_name',
            x->>'full_name'
        FROM jsonb_array_elements(t_data->'program') AS x
        ON CONFLICT (id) DO UPDATE
        SET program_name = EXCLUDED.program_name,
            full_name = EXCLUDED.full_name;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('program', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.3 users
    IF t_data ? 'users' AND jsonb_array_length(t_data->'users') > 0 AND to_regclass('public.users') IS NOT NULL THEN
        INSERT INTO public.users (
            id, username, program, program_id, profile_picture, role, first_name, last_name, email
        )
        SELECT
            (x->>'id')::uuid,
            x->>'username',
            x->>'program',
            NULLIF(x->>'program_id', '')::integer,
            x->>'profile_picture',
            x->>'role',
            x->>'first_name',
            x->>'last_name',
            x->>'email'
        FROM jsonb_array_elements(t_data->'users') AS x
        ON CONFLICT (id) DO UPDATE
        SET username = EXCLUDED.username,
            program = EXCLUDED.program,
            program_id = EXCLUDED.program_id,
            profile_picture = EXCLUDED.profile_picture,
            role = EXCLUDED.role,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            email = EXCLUDED.email;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('users', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.4 academic_ranking
    IF t_data ? 'academic_ranking' AND jsonb_array_length(t_data->'academic_ranking') > 0 AND to_regclass('public.academic_ranking') IS NOT NULL THEN
        INSERT INTO public.academic_ranking (academic_ranking_id, name, units_required, program, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'academic_ranking_id')::bigint,
            x->>'name',
            COALESCE(NULLIF(x->>'units_required', '')::numeric, 0),
            x->>'program',
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'academic_ranking') AS x
        ON CONFLICT (academic_ranking_id) DO UPDATE
        SET name = EXCLUDED.name,
            units_required = EXCLUDED.units_required,
            program = EXCLUDED.program;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('academic_ranking', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.5 room (without dropped department and program_id)
    IF t_data ? 'room' AND jsonb_array_length(t_data->'room') > 0 AND to_regclass('public.room') IS NOT NULL THEN
        INSERT INTO public.room (room_id, room_name, room_type)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'room_id')::integer,
            (x->>'room_name')::varchar,
            (x->>'room_type')::varchar
        FROM jsonb_array_elements(t_data->'room') AS x
        ON CONFLICT (room_id) DO UPDATE
        SET room_name = EXCLUDED.room_name,
            room_type = EXCLUDED.room_type;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('room', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.6 working_hours (supports working_hours and legacy timeslot)
    IF (t_data ? 'working_hours' OR t_data ? 'timeslot') THEN
        IF to_regclass('public.working_hours') IS NOT NULL THEN
            INSERT INTO public.working_hours (
                timeslot_id, start_time, end_time, lunch_time, start_day, end_day, professor_cutoff, day, semester_id
            )
            OVERRIDING SYSTEM VALUE
            SELECT
                (x->>'timeslot_id')::integer,
                (x->>'start_time')::time,
                (x->>'end_time')::time,
                NULLIF(x->>'lunch_time', '')::time,
                x->>'start_day',
                x->>'end_day',
                NULLIF(x->>'professor_cutoff', '')::time,
                x->>'day',
                NULLIF(x->>'semester_id', '')::integer
            FROM jsonb_array_elements(COALESCE(t_data->'working_hours', t_data->'timeslot')) AS x
            ON CONFLICT (timeslot_id) DO UPDATE
            SET start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                lunch_time = EXCLUDED.lunch_time,
                start_day = EXCLUDED.start_day,
                end_day = EXCLUDED.end_day,
                professor_cutoff = EXCLUDED.professor_cutoff,
                day = EXCLUDED.day,
                semester_id = EXCLUDED.semester_id;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;
            result := result || jsonb_build_object('working_hours', inserted_count);
            total_restored := total_restored + inserted_count;
        ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
            INSERT INTO public.timeslot (
                timeslot_id, start_time, end_time, lunch_time, start_day, end_day, professor_cutoff, day, semester_id
            )
            OVERRIDING SYSTEM VALUE
            SELECT
                (x->>'timeslot_id')::integer,
                (x->>'start_time')::time,
                (x->>'end_time')::time,
                NULLIF(x->>'lunch_time', '')::time,
                x->>'start_day',
                x->>'end_day',
                NULLIF(x->>'professor_cutoff', '')::time,
                x->>'day',
                NULLIF(x->>'semester_id', '')::integer
            FROM jsonb_array_elements(COALESCE(t_data->'working_hours', t_data->'timeslot')) AS x
            ON CONFLICT (timeslot_id) DO UPDATE
            SET start_time = EXCLUDED.start_time,
                end_time = EXCLUDED.end_time,
                lunch_time = EXCLUDED.lunch_time,
                start_day = EXCLUDED.start_day,
                end_day = EXCLUDED.end_day,
                professor_cutoff = EXCLUDED.professor_cutoff,
                day = EXCLUDED.day,
                semester_id = EXCLUDED.semester_id;
            GET DIAGNOSTICS inserted_count = ROW_COUNT;
            result := result || jsonb_build_object('timeslot', inserted_count);
            total_restored := total_restored + inserted_count;
        END IF;
    END IF;

    -- 3.7 course (supports course_code with fallback from course_name, specialization from major)
    IF t_data ? 'course' AND jsonb_array_length(t_data->'course') > 0 AND to_regclass('public.course') IS NOT NULL THEN
        INSERT INTO public.course (
            course_id, course_code, lecture_hours, lab_hours, program, units, year_level, ilp_hours, major, semester, program_id, specialization
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'course_id')::integer,
            COALESCE(NULLIF(x->>'course_code', ''), x->>'course_name')::varchar,
            COALESCE(NULLIF(x->>'lecture_hours', '')::integer, 0),
            COALESCE(NULLIF(x->>'lab_hours', '')::integer, 0),
            (x->>'program')::varchar,
            COALESCE(NULLIF(x->>'units', '')::numeric, 0),
            NULLIF(x->>'year_level', '')::integer,
            COALESCE(NULLIF(x->>'ilp_hours', '')::integer, 0),
            x->>'major',
            x->>'semester',
            COALESCE(NULLIF(x->>'program_id', '')::integer, (SELECT id FROM public.program ORDER BY id LIMIT 1)),
            COALESCE(NULLIF(x->>'specialization', ''), x->>'major')
        FROM jsonb_array_elements(t_data->'course') AS x
        ON CONFLICT (course_id) DO UPDATE
        SET course_code = EXCLUDED.course_code,
            lecture_hours = EXCLUDED.lecture_hours,
            lab_hours = EXCLUDED.lab_hours,
            program = EXCLUDED.program,
            units = EXCLUDED.units,
            year_level = EXCLUDED.year_level,
            ilp_hours = EXCLUDED.ilp_hours,
            major = EXCLUDED.major,
            semester = EXCLUDED.semester,
            program_id = EXCLUDED.program_id,
            specialization = EXCLUDED.specialization;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('course', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.8 professor_load (without dropped program_id)
    IF (t_data ? 'professor_load' AND jsonb_array_length(t_data->'professor_load') > 0)
       OR (t_data ? 'prof_course' AND jsonb_array_length(t_data->'prof_course') > 0) THEN

        WITH legacy_profs AS (
            SELECT
                (p->>'prof_id')::integer AS p_id,
                trim(regexp_replace(replace(trim(p->>'first_name') || ' ' || trim(p->>'last_name'), chr(160), ' '), '\s+', ' ', 'g')) AS p_name,
                lower(trim(regexp_replace(replace(trim(p->>'first_name') || ' ' || trim(p->>'last_name'), chr(160), ' '), '\s+', ' ', 'g'))) AS p_key
            FROM jsonb_array_elements(COALESCE(t_data->'professor', '[]'::jsonb)) AS p
            WHERE NULLIF(p->>'prof_id', '') IS NOT NULL
        )
        INSERT INTO public.professor_load (id, course_id, sections, ilp_hours, professor_name, professor_key)
        OVERRIDING SYSTEM VALUE
        SELECT
            COALESCE((x->>'id')::integer, (x->>'prof_course_id')::integer, (x->>'professor_load_id')::integer),
            (x->>'course_id')::integer,
            COALESCE(NULLIF(x->>'sections', '')::integer, 1),
            COALESCE(NULLIF(x->>'ilp_hours', '')::integer, 0),
            COALESCE(
                NULLIF(trim(regexp_replace(replace(x->>'professor_name', chr(160), ' '), '\s+', ' ', 'g')), ''),
                lp.p_name,
                'Unknown Professor'
            ),
            COALESCE(
                NULLIF(lower(trim(regexp_replace(replace(x->>'professor_key', chr(160), ' '), '\s+', ' ', 'g'))), ''),
                NULLIF(lower(trim(regexp_replace(replace(x->>'professor_name', chr(160), ' '), '\s+', ' ', 'g'))), ''),
                lp.p_key,
                'unknown_professor'
            )
        FROM jsonb_array_elements(COALESCE(t_data->'professor_load', t_data->'prof_course')) AS x
        LEFT JOIN legacy_profs lp ON lp.p_id = NULLIF(x->>'prof_id', '')::integer
        ON CONFLICT (id) DO UPDATE
        SET course_id = EXCLUDED.course_id,
            sections = EXCLUDED.sections,
            ilp_hours = EXCLUDED.ilp_hours,
            professor_name = EXCLUDED.professor_name,
            professor_key = EXCLUDED.professor_key;

        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('professor_load', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.9 schedule (without dropped course_id, program, program_id, prepared_by string)
    IF t_data ? 'schedule' AND jsonb_array_length(t_data->'schedule') > 0 AND to_regclass('public.schedule') IS NOT NULL THEN
        INSERT INTO public.schedule (
            schedule_id, professor_load_id, room_id, day,
            class_start, class_end, session_type, section, semester,
            major, specialization, archive, archive_batch_id,
            archived_at, archived_by, archive_reason, prepared_by_user_id
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'schedule_id')::integer,
            COALESCE((x->>'professor_load_id')::integer, (x->>'prof_course_id')::integer),
            (x->>'room_id')::integer,
            (x->>'day')::varchar,
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            x->>'session_type',
            x->>'section',
            x->>'semester',
            x->>'major',
            COALESCE(NULLIF(x->>'specialization', ''), x->>'major'),
            COALESCE((x->>'archive')::boolean, false),
            NULLIF(x->>'archive_batch_id', '')::uuid,
            NULLIF(x->>'archived_at', '')::timestamptz,
            x->>'archived_by',
            x->>'archive_reason',
            NULLIF(x->>'prepared_by_user_id', '')::uuid
        FROM jsonb_array_elements(t_data->'schedule') AS x
        ON CONFLICT (schedule_id) DO UPDATE
        SET professor_load_id = EXCLUDED.professor_load_id,
            room_id = EXCLUDED.room_id,
            day = EXCLUDED.day,
            class_start = EXCLUDED.class_start,
            class_end = EXCLUDED.class_end,
            session_type = EXCLUDED.session_type,
            section = EXCLUDED.section,
            semester = EXCLUDED.semester,
            major = EXCLUDED.major,
            specialization = EXCLUDED.specialization,
            archive = EXCLUDED.archive,
            archive_batch_id = EXCLUDED.archive_batch_id,
            archived_at = EXCLUDED.archived_at,
            archived_by = EXCLUDED.archived_by,
            archive_reason = EXCLUDED.archive_reason,
            prepared_by_user_id = EXCLUDED.prepared_by_user_id;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.10 irregular_students
    IF t_data ? 'irregular_students' AND jsonb_array_length(t_data->'irregular_students') > 0 AND to_regclass('public.irregular_students') IS NOT NULL THEN
        INSERT INTO public.irregular_students (student_id, student_id_number, first_name, last_name, program, year_level, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'student_id')::integer,
            (x->>'student_id_number')::varchar,
            (x->>'first_name')::varchar,
            (x->>'last_name')::varchar,
            (x->>'program')::varchar,
            (x->>'year_level')::varchar,
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'irregular_students') AS x
        ON CONFLICT (student_id) DO UPDATE
        SET student_id_number = EXCLUDED.student_id_number,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            program = EXCLUDED.program,
            year_level = EXCLUDED.year_level;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_students', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.11 irregular_student_schedule
    IF t_data ? 'irregular_student_schedule' AND jsonb_array_length(t_data->'irregular_student_schedule') > 0 AND to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
        INSERT INTO public.irregular_student_schedule (id, student_id, schedule_id, course_id, section, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'student_id')::integer,
            (x->>'schedule_id')::integer,
            (x->>'course_id')::integer,
            (x->>'section')::varchar,
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'irregular_student_schedule') AS x
        ON CONFLICT (id) DO UPDATE
        SET student_id = EXCLUDED.student_id,
            schedule_id = EXCLUDED.schedule_id,
            course_id = EXCLUDED.course_id,
            section = EXCLUDED.section;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_student_schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.12 delete_requests
    IF t_data ? 'delete_requests' AND jsonb_array_length(t_data->'delete_requests') > 0 AND to_regclass('public.delete_requests') IS NOT NULL THEN
        INSERT INTO public.delete_requests (
            id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, is_read, created_at, updated_at
        )
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            (x->>'username')::varchar,
            (x->>'first_name')::varchar,
            (x->>'last_name')::varchar,
            (x->>'item_type')::varchar,
            (x->>'item_id')::varchar,
            (x->>'item_details')::varchar,
            COALESCE(x->>'status', 'pending')::varchar,
            COALESCE((x->>'is_read')::boolean, false),
            COALESCE((x->>'created_at')::timestamptz, now()),
            COALESCE((x->>'updated_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'delete_requests') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            item_type = EXCLUDED.item_type,
            item_id = EXCLUDED.item_id,
            item_details = EXCLUDED.item_details,
            status = EXCLUDED.status,
            is_read = EXCLUDED.is_read,
            updated_at = EXCLUDED.updated_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('delete_requests', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 3.13 activity_log
    IF t_data ? 'activity_log' AND jsonb_array_length(t_data->'activity_log') > 0 AND to_regclass('public.activity_log') IS NOT NULL THEN
        INSERT INTO public.activity_log (id, user_id, username, action, target_type, target_detail, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            x->>'username',
            x->>'action',
            x->>'target_type',
            x->>'target_detail',
            COALESCE((x->>'created_at')::timestamptz, now())
        FROM jsonb_array_elements(t_data->'activity_log') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            action = EXCLUDED.action,
            target_type = EXCLUDED.target_type,
            target_detail = EXCLUDED.target_detail;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('activity_log', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 4. SYNCHRONIZE IDENTITY SEQUENCES
    IF to_regclass('public.program') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.program', 'id'), COALESCE(MAX(id), 1)) FROM public.program;
    END IF;
    IF to_regclass('public.academic_ranking') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.academic_ranking', 'academic_ranking_id'), COALESCE(MAX(academic_ranking_id), 1)) FROM public.academic_ranking;
    END IF;
    IF to_regclass('public.room') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.room', 'room_id'), COALESCE(MAX(room_id), 1)) FROM public.room;
    END IF;
    IF to_regclass('public.working_hours') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.working_hours', 'timeslot_id'), COALESCE(MAX(timeslot_id), 1)) FROM public.working_hours;
    ELSIF to_regclass('public.timeslot') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.timeslot', 'timeslot_id'), COALESCE(MAX(timeslot_id), 1)) FROM public.timeslot;
    END IF;
    IF to_regclass('public.course') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.course', 'course_id'), COALESCE(MAX(course_id), 1)) FROM public.course;
    END IF;
    IF to_regclass('public.professor_load') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.professor_load', 'id'), COALESCE(MAX(id), 1)) FROM public.professor_load;
    END IF;
    IF to_regclass('public.schedule') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;
    END IF;
    IF to_regclass('public.irregular_students') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.irregular_students', 'student_id'), COALESCE(MAX(student_id), 1)) FROM public.irregular_students;
    END IF;
    IF to_regclass('public.irregular_student_schedule') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.irregular_student_schedule', 'id'), COALESCE(MAX(id), 1)) FROM public.irregular_student_schedule;
    END IF;
    IF to_regclass('public.delete_requests') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.delete_requests', 'id'), COALESCE(MAX(id), 1)) FROM public.delete_requests;
    END IF;
    IF to_regclass('public.activity_log') IS NOT NULL THEN
        PERFORM setval(pg_get_serial_sequence('public.activity_log', 'id'), COALESCE(MAX(id), 1)) FROM public.activity_log;
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'total_restored', total_restored,
        'details', result
    );
END;
$$;

-- Create wrapper / alias for restore_database_backup so both RPC names work identically
CREATE OR REPLACE FUNCTION public.restore_database_backup(
    payload jsonb,
    clear_existing boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    RETURN public.restore_database_json(payload, clear_existing);
END;
$$;

-- Restrict execution
REVOKE EXECUTE ON FUNCTION public.restore_database_json(jsonb, boolean) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.restore_database_backup(jsonb, boolean) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.restore_database_json(jsonb, boolean) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.restore_database_backup(jsonb, boolean) TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
