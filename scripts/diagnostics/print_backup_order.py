import json
import dotenv
import os
import sys

with open('backups/professor_load_backup_20261003_191218.json') as f:
    data = json.load(f)

pls = data.get('professor_load_rows', [])
print(f"Total rows in backup: {len(pls)}")
for i, r in enumerate(pls):
    print(f"{i:2d}: id={r.get('id'):3d} prof={r.get('prof_id'):2d} course={r.get('course_id'):2d} sec={r.get('sections'):2d}")
