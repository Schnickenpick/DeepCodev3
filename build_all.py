"""Build the two shippable exes in one shot.

    python build_all.py

Produces (in ./release/):
    DeepCodeCLI.exe   — the terminal app (no Python needed)
    DeepCodeGUI.exe   — the desktop GUI (bundles the backend; no Python needed)

Steps:
 1. PyInstaller freezes deepcode.exe (CLI) + bridge.exe (GUI backend) into dist/.
 2. electron-builder builds the GUI portable exe, bundling dist/bridge.exe.
 3. Both final exes are copied into release/ with friendly names.

Run from the repo root. Requires: PyInstaller, Node/npm with app deps installed
(cd app && npm install) including electron-builder.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
APP = os.path.join(ROOT, "app")
RELEASE = os.path.join(ROOT, "release")


def run(cmd, cwd=None, shell=False):
    print(f"\n>>> {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    r = subprocess.run(cmd, cwd=cwd, shell=shell)
    if r.returncode != 0:
        print(f"!!! step failed (exit {r.returncode})")
        sys.exit(r.returncode)


def kill(name):
    # a running exe locks the file; kill before rebuilding
    subprocess.run(["taskkill", "/IM", name, "/F", "/T"],
                   capture_output=True, text=True)


def main():
    kill("bridge.exe")
    kill("DeepCodeGUI.exe")

    # 1. freeze python exes
    run([sys.executable, "-m", "PyInstaller", "build/deepcode.spec",
         "--noconfirm", "--distpath", "dist", "--workpath", "build/_work"],
        cwd=ROOT)

    # 2. build the GUI portable exe (bundles dist/bridge.exe)
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run([npm, "run", "dist"], cwd=APP, shell=(os.name == "nt"))

    # 3. collect into release/
    os.makedirs(RELEASE, exist_ok=True)
    shutil.copy2(os.path.join(DIST, "deepcode.exe"),
                 os.path.join(RELEASE, "DeepCodeCLI.exe"))
    gui_src = os.path.join(APP, "release", "DeepCodeGUI.exe")
    if os.path.isfile(gui_src):
        shutil.copy2(gui_src, os.path.join(RELEASE, "DeepCodeGUI.exe"))
    else:
        print(f"!!! GUI exe not found at {gui_src} — check electron-builder output")

    print("\n=== done ===")
    for f in sorted(os.listdir(RELEASE)):
        p = os.path.join(RELEASE, f)
        print(f"  release/{f}   ({os.path.getsize(p)//1_000_000} MB)")


if __name__ == "__main__":
    main()
