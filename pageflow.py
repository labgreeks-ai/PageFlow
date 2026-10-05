#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Amman (https://github.com/labgreeks-ai)
"""PageFlow - a lightweight PDF reader built with PyMuPDF and PySide6.

Created by Amman - https://github.com/labgreeks-ai

Run:  python pageflow.py [file.pdf]
"""
import bisect
import hashlib
import math
import os
import sys
import time

import pymupdf
from PySide6.QtCore import QCoreApplication, QRectF, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

import winassoc  # Windows "Open with" / default app registration

APP_NAME = "PageFlow"
APP_VERSION = "1.1.0"
AUTHOR = "Amman"
GITHUB_USER = "labgreeks-ai"
AUTHOR_URL = f"https://github.com/{GITHUB_USER}"
REPO_URL = f"{AUTHOR_URL}/PageFlow"          # change this if your repository has another name
LICENSE_NAME = "GNU AGPL-3.0"
MARGIN = 14          # space around the page column, in pixels
PAGE_GAP = 12        # space between pages, in pixels
MIN_ZOOM, MAX_ZOOM = 0.1, 6.0
ZOOM_STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]
HIGHLIGHT = QColor(255, 220, 0, 120)
CURRENT_HIGHLIGHT = QColor(255, 120, 0, 170)
SELECTION_COLOR = QColor(40, 110, 255, 90)
END = 10 ** 9        # 'end of page' marker used by select-all


