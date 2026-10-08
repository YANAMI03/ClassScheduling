import difflib

with open('scripts/diagnostics/reproduce_generation.py', 'r', encoding='utf-8') as f:
    sim_code = f.read()

with open('app.py', 'r', encoding='utf-8') as f:
    app_code = f.read()

# Let's inspect where simulate_generation differs from generate_schedule
# In app.py, extract generate_schedule
lines = app_code.splitlines()
start = None
for i, l in enumerate(lines):
    if 'def generate_schedule():' in l:
        start = i
        break

end = None
for i in range(start + 1, len(lines)):
    if lines[i].startswith('def ') or lines[i].startswith('@app.route'):
        if not lines[i].startswith('    '):
            end = i
            break

gen_lines = lines[start:end]
sim_lines = sim_code.splitlines()

print(f"gen_lines count: {len(gen_lines)}, sim_lines count: {len(sim_lines)}")

# Let's check specific parts:
# 1. How does app.py sort candidate slots vs reproduce_generation?
# 2. How does app.py assign section_courses vs reproduce_generation?
# 3. How does app.py handle cutoff vs reproduce_generation?

for phrase in ['prof_track_affinity', 'section_courses.sort', '_schedule_paired_block', 'slot_groups', 'room_bookings']:
    gen_occurrences = [i for i, l in enumerate(gen_lines) if phrase in l]
    sim_occurrences = [i for i, l in enumerate(sim_lines) if phrase in l]
    print(f"Phrase '{phrase}': in app.py at {gen_occurrences[:3]}, in sim at {sim_occurrences[:3]}")
