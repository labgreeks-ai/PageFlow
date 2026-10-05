"""Headless tests for the reader window. Run:  python tests/test_reader.py"""
import os, sys, tempfile, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import pymupdf
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QImage

S = tempfile.mkdtemp(prefix="pageflow-test-")

# 7-page test document with bookmarks
_d = pymupdf.open()
for _i in range(1, 8):
    _p = _d.new_page(width=595, height=842 if _i != 4 else 500)
    _p.insert_text((72, 100), f"Test page {_i}", fontsize=28)
    _p.insert_text((72, 150), "The quick brown fox jumps over the lazy dog. Reader search target: needle", fontsize=11)
_d.set_toc([[1, "Intro", 1], [1, "Middle", 3], [2, "Sub section", 4], [1, "End", 7]])
_d.save(f"{S}/test.pdf")
_d.close()

# second test PDF: a page with its own 90 degree rotation
doc = pymupdf.open()
pg = doc.new_page(width=595, height=842)
pg.insert_text((72, 300), "needle on a rotated page", fontsize=20)
pg.set_rotation(90)
doc.save(f"{S}/rotated.pdf"); doc.close()

import pageflow as pdf_reader
QCoreApplication.setOrganizationName("PageFlowTest")
QCoreApplication.setApplicationName("PageFlowTest")
app = QApplication([])

def pump(ms=200):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    ok = ok and bool(cond)

from PySide6.QtCore import QSettings
QSettings().clear()
win = pdf_reader.MainWindow()
win.show(); pump(300)

win.open_path(f"{S}/test.pdf"); pump(600)
v = win.view
check("document opened", win.doc is not None and v.page_count() == 7)
check("thumbnail list has 7 items", win.thumbs.count() == 7)
top_items = win.outline.topLevelItemCount()
check("outline has 3 top-level entries", top_items == 3, f"{top_items}")
check("outline nested entry", win.outline.topLevelItem(1).childCount() == 1)
check("some page is rendered", len(v._rendered) > 0, f"rendered={sorted(v._rendered)}")
check("fit-width scale sensible", 0.3 < v.scale < 6, f"{v.scale:.2f}")
check("page spin max", win.page_spin.maximum() == 7)

# zoom
s0 = v.scale
v.zoom_in(); pump(200)
check("zoom in increases scale", v.scale > s0, f"{s0:.2f}->{v.scale:.2f}")
v.zoom_out(); v.zoom_out(); pump(200)
check("zoom out decreases scale", v.scale < s0, f"{v.scale:.2f}")
win.apply_zoom_text("150%"); pump(200)
check("typed zoom 150%", abs(v.scale - 1.5) < 0.01, f"{v.scale:.2f}")
win.apply_zoom_text("Fit page"); pump(200)
check("fit page mode", v.zoom_mode == "fit-page")
win.apply_zoom_text("Fit width"); pump(200)

# navigation
v.go_to_page(3); pump(300)
check("go to page 4", v.current_page() == 3, f"{v.current_page()}")
check("spin follows", win.page_spin.value() == 4)
v.next_page(); pump(200)
check("next page", v.current_page() == 4)
v.prev_page(); pump(200)
check("prev page", v.current_page() == 3)
v.go_to_page(6); pump(300)
check("last page is current at bottom", v.current_page() == 6, f"{v.current_page()}")
win.view.go_to_page(0); pump(200)
win.on_outline_clicked(win.outline.topLevelItem(2), 0); pump(300)
check("outline click goes to page 7", v.current_page() == 6)
win.view.go_to_page(0); pump(200)
win.page_spin.setValue(3); pump(300)
check("page spin goes to page 3", v.current_page() == 2, f"{v.current_page()}")

# search
win.search_box.setText("needle"); win.search_timer.stop(); win.start_search("needle")
pump(800)
check("search finds 7 matches", len(win.matches) == 7, f"{len(win.matches)}")
check("status text", win.search_status.text().endswith("of 7"), win.search_status.text())
cur0 = win.match_index
win.step_match(1); pump(300)
check("next match advances", win.match_index == (cur0 + 1) % 7)
check("view scrolled to match page", abs(v.current_page() - win.matches[win.match_index][0]) <= 1)
win.start_search("zzzz-not-there"); pump(500)
check("not found status", win.search_status.text() == "Not found", win.search_status.text())
win.start_search("needle"); pump(600)