def resource_path(name):
    """Find a bundled file, both when running from source and from a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def clamp(value, low, high):
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# Text of one page, for selecting and copying
# --------------------------------------------------------------------------
class PageText:
    """The characters of one page in reading order, with their boxes.

    Boxes are in PDF units on the page's own (unrotated) coordinate system.
    """

    __slots__ = ("chars", "lines", "starts")

    def __init__(self, chars, lines):
        self.chars = chars      # [(x0, y0, x1, y1, text), ...]
        self.lines = lines      # [(first char index, last char index, (x0, y0, x1, y1)), ...]
        self.starts = [ln[0] for ln in lines]

    def line_of(self, index):
        return max(0, bisect.bisect_right(self.starts, index) - 1)


def extract_page_text(page):
    chars, lines = [], []
    try:
        flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
        data = page.get_text("rawdict", flags=flags)
    except Exception:
        return PageText([], [])
    for block in data.get("blocks", []):
        if block.get("type") != 0:      # 0 = text, 1 = image
            continue
        for line in block.get("lines", []):
            first = len(chars)
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    x0, y0, x1, y1 = ch["bbox"]
                    chars.append((x0, y0, x1, y1, ch["c"]))
            if len(chars) > first:
                lines.append((first, len(chars) - 1, tuple(line["bbox"])))
    return PageText(chars, lines)


# --------------------------------------------------------------------------
# One page of the document
# --------------------------------------------------------------------------
class PageWidget(QWidget):
    def __init__(self, index, view, parent=None):
        super().__init__(parent)
        self.index = index
        self.view = view
        self.pixmap = None
        self.ibeam = False
        self.setMouseTracking(True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        if self.pixmap is not None:
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.fillRect(self.rect(), QColor("#2b2b2b") if self.view.dark else QColor("white"))
            painter.setPen(QColor("#888888"))
            painter.drawText(self.rect(), Qt.AlignCenter, str(self.index + 1))
        rects = self.view.selection_rects(self.index)
        if rects:
            painter.setPen(Qt.NoPen)
            painter.setBrush(SELECTION_COLOR)
            for rect in rects:
                painter.drawRect(rect)

    def set_text_cursor(self, on):
        if on != self.ibeam:
            self.ibeam = on
            self.setCursor(Qt.IBeamCursor if on else Qt.ArrowCursor)

    def mousePressEvent(self, event):
        self.view.page_mouse_press(self, event)

    def mouseMoveEvent(self, event):
        self.view.page_mouse_move(self, event)

    def mouseReleaseEvent(self, event):
        self.view.page_mouse_release(self, event)

    def mouseDoubleClickEvent(self, event):
        self.view.page_double_click(self, event)

    def contextMenuEvent(self, event):
        self.view.contextRequested.emit(event.globalPos())
        event.accept()


# --------------------------------------------------------------------------
# Continuous-scroll page view
# --------------------------------------------------------------------------
class PdfView(QScrollArea):
    pageChanged = Signal(int)          # 0-based page index
    zoomChanged = Signal(float)        # current scale (1.0 = 100%)
    rotateRequested = Signal()
    darkToggleRequested = Signal()
    selectionChanged = Signal(bool)    # True when some text is selected
    message = Signal(str)              # short status messages
    contextRequested = Signal(object)  # global QPoint of a right-click

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(False)

        self.container = QWidget()
        self.container.setAutoFillBackground(True)
        self.setWidget(self.container)

        self.hint = QLabel(
            f"<div style='font-size:34px; color:#f0f1f3;'><b>{APP_NAME}</b></div>"
            f"<div style='font-size:14px; color:#c9ccd1;'>Created by {AUTHOR} &middot; github.com/{GITHUB_USER}</div>"
            "<br><div style='font-size:16px;'>Drag a PDF here, or press Ctrl+O to open one</div>",
            self.container,
        )
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: #aaaaaa;")

        self.doc = None
        self.widgets = []
        self.sizes = []        # page sizes in points at the document's own rotation
        self.base_rot = []     # each page's original rotation
        self.tops = []         # y of each page widget inside the container
        self.heights = []
        self.scale = 1.0
        self.zoom_mode = "fit-width"   # "fit-width", "fit-page" or "custom"
        self._custom_scale = 1.0
        self.view_rot = 0
        self.dark = False
        self.highlights = {}           # page index -> [pymupdf.Rect, ...]
        self.current_match = None      # (page index, index within that page)
        self._rendered = set()
        self._last_page = -1

        # text selection
        self._text = {}                 # page index -> PageText
        self._rotm = {}                 # (page index, view rotation) -> Matrix
        self.sel_anchor = None          # (page index, caret) - caret = gap between characters
        self.sel_head = None
        self._press = None              # (page index, caret, global QPoint) while the button is down
        self._dragging = False
        self._last_global = None
        self._multi_click_time = 0.0
        self._multi_click_pos = None
        self._warned_pages = set()

        self.autoscroll_timer = QTimer(self)
        self.autoscroll_timer.setInterval(40)
        self.autoscroll_timer.timeout.connect(self._autoscroll_step)

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(15)
        self.render_timer.timeout.connect(self._render_visible)

        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(50)
        self.resize_timer.timeout.connect(self.relayout)

        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_render)

        self._apply_background()
        self.relayout()

    # ----- document -----
    def set_document(self, doc, page=0):
        self.clear()
        self.doc = doc
        for i in range(doc.page_count):
            p = doc.load_page(i)
            self.base_rot.append(p.rotation)
            self.sizes.append((p.rect.width, p.rect.height))
            w = PageWidget(i, self, self.container)
            self.widgets.append(w)
        self.view_rot = 0
        self.relayout(anchor=(0, 0.0))
        self.go_to_page(clamp(page, 0, doc.page_count - 1))

    def clear(self):
        for w in self.widgets:
            w.setParent(None)
            w.deleteLater()
        self.widgets, self.sizes, self.base_rot = [], [], []
        self.tops, self.heights = [], []
        self.highlights, self.current_match = {}, None
        self._rendered.clear()
        self._last_page = -1
        self.doc = None
        self._text.clear()
        self._rotm.clear()
        self._warned_pages.clear()
        self.sel_anchor = self.sel_head = None
        self._press = None
        self.autoscroll_timer.stop()
        self.selectionChanged.emit(False)
        self.relayout(anchor=(0, 0.0))

    # ----- geometry -----
    def _display_size(self, i):
        w, h = self.sizes[i]
        if (self.view_rot // 90) % 2:
            w, h = h, w
        return w, h

    def _compute_scale(self):
        if not self.doc:
            return 1.0
        if self.zoom_mode == "custom":
            return self._custom_scale
        vp = self.viewport()
        avail_w = max(50, vp.width() - 2 * MARGIN)
        if self.zoom_mode == "fit-width":
            widest = max(self._display_size(i)[0] for i in range(len(self.sizes)))
            return clamp(avail_w / widest, MIN_ZOOM, MAX_ZOOM)
        # fit-page: fit the page that is currently shown
        cur = clamp(self.current_page(), 0, len(self.sizes) - 1)
        w, h = self._display_size(cur)
        avail_h = max(50, vp.height() - 2 * MARGIN)
        return clamp(min(avail_w / w, avail_h / h), MIN_ZOOM, MAX_ZOOM)

    def _anchor(self):
        if not self.tops:
            return (0, 0.0)
        y = self.verticalScrollBar().value()
        i = max(0, bisect.bisect_right(self.tops, y) - 1)
        return (i, (y - self.tops[i]) / max(1, self.heights[i]))

    def relayout(self, anchor=None):
        if anchor is None:
            anchor = self._anchor()
        vp = self.viewport()
        if not self.doc:
            self.container.resize(vp.size())
            self.hint.setGeometry(0, 0, vp.width(), vp.height())
            self.hint.show()
            self.tops, self.heights = [], []
            return
        self.hint.hide()

        self.scale = self._compute_scale()
        s = self.scale
        dims = [(max(1, round(self._display_size(i)[0] * s)), max(1, round(self._display_size(i)[1] * s)))
                for i in range(len(self.widgets))]
        cw = max(vp.width(), max(w for w, _ in dims) + 2 * MARGIN)

        y = MARGIN
        self.tops, self.heights = [], []
        for widget, (wpx, hpx) in zip(self.widgets, dims):
            widget.setGeometry((cw - wpx) // 2, y, wpx, hpx)
            widget.pixmap = None
            widget.show()
            self.tops.append(y)
            self.heights.append(hpx)
            y += hpx + PAGE_GAP
        self._rendered.clear()
        self.container.resize(cw, max(y - PAGE_GAP + MARGIN, vp.height()))

        i, frac = anchor
        i = clamp(i, 0, len(self.tops) - 1)
        self.verticalScrollBar().setValue(int(self.tops[i] + frac * self.heights[i]))
        self.zoomChanged.emit(self.scale)
        self._schedule_render()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_timer.start()

    # ----- pages / navigation -----
    def page_count(self):
        return len(self.widgets)

    def _page_at(self, y):
        return clamp(bisect.bisect_right(self.tops, y) - 1, 0, max(0, len(self.tops) - 1))

    def current_page(self):
        if not self.tops:
            return 0
        bar = self.verticalScrollBar()
        if bar.maximum() > 0 and bar.value() >= bar.maximum():
            return len(self.tops) - 1
        return self._page_at(bar.value() + int(self.viewport().height() * 0.35))

    def go_to_page(self, i):
        if not self.tops:
            return
        i = clamp(i, 0, len(self.tops) - 1)
        self.verticalScrollBar().setValue(max(0, self.tops[i] - PAGE_GAP // 2))
        self._emit_page_if_changed()

    def next_page(self):
        self.go_to_page(self.current_page() + 1)

    def prev_page(self):
        self.go_to_page(self.current_page() - 1)

    def _emit_page_if_changed(self):
        page = self.current_page()
        if page != self._last_page:
            self._last_page = page
            self.pageChanged.emit(page)

    def _on_scroll(self, _value):
        self._emit_page_if_changed()
        self._schedule_render()

    # ----- zoom / rotation / theme -----
    def set_zoom_mode(self, mode):
        self.zoom_mode = mode
        self.relayout()

    def set_scale(self, scale):
        self._custom_scale = clamp(scale, MIN_ZOOM, MAX_ZOOM)
        self.zoom_mode = "custom"
        self.relayout()

    def zoom_in(self):
        for step in ZOOM_STEPS:
            if step > self.scale * 1.01:
                return self.set_scale(step)
        self.set_scale(MAX_ZOOM)

    def zoom_out(self):
        for step in reversed(ZOOM_STEPS):
            if step < self.scale / 1.01:
                return self.set_scale(step)
        self.set_scale(MIN_ZOOM)

    def rotate(self):
        self.view_rot = (self.view_rot + 90) % 360
        self.relayout()

    def set_dark(self, dark):
        self.dark = dark
        self._apply_background()
        for w in self.widgets:
            w.pixmap = None
            w.update()
        self._rendered.clear()
        self._schedule_render()

    def _apply_background(self):
        pal = self.container.palette()
        pal.setColor(QPalette.Window, QColor("#1b1c1f") if self.dark else QColor("#5a5d63"))
        self.container.setPalette(pal)

    # ----- highlights (search results) -----
    def set_highlights(self, highlights, current=None):
        self.highlights = highlights
        self.current_match = current
        for w in self.widgets:
            w.pixmap = None
        self._rendered.clear()
        self._schedule_render()

    def _view_rect(self, pno, rect):
        page = self.doc.load_page(pno)
        page.set_rotation((self.base_rot[pno] + self.view_rot) % 360)
        return (rect * page.rotation_matrix) * pymupdf.Matrix(self.scale, self.scale)

    def scroll_to_match(self, pno, rect):
        if not self.doc:
            return
        r = self._view_rect(pno, rect)
        widget = self.widgets[pno]
        y = self.tops[pno] + r.y0
        x = widget.x() + r.x0
        vbar, hbar = self.verticalScrollBar(), self.horizontalScrollBar()
        vph = self.viewport().height()
        if not (vbar.value() + vph * 0.1 <= y <= vbar.value() + vph * 0.85):
            vbar.setValue(int(y - vph / 2 + r.height / 2))
        vpw = self.viewport().width()
        if not (hbar.value() <= x <= hbar.value() + vpw - 40):
            hbar.setValue(int(x - vpw / 2))

    # ----- rendering -----
    def _schedule_render(self, *_):
        self.render_timer.start()

    def _render_visible(self):
        if not self.doc or not self.tops:
            return
        top = self.verticalScrollBar().value()
        h = self.viewport().height()
        lo = self._page_at(top - h // 2)
        hi = self._page_at(top + h + h // 2)
        for i in range(lo, hi + 1):
            if self.widgets[i].pixmap is None:
                self._render_page(i)
        keep_lo, keep_hi = lo - 4, hi + 4
        for i in list(self._rendered):
            if i < keep_lo or i > keep_hi:
                self.widgets[i].pixmap = None
                self.widgets[i].update()
                self._rendered.discard(i)

    def render_image(self, pno, scale, dpr=1.0, with_highlights=True):
        """Render one page to a QImage using the current rotation / theme."""
        page = self.doc.load_page(pno)
        page.set_rotation((self.base_rot[pno] + self.view_rot) % 360)
        mat = pymupdf.Matrix(scale * dpr, scale * dpr)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        if self.dark:
            img.invertPixels()
        rects = self.highlights.get(pno) if with_highlights else None
        if rects:
            to_pixels = page.rotation_matrix * mat
            painter = QPainter(img)
            for idx, rect in enumerate(rects):
                r = rect * to_pixels
                is_current = self.current_match == (pno, idx)
                painter.fillRect(r.x0, r.y0, r.width, r.height, CURRENT_HIGHLIGHT if is_current else HIGHLIGHT)
            painter.end()
        return img

    def _render_page(self, i):
        dpr = self.devicePixelRatioF()
        try:
            img = self.render_image(i, self.scale, dpr)
        except Exception as exc:  # a broken page should not take the app down
            print(f"Could not render page {i + 1}: {exc}", file=sys.stderr)
            return
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self.widgets[i].pixmap = pm
        self.widgets[i].update()
        self._rendered.add(i)

    # ----- text selection -----
    def _page_text(self, pno):
        text = self._text.get(pno)
        if text is None:
            text = extract_page_text(self.doc.load_page(pno))
            if len(self._text) >= 120:
                self._text.pop(next(iter(self._text)))
            self._text[pno] = text
        return text

    def _rot_matrix(self, pno):
        key = (pno, self.view_rot)
        matrix = self._rotm.get(key)
        if matrix is None:
            page = self.doc.load_page(pno)
            page.set_rotation((self.base_rot[pno] + self.view_rot) % 360)
            matrix = pymupdf.Matrix(page.rotation_matrix)
            self._rotm[key] = matrix
        return matrix

    def _locate(self, cpos):
        """Container position -> (page index, x, y inside that page's widget)."""
        pno = self._page_at(cpos.y())
        widget = self.widgets[pno]
        return pno, cpos.x() - widget.x(), cpos.y() - widget.y()

    def _hit(self, pno, x, y):
        """Nearest character to a pixel position on page `pno`.

        Returns (caret, character index, distance in pixels), or None if the page has no text.
        """
        text = self._page_text(pno)
        if not text.lines:
            return None
        point = pymupdf.Point(x / self.scale, y / self.scale) * ~self._rot_matrix(pno)
        px, py = point.x, point.y

        best_line, best_d = None, None
        for line in text.lines:
            bx0, by0, bx1, by1 = line[2]
            dx = max(bx0 - px, 0.0, px - bx1)
            dy = max(by0 - py, 0.0, py - by1)
            d = dx * dx + 4.0 * dy * dy        # lines above/below count more than sideways
            if best_d is None or d < best_d:
                best_line, best_d = line, d
                if d == 0.0:
                    break

        first, last, _box = best_line
        best_i, best_dx = first, None
        for i in range(first, last + 1):
            c = text.chars[i]
            dx = max(c[0] - px, 0.0, px - c[2])
            if best_dx is None or dx < best_dx:
                best_i, best_dx = i, dx
                if dx == 0.0:
                    break
        c = text.chars[best_i]
        caret = best_i + (1 if px > (c[0] + c[2]) / 2 else 0)
        return caret, best_i, math.sqrt(best_d) * self.scale

    def _sel_range(self):
        if self.sel_anchor is None or self.sel_head is None:
            return None
        a, b = sorted((self.sel_anchor, self.sel_head))
        return a, b

    def has_selection(self):
        r = self._sel_range()
        return bool(r) and r[0] != r[1]

    def _set_selection(self, anchor, head):
        if (anchor, head) == (self.sel_anchor, self.sel_head):
            return
        self.sel_anchor, self.sel_head = anchor, head
        self._repaint_visible_pages()
        self.selectionChanged.emit(self.has_selection())

    def clear_selection(self):
        if self.sel_anchor is not None:
            self._set_selection(None, None)

    def select_all(self):
        if self.doc:
            self._set_selection((0, 0), (self.page_count() - 1, END))

    def _repaint_visible_pages(self):
        if not self.tops:
            return
        top = self.verticalScrollBar().value()
        for i in range(self._page_at(top), self._page_at(top + self.viewport().height()) + 1):
            self.widgets[i].update()

    def selection_rects(self, pno):
        """Highlight rectangles for the selected text on page `pno`, in that page widget's pixels."""
        r = self._sel_range()
        if not r or r[0] == r[1] or not self.doc:
            return []
        (p0, c0), (p1, c1) = r
        if pno < p0 or pno > p1:
            return []
        text = self._page_text(pno)
        n = len(text.chars)
        start = c0 if pno == p0 else 0
        end = min(c1, n) if pno == p1 else n          # end is exclusive
        if start >= end:
            return []
        matrix = self._rot_matrix(pno) * pymupdf.Matrix(self.scale, self.scale)
        out = []
        for first, last, _box in text.lines[text.line_of(start): text.line_of(end - 1) + 1]:
            a, b = max(first, start), min(last, end - 1)
            if a > b:
                continue
            chars = text.chars[a:b + 1]
            box = pymupdf.Rect(min(c[0] for c in chars), min(c[1] for c in chars),
                               max(c[2] for c in chars), max(c[3] for c in chars)) * matrix
            out.append(QRectF(box.x0, box.y0, box.width, box.height))
        return out

    @staticmethod
    def _join(text, start, end):
        if start >= end:
            return ""
        rows = []
        for first, last, _box in text.lines[text.line_of(start): text.line_of(end - 1) + 1]:
            a, b = max(first, start), min(last, end - 1)
            if a <= b:
                row = "".join(text.chars[i][4] for i in range(a, b + 1)).rstrip()
                if row:
                    rows.append(row)
        return "\n".join(rows)

    def selected_text(self):
        r = self._sel_range()
        if not r or r[0] == r[1] or not self.doc:
            return ""
        (p0, c0), (p1, c1) = r
        parts = []
        for pno in range(p0, p1 + 1):
            if (pno > p0 or c0 == 0) and (pno < p1 or c1 >= END):
                text = self.doc.load_page(pno).get_text("text").strip("\n")      # whole page: fast path
            else:
                page_text = self._page_text(pno)
                n = len(page_text.chars)
                text = self._join(page_text, c0 if pno == p0 else 0, min(c1, n) if pno == p1 else n)
            if text.strip():
                parts.append(text)
        return "\n".join(parts)

    def selected_preview(self, limit=200):
        r = self._sel_range()
        if not r or r[0] == r[1] or r[1][0] - r[0][0] > 1:
            return ""
        return self.selected_text()[:limit]

    def _select_unit(self, pno, x, y, unit):
        hit = self._hit(pno, x, y)
        if hit is None or hit[2] > 20:
            return
        text = self._page_text(pno)
        i = hit[1]
        first, last, _box = text.lines[text.line_of(i)]
        if unit == "line":
            a, b = first, last
        else:
            blank = text.chars[i][4].isspace()
            a = b = i
            while a > first and text.chars[a - 1][4].isspace() == blank:
                a -= 1
            while b < last and text.chars[b + 1][4].isspace() == blank:
                b += 1
        self._set_selection((pno, a), (pno, b + 1))

    def _update_drag(self, gpos):
        if self._press is None:
            return
        pno, x, y = self._locate(self.container.mapFromGlobal(gpos))
        hit = self._hit(pno, x, y)
        if hit is not None:
            self._set_selection((self._press[0], self._press[1]), (pno, hit[0]))

    def _autoscroll_step(self):
        if self._press is None or self._last_global is None or not self._dragging:
            self.autoscroll_timer.stop()
            return
        vp = self.viewport()
        p = vp.mapFromGlobal(self._last_global)
        dy = p.y() if p.y() < 0 else (p.y() - vp.height() if p.y() > vp.height() else 0)
        dx = p.x() if p.x() < 0 else (p.x() - vp.width() if p.x() > vp.width() else 0)
        if not dx and not dy:
            return

        def step(d):
            return int(clamp(d / 3, -60, 60)) or (1 if d > 0 else -1)

        if dy:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + step(dy))
        if dx:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + step(dx))
        self._update_drag(self._last_global)

    # mouse events arrive from the page widgets
    def page_mouse_press(self, widget, event):
        if event.button() != Qt.LeftButton or not self.doc:
            event.ignore()
            return
        self.setFocus()
        gpos = event.globalPosition().toPoint()
        pno, x, y = self._locate(self.container.mapFromGlobal(gpos))
        now = time.monotonic()
        # third click in a row: select the whole line
        if (self._multi_click_pos is not None
                and now - self._multi_click_time < QApplication.doubleClickInterval() / 1000.0
                and (gpos - self._multi_click_pos).manhattanLength() < 6):
            self._multi_click_pos = None
            self._press = None
            self._select_unit(pno, x, y, "line")
            return
        self._press = None
        self.clear_selection()
        hit = self._hit(pno, x, y)
        if hit is None:
            if pno not in self._warned_pages:
                self._warned_pages.add(pno)
                self.message.emit("There is no selectable text on this page (it may be a scanned image).")
            return
        self._press = (pno, hit[0], gpos)
        self._dragging = False

    def page_mouse_move(self, widget, event):
        if not self.doc:
            return
        gpos = event.globalPosition().toPoint()
        if event.buttons() & Qt.LeftButton and self._press is not None:
            self._last_global = gpos
            if not self._dragging and (gpos - self._press[2]).manhattanLength() < 4:
                return
            self._dragging = True
            self._update_drag(gpos)
            if not self.autoscroll_timer.isActive():
                self.autoscroll_timer.start()
            return
        pno, x, y = self._locate(self.container.mapFromGlobal(gpos))
        hit = self._hit(pno, x, y)
        widget.set_text_cursor(hit is not None and hit[2] <= 2.0)

    def page_mouse_release(self, widget, event):
        if event.button() == Qt.LeftButton:
            self._press = None
            self._dragging = False
            self.autoscroll_timer.stop()

    def page_double_click(self, widget, event):
        if event.button() != Qt.LeftButton or not self.doc:
            return
        gpos = event.globalPosition().toPoint()
        pno, x, y = self._locate(self.container.mapFromGlobal(gpos))
        self._press = None
        self._select_unit(pno, x, y, "word")
        self._multi_click_time = time.monotonic()
        self._multi_click_pos = gpos

    # ----- input -----
    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and self.doc:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            elif event.angleDelta().y() < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        key, mods = event.key(), event.modifiers()
        if key == Qt.Key_Escape and self.has_selection():
            return self.clear_selection()
        if self.doc and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            has_hscroll = self.horizontalScrollBar().maximum() > 0
            if key == Qt.Key_Right and not has_hscroll:
                return self.next_page()
            if key == Qt.Key_Left and not has_hscroll:
                return self.prev_page()
            if key == Qt.Key_Home:
                return self.go_to_page(0)
            if key == Qt.Key_End:
                return self.go_to_page(self.page_count() - 1)
            if key == Qt.Key_R:
                return self.rotateRequested.emit()
            if key == Qt.Key_D:
                return self.darkToggleRequested.emit()
        super().keyPressEvent(event)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    THUMB_W = 120

    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.doc = None
        self.path = None

        self.matches = []        # (page index, rect, index within page)
        self.match_index = -1
        self.search_gen = 0
        self.search_text = ""
        self.search_page = 0
        self.thumb_gen = 0

        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)
        self.setAcceptDrops(True)

        self.view = PdfView()
        self.view.pageChanged.connect(self.on_page_changed)
        self.view.zoomChanged.connect(self.on_zoom_changed)
        self.view.rotateRequested.connect(self.rotate)
        self.view.darkToggleRequested.connect(lambda: self.act_dark.toggle())
        self.view.selectionChanged.connect(lambda has: self.act_copy.setEnabled(has))
        self.view.message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.view.contextRequested.connect(self.show_context_menu)

        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListView.IconMode)
        self.thumbs.setFlow(QListView.TopToBottom)
        self.thumbs.setWrapping(False)
        self.thumbs.setMovement(QListView.Static)
        self.thumbs.setResizeMode(QListView.Adjust)
        self.thumbs.setIconSize(QSize(self.THUMB_W, int(self.THUMB_W * 1.45)))
        self.thumbs.setSpacing(8)
        self.thumbs.setUniformItemSizes(False)
        self.thumbs.setStyleSheet(
            "QListWidget { background: #4a4d52; }"
            "QListWidget::item { color: #e3e5e8; padding: 4px; }"
            "QListWidget::item:selected { background: #4f8cff; color: white; }"
        )
        self.thumbs.currentRowChanged.connect(self.on_thumb_selected)

        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.itemClicked.connect(self.on_outline_clicked)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.thumbs, "Pages")
        self.tabs.addTab(self.outline, "Outline")

        self.splitter = QSplitter()
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.view)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 980])
        self.setCentralWidget(self.splitter)

        self.status_file = QLabel("")
        self.status_zoom = QLabel("")
        self.statusBar().addWidget(self.status_file, 1)
        self.statusBar().addPermanentWidget(self.status_zoom)

        self.build_actions()
        self.build_menus()
        self.build_toolbar()
        self.set_doc_controls_enabled(False)

        dark = self.settings.value("dark_pages", False, type=bool)
        self.act_dark.setChecked(dark)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.act_sidebar.setChecked(self.settings.value("sidebar", True, type=bool))
        self.tabs.setVisible(self.act_sidebar.isChecked())

    # ----- actions, menus, toolbar -----
    def build_actions(self):
        def act(text, slot, shortcut=None, checkable=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setCheckable(checkable)
            if checkable:
                a.toggled.connect(slot)
            else:
                a.triggered.connect(lambda _checked=False: slot())
            return a

        self.act_open = act("&Open...", self.choose_file, QKeySequence.Open)
        self.act_save_copy = act("Save a &copy (with current rotation)...", self.save_copy, "Ctrl+Shift+S")
        self.act_quit = act("&Quit", self.close, QKeySequence.Quit)

        self.act_zoom_in = act("Zoom &in", self.view.zoom_in, "Ctrl++")
        self.act_zoom_in2 = act("Zoom in", self.view.zoom_in, "Ctrl+=")
        self.act_zoom_out = act("Zoom &out", self.view.zoom_out, "Ctrl+-")
        self.act_fit_width = act("Fit &width", lambda: self.view.set_zoom_mode("fit-width"), "Ctrl+0")
        self.act_fit_page = act("Fit &page", lambda: self.view.set_zoom_mode("fit-page"), "Ctrl+9")
        self.act_actual = act("&Actual size", lambda: self.view.set_scale(1.0), "Ctrl+1")
        # R and D are handled by the page view; the tab character only shows the hint in the menu.
        self.act_rotate = act("&Rotate clockwise\tR", self.rotate)
        self.act_dark = act("&Dark pages\tD", self.set_dark, checkable=True)
        self.act_sidebar = act("&Sidebar", self.toggle_sidebar, "F4", checkable=True)
        for a in (self.act_zoom_in2,):
            self.addAction(a)

        self.act_find = act("&Find...", self.focus_search, QKeySequence.Find)
        self.act_find_next = act("Find &next", lambda: self.step_match(1), "F3")
        self.act_find_prev = act("Find &previous", lambda: self.step_match(-1), "Shift+F3")
        self.act_goto = act("&Go to page...", self.goto_dialog, "Ctrl+G")
        self.act_next_page = act("&Next page", lambda: self.view.next_page(), "Ctrl+Right")
        self.act_prev_page = act("&Previous page", lambda: self.view.prev_page(), "Ctrl+Left")
        self.act_first = act("&First page", lambda: self.view.go_to_page(0), "Ctrl+Home")
        self.act_last = act("&Last page", lambda: self.view.go_to_page(self.view.page_count() - 1), "Ctrl+End")

        self.act_copy_text = act("&Copy text of current page", self.copy_page_text, "Ctrl+Shift+C")
        self.act_save_image = act("Save current page as &image...", self.save_page_image, "Ctrl+Shift+I")
        self.act_about = act(f"&About {APP_NAME}", self.show_about)
        self.act_github = act(f"{APP_NAME} on &GitHub", lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        self.act_copy = act("&Copy", self.copy_selection, QKeySequence.Copy)
        self.act_copy.setEnabled(False)
        self.act_select_all = act("Select &all", self.view.select_all, QKeySequence.SelectAll)
        self.act_default_app = act("Set as &default PDF reader...", self.set_as_default)
        self.act_remove_assoc = act("&Remove PDF file association entries", self.remove_association)

        self.doc_actions = [
            self.act_save_copy, self.act_zoom_in, self.act_zoom_in2, self.act_zoom_out, self.act_fit_width,
            self.act_fit_page, self.act_actual, self.act_rotate, self.act_find, self.act_find_next,
            self.act_find_prev, self.act_goto, self.act_next_page, self.act_prev_page, self.act_first,
            self.act_last, self.act_copy_text, self.act_save_image, self.act_select_all,
        ]

    def build_menus(self):
        bar = self.menuBar()
        m = bar.addMenu("&File")
        m.addAction(self.act_open)
        self.recent_menu = m.addMenu("Open &recent")
        m.addAction(self.act_save_copy)
        m.addSeparator()
        m.addAction(self.act_quit)

        m = bar.addMenu("&Edit")
        m.addAction(self.act_copy)
        m.addAction(self.act_select_all)

        m = bar.addMenu("&View")
        for a in (self.act_zoom_in, self.act_zoom_out, self.act_fit_width, self.act_fit_page, self.act_actual):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_rotate)
        m.addAction(self.act_dark)
        m.addAction(self.act_sidebar)

        m = bar.addMenu("&Go")
        for a in (self.act_find, self.act_find_next, self.act_find_prev):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_goto, self.act_next_page, self.act_prev_page, self.act_first, self.act_last):
            m.addAction(a)

        m = bar.addMenu("&Tools")
        m.addAction(self.act_copy_text)
        m.addAction(self.act_save_image)

        m = bar.addMenu("&Help")
        if winassoc.is_supported():
            m.addAction(self.act_default_app)
            m.addAction(self.act_remove_assoc)
            m.addSeparator()
        m.addAction(self.act_github)
        m.addAction(self.act_about)
        self.rebuild_recent_menu()

    def build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        self.btn_sidebar = tb.addAction("☰", self.act_sidebar.toggle)
        self.btn_sidebar.setToolTip("Toggle sidebar (F4)")
        self.btn_open = tb.addAction("Open", self.choose_file)
        self.btn_open.setToolTip("Open PDF (Ctrl+O)")
        tb.addSeparator()

        self.page_spin = QSpinBox()
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setFixedWidth(70)
        self.page_spin.setToolTip("Go to page")
        self.page_spin.valueChanged.connect(lambda v: self.view.go_to_page(v - 1))
        self.page_total = QLabel(" / 0 ")
        self.doc_toolbar_actions = []

        def tool(text, slot, tip):
            a = tb.addAction(text, slot)
            a.setToolTip(tip)
            self.doc_toolbar_actions.append(a)
            return a

        tool("▲", self.view.prev_page, "Previous page")
        tb.addWidget(self.page_spin)
        tb.addWidget(self.page_total)
        tool("▼", self.view.next_page, "Next page")
        tb.addSeparator()

        tool("−", self.view.zoom_out, "Zoom out (Ctrl+-)")
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setFixedWidth(110)
        self.zoom_box.addItems(["Fit width", "Fit page"] + [f"{int(z * 100)}%" for z in (0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4)])
        self.zoom_box.activated.connect(lambda _i: self.apply_zoom_text(self.zoom_box.currentText()))
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self.apply_zoom_text(self.zoom_box.currentText()))
        tb.addWidget(self.zoom_box)
        tool("+", self.view.zoom_in, "Zoom in (Ctrl++)")
        tb.addSeparator()

        tool("↻", self.rotate, "Rotate clockwise (R)")
        self.btn_dark = tb.addAction("Dark pages", self.act_dark.toggle)
        self.btn_dark.setToolTip("Dark page colors (D)")
        self.btn_dark.setCheckable(True)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search (Ctrl+F)")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(220)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(lambda: self.start_search(self.search_box.text()))
        self.search_box.textChanged.connect(lambda _t: self.search_timer.start())
        self.search_box.returnPressed.connect(self.on_search_enter)
        tb.addWidget(self.search_box)
        tool("‹", lambda: self.step_match(-1), "Previous match (Shift+F3)")
        tool("›", lambda: self.step_match(1), "Next match (F3)")
        self.search_status = QLabel("")
        self.search_status.setMinimumWidth(90)
        tb.addWidget(self.search_status)

        self.doc_widgets = [self.page_spin, self.zoom_box, self.search_box]
        self.toolbar = tb

    def set_doc_controls_enabled(self, enabled):
        for a in self.doc_actions:
            a.setEnabled(enabled)
        for w in self.doc_widgets:
            w.setEnabled(enabled)
        for a in self.doc_toolbar_actions:
            a.setEnabled(enabled)

    # ----- opening documents -----
    def choose_file(self):
        start = os.path.dirname(self.path) if self.path else self.settings.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF documents (*.pdf);;All files (*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        path = os.path.abspath(path)
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not open this file:\n{path}\n\n{exc}")
            return
        if doc.needs_pass:
            for _attempt in range(3):
                pw, ok = QInputDialog.getText(
                    self, "Password required",
                    f"{os.path.basename(path)} is password protected.\nEnter the password:",
                    QLineEdit.Password,
                )
                if not ok:
                    doc.close()
                    return
                if doc.authenticate(pw):
                    break
                QMessageBox.warning(self, APP_NAME, "That password is not correct.")
            else:
                doc.close()
                return
        if doc.page_count == 0:
            doc.close()
            QMessageBox.warning(self, APP_NAME, "This PDF has no pages.")
            return

        # Reset search and release the previous document.
        self.search_gen += 1
        self.thumb_gen += 1
        self.matches, self.match_index = [], -1
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.search_status.setText("")
        old = self.doc
        self.doc = doc
        self.path = path

        last_page = self.settings.value(self.pos_key(path), 0, type=int)
        self.view.set_document(doc, last_page)
        if old is not None:
            old.close()

        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(doc.page_count)
        self.page_spin.setValue(self.view.current_page() + 1)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {doc.page_count} ")
        self.set_doc_controls_enabled(True)
        self.setWindowTitle(f"{os.path.basename(path)} - {APP_NAME}")
        self.status_file.setText(path)
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.add_recent(path)
        self.build_outline()
        self.build_thumbnails()
        self.view.setFocus()

    @staticmethod
    def pos_key(path):
        return "pos/" + hashlib.md5(path.encode("utf-8")).hexdigest()

    def add_recent(self, path):
        recent = [p for p in self.settings.value("recent", [], type=list) if p != path]
        recent.insert(0, path)
        self.settings.setValue("recent", recent[:10])
        self.rebuild_recent_menu()

    def rebuild_recent_menu(self):
        self.recent_menu.clear()
        recent = [p for p in self.settings.value("recent", [], type=list) if os.path.exists(p)]
        if not recent:
            self.recent_menu.addAction("(none)").setEnabled(False)
            return
        for p in recent:
            self.recent_menu.addAction(p, lambda _c=False, p=p: self.open_path(p))

    # ----- sidebar -----
    def toggle_sidebar(self, visible):
        self.tabs.setVisible(visible)
        self.settings.setValue("sidebar", visible)
        QTimer.singleShot(0, self.view.relayout)

    def build_outline(self):
        self.outline.clear()
        toc = self.doc.get_toc(simple=True)
        stack = []  # (level, item)
        for level, title, page in toc:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.UserRole, page - 1)
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.outline.addTopLevelItem(item)
            stack.append((level, item))
        self.outline.expandToDepth(0)
        self.tabs.setTabText(1, "Outline" if toc else "Outline (none)")

    def on_outline_clicked(self, item, _column):
        page = item.data(0, Qt.UserRole)
        if isinstance(page, int) and page >= 0:
            self.view.go_to_page(page)

    def build_thumbnails(self):
        self.thumbs.blockSignals(True)
        self.thumbs.clear()
        blank = QPixmap(self.THUMB_W, int(self.THUMB_W * 1.4))
        blank.fill(QColor("#dddddd"))
        for i in range(self.doc.page_count):
            self.thumbs.addItem(QListWidgetItem(QIcon(blank), str(i + 1)))
        self.thumbs.blockSignals(False)
        self.thumbs.setCurrentRow(self.view.current_page())
        gen = self.thumb_gen
        QTimer.singleShot(0, lambda: self.thumb_step(gen, 0))

    def thumb_step(self, gen, start):
        if gen != self.thumb_gen or not self.doc:
            return
        end = min(start + 4, self.doc.page_count)
        for i in range(start, end):
            page = self.doc.load_page(i)
            page.set_rotation(self.view.base_rot[i])
            scale = self.THUMB_W / max(1.0, page.rect.width)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            self.thumbs.item(i).setIcon(QIcon(QPixmap.fromImage(img)))
        if end < self.doc.page_count:
            QTimer.singleShot(0, lambda: self.thumb_step(gen, end))

    def on_thumb_selected(self, row):
        if row >= 0:
            self.view.go_to_page(row)

    # ----- view feedback -----
    def on_page_changed(self, page):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        if 0 <= page < self.thumbs.count() and self.thumbs.currentRow() != page:
            self.thumbs.blockSignals(True)
            self.thumbs.setCurrentRow(page)
            self.thumbs.blockSignals(False)
        if self.path:
            self.settings.setValue(self.pos_key(self.path), page)

    def on_zoom_changed(self, scale):
        self.status_zoom.setText(f"{round(scale * 100)}%  ")
        mode = self.view.zoom_mode
        text = "Fit width" if mode == "fit-width" else "Fit page" if mode == "fit-page" else f"{round(scale * 100)}%"
        self.zoom_box.setEditText(text)

    def apply_zoom_text(self, text):
        t = text.strip().lower()
        if t.startswith("fit w"):
            self.view.set_zoom_mode("fit-width")
        elif t.startswith("fit p") or t == "fit page":
            self.view.set_zoom_mode("fit-page")
        else:
            try:
                value = float(t.rstrip("%").strip()) / 100.0
            except ValueError:
                self.on_zoom_changed(self.view.scale)
                return
            self.view.set_scale(value)
        self.view.setFocus()

    def rotate(self):
        if self.doc:
            self.view.rotate()
            if self.matches:
                self.scroll_to_current_match()

    def set_dark(self, on):
        self.view.set_dark(on)
        self.btn_dark.setChecked(on)
        self.settings.setValue("dark_pages", on)

    def goto_dialog(self):
        n, ok = QInputDialog.getInt(self, "Go to page", f"Page (1-{self.doc.page_count}):",
                                    self.view.current_page() + 1, 1, self.doc.page_count)
        if ok:
            self.view.go_to_page(n - 1)

    # ----- search -----
    def focus_search(self):
        self.search_box.setFocus()
        self.search_box.selectAll()

    def on_search_enter(self):
        if self.search_box.text() != self.search_text:
            self.search_timer.stop()
            self.start_search(self.search_box.text())
        else:
            self.step_match(1)

    def start_search(self, text):
        self.search_gen += 1
        self.matches, self.match_index = [], -1
        self.view.set_highlights({}, None)
        self.search_text = text
        if not text or not self.doc:
            self.search_status.setText("")
            return
        self.search_page = 0
        self.search_status.setText("Searching...")
        gen = self.search_gen
        QTimer.singleShot(0, lambda: self.search_step(gen))

    def search_step(self, gen):
        if gen != self.search_gen or not self.doc:
            return
        n = self.doc.page_count
        end = min(self.search_page + 15, n)
        for i in range(self.search_page, end):
            page = self.doc.load_page(i)
            page.set_rotation(self.view.base_rot[i])
            for k, rect in enumerate(page.search_for(self.search_text)):
                self.matches.append((i, rect, k))
        self.search_page = end

        highlights = {}
        for pno, rect, _k in self.matches:
            highlights.setdefault(pno, []).append(rect)

        first_hit = self.match_index == -1 and bool(self.matches)
        if first_hit:
            cur = self.view.current_page()
            self.match_index = next((j for j, m in enumerate(self.matches) if m[0] >= cur), 0)
        current = None
        if self.match_index >= 0:
            m = self.matches[self.match_index]
            current = (m[0], m[2])
        self.view.set_highlights(highlights, current)
        if first_hit:
            self.scroll_to_current_match()
        self.update_search_status(done=end >= n)
        if end < n:
            QTimer.singleShot(0, lambda: self.search_step(gen))

    def update_search_status(self, done=True):
        if not self.matches:
            self.search_status.setText("Not found" if done else "Searching...")
        else:
            suffix = "" if done else "+"
            self.search_status.setText(f"{self.match_index + 1} of {len(self.matches)}{suffix}")

    def scroll_to_current_match(self):
        if 0 <= self.match_index < len(self.matches):
            pno, rect, _k = self.matches[self.match_index]
            self.view.scroll_to_match(pno, rect)

    def step_match(self, direction):
        if not self.matches:
            if self.search_box.text():
                self.on_search_enter()
            return
        self.match_index = (self.match_index + direction) % len(self.matches)
        pno, rect, k = self.matches[self.match_index]
        highlights = {}
        for p, r, _ in self.matches:
            highlights.setdefault(p, []).append(r)
        self.view.set_highlights(highlights, (pno, k))
        self.scroll_to_current_match()
        self.update_search_status(done=self.search_page >= self.doc.page_count)

    # ----- tools -----
    def apply_view_rotation_to_doc(self):
        for i in range(self.doc.page_count):
            self.doc.load_page(i).set_rotation((self.view.base_rot[i] + self.view.view_rot) % 360)

    def save_copy(self):
        base, _ext = os.path.splitext(os.path.basename(self.path))
        suggested = os.path.join(os.path.dirname(self.path), f"{base}-copy.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Save a copy", suggested, "PDF documents (*.pdf)")
        if not path:
            return
        if os.path.abspath(path) == self.path:
            QMessageBox.warning(self, APP_NAME, "Choose a different file name; the original is not overwritten.")
            return
        try:
            self.apply_view_rotation_to_doc()
            self.doc.save(path, garbage=3, deflate=True)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not save the copy:\n{exc}")
            return
        self.statusBar().showMessage(f"Saved {path}", 5000)

    def copy_page_text(self):
        page = self.doc.load_page(self.view.current_page())
        text = page.get_text()
        QGuiApplication.clipboard().setText(text)
        if text.strip():
            self.statusBar().showMessage(f"Copied text of page {page.number + 1}", 4000)
        else:
            self.statusBar().showMessage("This page has no selectable text (it may be a scanned image)", 6000)

    def save_page_image(self):
        pno = self.view.current_page()
        base, _ext = os.path.splitext(os.path.basename(self.path))
        suggested = os.path.join(os.path.dirname(self.path), f"{base}-page{pno + 1}.png")
        path, _ = QFileDialog.getSaveFileName(self, "Save page as image", suggested, "PNG image (*.png)")
        if not path:
            return
        page = self.doc.load_page(pno)
        page.set_rotation((self.view.base_rot[pno] + self.view.view_rot) % 360)
        page.get_pixmap(dpi=200, alpha=False).save(path)
        self.statusBar().showMessage(f"Saved {path}", 5000)

    # ----- selection, clipboard, Windows integration -----
    def copy_selection(self):
        text = self.view.selected_text()
        if not text.strip():
            self.statusBar().showMessage("There is no selectable text there (scanned pages have none).", 5000)
            return
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage(f"Copied {len(text)} characters", 4000)

    def show_context_menu(self, global_pos):
        if not self.doc:
            return
        menu = QMenu(self)
        menu.addAction(self.act_copy)
        preview = " ".join(self.view.selected_preview().split())
        if preview:
            label = preview if len(preview) <= 30 else preview[:30] + "..."
            menu.addAction(f'Search for "{label}"', lambda: self.search_for_text(preview))
        menu.addAction(self.act_select_all)
        menu.exec(global_pos)

    def search_for_text(self, text):
        self.search_box.setText(text)
        self.search_timer.stop()
        self.start_search(text)

    def set_as_default(self):
        try:
            winassoc.register(os.path.abspath(__file__))
            self.settings.setValue("assoc_opt_out", False)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not register {APP_NAME} with Windows:\n{exc}")
            return
        QMessageBox.information(
            self, APP_NAME,
            f'{APP_NAME} is now listed under "Open with" for PDF files.\n\n'
            "Windows only lets you choose the default app yourself, so the Settings page opens next. "
            f"Pick {APP_NAME} for .pdf there.\n\n"
            f"Another way: right-click a PDF > Open with > Choose another app > {APP_NAME} > Always.",
        )
        winassoc.open_default_apps_settings()

    def remove_association(self):
        answer = QMessageBox.question(
            self, APP_NAME,
            f'Remove {APP_NAME} from the "Open with" list and from Default apps?\n\n'
            f"{APP_NAME} will not add itself again unless you choose \"Set as default PDF reader\".",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            winassoc.unregister()
            self.settings.setValue("assoc_opt_out", True)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Could not remove the entries:\n{exc}")
            return
        self.statusBar().showMessage("Removed the Windows file association entries", 6000)

    def show_about(self):
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h2>{APP_NAME} <small>{APP_VERSION}</small></h2>"
            "<p>A lightweight PDF reader.</p>"
            f"<p><b>Created by {AUTHOR}</b><br>"
            f"GitHub: <a href='{AUTHOR_URL}'>@{GITHUB_USER}</a><br>"
            f"Source code: <a href='{REPO_URL}'>{REPO_URL.replace('https://', '')}</a></p>"
            f"<p>Free software under the {LICENSE_NAME}.<br>Copyright &copy; 2026 {AUTHOR}.</p>"
            "<p style='color:gray'>Built with open source software:<br>"
            "PyMuPDF and MuPDF by Artifex Software (AGPL-3.0)<br>"
            "PySide6 and Qt by The Qt Company (LGPL-3.0)<br>"
            "PyInstaller (GPL-2.0 with exception)</p>",
        )

    # ----- drag and drop, closing -----
    @staticmethod
    def pdf_from_mime(mime):
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".pdf"):
                return url.toLocalFile()
        return None

    def dragEnterEvent(self, event):
        if self.pdf_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = self.pdf_from_mime(event.mimeData())
        if path:
            event.acceptProposedAction()
            self.open_path(path)

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        self.search_gen += 1
        self.thumb_gen += 1
        super().closeEvent(event)


def auto_register():
    """On Windows, list PageFlow under "Open with" (and re-do it if the exe moved).

    Skipped if the user removed the entries on purpose. Only runs for the built .exe.
    """
    if not (winassoc.is_supported() and winassoc.is_frozen()):
        return
    if QSettings().value("assoc_opt_out", False, type=bool):
        return
    try:
        winassoc.ensure_registered()
    except Exception as exc:  # never stop the reader from starting over this
        print(f"Could not register the file association: {exc}", file=sys.stderr)


def main():
    QCoreApplication.setOrganizationName(AUTHOR)
    QCoreApplication.setApplicationName(APP_NAME)
    args = sys.argv[1:]
    if "--register" in args or "--unregister" in args:     # command-line switches, no window
        if winassoc.is_supported():
            if "--register" in args:
                winassoc.register(os.path.abspath(__file__))
            else:
                winassoc.unregister()
        return
    app = QApplication(sys.argv)
    icon_file = resource_path("icon.png")
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))
    auto_register()
    win = MainWindow()
    win.show()
    files = [a for a in sys.argv[1:] if a.lower().endswith(".pdf") and os.path.exists(a)]
    if files:
        QTimer.singleShot(0, lambda: win.open_path(files[0]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
