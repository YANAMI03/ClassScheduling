BEGIN;

ALTER VIEW public.schedule_with_semester
    RENAME COLUMN course_name TO course_code;

DO $migration$
DECLARE
    function_definition text;
BEGIN
    SELECT pg_get_functiondef(
        'public.restore_database_json(jsonb,boolean)'::regprocedure
    )
    INTO function_definition;

    IF position('course_name' IN function_definition) = 0 THEN
        RAISE EXCEPTION 'restore_database_json does not contain the expected legacy course field';
    END IF;

    function_definition := replace(function_definition, 'course_name', 'course_code');
    function_definition := replace(
        function_definition,
        '(x->>''course_code'')::varchar',
        'COALESCE(NULLIF(x->>''course_code'', ''''), x->>''course_name'')::varchar'
    );

    EXECUTE function_definition;
END
$migration$;

NOTIFY pgrst, 'reload schema';

COMMIT;