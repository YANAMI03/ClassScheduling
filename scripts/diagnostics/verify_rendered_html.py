import os
import sys
import dotenv
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
dotenv.load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

import app

client = app.app.test_client()

# Login as scheduler
login_res = client.post('/login', data={'username': 'scheduler@example.com', 'password': 'password123'}, follow_redirects=True)
print('Login status:', login_res.status_code)

# Generate schedule for 2nd Semester
gen_res = client.post('/generate_schedule', data={'semester': '2nd Semester', 'number_of_sections': '1'}, follow_redirects=True)
print('Generate status:', gen_res.status_code)

soup = BeautifulSoup(gen_res.data.decode('utf-8'), 'html.parser')

unsched_card = None
for card in soup.find_all('div', class_='card'):
    if 'Unscheduled Professor Load Classes' in card.get_text():
        unsched_card = card
        break

if unsched_card:
    h6 = unsched_card.find('h6')
    print('\nFound Unscheduled Table Header:', h6.get_text(strip=True) if h6 else 'N/A')
    rows = unsched_card.find('tbody').find_all('tr')
    print(f'Total Unscheduled Rows: {len(rows)}')
    for idx, r in enumerate(rows, start=1):
        cols = [td.get_text(strip=True) for td in r.find_all('td')]
        print(f'Row {idx}: Professor="{cols[0]}", Course="{cols[1]}", Section="{cols[2]}", Placed/Req="{cols[3]}", Reason="{cols[4]}"')
else:
    print('Unscheduled card not found!')

html_text = gen_res.data.decode('utf-8')
print('\nAny "Prof #None" in HTML:', 'Prof #None' in html_text)
print('Any "Prof #" in HTML:', 'Prof #' in html_text)
print('Any "None" in Professor column:', any('None' == td.get_text(strip=True) for td in soup.find_all('td')))
