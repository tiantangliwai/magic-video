"""直接读 MSI 的 Property / AppSearch / Directory 表（ctypes 调 msi.dll，仅只读查询）。

用途：核对安装包是否真的把「安装目录属性」写成 public property、向导绑定的
WIXUI_INSTALLDIR 是否指向它、以及升级用的 RegistrySearch 有没有进表。
用法：.venv/Scripts/python.exe installer/inspect_msi.py <msi 路径> [property|appsearch|directory|customaction]
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

msi = ctypes.WinDLL("msi.dll")

MSIDBOPEN_READONLY = 0


def _open(path: str):
    handle = wintypes.HANDLE()
    rc = msi.MsiOpenDatabaseW(ctypes.c_wchar_p(path), ctypes.c_void_p(MSIDBOPEN_READONLY),
                              ctypes.byref(handle))
    if rc != 0:
        raise SystemExit(f"MsiOpenDatabase 失败，错误码 {rc}")
    return handle


def query(handle, sql: str, columns: int) -> list[list[str]]:
    view = wintypes.HANDLE()
    rc = msi.MsiDatabaseOpenViewW(handle, ctypes.c_wchar_p(sql), ctypes.byref(view))
    if rc != 0:
        raise SystemExit(f"MsiDatabaseOpenView 失败（{sql}），错误码 {rc}")
    msi.MsiViewExecute(view, None)
    rows: list[list[str]] = []
    buf = ctypes.create_unicode_buffer(1024)
    while True:
        record = wintypes.HANDLE()
        rc = msi.MsiViewFetch(view, ctypes.byref(record))
        if rc != 0:
            break
        values = []
        for index in range(1, columns + 1):
            size = wintypes.DWORD(len(buf))
            msi.MsiRecordGetStringW(record, index, buf, ctypes.byref(size))
            values.append(buf.value)
        rows.append(values)
        msi.MsiCloseHandle(record)
    msi.MsiCloseHandle(view)
    return rows


def main() -> None:
    path = sys.argv[1]
    tables = sys.argv[2:] or ["property", "appsearch", "directory"]
    handle = _open(path)
    try:
        if "property" in tables:
            print("== Property（只列关心的键）==")
            for key, value in query(handle, "SELECT Property, Value FROM Property", 2):
                if key in ("WIXUI_INSTALLDIR", "APPFOLDER", "ARPINSTALLLOCATION",
                           "INSTALLFOLDER", "ProductVersion", "ProductName", "UpgradeCode",
                           "ALLUSERS", "MSIINSTALLPERUSER"):
                    print(f"  {key} = {value!r}")
        if "appsearch" in tables:
            print("== AppSearch ==")
            for prop, sig in query(handle, "SELECT Property, Signature_ FROM AppSearch", 2):
                print(f"  {prop} <- {sig}")
            print("== RegLocator ==")
            for row in query(handle, "SELECT Signature_, Root, Key, Name, Type FROM RegLocator", 5):
                print("  " + " | ".join(row))
        if "directory" in tables:
            print("== Directory ==")
            for row in query(handle, "SELECT Directory, Directory_Parent, DefaultDir FROM Directory", 3):
                if row[0] in ("TARGETDIR", "LocalAppDataFolder", "INSTALLFOLDER",
                              "APPFOLDER", "AppMenuFolder", "ProgramMenuFolder"):
                    print("  " + " | ".join(row))
        if "customaction" in tables:
            print("== CustomAction ==")
            for row in query(handle, "SELECT Action, Type, Source, Target FROM CustomAction", 4):
                print("  " + " | ".join(row))
    finally:
        msi.MsiCloseHandle(handle)


if __name__ == "__main__":
    main()
