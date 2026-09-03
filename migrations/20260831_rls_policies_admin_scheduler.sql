-- ==============================================================================
-- Least Privilege RLS Policies for Backup & Restore (Admin & Scheduler)
-- Ensures all 13 application tables permit full CRUD processes (Create, Read,
-- Update, Delete) when authenticated as 'admin' or 'scheduler' without
-- disabling RLS or using service_role keys.
-- ==============================================================================

-- Helper Function: Check User Role from JWT claims and fallback to public.users
CREATE OR REPLACE FUNCTION public.get_user_role()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT LOWER(COALESCE(
        (auth.jwt() -> 'app_metadata' ->> 'role'),
        (auth.jwt() -> 'user_metadata' ->> 'role'),
        (auth.jwt() ->> 'role'),
        (SELECT role FROM public.users WHERE id::text = auth.uid()::text LIMIT 1),
        'viewer'
    ));
$$;

-- 1. program_department
DROP POLICY IF EXISTS "program_department_admin_scheduler_all" ON public.program_department;
CREATE POLICY "program_department_admin_scheduler_all"
ON public.program_department
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 2. activity_log
DROP POLICY IF EXISTS "activity_log_admin_scheduler_all" ON public.activity_log;
CREATE POLICY "activity_log_admin_scheduler_all"
ON public.activity_log
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 3. users
DROP POLICY IF EXISTS "users_admin_scheduler_all" ON public.users;
CREATE POLICY "users_admin_scheduler_all"
ON public.users
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 4. delete_requests
DROP POLICY IF EXISTS "delete_requests_scheduler_all" ON public.delete_requests;
CREATE POLICY "delete_requests_scheduler_all"
ON public.delete_requests
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 5. scheduler_notifications
DROP POLICY IF EXISTS "scheduler_notifications_admin_scheduler_all" ON public.scheduler_notifications;
CREATE POLICY "scheduler_notifications_admin_scheduler_all"
ON public.scheduler_notifications
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 6. schedule
DROP POLICY IF EXISTS "schedule_admin_scheduler_all" ON public.schedule;
CREATE POLICY "schedule_admin_scheduler_all"
ON public.schedule
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 7. course
DROP POLICY IF EXISTS "course_admin_scheduler_all" ON public.course;
CREATE POLICY "course_admin_scheduler_all"
ON public.course
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 8. professor
DROP POLICY IF EXISTS "professor_admin_scheduler_all" ON public.professor;
CREATE POLICY "professor_admin_scheduler_all"
ON public.professor
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 9. room
DROP POLICY IF EXISTS "room_admin_scheduler_all" ON public.room;
CREATE POLICY "room_admin_scheduler_all"
ON public.room
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 10. timeslot
DROP POLICY IF EXISTS "timeslot_admin_scheduler_all" ON public.timeslot;
CREATE POLICY "timeslot_admin_scheduler_all"
ON public.timeslot
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 11. prof_course
DROP POLICY IF EXISTS "prof_course_admin_scheduler_all" ON public.prof_course;
CREATE POLICY "prof_course_admin_scheduler_all"
ON public.prof_course
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 12. irregular_students
DROP POLICY IF EXISTS "irregular_students_admin_scheduler_all" ON public.irregular_students;
CREATE POLICY "irregular_students_admin_scheduler_all"
ON public.irregular_students
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));

-- 13. irregular_student_schedule
DROP POLICY IF EXISTS "irregular_student_schedule_admin_scheduler_all" ON public.irregular_student_schedule;
CREATE POLICY "irregular_student_schedule_admin_scheduler_all"
ON public.irregular_student_schedule
FOR ALL
TO authenticated
USING (get_user_role() IN ('admin', 'scheduler'))
WITH CHECK (get_user_role() IN ('admin', 'scheduler'));
