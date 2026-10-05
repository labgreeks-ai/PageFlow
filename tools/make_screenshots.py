#!/usr/bin/env python3
"""Make docs/screenshot-light.png and docs/screenshot-dark.png from the real app, headless.

Run from the repository root:  python tools/make_screenshots.py
"""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymupdf
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtWidgets import QApplication

import pageflow

OUT = os.path.join(ROOT, "docs")

SAMPLE = [
    ("Welcome to PageFlow", [
        "PageFlow is a small, fast PDF reader. Pages are drawn only when they scroll into view, "
        "so even large documents open quickly and stay responsive.",
        "Select any text with the mouse, copy it with Ctrl+C, or search the whole document with Ctrl+F. "
        "Every match is highlighted, and the current match is shown in orange.",
        "Double-click selects a word, triple-click selects a line, and Ctrl+A selects everything.",
    ]),
    ("Reading comfortably", [
        "Switch to dark page colors with the D key, rotate pages with R, and zoom with Ctrl and the mouse wheel. "
        "PageFlow remembers the last page you read in every document.",
        "The sidebar shows page thumbnails and the document outline, so long documents are easy to move around in.",
    ]),
    ("Open with Windows", [
        "Run PageFlow.exe once and it adds itself to the Open with list for PDF files. "
        "Use Help, then Set as default PDF reader, to pick it as your default app.",
    ]),
]


def make_sample(path):
    doc = pymupdf.open()
    toc = []
    for number, (title, paragraphs) in enumerate(SAMPLE, start=1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 110), title, fontsize=26, fontname="hebo")
        page.draw_line((72, 124), (523, 124), color=(0.25, 0.45, 0.85), width=1.5)
        y = 160
        for text in paragraphs:
            rect = pymupdf.Rect(72, y, 523, y + 120)
            page.insert_textbox(rect, text, fontsize=12, fontname="helv", lineheight=1.35)
            y += 105
        toc.append([1, title, number])
    doc.set_toc(toc)
    doc.save(path)
    doc.close()


def main():
    QCoreApplication.setOrganizationName("PageFlowScreenshots")
    QCoreApplication.setApplicationName("PageFlowScreenshots")
    app = QApplication([])
    QSettings().clear()

    def pump(ms=400):
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents()
            time.sleep(0.005)

    work = tempfile.mkdtemp(prefix="pageflow-shots-")
    pdf = os.path.join(work, "Welcome to PageFlow.pdf")
    make_sample(pdf)

    win = pageflow.MainWindow()
    win.resize(1280, 800)
    win.show()
    pump(300)
    win.open_path(pdf)
    pump(900)
    view = win.view
    view.set_zoom_mode("fit-width")
    view.go_to_page(0)
    pump(600)

    # search highlight + a selected sentence, so the picture shows both
    win.search_for_text("highlighted")
    pump(500)
    chars = "".join(c[4] for c in view._page_text(0).chars)
    phrase = "Select any text with the mouse"
    start = chars.index(phrase)
    view._set_selection((0, start), (0, start + len(phrase)))
    pump(500)

    win.status_file.setText("Welcome to PageFlow.pdf")      # keep the temp folder out of the picture
    os.makedirs(OUT, exist_ok=True)
    win.grab().save(os.path.join(OUT, "screenshot-light.png"))
    win.act_dark.setChecked(True)
    pump(800)
    win.grab().save(os.path.join(OUT, "screenshot-dark.png"))
    win.close()
    print("saved to", OUT)


if __name__ == "__main__":
    main()
