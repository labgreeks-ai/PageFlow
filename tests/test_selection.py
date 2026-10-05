"""Headless tests for text selection and the Windows registration code (fake registry).
Run:  python tests/test_selection.py
"""
import ntpath
import os
import sys
import tempfile
import time
import types

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
S = tempfile.mkdtemp(prefix="pageflow-test-")

import pymupdf
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QGuiApplication, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

# ---------------------------------------------------------------- test document
doc = pymupdf.open()
p1 = doc.new_page(width=595, height=842)
p1.insert_text((50, 100), "ALPHA", fontsize=16)
p1.insert_text((120, 200), "beta gamma delta", fontsize=14)
p1.insert_text((450, 800), "OMEGA", fontsize=16)
p2 = doc.new_page(width=595, height=842)
p2.insert_text((72, 120), "second page words here", fontsize=14)
doc.new_page(width=595, height=842)                     # page 3: no text at all (like a scan)
p4 = doc.new_page(width=595, height=842)
p4.insert_text((72, 200), "ROTATED page text", fontsize=14)
p4.set_rotation(90)                                     # page 4 is rotated inside the file itself
doc.save(f"{S}/sel.pdf")
doc.close()

other = pymupdf.open()
other.new_page().insert_text((72, 100), "another document", fontsize=14)
other.save(f"{S}/other.pdf")
other.close()

import pageflow

QCoreApplication.setOrganizationName("PageFlowTest")
QCoreApplication.setApplicationName("PageFlowTest")
app = QApplication([])
QSettings().clear()


def pump(ms=250):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


ok = True


def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    ok = ok and bool(cond)


KINDS = {
    "press": (QEvent.MouseButtonPress, "mousePressEvent", Qt.LeftButton, Qt.LeftButton),
    "move": (QEvent.MouseMove, "mouseMoveEvent", Qt.NoButton, Qt.LeftButton),
    "hover": (QEvent.MouseMove, "mouseMoveEvent", Qt.NoButton, Qt.NoButton),
    "release": (QEvent.MouseButtonRelease, "mouseReleaseEvent", Qt.LeftButton, Qt.NoButton),
    "dbl": (QEvent.MouseButtonDblClick, "mouseDoubleClickEvent", Qt.LeftButton, Qt.LeftButton),
}


def send(view, what, pno, point, grab=None):
    """Send a mouse event. `pno`/`point` say where the pointer is; `grab` is the page widget that gets the event."""
    kind, handler, button, buttons = KINDS[what]
    src = view.widgets[pno]
    dst = view.widgets[pno if grab is None else grab]
    gp = src.mapToGlobal(point)
    ev = QMouseEvent(kind, QPointF(dst.mapFromGlobal(gp)), QPointF(gp), button, buttons, Qt.NoModifier)
    getattr(dst, handler)(ev)


def char_point(view, pno, idx, fx=0.5):
    """Widget-local pixel position of a point inside character `idx` (fx = 0 left edge .. 1 right edge)."""
    c = view._page_text(pno).chars[idx]
    pt = pymupdf.Point(c[0] + fx * (c[2] - c[0]), (c[1] + c[3]) / 2) * (view._rot_matrix(pno) * pymupdf.Matrix(view.scale, view.scale))
    return QPoint(round(pt.x), round(pt.y))


def drag(view, pno1, pt1, pno2, pt2):
    send(view, "press", pno1, pt1)
    send(view, "move", pno1, pt1 + QPoint(5, 0))
    send(view, "move", pno2, pt2, grab=pno1)
    send(view, "release", pno2, pt2, grab=pno1)


def click(view, pno, pt):
    send(view, "press", pno, pt)
    send(view, "release", pno, pt)


def double_click(view, pno, pt):
    send(view, "press", pno, pt)
    send(view, "release", pno, pt)
    send(view, "dbl", pno, pt)
    send(view, "release", pno, pt)


def joined(view, pno):
    return "".join(c[4] for c in view._page_text(pno).chars)


def as_rgb(img):
    return img.convertToFormat(QImage.Format_RGB32)


