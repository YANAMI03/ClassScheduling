import json
from scripts.populate_prof_course import distribute_courses_with_multi_prof

# 42 professors
profs = [
    {"prof_id": 1, "first_name": "Henry", "last_name": "Roque", "department": "CICT"},
    {"prof_id": 2, "first_name": "Pwet", "last_name": "Vien", "department": "CICT"},
    {"prof_id": 3, "first_name": "Burst", "last_name": "Fade", "department": "CICT"},
    {"prof_id": 4, "first_name": "Jerome", "last_name": "Paulo", "department": "CICT"},
    {"prof_id": 5, "first_name": "Gean", "last_name": "Grvic", "department": "CICT"},
    {"prof_id": 16, "first_name": "Maria", "last_name": "Santos", "department": "CICT"},
    {"prof_id": 17, "first_name": "Jose", "last_name": "Reyes", "department": "CICT"},
    {"prof_id": 18, "first_name": "Kevin", "last_name": "Cruz", "department": "CICT"},
    {"prof_id": 19, "first_name": "Laura", "last_name": "Garcia", "department": "CICT"},
    {"prof_id": 20, "first_name": "Antonio", "last_name": "Mendoza", "department": "CICT"},
    {"prof_id": 21, "first_name": "Rachel", "last_name": "Torres", "department": "CICT"},
    {"prof_id": 22, "first_name": "Steven", "last_name": "Villanueva", "department": "CICT"},
    {"prof_id": 23, "first_name": "Michelle", "last_name": "Ramos", "department": "CICT"},
    {"prof_id": 24, "first_name": "Carlos", "last_name": "Castro", "department": "CICT"},
    {"prof_id": 25, "first_name": "Amanda", "last_name": "Flores", "department": "CICT"},
    {"prof_id": 26, "first_name": "Micko", "last_name": "Pogi", "department": "CICT"},
    {"prof_id": 29, "first_name": "lad", "last_name": "grvic", "department": "CICT"},
    {"prof_id": 30, "first_name": "Micko", "last_name": "La madrid", "department": "CICT"},
    {"prof_id": 31, "first_name": "Alexander", "last_name": "Tan", "department": "CICT"},
    {"prof_id": 32, "first_name": "Bernadette", "last_name": "Aquino", "department": "CICT"},
    {"prof_id": 33, "first_name": "Christian", "last_name": "del Rosario", "department": "CICT"},
    {"prof_id": 34, "first_name": "Diana", "last_name": "Navarro", "department": "CICT"},
    {"prof_id": 35, "first_name": "Eduardo", "last_name": "Castillo", "department": "CICT"},
    {"prof_id": 36, "first_name": "Fatima", "last_name": "Soriano", "department": "CICT"},
    {"prof_id": 37, "first_name": "Gabriel", "last_name": "Bautista", "department": "CICT"},
    {"prof_id": 38, "first_name": "Hannah", "last_name": "Mercado", "department": "CICT"},
    {"prof_id": 39, "first_name": "Ivan", "last_name": "Padilla", "department": "CICT"},
    {"prof_id": 40, "first_name": "Jasmine", "last_name": "Lim", "department": "CICT"},
    {"prof_id": 41, "first_name": "Kenneth", "last_name": "Salazar", "department": "CICT"},
    {"prof_id": 42, "first_name": "Lorraine", "last_name": "Valdez", "department": "CICT"},
    {"prof_id": 43, "first_name": "Mark", "last_name": "Villanueva", "department": "CICT"},
    {"prof_id": 44, "first_name": "Nicole", "last_name": "Domingo", "department": "CICT"},
    {"prof_id": 45, "first_name": "Oliver", "last_name": "Pascual", "department": "CICT"},
    {"prof_id": 46, "first_name": "Patricia", "last_name": "Santiago", "department": "CICT"},
    {"prof_id": 47, "first_name": "Rafael", "last_name": "Estrada", "department": "CICT"},
    {"prof_id": 48, "first_name": "Stephanie", "last_name": "Guerrero", "department": "CICT"},
    {"prof_id": 49, "first_name": "Tristan", "last_name": "Alvarez", "department": "CICT"},
    {"prof_id": 50, "first_name": "Victoria", "last_name": "Samson", "department": "CICT"},
    {"prof_id": 51, "first_name": "Ramon", "last_name": "Dizon", "department": "CICT"},
    {"prof_id": 52, "first_name": "Katrina", "last_name": "Legaspi", "department": "CICT"},
    {"prof_id": 53, "first_name": "Miguel", "last_name": "Ocampo", "department": "CICT"},
    {"prof_id": 54, "first_name": "Bea", "last_name": "Morales", "department": "CICT"},
]

