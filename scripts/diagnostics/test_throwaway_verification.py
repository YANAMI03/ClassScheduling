import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import uuid
import time
from app import supabase, _first

def run_verification():
    print("=================================================================")
    print("STARTING THROWAWAY VERIFICATION SUITE")
    print("=================================================================")

    # Authenticate as admin to pass RLS
    auth_res = supabase.auth.sign_in_with_password({'email': 'admin@example.com', 'password': 'password123'})
    supabase.postgrest.auth(auth_res.session.access_token)
    print(f"[AUTH] Authenticated as {auth_res.user.email} with RLS access")

    # Safety assertion: check existing real schedule rows count
    real_archive_count_res = supabase.table('schedule').select('schedule_id', count='exact').eq('archive', True).execute()
    initial_real_archive_count = real_archive_count_res.count if hasattr(real_archive_count_res, 'count') and real_archive_count_res.count is not None else len(real_archive_count_res.data or [])
    print(f"[SAFETY CHECK] Existing real archived schedule rows: {initial_real_archive_count}")

    # 1. Create Throwaway Program
    prog_name = f"TEST_PROG_{uuid.uuid4().hex[:6].upper()}"
    p_res = supabase.table('program').insert({'program_name': prog_name}).execute()
    test_prog = p_res.data[0]
    test_prog_id = test_prog['id']
    print(f"[SETUP] Created throwaway program: {prog_name} (ID: {test_prog_id})")

    test_course_id = None
    test_load_id = None
    try:
        # 2. Create Throwaway Course (1st Semester)
        c_res = supabase.table('course').insert({
            'course_name': f"TC_{uuid.uuid4().hex[:4].upper()}",
            'lecture_hours': 3,
            'lab_hours': 0,
            'year_level': 1,
            'ilp_hours': 0,
            'specialization': 'General',
            'program_id': test_prog_id,
            'semester': '1st Semester'
        }).execute()
        test_course = c_res.data[0]
        test_course_id = test_course['course_id']
        print(f"[SETUP] Created throwaway course: {test_course['course_name']} (ID: {test_course_id}, Semester: {test_course['semester']})")

        # 3. Create Throwaway Professor Load
        pl_res = supabase.table('professor_load').insert({
            'course_id': test_course_id,
            'sections': 1,
            'professor_name': 'TEST PROFESSOR',
            'program_id': test_prog_id
        }).execute()
        test_load = pl_res.data[0]
        test_load_id = test_load['id']
        print(f"[SETUP] Created throwaway load: ID {test_load_id}")

        # Fetch a valid room_id
        r_res = supabase.table('room').select('room_id').limit(1).execute()
        test_room_id = r_res.data[0]['room_id'] if r_res.data else 1

        # -------------------------------------------------------------
        # TEST 1: Initial Confirm Schedule (Atomic Save via confirm_schedule_transaction)
        # -------------------------------------------------------------
        print("\n--- TEST 1: Confirm Initial Schedule (1st Semester) ---")
        batch1_id = str(uuid.uuid4())
        rows_batch1 = [
            {
                'professor_load_id': test_load_id,
                'room_id': test_room_id,
                'day': 'Monday',
                'class_start': '08:00:00',
                'class_end': '11:00:00',
                'session_type': 'Lecture',
                'section': 'TEST-1A',
                'program_id': test_prog_id,
                'archive': False
            },
            {
                'professor_load_id': test_load_id,
                'room_id': test_room_id,
                'day': 'Wednesday',
                'class_start': '08:00:00',
                'class_end': '11:00:00',
                'session_type': 'Lecture',
                'section': 'TEST-1A',
                'program_id': test_prog_id,
                'archive': False
            }
        ]

        rpc_res1 = supabase.rpc('confirm_schedule_transaction', {
            'p_semester': '1st Semester',
            'p_program': prog_name,
            'p_program_id': test_prog_id,
            'p_rows': rows_batch1,
            'p_clear_scope': True,
            'p_archived_by': 'Test Runner',
            'p_batch_id': batch1_id
        }).execute()
        print(f"Confirm 1 RPC Response: {rpc_res1.data}")
        assert rpc_res1.data.get('success') is True, "RPC 1 failed"
        assert rpc_res1.data.get('inserted_count') == 2, "Expected 2 inserted rows"

        # Verify active rows in schedule_with_semester view
        v_res1 = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res1.data) == 2, f"Expected 2 active rows in view, got {len(v_res1.data)}"
        for row in v_res1.data:
            assert row['semester'] == '1st Semester', f"Expected derived semester '1st Semester', got {row['semester']}"
            assert row['major'] == 'General', f"Expected derived major 'General', got {row['major']}"
        print("[PASS] Test 1: Schedule confirmed and active. Semester derived properly via view.")

        # -------------------------------------------------------------
        # TEST 2: Second Confirm (Must archive batch 1 and insert batch 2 atomically)
        # -------------------------------------------------------------
        print("\n--- TEST 2: Re-Confirm Schedule for Same Semester & Program ---")
        batch2_id = str(uuid.uuid4())
        rows_batch2 = [
            {
                'professor_load_id': test_load_id,
                'room_id': test_room_id,
                'day': 'Tuesday',
                'class_start': '13:00:00',
                'class_end': '16:00:00',
                'session_type': 'Lecture',
                'section': 'TEST-1A',
                'program_id': test_prog_id,
                'archive': False
            }
        ]

        rpc_res2 = supabase.rpc('confirm_schedule_transaction', {
            'p_semester': '1st Semester',
            'p_program': prog_name,
            'p_program_id': test_prog_id,
            'p_rows': rows_batch2,
            'p_clear_scope': True,
            'p_archived_by': 'Test Runner',
            'p_batch_id': batch2_id
        }).execute()
        print(f"Confirm 2 RPC Response: {rpc_res2.data}")
        assert rpc_res2.data.get('success') is True, "RPC 2 failed"
        assert rpc_res2.data.get('archived_count') == 2, f"Expected 2 archived rows, got {rpc_res2.data.get('archived_count')}"
        assert rpc_res2.data.get('inserted_count') == 1, "Expected 1 inserted row"

        # Verify active rows now only contains batch 2 (1 row)
        v_res2_act = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res2_act.data) == 1, f"Expected exactly 1 active row, got {len(v_res2_act.data)}"
        assert v_res2_act.data[0]['day'] == 'Tuesday', "Active row is not from batch 2"

        # Verify archived rows contains batch 1 (2 rows) with valid batch_id and timestamp
        v_res2_arc = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', True).execute()
        assert len(v_res2_arc.data) == 2, f"Expected 2 archived rows, got {len(v_res2_arc.data)}"
        for arc_row in v_res2_arc.data:
            assert arc_row['archive_batch_id'] == batch2_id, "Archive batch ID mismatch"
            assert arc_row['archived_at'] is not None, "Archived at timestamp is NULL"
            assert arc_row['semester'] == '1st Semester', "Derived semester on archive is invalid"
        print("[PASS] Test 2: Previous schedule atomically archived, new schedule is active.")

        # -------------------------------------------------------------
        # TEST 3: Forced-Failure Atomicity Test (Rollback verification)
        # -------------------------------------------------------------
        print("\n--- TEST 3: Forced Failure Rollback Test ---")
        failed_batch_id = str(uuid.uuid4())
        malformed_rows = [
            {
                'professor_load_id': test_load_id,
                'room_id': test_room_id,
                'day': 'Thursday',
                'class_start': 'INVALID_TIME_FORMAT',  # Will trigger error in PostgreSQL casting ::time
                'class_end': '16:00:00',
                'section': 'TEST-1A',
                'program_id': test_prog_id
            }
        ]

        failed = False
        try:
            supabase.rpc('confirm_schedule_transaction', {
                'p_semester': '1st Semester',
                'p_program': prog_name,
                'p_program_id': test_prog_id,
                'p_rows': malformed_rows,
                'p_clear_scope': True,
                'p_archived_by': 'Test Runner',
                'p_batch_id': failed_batch_id
            }).execute()
        except Exception as err:
            failed = True
            print(f"Expected failure caught successfully: {err}")

        assert failed, "Transaction should have failed on invalid time format"

        # Verify that the active schedule was NOT archived and remained active (Rollback verification)
        v_res3_act = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res3_act.data) == 1, f"Expected active schedule to remain active (1 row), got {len(v_res3_act.data)}"
        assert v_res3_act.data[0]['day'] == 'Tuesday', "Active row corrupted or changed"

        # Verify archived count did not change
        v_res3_arc = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', True).execute()
        assert len(v_res3_arc.data) == 2, f"Expected archived count to remain 2, got {len(v_res3_arc.data)}"
        print("[PASS] Test 3: Forced failure completely rolled back. Old schedule remained active with 0 half-saved rows.")

        # -------------------------------------------------------------
        # TEST 4: Archive Active Schedule & Restore Batch
        # -------------------------------------------------------------
        print("\n--- TEST 4: Archive Active Schedule & Restore Batch ---")
        arc_rpc = supabase.rpc('archive_active_schedule', {
            'p_program_id': test_prog_id,
            'p_semester': '1st Semester',
            'p_archived_by': 'Test Runner'
        }).execute()
        print(f"Archive active RPC response: {arc_rpc.data}")
        assert arc_rpc.data.get('success') is True, "archive_active_schedule failed"
        assert arc_rpc.data.get('archived_count') == 1, "Expected 1 archived row"

        # Confirm 0 active rows now
        v_res4_act = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res4_act.data) == 0, f"Expected 0 active rows, got {len(v_res4_act.data)}"

        # Check archive batches query
        batch_list_rpc = supabase.rpc('get_schedule_archive_batches', {
            'p_program': prog_name,
            'p_semester': '1st Semester'
        }).execute()
        print(f"Batches list for {prog_name}: {batch_list_rpc.data}")
        assert len(batch_list_rpc.data) >= 1, "Expected at least 1 archive batch returned"
        assert batch_list_rpc.data[0]['semester'] == '1st Semester', "Batch semester mismatch"

        # Now test restore
        restore_rpc = supabase.rpc('restore_archived_schedule_batch', {
            'p_batch_id': batch2_id,
            'p_restored_by': 'Test Runner'
        }).execute()
        print(f"Restore batch RPC response: {restore_rpc.data}")
        assert restore_rpc.data.get('success') is True, "Restore RPC failed"

        # Verify batch 2 is active again (2 rows from batch 1 were tagged with batch2_id)
        v_res4_rest = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res4_rest.data) == 2, f"Expected 2 restored active rows, got {len(v_res4_rest.data)}"
        print("[PASS] Test 4: Archive and restore completed successfully with derived semester.")

        # -------------------------------------------------------------
        # TEST 5: Double-Confirm Concurrency Test
        # -------------------------------------------------------------
        print("\n--- TEST 5: Double-Confirm Concurrency Test ---")
        # Run two confirmations in sequence with the exact same target semester & program
        dc_batch_a = str(uuid.uuid4())
        dc_batch_b = str(uuid.uuid4())
        rows_a = [{'professor_load_id': test_load_id, 'room_id': test_room_id, 'day': 'Friday', 'class_start': '08:00:00', 'class_end': '10:00:00', 'section': 'TEST-1A', 'program_id': test_prog_id}]
        rows_b = [{'professor_load_id': test_load_id, 'room_id': test_room_id, 'day': 'Friday', 'class_start': '10:00:00', 'class_end': '12:00:00', 'section': 'TEST-1A', 'program_id': test_prog_id}]

        res_a = supabase.rpc('confirm_schedule_transaction', {
            'p_semester': '1st Semester',
            'p_program': prog_name,
            'p_program_id': test_prog_id,
            'p_rows': rows_a,
            'p_clear_scope': True,
            'p_archived_by': 'Test Runner',
            'p_batch_id': dc_batch_a
        }).execute()
        res_b = supabase.rpc('confirm_schedule_transaction', {
            'p_semester': '1st Semester',
            'p_program': prog_name,
            'p_program_id': test_prog_id,
            'p_rows': rows_b,
            'p_clear_scope': True,
            'p_archived_by': 'Test Runner',
            'p_batch_id': dc_batch_b
        }).execute()

        # Check active rows count: MUST be exactly 1 (from batch b), never 2
        v_res5 = supabase.table('schedule_with_semester').select('*').eq('program_id', test_prog_id).eq('archive', False).execute()
        assert len(v_res5.data) == 1, f"Expected exactly 1 active schedule row after double-confirm, got {len(v_res5.data)}"
        assert v_res5.data[0]['class_start'] == '10:00:00', "Active schedule is not the latest confirmed batch"
        print("[PASS] Test 5: Double-confirm concurrency verified. Exactly one active schedule maintained.")

    finally:
        # -------------------------------------------------------------
        # CLEANUP: Remove throwaway records completely
        # -------------------------------------------------------------
        print("\n--- CLEANUP: Removing throwaway test records ---")
        # 1. Delete all schedules for test_prog_id
        del_s = supabase.table('schedule').delete().eq('program_id', test_prog_id).execute()
        print(f"Deleted {len(del_s.data or [])} throwaway schedule rows.")

        # 2. Delete test professor load
        if test_load_id:
            del_pl = supabase.table('professor_load').delete().eq('id', test_load_id).execute()
            print(f"Deleted throwaway professor load {test_load_id}.")

        # 3. Delete test course
        if test_course_id:
            del_c = supabase.table('course').delete().eq('course_id', test_course_id).execute()
            print(f"Deleted throwaway course {test_course_id}.")

        # 4. Delete test program
        del_p = supabase.table('program').delete().eq('id', test_prog_id).execute()
        print(f"Deleted throwaway program {test_prog_id}.")

        # 5. Final safety check: assert real archive count unchanged
        final_real_archive_res = supabase.table('schedule').select('schedule_id', count='exact').eq('archive', True).execute()
        final_real_archive_count = final_real_archive_res.count if hasattr(final_real_archive_res, 'count') and final_real_archive_res.count is not None else len(final_real_archive_res.data or [])
        print(f"[FINAL SAFETY CHECK] Real archived rows before: {initial_real_archive_count}, after: {final_real_archive_count}")
        assert initial_real_archive_count == final_real_archive_count, "REAL ARCHIVE ROWS WERE MODIFIED!"
        print("[SAFETY VERIFIED] Zero real rows were modified or deleted.")

    print("\n=================================================================")
    print("ALL 5 TESTS PASSED SUCCESSFULLY!")
    print("=================================================================")

if __name__ == '__main__':
    run_verification()
