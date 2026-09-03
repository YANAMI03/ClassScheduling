-- ==============================================================================
-- Supabase Database Restore Function: restore_database_json
-- Executes a complete database restore from JSON payload inside a single
-- atomic PostgreSQL transaction. Automatically rolls back if any error occurs.
-- ==============================================================================

CREATE OR REPLACE FUNCTION restore_database_json(
    payload jsonb,
    clear_existing boolean DEFAULT true
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result jsonb := '{}'::jsonb;
    inserted_count integer;
    total_restored integer := 0;
    t_data jsonb;
BEGIN
    -- Extract the data object whether root is { "data": {...} } or direct { "table": [...] }
    IF payload ? 'data' AND jsonb_typeof(payload->'data') = 'object' THEN
        t_data := payload->'data';
    ELSE
        t_data := payload;
    END IF;

    -- ==========================================================================
    -- 1. CLEAR EXISTING DATA (Child -> Parent Order to avoid FK violations)
    -- ==========================================================================
    IF clear_existing THEN
        IF t_data ? 'irregular_student_schedule' THEN
            DELETE FROM public.irregular_student_schedule;
        END IF;
        IF t_data ? 'schedule' THEN
            DELETE FROM public.schedule;
        END IF;
        IF t_data ? 'prof_course' THEN
            DELETE FROM public.prof_course;
        END IF;
        IF t_data ? 'delete_requests' THEN
            DELETE FROM public.delete_requests;
        END IF;
        IF t_data ? 'scheduler_notifications' THEN
            DELETE FROM public.scheduler_notifications;
        END IF;
        IF t_data ? 'activity_log' THEN
            DELETE FROM public.activity_log;
        END IF;
        IF t_data ? 'irregular_students' THEN
            DELETE FROM public.irregular_students;
        END IF;
        IF t_data ? 'timeslot' THEN
            DELETE FROM public.timeslot;
        END IF;
        IF t_data ? 'room' THEN
            DELETE FROM public.room;
        END IF;
        IF t_data ? 'course' THEN
            DELETE FROM public.course;
        END IF;
        IF t_data ? 'professor' THEN
            DELETE FROM public.professor;
        END IF;
        IF t_data ? 'program_department' THEN
            DELETE FROM public.program_department;
        END IF;
        IF t_data ? 'users' THEN
            DELETE FROM public.users;
        END IF;
    END IF;

    -- ==========================================================================
    -- 2. INSERT DATA (Parent -> Child Order to respect Foreign Keys)
    -- ==========================================================================

    -- 2.1 program_department
    IF t_data ? 'program_department' AND jsonb_array_length(t_data->'program_department') > 0 THEN
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

    -- 2.2 users (public.users)
    IF t_data ? 'users' AND jsonb_array_length(t_data->'users') > 0 THEN
        INSERT INTO public.users (id, username, program, profile_picture, role, first_name, last_name, email)
        SELECT
            (x->>'id')::uuid,
            x->>'username',
            (x->>'program')::varchar,
            x->>'profile_picture',
            x->>'role',
            x->>'first_name',
            x->>'last_name',
            x->>'email'
        FROM jsonb_array_elements(t_data->'users') AS x
        ON CONFLICT (id) DO UPDATE
        SET username = EXCLUDED.username,
            program = EXCLUDED.program,
            profile_picture = EXCLUDED.profile_picture,
            role = EXCLUDED.role,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            email = EXCLUDED.email;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('users', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.3 professor
    IF t_data ? 'professor' AND jsonb_array_length(t_data->'professor') > 0 THEN
        INSERT INTO public.professor (prof_id, first_name, last_name, department, max_hours)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'prof_id')::integer,
            (x->>'first_name')::varchar,
            x->>'last_name',
            (x->>'department')::varchar,
            COALESCE((x->>'max_hours')::integer, 40)
        FROM jsonb_array_elements(t_data->'professor') AS x
        ON CONFLICT (prof_id) DO UPDATE
        SET first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            department = EXCLUDED.department,
            max_hours = EXCLUDED.max_hours;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('professor', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.4 room
    IF t_data ? 'room' AND jsonb_array_length(t_data->'room') > 0 THEN
        INSERT INTO public.room (room_id, room_name, room_type, department)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'room_id')::integer,
            (x->>'room_name')::varchar,
            (x->>'room_type')::varchar,
            x->>'department'
        FROM jsonb_array_elements(t_data->'room') AS x
        ON CONFLICT (room_id) DO UPDATE
        SET room_name = EXCLUDED.room_name,
            room_type = EXCLUDED.room_type,
            department = EXCLUDED.department;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('room', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.5 timeslot
    IF t_data ? 'timeslot' AND jsonb_array_length(t_data->'timeslot') > 0 THEN
        INSERT INTO public.timeslot (timeslot_id, start_time, end_time, lunch_time, start_day, end_day)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'timeslot_id')::integer,
            (x->>'start_time')::time,
            (x->>'end_time')::time,
            NULLIF(x->>'lunch_time', '')::time,
            x->>'start_day',
            x->>'end_day'
        FROM jsonb_array_elements(t_data->'timeslot') AS x
        ON CONFLICT (timeslot_id) DO UPDATE
        SET start_time = EXCLUDED.start_time,
            end_time = EXCLUDED.end_time,
            lunch_time = EXCLUDED.lunch_time,
            start_day = EXCLUDED.start_day,
            end_day = EXCLUDED.end_day;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('timeslot', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.6 course
    IF t_data ? 'course' AND jsonb_array_length(t_data->'course') > 0 THEN
        INSERT INTO public.course (course_id, course_name, lecture_hours, lab_hours, program, units, year_level, ilp_hours, major, semester)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'course_id')::integer,
            (x->>'course_name')::varchar,
            (x->>'lecture_hours')::integer,
            (x->>'lab_hours')::integer,
            (x->>'program')::varchar,
            (x->>'units')::numeric,
            (x->>'year_level')::integer,
            (x->>'ilp_hours')::integer,
            x->>'major',
            x->>'semester'
        FROM jsonb_array_elements(t_data->'course') AS x
        ON CONFLICT (course_id) DO UPDATE
        SET course_name = EXCLUDED.course_name,
            lecture_hours = EXCLUDED.lecture_hours,
            lab_hours = EXCLUDED.lab_hours,
            program = EXCLUDED.program,
            units = EXCLUDED.units,
            year_level = EXCLUDED.year_level,
            ilp_hours = EXCLUDED.ilp_hours,
            major = EXCLUDED.major,
            semester = EXCLUDED.semester;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('course', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.7 prof_course
    IF t_data ? 'prof_course' AND jsonb_array_length(t_data->'prof_course') > 0 THEN
        INSERT INTO public.prof_course (prof_course_id, prof_id, course_id)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'prof_course_id')::integer,
            (x->>'prof_id')::integer,
            (x->>'course_id')::integer
        FROM jsonb_array_elements(t_data->'prof_course') AS x
        ON CONFLICT (prof_course_id) DO UPDATE
        SET prof_id = EXCLUDED.prof_id,
            course_id = EXCLUDED.course_id;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('prof_course', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.8 schedule
    IF t_data ? 'schedule' AND jsonb_array_length(t_data->'schedule') > 0 THEN
        INSERT INTO public.schedule (schedule_id, course_id, prof_id, room_id, day, class_start, class_end, session_type, section, semester, major, program)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'schedule_id')::integer,
            (x->>'course_id')::integer,
            (x->>'prof_id')::integer,
            (x->>'room_id')::integer,
            (x->>'day')::varchar,
            (x->>'class_start')::time,
            (x->>'class_end')::time,
            x->>'session_type',
            x->>'section',
            x->>'semester',
            x->>'major',
            x->>'program'
        FROM jsonb_array_elements(t_data->'schedule') AS x
        ON CONFLICT (schedule_id) DO UPDATE
        SET course_id = EXCLUDED.course_id,
            prof_id = EXCLUDED.prof_id,
            room_id = EXCLUDED.room_id,
            day = EXCLUDED.day,
            class_start = EXCLUDED.class_start,
            class_end = EXCLUDED.class_end,
            session_type = EXCLUDED.session_type,
            section = EXCLUDED.section,
            semester = EXCLUDED.semester,
            major = EXCLUDED.major,
            program = EXCLUDED.program;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.9 irregular_students
    IF t_data ? 'irregular_students' AND jsonb_array_length(t_data->'irregular_students') > 0 THEN
        INSERT INTO public.irregular_students (student_id, student_id_number, first_name, last_name, program, year_level, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'student_id')::integer,
            (x->>'student_id_number')::varchar,
            (x->>'first_name')::varchar,
            (x->>'last_name')::varchar,
            (x->>'program')::varchar,
            (x->>'year_level')::varchar,
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP)
        FROM jsonb_array_elements(t_data->'irregular_students') AS x
        ON CONFLICT (student_id) DO UPDATE
        SET student_id_number = EXCLUDED.student_id_number,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            program = EXCLUDED.program,
            year_level = EXCLUDED.year_level,
            created_at = EXCLUDED.created_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_students', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.10 irregular_student_schedule
    IF t_data ? 'irregular_student_schedule' AND jsonb_array_length(t_data->'irregular_student_schedule') > 0 THEN
        INSERT INTO public.irregular_student_schedule (id, student_id, schedule_id, course_id, section, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'student_id')::integer,
            (x->>'schedule_id')::integer,
            (x->>'course_id')::integer,
            (x->>'section')::varchar,
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP)
        FROM jsonb_array_elements(t_data->'irregular_student_schedule') AS x
        ON CONFLICT (id) DO UPDATE
        SET student_id = EXCLUDED.student_id,
            schedule_id = EXCLUDED.schedule_id,
            course_id = EXCLUDED.course_id,
            section = EXCLUDED.section,
            created_at = EXCLUDED.created_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('irregular_student_schedule', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.11 delete_requests
    IF t_data ? 'delete_requests' AND jsonb_array_length(t_data->'delete_requests') > 0 THEN
        INSERT INTO public.delete_requests (id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            (x->>'username')::varchar,
            x->>'first_name',
            x->>'last_name',
            (x->>'item_type')::varchar,
            (x->>'item_id')::varchar,
            (x->>'item_details')::varchar,
            COALESCE(x->>'status', 'pending')::varchar,
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP)
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
            created_at = EXCLUDED.created_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('delete_requests', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.12 scheduler_notifications
    IF t_data ? 'scheduler_notifications' AND jsonb_array_length(t_data->'scheduler_notifications') > 0 THEN
        INSERT INTO public.scheduler_notifications (id, user_id, request_id, message, status, is_read, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            (x->>'request_id')::integer,
            (x->>'message')::varchar,
            COALESCE(x->>'status', 'approved')::varchar,
            COALESCE((x->>'is_read')::boolean, false),
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP)
        FROM jsonb_array_elements(t_data->'scheduler_notifications') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            request_id = EXCLUDED.request_id,
            message = EXCLUDED.message,
            status = EXCLUDED.status,
            is_read = EXCLUDED.is_read,
            created_at = EXCLUDED.created_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('scheduler_notifications', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- 2.13 activity_log
    IF t_data ? 'activity_log' AND jsonb_array_length(t_data->'activity_log') > 0 THEN
        INSERT INTO public.activity_log (id, user_id, username, action, target_type, target_detail, created_at)
        OVERRIDING SYSTEM VALUE
        SELECT
            (x->>'id')::integer,
            (x->>'user_id')::uuid,
            x->>'username',
            x->>'action',
            x->>'target_type',
            x->>'target_detail',
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP)
        FROM jsonb_array_elements(t_data->'activity_log') AS x
        ON CONFLICT (id) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            username = EXCLUDED.username,
            action = EXCLUDED.action,
            target_type = EXCLUDED.target_type,
            target_detail = EXCLUDED.target_detail,
            created_at = EXCLUDED.created_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('activity_log', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    -- ==========================================================================
    -- 3. SYNCHRONIZE IDENTITY SEQUENCES
    -- Prevent duplicate key errors on subsequent application inserts
    -- ==========================================================================
    PERFORM setval(pg_get_serial_sequence('public.professor', 'prof_id'), COALESCE(MAX(prof_id), 1)) FROM public.professor;
    PERFORM setval(pg_get_serial_sequence('public.room', 'room_id'), COALESCE(MAX(room_id), 1)) FROM public.room;
    PERFORM setval(pg_get_serial_sequence('public.timeslot', 'timeslot_id'), COALESCE(MAX(timeslot_id), 1)) FROM public.timeslot;
    PERFORM setval(pg_get_serial_sequence('public.course', 'course_id'), COALESCE(MAX(course_id), 1)) FROM public.course;
    PERFORM setval(pg_get_serial_sequence('public.prof_course', 'prof_course_id'), COALESCE(MAX(prof_course_id), 1)) FROM public.prof_course;
    PERFORM setval(pg_get_serial_sequence('public.schedule', 'schedule_id'), COALESCE(MAX(schedule_id), 1)) FROM public.schedule;
    PERFORM setval(pg_get_serial_sequence('public.irregular_students', 'student_id'), COALESCE(MAX(student_id), 1)) FROM public.irregular_students;
    PERFORM setval(pg_get_serial_sequence('public.irregular_student_schedule', 'id'), COALESCE(MAX(id), 1)) FROM public.irregular_student_schedule;
    PERFORM setval(pg_get_serial_sequence('public.delete_requests', 'id'), COALESCE(MAX(id), 1)) FROM public.delete_requests;
    PERFORM setval(pg_get_serial_sequence('public.scheduler_notifications', 'id'), COALESCE(MAX(id), 1)) FROM public.scheduler_notifications;
    PERFORM setval(pg_get_serial_sequence('public.activity_log', 'id'), COALESCE(MAX(id), 1)) FROM public.activity_log;

    RETURN jsonb_build_object(
        'success', true,
        'total_restored', total_restored,
        'details', result
    );
END;
$$;