# 42 courses
courses = [
    {"course_id": 1, "course_name": "CC-100", "program": "BSIT", "year_level": 1, "semester": "1st Semester", "major": None},
    {"course_id": 2, "course_name": "CC-101", "program": "BSIT", "year_level": 1, "semester": "1st Semester", "major": None},
    {"course_id": 3, "course_name": "IT-NET01", "program": "BSIT", "year_level": 1, "semester": "1st Semester", "major": None},
    {"course_id": 10, "course_name": "CC-102", "program": "BSIT", "year_level": 1, "semester": "2nd Semester", "major": None},
    {"course_id": 11, "course_name": "IT-NET02", "program": "BSIT", "year_level": 1, "semester": "2nd Semester", "major": None},
    {"course_id": 12, "course_name": "IT-WS01", "program": "BSIT", "year_level": 1, "semester": "2nd Semester", "major": None},
    {"course_id": 18, "course_name": "CC-103", "program": "BSIT", "year_level": 2, "semester": "1st Semester", "major": None},
    {"course_id": 19, "course_name": "IT-PF01", "program": "BSIT", "year_level": 2, "semester": "1st Semester", "major": None},
    {"course_id": 20, "course_name": "IT-WS02", "program": "BSIT", "year_level": 2, "semester": "1st Semester", "major": None},
    {"course_id": 21, "course_name": "IT-MS01", "program": "BSIT", "year_level": 2, "semester": "1st Semester", "major": None},
    {"course_id": 26, "course_name": "CC-104", "program": "BSIT", "year_level": 2, "semester": "2nd Semester", "major": None},
    {"course_id": 27, "course_name": "CC-105", "program": "BSIT", "year_level": 2, "semester": "2nd Semester", "major": None},
    {"course_id": 28, "course_name": "IT-PF02", "program": "BSIT", "year_level": 2, "semester": "2nd Semester", "major": None},
    {"course_id": 29, "course_name": "IT-HCI01", "program": "BSIT", "year_level": 2, "semester": "2nd Semester", "major": None},
    {"course_id": 30, "course_name": "IT-MS02", "program": "BSIT", "year_level": 2, "semester": "2nd Semester", "major": None},
    {"course_id": 44, "course_name": "IT-IM01", "program": "BSIT", "year_level": 3, "semester": "1st Semester", "major": None},
    {"course_id": 45, "course_name": "IT-IPT01", "program": "BSIT", "year_level": 3, "semester": "1st Semester", "major": None},
    {"course_id": 46, "course_name": "IT-SA01", "program": "BSIT", "year_level": 3, "semester": "1st Semester", "major": None},
    {"course_id": 47, "course_name": "IT-IAS01", "program": "BSIT", "year_level": 3, "semester": "1st Semester", "major": None},
    {"course_id": 48, "course_name": "IT-WS02", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Web"},
    {"course_id": 50, "course_name": "IT-IAS02", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "General"},
    {"course_id": 51, "course_name": "IT-CAP01", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "General"},
    {"course_id": 52, "course_name": "IT-WS03", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Web Development"},
    {"course_id": 53, "course_name": "IT-WS04", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Web Development"},
    {"course_id": 54, "course_name": "IT-WS05", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Web Development"},
    {"course_id": 55, "course_name": "IT-IM02", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Database Systems"},
    {"course_id": 56, "course_name": "IT-IM03", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Database Systems"},
    {"course_id": 57, "course_name": "IT-IM04", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Database Systems"},
    {"course_id": 58, "course_name": "IT-NET03", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Networking"},
    {"course_id": 59, "course_name": "IT-NET04", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Networking"},
    {"course_id": 60, "course_name": "IT-NET05", "program": "BSIT", "year_level": 3, "semester": "2nd Semester", "major": "Networking"},
    {"course_id": 61, "course_name": "IT-SIA01", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "General"},
    {"course_id": 62, "course_name": "IT-SP01", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "General"},
    {"course_id": 63, "course_name": "IT-CAP02", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "General"},
    {"course_id": 64, "course_name": "IT-SW01", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "General"},
    {"course_id": 65, "course_name": "IT-WS06", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Web Development"},
    {"course_id": 66, "course_name": "IT-WS07", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Web Development"},
    {"course_id": 67, "course_name": "IT-IM05", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Database Systems"},
    {"course_id": 68, "course_name": "IT-IM06", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Database Systems"},
    {"course_id": 69, "course_name": "IT-NET06", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Networking"},
    {"course_id": 70, "course_name": "IT-NET07", "program": "BSIT", "year_level": 4, "semester": "1st Semester", "major": "Networking"},
    {"course_id": 72, "course_name": "IT-IPT02", "program": "BSIT", "year_level": 3, "semester": "1st Semester", "major": None},
]

assignments = distribute_courses_with_multi_prof(courses, profs, profs_per_course=3, max_load_per_prof=3)

print("=" * 80)
print(f"TOTAL ASSIGNMENTS: {len(assignments)}")
print("=" * 80)

# Check per course counts
course_summary = {}
for a in assignments:
    cid = a['course_id']
    if cid not in course_summary:
        course_summary[cid] = {'name': a['course_name'], 'profs': []}
    course_summary[cid]['profs'].append((a['prof_id'], a['prof_name']))

for cid, info in sorted(course_summary.items()):
    p_list = [f"{pname} (#{pid})" for pid, pname in info['profs']]
    print(f"Course ID {cid:2d} ({info['name']:<10}): {len(info['profs'])} profs -> {', '.join(p_list)}")

print("=" * 80)
sql_values = [f"({a['prof_id']}, {a['course_id']})" for a in assignments]
sql = f"INSERT INTO prof_course (prof_id, course_id) VALUES\n" + ",\n".join(sql_values) + ";"
with open("scripts/populate.sql", "w") as f:
    f.write("DELETE FROM prof_course;\n\n" + sql + "\n")
print(f"Saved {len(assignments)} assignments to scripts/populate.sql")
