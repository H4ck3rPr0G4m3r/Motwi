# PyInstaller build of Motwi.exe, one-folder. Run through packaging/build.py, which builds
# the web parts first and writes version_info.txt.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

here = Path(SPECPATH)
root = here.parent

analysis = Analysis(
    [str(here / "motwi_app.py")],
    pathex=[str(root / "core" / "src")],
    datas=[
        (str(root / "ui" / "dist"), "ui"),
        (str(root / "extension" / "dist"), "extension"),
        # build.py downloads it, pinned by checksum, along with its license
        (str(here / "out" / "cloudflared" / "cloudflared.exe"), "cloudflared"),
        (str(here / "out" / "cloudflared" / "LICENSE.txt"), "cloudflared"),
        # keyring finds its backends through package metadata; without this it silently
        # falls back to one that can't store anything
        *copy_metadata("keyring"),
        *collect_data_files("certifi"),
        *collect_data_files("webview"),
    ],
    hiddenimports=[
        "keyring.backends.Windows",
        "pystray._win32",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",
    ],
    excludes=["tkinter", "pytest", "respx"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Motwi",
    console=False,
    icon=str(here / "motwi.ico"),
    version=str(here / "out" / "version_info.txt"),
)
COLLECT(exe, analysis.binaries, analysis.datas, name="Motwi")
