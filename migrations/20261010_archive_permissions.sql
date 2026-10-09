-- ==============================================================================
-- Migration: 20261010_archive_permissions.sql
-- Description:
--   Archive Permissions and RLS for User Roles: Admin, Scheduler, Viewer
--
-- Roles:
--   1. Admin:
--      - Global access across all programs.
--      - Can archive any active schedule.
--      - Can restore any archived schedule (subject to 1-active-schedule conflict check).
--      - Exclusively authorized to permanently delete archived schedule batches.
--   2. Scheduler:
--      - Scoped strictly to their own program (program_id).
--      - Can archive their own program's active schedule directly without deletion request.
--      - Can restore their own program's archived schedule (subject to 1-active-schedule conflict check).
--      - CANNOT permanently delete schedules (HTTP 403 / RLS block).
--   3. Viewer:
--      - Read-only access to published active schedules (archive = false).
--      - Cannot view schedule archives, cannot archive, restore, or delete.
-- ==============================================================================

BEGIN;

-- ------------------------------------------------------------------------------
-- 1. SECURITY DEFINER HELPER FUNCTIONS
-- ------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
    SELECT LOWER(COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'role'),
        (auth.jwt() -> 'user_metadata' ->> 'role'),
        (auth.jwt() ->> 'role'),
        (SELECT role FROM public.users WHERE id::text = auth.uid()::text LIMIT 1),
        'viewer'
    ));
$$;

GRANT EXECUTE ON FUNCTION public.get_user_role() TO anon, authenticated, service_role;

CREATE OR REPLACE FUNCTION public.get_user_program_id()
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
    SELECT COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'program_id')::integer,
        (auth.jwt() -> 'user_metadata' ->> 'program_id')::integer,
        (SELECT program_id FROM public.users WHERE id::text = auth.uid()::text LIMIT 1)
    );
$$;

GRANT EXECUTE ON FUNCTION public.get_user_program_id() TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 2. GRANULAR RLS POLICIES ON public.schedule
-- ------------------------------------------------------------------------------
ALTER TABLE public.schedule ENABLE ROW LEVEL SECURITY;

-- Drop obsolete or overly permissive policies
DROP POLICY IF EXISTS "schedule_scoped_all" ON public.schedule;
DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;
DROP POLICY IF EXISTS "schedule_select" ON public.schedule;
DROP POLICY IF EXISTS "schedule_insert" ON public.schedule;
DROP POLICY IF EXISTS "schedule_update" ON public.schedule;
DROP POLICY IF EXISTS "schedule_delete" ON public.schedule;

-- (a) SELECT:
--     - Admin sees all schedules (active and archived) across all programs.
--     - Scheduler sees own program's schedules (active and archived) PLUS active schedules of all programs for conflict detection.
--     - Viewer sees active schedules only (archive = false).
CREATE POLICY "schedule_select" ON public.schedule
FOR SELECT TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (
        public.get_user_role() = 'scheduler'
        AND (program_id = public.get_user_program_id() OR program_id IS NULL)
    )
    OR (archive = false)
);

-- (b) INSERT:
--     - Admin can insert for any program.
--     - Scheduler can insert only for their assigned program.
--     - Viewer cannot insert.
CREATE POLICY "schedule_insert" ON public.schedule
FOR INSERT TO authenticated
WITH CHECK (
    public.get_user_role() = 'admin'
    OR (
        public.get_user_role() = 'scheduler'
        AND (program_id = public.get_user_program_id() OR program_id IS NULL)
    )
);

-- (c) UPDATE:
--     - Admin can update for any program.
--     - Scheduler can update only for their assigned program (archive and restore operations).
--     - Viewer cannot update.
CREATE POLICY "schedule_update" ON public.schedule
FOR UPDATE TO authenticated
USING (
    public.get_user_role() = 'admin'
    OR (
        public.get_user_role() = 'scheduler'
        AND (program_id = public.get_user_program_id() OR program_id IS NULL)
    )
)
WITH CHECK (
    public.get_user_role() = 'admin'
    OR (
        public.get_user_role() = 'scheduler'
        AND (program_id = public.get_user_program_id() OR program_id IS NULL)
    )
);

-- (d) DELETE:
--     - Admin ONLY, and ONLY where archive = true.
--     - Schedulers and Viewers cannot delete rows.
CREATE POLICY "schedule_delete" ON public.schedule
FOR DELETE TO authenticated
USING (
    public.get_user_role() = 'admin'
    AND archive = true
);


-- ------------------------------------------------------------------------------
-- 3. RPC: archive_active_schedule (Scoped, Atomic, leaves prepared_by unchanged)
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.archive_active_schedule(integer, text, text, text);