def leftmost_dark_point(img):
    """Pixel position of the leftmost dark pixel, found by looking at the picture only."""
    img = as_rgb(img)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = bytes(img.constBits())
    for x in range(w):
        ys = [y for y in range(h)
              if buf[y * bpl + x * 4] < 140 and buf[y * bpl + x * 4 + 1] < 140 and buf[y * bpl + x * 4 + 2] < 140]
        if ys:
            return x, ys[len(ys) // 2]
    return None


def diff_bbox(a, b):
    a, b = as_rgb(a), as_rgb(b)
    w, h, bpl = a.width(), a.height(), a.bytesPerLine()
    ba, bb = bytes(a.constBits()), bytes(b.constBits())
    xs, ys = [], []
    for y in range(h):
        ra, rb = ba[y * bpl:(y + 1) * bpl], bb[y * bpl:(y + 1) * bpl]
        if ra != rb:
            ys.append(y)
            xs.extend(x for x in range(w) if ra[x * 4:x * 4 + 4] != rb[x * 4:x * 4 + 4])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def dark_pixels_in(img, box):
    img = as_rgb(img)
    bpl = img.bytesPerLine()
    buf = bytes(img.constBits())
    x0, y0, x1, y1 = box
    return sum(1 for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
               if buf[y * bpl + x * 4] < 110 and buf[y * bpl + x * 4 + 1] < 110 and buf[y * bpl + x * 4 + 2] < 110)


# ---------------------------------------------------------------- open the document
win = pageflow.MainWindow()
win.show()
pump(300)
win.open_path(f"{S}/sel.pdf")
pump(600)
view = win.view
msgs = []
view.message.connect(msgs.append)
view.set_zoom_mode("fit-width")
pump(300)

line = joined(view, 0)
check("page 1 text extracted", "ALPHA" in line and "beta gamma delta" in line and "OMEGA" in line, repr(line))
check("no selection at start", not view.has_selection() and not win.act_copy.isEnabled())

# ---------------------------------------------------------------- drag selection
b = line.index("beta")
g = line.index("gamma")
drag(view, 0, char_point(view, 0, b, 0.25), 0, char_point(view, 0, g + 4, 0.75))
check("drag selects 'beta gamma'", view.selected_text() == "beta gamma", repr(view.selected_text()))
check("copy action enabled while selected", win.act_copy.isEnabled())
win.act_copy.trigger()
check("copy puts the text on the clipboard", QGuiApplication.clipboard().text() == "beta gamma")

drag(view, 0, char_point(view, 0, b, 0.75), 0, char_point(view, 0, g + 2, 0.25))
check("partial word selection ('eta ga')", view.selected_text() == "eta ga", repr(view.selected_text()))

drag(view, 0, char_point(view, 0, g + 4, 0.75), 0, char_point(view, 0, b, 0.25))
check("dragging backwards works", view.selected_text() == "beta gamma", repr(view.selected_text()))

# ---------------------------------------------------------------- double / triple click, click, escape
double_click(view, 0, char_point(view, 0, g + 1))
check("double-click selects a word", view.selected_text() == "gamma", repr(view.selected_text()))
click(view, 0, char_point(view, 0, g + 1))          # third click in a row
check("triple-click selects the line", view.selected_text() == "beta gamma delta", repr(view.selected_text()))

pump(500)                                            # let the multi-click window pass
click(view, 0, char_point(view, 0, b))
check("a plain click clears the selection", not view.has_selection())
check("copy action disabled again", not win.act_copy.isEnabled())

double_click(view, 0, char_point(view, 0, g + 1))
from PySide6.QtTest import QTest

QTest.keyClick(view, Qt.Key_Escape)
check("Escape clears the selection", not view.has_selection())

# ---------------------------------------------------------------- across pages, select all
second = joined(view, 1)
w_end = second.index("words") + 4
drag(view, 0, char_point(view, 0, b, 0.25), 1, char_point(view, 1, w_end, 0.75))
expected = "beta gamma delta\nOMEGA\nsecond page words"
check("selection across two pages", view.selected_text() == expected, repr(view.selected_text()))

win.act_select_all.trigger()
txt = view.selected_text()
check("select all covers every page", all(w in txt for w in ("ALPHA", "OMEGA", "second page words here", "ROTATED page text")), repr(txt[:60]))
check("select all starts with the first word", txt.startswith("ALPHA"))
win.act_copy.trigger()
check("copy after select all", QGuiApplication.clipboard().text() == txt)
view.clear_selection()

# ---------------------------------------------------------------- page without text
click(view, 2, QPoint(100, 100))
click(view, 2, QPoint(120, 120))
check("pages without text give a one-time hint", len(msgs) == 1 and "no selectable text" in msgs[0], f"{len(msgs)} {msgs}")

# ---------------------------------------------------------------- cursor
view.go_to_page(0)
pump(300)
send(view, "hover", 0, char_point(view, 0, g + 1))
check("I-beam cursor over text", view.widgets[0].cursor().shape() == Qt.IBeamCursor)
send(view, "hover", 0, QPoint(4, 4))
check("arrow cursor over empty page area", view.widgets[0].cursor().shape() == Qt.ArrowCursor)

# ---------------------------------------------------------------- zoom
view.set_scale(2.0)
pump(400)
double_click(view, 0, char_point(view, 0, g + 1))
check("selection works at 200% zoom", view.selected_text() == "gamma", repr(view.selected_text()))
view.clear_selection()
view.set_zoom_mode("fit-width")
pump(400)

# ---------------------------------------------------------------- rotations: pixels decide where to click
expect = {0: "ALPHA", 90: "OMEGA", 180: "OMEGA", 270: "ALPHA"}
pump(500)
for rot in (0, 90, 180, 270):
    if rot:
        win.rotate()
    view.go_to_page(0)
    pump(700)
    view.clear_selection()
    plain = view.widgets[0].grab().toImage()
    x, y = leftmost_dark_point(plain)
    double_click(view, 0, QPoint(x + 1, y))
    word = view.selected_text()
    check(f"rotation {rot}: clicking the leftmost glyph selects {expect[rot]}", word == expect[rot], repr(word))
    pump(100)
    marked = view.widgets[0].grab().toImage()
    box = diff_bbox(plain, marked)
    inside = box is not None and box[0] <= x + 1 <= box[2] and box[1] <= y <= box[3]
    ink = dark_pixels_in(plain, box) if box else 0
    check(f"rotation {rot}: blue overlay sits on the clicked text", inside and ink > 30, f"box={box} ink={ink}")
    pump(450)                                       # let multi-click window pass
win.rotate()                                        # back to 0 after the 4th turn
check("back at rotation 0", view.view_rot == 0)
pump(300)

# drag selection while rotated (uses the same transform for clicking and painting, so only a consistency check)
win.rotate()
view.go_to_page(0)
pump(500)
view.clear_selection()
drag(view, 0, char_point(view, 0, b, 0.25), 0, char_point(view, 0, line.index("delta") + 4, 0.75))
check("drag on a rotated view selects 'beta gamma delta'", view.selected_text() == "beta gamma delta", repr(view.selected_text()))
for _ in range(3):
    win.rotate()
view.clear_selection()
pump(300)

# ---------------------------------------------------------------- a page that is rotated inside the PDF itself
view.go_to_page(3)
pump(700)
plain = view.widgets[3].grab().toImage()
x, y = leftmost_dark_point(plain)
pump(450)
double_click(view, 3, QPoint(x + 1, y))
check("page rotated inside the file: leftmost glyph is in 'page'", view.selected_text() == "page", repr(view.selected_text()))
marked = view.widgets[3].grab().toImage()
box = diff_bbox(plain, marked)
check("page rotated inside the file: overlay on the ink", box is not None and dark_pixels_in(plain, box) > 20, f"box={box}")
view.clear_selection()

# ---------------------------------------------------------------- search from selection, new document
view.go_to_page(0)
pump(300)
pump(450)
double_click(view, 0, char_point(view, 0, g + 1))
check("preview for the context menu", view.selected_preview() == "gamma")
win.search_for_text(view.selected_preview())
pump(500)
check("'Search for' finds the selected word", len(win.matches) == 1, f"{len(win.matches)}")
win.grab().save(f"{S}/shot_selection.png")
win.open_path(f"{S}/other.pdf")
pump(500)
check("opening another file clears the selection", not view.has_selection() and not win.act_copy.isEnabled())

# ---------------------------------------------------------------- Windows registration with a fake registry
class FakeKey:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_WRITE, KEY_SET_VALUE, KEY_ALL_ACCESS = 0x20006, 2, 0xF003F
    REG_SZ, REG_NONE = 1, 0

    def __init__(self):
        self.keys = {}

    @staticmethod
    def norm(p):
        return p.strip("\\").lower()

    def CreateKeyEx(self, root, path, reserved=0, access=0):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):
            self.keys.setdefault(self.norm("\\".join(parts[:i])), {})
        return FakeKey(self.norm(path))

    def OpenKey(self, root, path, reserved=0, access=0):
        n = self.norm(path)
        if n not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(n)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key.path][name] = (kind, value)

    def QueryValueEx(self, key, name):
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        kind, value = self.keys[key.path][name]
        return value, kind

    def DeleteValue(self, key, name):
        if name not in self.keys[key.path]:
            raise FileNotFoundError(name)
        del self.keys[key.path][name]

    def children(self, n):
        prefix = n + "\\"
        return sorted({k[len(prefix):].split("\\")[0] for k in self.keys if k.startswith(prefix)})

    def EnumKey(self, key, index):
        kids = self.children(key.path)
        if index >= len(kids):
            raise OSError("no more items")
        return kids[index]

    def DeleteKey(self, root, path):
        n = self.norm(path)
        if n not in self.keys:
            raise FileNotFoundError(path)
        if self.children(n):
            raise OSError("key has subkeys")
        del self.keys[n]