def analyse(pno, rot):
    v.view_rot = rot
    v.relayout(); pump(100)
    plain = v.render_image(pno, 1.0, 1.0, with_highlights=False)
    hl = v.render_image(pno, 1.0, 1.0, with_highlights=True)
    xs, ys = [], []
    dark_in_box = 0
    w, h = plain.width(), plain.height()
    for y in range(h):
        for x in range(w):
            if plain.pixel(x, y) != hl.pixel(x, y):
                xs.append(x); ys.append(y)
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            c = plain.pixelColor(x, y)
            if c.red() < 110 and c.green() < 110 and c.blue() < 110:
                dark_in_box += 1
    return (x0, y0, x1, y1), dark_in_box, (w, h)

for rot in (0, 90, 180, 270):
    r = analyse(0, rot)
    check(f"highlight lands on text at view rotation {rot}", r is not None and r[1] > 20, f"{r}")
v.view_rot = 0; v.relayout(); pump(100)

# a page that is itself rotated 90 degrees in the file
win.open_path(f"{S}/rotated.pdf"); pump(500)
win.start_search("needle"); pump(500)
for rot in (0, 90):
    r = analyse(0, rot)
    check(f"highlight on intrinsically rotated page, view rotation {rot}", r is not None and r[1] > 20, f"{r}")
v.view_rot = 0; v.relayout()

# dark mode
win.open_path(f"{S}/test.pdf"); pump(500)
win.start_search(""); pump(100)
img_light = v.render_image(0, 0.5, 1.0)
win.act_dark.setChecked(True); pump(300)
img_dark = v.render_image(0, 0.5, 1.0)
def mean_lum(img):
    t = n = 0
    for y in range(0, img.height(), 4):
        for x in range(0, img.width(), 4):
            c = img.pixelColor(x, y); t += c.red() + c.green() + c.blue(); n += 3
    return t / n
check("dark pages are darker", mean_lum(img_dark) < mean_lum(img_light) - 100, f"{mean_lum(img_light):.0f} vs {mean_lum(img_dark):.0f}")
win.act_dark.setChecked(False)

# rotation through the UI
win.rotate(); pump(300)
check("rotate turns view 90", v.view_rot == 90)
w0 = v.widgets[0].width(); h0 = v.widgets[0].height()
check("rotated page is landscape", w0 > h0, f"{w0}x{h0}")
win.rotate(); win.rotate(); win.rotate(); pump(200)
check("four rotates return to 0", v.view_rot == 0)

# tools
win.copy_page_text()
from PySide6.QtGui import QGuiApplication
txt = QGuiApplication.clipboard().text()
check("copy page text", "Test page" in txt, repr(txt[:30]))
v.view_rot = 90
win.apply_view_rotation_to_doc()
out = f"{S}/copy.pdf"
win.doc.save(out, garbage=3, deflate=True)
d2 = pymupdf.open(out)
check("saved copy has rotation", d2[0].rotation == 90, f"{d2[0].rotation}")
d2.close(); v.view_rot = 0; v.relayout()

# encrypted document
enc = pymupdf.open(f"{S}/test.pdf")
enc.save(f"{S}/enc.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
e = pymupdf.open(f"{S}/enc.pdf")
check("encrypted doc needs password", e.needs_pass and not e.authenticate("bad") and e.authenticate("secret"))

# screenshots
win.open_path(f"{S}/test.pdf"); pump(500)
win.start_search("needle"); pump(600)
win.resize(1200, 800); pump(300)
win.grab().save(f"{S}/shot_light.png")
win.act_dark.setChecked(True); pump(400)
win.grab().save(f"{S}/shot_dark.png")


# branding checks
import pageflow
check("app name is PageFlow", pageflow.APP_NAME == "PageFlow")
check("author is Amman", pageflow.AUTHOR == "Amman")
check("empty state shows name and author", "PageFlow" in v.hint.text() and "Amman" in v.hint.text() and "labgreeks-ai" in v.hint.text())
check("window title uses new name", win.windowTitle().endswith("- PageFlow"), win.windowTitle())
check("icon file found", os.path.exists(pageflow.resource_path("icon.png")))
from PySide6.QtWidgets import QMessageBox
captured = {}
QMessageBox.about = staticmethod(lambda parent, title, text: captured.update(title=title, text=text))
win.act_about.trigger()
check("About dialog credits Amman, the GitHub profile and the libraries", "Amman" in captured.get("text", "") and "labgreeks-ai" in captured.get("text", "") and "PyMuPDF" in captured.get("text", "") and "PageFlow" in captured.get("title", ""), captured.get("title", ""))
help_menu = [m.title() for m in win.menuBar().findChildren(type(win.menuBar().addMenu("x")))]

# close cleanly
win.close(); pump(100)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
