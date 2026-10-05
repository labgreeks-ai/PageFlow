# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Amman (https://github.com/labgreeks-ai)
"""Windows integration for PageFlow.

Makes PageFlow appear in the "Open with" list for PDF files and in Settings >
Default apps. Everything is written under HKEY_CURRENT_USER, so no administrator
rights are needed, and unregister() removes every entry again.

Windows does not allow a program to make itself the default app silently; the
user has to pick it once (open_default_apps_settings() takes them there).
"""
import os
import sys

APP_NAME = "PageFlow"
DESCRIPTION = "A lightweight PDF reader"
PROGID = "PageFlow.PDF"
CLASSES = r"Software\Classes"
CAPABILITIES_KEY = r"Software\PageFlow\Capabilities"
REGISTERED_APPS_KEY = r"Software\RegisteredApplications"
OPEN_WITH_KEY = CLASSES + r"\.pdf\OpenWithProgids"


def is_supported():
    return sys.platform == "win32"


def is_frozen():
    """True when running as the PyInstaller-built PageFlow.exe."""
    return bool(getattr(sys, "frozen", False))


def _target(script_path=None):
    """What Windows should launch: (command line, icon, exe file name or None)."""
    if is_frozen():
        exe = os.path.abspath(sys.executable)
        return f'"{exe}" "%1"', f'"{exe}",0', os.path.basename(exe)
    # Running from source: use pythonw.exe so no console window appears.
    script = os.path.abspath(script_path or sys.argv[0])
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    interpreter = pythonw if os.path.exists(pythonw) else sys.executable
    ico = os.path.join(os.path.dirname(script), "icon.ico")
    icon = f'"{ico}"' if os.path.exists(ico) else f'"{sys.executable}",0'
    return f'"{interpreter}" "{script}" "%1"', icon, None


def _notify_shell():
    """Tell Explorer that file associations changed so menus refresh."""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def register(script_path=None):
    """List PageFlow under "Open with" for .pdf and in Settings > Default apps."""
    import winreg

    command, icon, exe_name = _target(script_path)

    def put(path, name, value, kind=None):
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ if kind is None else kind, value)

    # 1. The file type: how PageFlow opens a PDF.
    put(rf"{CLASSES}\{PROGID}", "", f"{APP_NAME} PDF Document")
    put(rf"{CLASSES}\{PROGID}\DefaultIcon", "", icon)
    put(rf"{CLASSES}\{PROGID}\shell\open\command", "", command)

    # 2. Offer it in the "Open with" list for .pdf files.
    put(OPEN_WITH_KEY, PROGID, b"", winreg.REG_NONE)

    # 3. The program itself (Explorer also uses this for "Open with" suggestions).
    if exe_name:
        app = rf"{CLASSES}\Applications\{exe_name}"
        put(app, "FriendlyAppName", APP_NAME)
        put(app + r"\DefaultIcon", "", icon)
        put(app + r"\shell\open\command", "", command)
        put(app + r"\SupportedTypes", ".pdf", "")

    # 4. Show it in Settings > Apps > Default apps.
    put(CAPABILITIES_KEY, "ApplicationName", APP_NAME)
    put(CAPABILITIES_KEY, "ApplicationDescription", DESCRIPTION)
    put(CAPABILITIES_KEY + r"\FileAssociations", ".pdf", PROGID)
    put(REGISTERED_APPS_KEY, APP_NAME, CAPABILITIES_KEY)

    _notify_shell()


def is_registered(script_path=None):
    """True if the entries exist and point at the program that is running now."""
    import winreg

    command, _icon, _exe = _target(script_path)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{PROGID}\shell\open\command") as key:
            current, _kind = winreg.QueryValueEx(key, "")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTERED_APPS_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return False
    return current == command


def ensure_registered(script_path=None):
    """Register if missing or if the program has moved. Returns True if it wrote anything."""
    if is_registered(script_path):
        return False
    register(script_path)
    return True


def _delete_value(winreg, path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def _delete_tree(winreg, path):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    with key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(winreg, path + "\\" + child)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def unregister():
    """Remove every entry that register() created."""
    import winreg

    _command, _icon, exe_name = _target()
    _delete_value(winreg, OPEN_WITH_KEY, PROGID)
    _delete_value(winreg, REGISTERED_APPS_KEY, APP_NAME)
    _delete_tree(winreg, rf"{CLASSES}\{PROGID}")
    if exe_name:
        _delete_tree(winreg, rf"{CLASSES}\Applications\{exe_name}")
    _delete_tree(winreg, r"Software\PageFlow")
    _notify_shell()


def open_default_apps_settings():
    """Open the Windows Settings page where the user picks the default PDF app."""
    for uri in (f"ms-settings:defaultapps?registeredAppUser={APP_NAME}", "ms-settings:defaultapps"):
        try:
            os.startfile(uri)  # type: ignore[attr-defined]  (Windows only)
            return True
        except (OSError, AttributeError):
            continue
    return False