import winassoc

reg = FakeWinreg()
sys.modules["winreg"] = reg
opened = []
real_os, real_exe = winassoc.os, sys.executable
winassoc.os = types.SimpleNamespace(path=ntpath, startfile=lambda uri: opened.append(uri))
sys.frozen = True
sys.executable = r"C:\PageFlow\PageFlow.exe"
CMD = '"C:\\PageFlow\\PageFlow.exe" "%1"'


def val(path, name=""):
    return reg.keys[path.lower()][name]


winassoc.register()
check("progid command points at the exe", val(r"Software\Classes\PageFlow.PDF\shell\open\command")[1] == CMD)
check("progid has a name and icon", "PageFlow" in val(r"Software\Classes\PageFlow.PDF")[1]
      and val(r"Software\Classes\PageFlow.PDF\DefaultIcon")[1] == '"C:\\PageFlow\\PageFlow.exe",0')
check("listed under .pdf OpenWithProgids", val(r"Software\Classes\.pdf\OpenWithProgids", "PageFlow.PDF") == (0, b""))
check("Applications key for the exe", val(r"Software\Classes\Applications\PageFlow.exe\shell\open\command")[1] == CMD
      and val(r"Software\Classes\Applications\PageFlow.exe", "FriendlyAppName")[1] == "PageFlow"
      and ".pdf" in reg.keys[r"software\classes\applications\pageflow.exe\supportedtypes"])