CREATE OR REPLACE FUNCTION public.archive_active_schedule(
    p_program_id   integer DEFAULT NULL,
    p_semester     text    DEFAULT NULL,
    p_archived_by  text    DEFAULT 'Scheduler',
    p_reason       text    DEFAULT 'Manually archived'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_batch_id     uuid        := gen_random_uuid();
    v_now          timestamptz := clock_timestamp();
    v_count        integer     := 0;
    v_caller_role  text;
    v_caller_pid   integer;
    v_target_pid   integer     := p_program_id;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_pid  := public.get_user_program_id();

    -- Scoping check
    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot archive schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role = 'scheduler' THEN
        IF v_target_pid IS NOT NULL AND v_caller_pid IS NOT NULL AND v_target_pid != v_caller_pid THEN
            RAISE EXCEPTION 'Permission denied: Schedulers can only archive their own program.'
                USING ERRCODE = '42501';
        END IF;
        v_target_pid := coalesce(v_target_pid, v_caller_pid);
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Soft-archive active schedule rows atomically. Keep prepared_by fields unchanged.
    UPDATE public.schedule sc
    SET archive = true,
        archive_batch_id = v_batch_id,
        archived_at = v_now
    FROM public.professor_load pl
    JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.professor_load_id = pl.id
      AND sc.archive = false
      AND (v_target_pid IS NULL OR sc.program_id = v_target_pid)
      AND (p_semester IS NULL OR trim(p_semester) = '' OR trim(lower(c.semester)) = trim(lower(p_semester)));

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',        true,
        'batch_id',       v_batch_id,
        'archived_at',    v_now,
        'archived_count', v_count,
        'program_id',     v_target_pid,
        'semester',       p_semester
    );
END;
$$;

REVOKE ALL ON FUNCTION public.archive_active_schedule(integer, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.archive_active_schedule(integer, text, text, text) TO anon, authenticated, service_role;


-- ------------------------------------------------------------------------------
-- 4. RPC: restore_archived_schedule_batch (Enforce 1 active schedule, clean unarchive)
-- ------------------------------------------------------------------------------
DROP FUNCTION IF EXISTS public.restore_archived_schedule_batch(text, text);

CREATE OR REPLACE FUNCTION public.restore_archived_schedule_batch(
    p_batch_id      text,
    p_restored_by   text DEFAULT 'Scheduler'
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_target_semester   text;
    v_target_program_id integer;
    v_target_program    text;
    v_restored          integer := 0;
    v_active_count      integer := 0;
    v_caller_role       text;
    v_caller_prog_id    integer;
BEGIN
    v_caller_role := public.get_user_role();
    v_caller_prog_id := public.get_user_program_id();

    -- Identify the batch scope via course join
    SELECT coalesce(c.semester, '1st Semester'), sc.program_id, p.program_name
    INTO v_target_semester, v_target_program_id, v_target_program
    FROM public.schedule sc
    LEFT JOIN public.program p ON sc.program_id = p.id
    LEFT JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    LEFT JOIN public.course c ON pl.course_id = c.course_id
    WHERE (
        (p_batch_id = 'legacy' AND sc.archive_batch_id IS NULL AND sc.archive = true)
        OR
        (sc.archive_batch_id::text = p_batch_id AND sc.archive = true)
    )
    LIMIT 1;

    IF v_target_semester IS NULL THEN
        RAISE EXCEPTION 'Archived batch % not found.', p_batch_id;
    END IF;

    -- Security enforcement
    IF v_caller_role = 'viewer' THEN
        RAISE EXCEPTION 'Permission denied: Viewers cannot restore schedules.'
            USING ERRCODE = '42501';
    ELSIF v_caller_role = 'scheduler' THEN
        IF v_target_program_id IS NOT NULL AND v_caller_prog_id IS NOT NULL AND v_target_program_id != v_caller_prog_id THEN
            RAISE EXCEPTION 'Permission denied: Schedulers can only restore their own program archives.'
                USING ERRCODE = '42501';
        END IF;
    ELSIF v_caller_role != 'admin' THEN
        RAISE EXCEPTION 'Permission denied: Unauthorized role.'
            USING ERRCODE = '42501';
    END IF;

    -- Enforce single active schedule rule: If active schedule already exists, block restore with clear message
    SELECT count(*)
    INTO v_active_count
    FROM public.schedule sc
    JOIN public.professor_load pl ON sc.professor_load_id = pl.id
    JOIN public.course c ON pl.course_id = c.course_id
    WHERE sc.archive = false
      AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id)
      AND (trim(lower(c.semester)) = trim(lower(v_target_semester)));

    IF v_active_count > 0 THEN
        RAISE EXCEPTION 'Cannot restore schedule: An active schedule already exists for % (%). Please archive it before restoring.',
            coalesce(v_target_program, 'this program'), v_target_semester;
    END IF;

    -- Reactivate the specified batch: set archive = false, clear archived_at and archive_batch_id
    IF p_batch_id = 'legacy' THEN
        UPDATE public.schedule sc
        SET archive = false,
            archived_at = NULL,
            archive_batch_id = NULL
        FROM public.professor_load pl
        JOIN public.course c ON pl.course_id = c.course_id
        WHERE sc.professor_load_id = pl.id
          AND sc.archive = true
          AND sc.archive_batch_id IS NULL
          AND (v_target_program_id IS NULL OR sc.program_id = v_target_program_id)
          AND (trim(lower(c.semester)) = trim(lower(v_target_semester)));
    ELSE
        UPDATE public.schedule
        SET archive = false,
            archived_at = NULL,
            archive_batch_id = NULL
        WHERE archive = true
          AND archive_batch_id::text = p_batch_id;
    END IF;

    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN jsonb_build_object(
        'success',           true,
        'restored_batch_id', p_batch_id,
        'restored_count',    v_restored,
        'semester',          v_target_semester,
        'program',           v_target_program,
        'program_id',        v_target_program_id
    );
END;
$$;

REVOKE ALL ON FUNCTION public.restore_archived_schedule_batch(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.restore_archived_schedule_batch(text, text) TO anon, authenticated, service_role;

-- ------------------------------------------------------------------------------
-- 5. RELOAD POSTGREST SCHEMA CACHE
-- ------------------------------------------------------------------------------
NOTIFY pgrst, 'reload schema';

COMMIT;
