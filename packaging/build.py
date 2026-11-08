"""Builds Motwi's installer and the extension zip into packaging/out.

    uv run --project core --group packaging python packaging/build.py [--skip-web] [--no-installer]

Needs Node, and Inno Setup 6 for the installer (ISCC on PATH, in its usual folder, or in the
ISCC environment variable).
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"

# the tunnel mods reach Motwi through; pinned, so a build only ever ships this exact file
CLOUDFLARED_VERSION = "2026.10.7"
CLOUDFLARED_SHA256 = "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343"
CLOUDFLARED_RELEASES = "https://github.com/cloudflare/cloudflared/releases/download"
CLOUDFLARED_LICENSE = "https://raw.githubusercontent.com/cloudflare/cloudflared/{}/LICENSE"

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a}, {b}, {c}, 0), prodvers=({a}, {b}, {c}, 0), mask=0x3f, flags=0,
                    OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'H4ck3rPr0G4m3r'),
      StringStruct('FileDescription', 'Motwi'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'Motwi'),
      StringStruct('OriginalFilename', 'Motwi.exe'),
      StringStruct('ProductName', 'Motwi'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def run(*cmd: object, cwd: Path = ROOT) -> None:
    args = [str(c) for c in cmd]
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def versions() -> dict[str, str]:
    """Every place the version is written down."""

    def find(path: str, pattern: str) -> str:
        match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.M)
        if match is None:
            raise SystemExit(f"no version found in {path}")
        return match[1]

    def package(path: str) -> str:
        return json.loads((ROOT / path).read_text(encoding="utf-8"))["version"]

    return {
        "core/src/motwi/__init__.py": find("core/src/motwi/__init__.py", r'__version__ = "(.+)"'),
        "core/pyproject.toml": find("core/pyproject.toml", r'^version = "(.+)"'),
        "ui/package.json": package("ui/package.json"),
        "extension/package.json": package("extension/package.json"),
        "extension/public/manifest.json": package("extension/public/manifest.json"),
    }


def the_version() -> str:
    found = versions()
    if len(set(found.values())) != 1:
        lines = "\n".join(f"  {path}: {v}" for path, v in found.items())
        raise SystemExit(f"versions disagree:\n{lines}")
    return next(iter(found.values()))


def build_web() -> None:
    npm = shutil.which("npm") or "npm"
    # ui first: the extension shares code with it
    run(npm, "ci", cwd=ROOT / "ui")
    run(npm, "run", "build", cwd=ROOT / "ui")
    run(npm, "ci", cwd=ROOT / "extension")
    run(npm, "run", "build", cwd=ROOT / "extension")


def zip_extension(version: str) -> Path:
    target = OUT / f"Motwi-extension-{version}.zip"
    source = ROOT / "extension" / "dist"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(source.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(source))
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_cloudflared() -> Path:
    folder = OUT / "cloudflared"
    folder.mkdir(exist_ok=True)
    exe = folder / "cloudflared.exe"
    if not exe.is_file() or _sha256(exe) != CLOUDFLARED_SHA256:
        url = f"{CLOUDFLARED_RELEASES}/{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe"
        print(f"+ download {url}", flush=True)
        partial = exe.with_suffix(".part")
        urllib.request.urlretrieve(url, partial)
        found = _sha256(partial)
        if found != CLOUDFLARED_SHA256:
            partial.unlink()
            raise SystemExit(f"cloudflared's checksum is wrong: {found}")
        partial.replace(exe)
    license_file = folder / "LICENSE.txt"
    if not license_file.is_file():
        urllib.request.urlretrieve(CLOUDFLARED_LICENSE.format(CLOUDFLARED_VERSION), license_file)
    return exe


def build_app(version: str) -> Path:
    a, b, c = (version.split(".") + ["0", "0"])[:3]
    (OUT / "version_info.txt").write_text(
        VERSION_INFO.format(a=a, b=b, c=c, version=version), encoding="utf-8"
    )
    run(
        sys.executable,
        "-m",
        "PyInstaller",
        HERE / "motwi.spec",
        "--noconfirm",
        "--clean",
        "--distpath",
        OUT / "pyinstaller",
        "--workpath",
        OUT / "pyinstaller-work",
    )
    return OUT / "pyinstaller" / "Motwi" / "Motwi.exe"


def self_test(exe: Path) -> None:
    """Runs the frozen app's own checks, and fails the build if it's missing anything."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        env = {**os.environ, "MOTWI_HOME": tmp}
        code = subprocess.run(
            [str(exe), "--self-test", str(report)], env=env, timeout=120, check=False
        ).returncode
        if not report.is_file():
            raise SystemExit(f"the frozen app didn't write its report (exit {code})")
        data = json.loads(report.read_text(encoding="utf-8"))
    for name, check in data["checks"].items():
        print(f"  {'ok  ' if check['ok'] else 'FAIL'} {name}: {check['detail']}")
    if not data["ok"]:
        raise SystemExit("the frozen app failed its self-test")


def find_iscc() -> str:
    candidates = [
        os.environ.get("ISCC"),
        shutil.which("ISCC"),
        *(
            Path(base) / "Inno Setup 6" / "ISCC.exe"
            for base in (
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.environ.get("ProgramFiles", r"C:\Program Files"),
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
            )
        ),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    raise SystemExit("Inno Setup 6 not found. Install it, or set ISCC to ISCC.exe.")


def build_installer(version: str) -> Path:
    run(find_iscc(), f"/DAppVersion={version}", HERE / "motwi.iss", cwd=HERE)
    return OUT / f"Motwi-Setup-{version}.exe"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-web", action="store_true", help="use the existing web builds")
    parser.add_argument("--no-installer", action="store_true", help="stop after the frozen app")
    args = parser.parse_args()

    version = the_version()
    print(f"Building Motwi {version}", flush=True)
    OUT.mkdir(exist_ok=True)
    if not args.skip_web:
        build_web()
    built = [zip_extension(version)]
    fetch_cloudflared()
    exe = build_app(version)
    self_test(exe)
    if not args.no_installer:
        built.append(build_installer(version))
    print("\nBuilt:")
    for path in built:
        print(f"  {path}  ({path.stat().st_size / 1_048_576:.1f} MB)")


if __name__ == "__main__":
    main()