check("Default apps capabilities", val(r"Software\PageFlow\Capabilities\FileAssociations", ".pdf")[1] == "PageFlow.PDF"
      and val(r"Software\RegisteredApplications", "PageFlow")[1] == r"Software\PageFlow\Capabilities"
      and val(r"Software\PageFlow\Capabilities", "ApplicationName")[1] == "PageFlow")
check("is_registered after register", winassoc.is_registered())
check("ensure_registered does nothing when up to date", winassoc.ensure_registered() is False)

sys.executable = r"D:\Tools\PageFlow.exe"
check("moving the exe makes is_registered False", not winassoc.is_registered())
check("ensure_registered fixes it", winassoc.ensure_registered() is True and winassoc.is_registered())
check("new location is stored", val(r"Software\Classes\PageFlow.PDF\shell\open\command")[1] == '"D:\\Tools\\PageFlow.exe" "%1"')

winassoc.unregister()
left = [k for k in reg.keys if "pageflow" in k]
check("unregister removes every PageFlow key", not left, str(left))
check("unregister removes the values", "PageFlow.PDF" not in reg.keys.get(r"software\classes\.pdf\openwithprogids", {})
      and "PageFlow" not in reg.keys.get(r"software\registeredapplications", {}))
winassoc.unregister()
check("unregister twice is harmless", True)

# the window's menu items and settings flag
winassoc.is_supported = lambda: True
winassoc.open_default_apps_settings = lambda: opened.append("settings page")
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
sys.executable = r"C:\PageFlow\PageFlow.exe"
win2 = pageflow.MainWindow()
help_items = []
for action in win2.menuBar().actions():
    if action.text().replace("&", "") == "Help":
        help_items = [a.text().replace("&", "") for a in action.menu().actions()]
check("Help menu offers the Windows items", "Set as default PDF reader..." in help_items
      and "Remove PDF file association entries" in help_items
      and "PageFlow on GitHub" in help_items, str(help_items))
urls = []
pageflow.QDesktopServices = types.SimpleNamespace(openUrl=lambda u: urls.append(u.toString()))
win2.act_github.trigger()
check("Help > PageFlow on GitHub opens the repository", urls == ["https://github.com/labgreeks-ai/PageFlow"], str(urls))
win2.set_as_default()
check("'Set as default' registers and opens Settings", winassoc.is_registered() and "settings page" in opened)
win2.remove_association()
check("'Remove' unregisters and remembers the choice", not winassoc.is_registered()
      and win2.settings.value("assoc_opt_out", False, type=bool) is True)
pageflow.auto_register()
check("auto-register respects the opt-out", not winassoc.is_registered())
win2.settings.setValue("assoc_opt_out", False)
pageflow.auto_register()
check("auto-register registers the exe on startup", winassoc.is_registered())
winassoc.unregister()
sys.argv = ["pageflow.py", "--register"]
pageflow.main()
check("--register switch", winassoc.is_registered())
sys.argv = ["pageflow.py", "--unregister"]
pageflow.main()
check("--unregister switch", not winassoc.is_registered())

# running from source (not frozen) uses python + script
del sys.frozen
winassoc.os = real_os
sys.executable = real_exe
cmd, icon, exe_name = winassoc._target("/some/where/pageflow.py")
check("source mode launches python with the script", cmd.endswith('/some/where/pageflow.py" "%1"') and exe_name is None, cmd)

win.close()
win2.close()
pump(100)
print("ALL OK" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
