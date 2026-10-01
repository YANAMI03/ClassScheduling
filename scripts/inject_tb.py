with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('def generate_schedule():\n    try:', 'def generate_schedule():\n    try:\n        import traceback')
content = content.replace('        flash(f"Error generating schedule: {err}", "danger")', '        import traceback; traceback.print_exc()\n        flash(f"Error generating schedule: {err}", "danger")')

with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)
