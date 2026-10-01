import sys
sys.path.insert(0, '.')
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas
import fitz

cs = ParagraphStyle('cs', fontName='Helvetica', fontSize=7.0, leading=8.5, alignment=1)

# Test 1: What happens when cell content is taller than row height?
# 11 lines of text in a 37pt row (37pt can only fit ~4 lines of 8.5 leading!)
text_11_lines = "IT-HCI01<br/>2H<br/>308<br/>—<br/>IT-HCI01<br/>2H<br/>308<br/>—<br/>IT-HCI01<br/>2H<br/>308"

tdata = [
    [Paragraph('Row 0', cs)],
    [Paragraph(text_11_lines, cs)],
    [Paragraph('Next Row', cs)]
]

tbl = Table(tdata, colWidths=[100], rowHeights=[20, 37, 37])
tbl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.5, colors.black),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
]))

c = canvas.Canvas('try/test_overflow.pdf', pagesize=(300, 300))
tbl.wrapOn(c, 100, 94)
tbl.drawOn(c, 50, 150)
c.save()

doc = fitz.open('try/test_overflow.pdf')
page = doc[0]
pix = page.get_pixmap(dpi=150)
pix.save('try/test_overflow.png')
print('Rendered test_overflow.png')
