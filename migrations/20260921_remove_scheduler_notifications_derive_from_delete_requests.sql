-- ============================================================================
-- Migration: Remove scheduler_notifications & Derive Notifications from delete_requests
-- ============================================================================

-- 1. Add `is_read` and `updated_at` columns to `delete_requests` if not present
ALTER TABLE public.delete_requests
    ADD COLUMN IF NOT EXISTS is_read BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

-- 2. Mark existing approved/rejected requests as already read so schedulers 
--    aren't flooded with old historical notifications
UPDATE public.delete_requests
SET is_read = true,
    updated_at = COALESCE(updated_at, created_at, now())
WHERE status IN ('approved', 'rejected');

-- 3. Create or replace the trigger to auto-update `updated_at` on row modification
CREATE OR REPLACE FUNCTION public.set_delete_requests_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_delete_requests_updated_at ON public.delete_requests;

CREATE TRIGGER trg_delete_requests_updated_at
BEFORE UPDATE ON public.delete_requests
FOR EACH ROW
EXECUTE FUNCTION public.set_delete_requests_updated_at();

-- 4. Drop RLS policies on scheduler_notifications
DROP POLICY IF EXISTS "scheduler_notifications_admin_scheduler_all" ON public.scheduler_notifications;

-- 5. Safely drop the scheduler_notifications table
DROP TABLE IF EXISTS public.scheduler_notifications CASCADE;

-- 6. Update the database restore RPC procedure so it doesn't reference scheduler_notifications
CREATE OR REPLACE FUNCTION public.restore_database_backup(payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    t_data jsonb;
    result jsonb := '{}'::jsonb;
    total_restored integer := 0;
    inserted_count integer;
BEGIN
    IF payload IS NULL THEN
        RAISE EXCEPTION 'Payload cannot be null';
    END IF;

    IF payload ? 'tables' THEN
        t_data := payload->'tables';
    ELSE
        t_data := payload;
    END IF;

    -- Truncate / clear in reverse dependency order
    IF t_data ? 'activity_log' THEN
        DELETE FROM public.activity_log;
    END IF;
    IF t_data ? 'delete_requests' THEN
        DELETE FROM public.delete_requests;
    END IF;
    IF t_data ? 'irregular_student_schedule' THEN
        DELETE FROM public.irregular_student_schedule;
    END IF;
    IF t_data ? 'irregular_students' THEN
        DELETE FROM public.irregular_students;
    END IF;
    IF t_data ? 'schedule' THEN
        DELETE FROM public.schedule;
    END IF;
    IF t_data ? 'prof_course' THEN
        DELETE FROM public.prof_course;
    END IF;
    IF t_data ? 'course' THEN
        DELETE FROM public.course;
    END IF;
    IF t_data ? 'room' THEN
        DELETE FROM public.room;
    END IF;
    IF t_data ? 'timeslot' THEN
        DELETE FROM public.timeslot;
    END IF;
    IF t_data ? 'professor' THEN
        DELETE FROM public.professor;
    END IF;
    IF t_data ? 'program_department' THEN
        DELETE FROM public.program_department;
    END IF;

    -- Restore delete_requests with is_read and updated_at
    IF t_data ? 'delete_requests' AND jsonb_array_length(t_data->'delete_requests') > 0 THEN
        INSERT INTO public.delete_requests (id, user_id, username, first_name, last_name, item_type, item_id, item_details, status, is_read, created_at, updated_at)
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
            COALESCE((x->>'created_at')::timestamptz, CURRENT_TIMESTAMP),
            COALESCE((x->>'updated_at')::timestamptz, CURRENT_TIMESTAMP)
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
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at;
        GET DIAGNOSTICS inserted_count = ROW_COUNT;
        result := result || jsonb_build_object('delete_requests', inserted_count);
        total_restored := total_restored + inserted_count;
    END IF;

    PERFORM setval(pg_get_serial_sequence('public.delete_requests', 'id'), COALESCE(MAX(id), 1)) FROM public.delete_requests;

    result := result || jsonb_build_object('total_restored', total_restored);
    RETURN result;
END;
$$;
