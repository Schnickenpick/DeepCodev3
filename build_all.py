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
APP = os.path.join(ROOT, "app")
DIST = os.path.join(ROOT, "dist")          # FINAL output (the only folder users see)
PYDIST = os.path.join(ROOT, "build", "_pydist")  # PyInstaller raw exes (intermediate)
GUI_OUT = os.path.join(APP, "release")     # electron-builder raw output (intermediate)


def run(cmd, cwd=None, shell=False, env=None):
    print(f"\n>>> {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    r = subprocess.run(cmd, cwd=cwd, shell=shell, env=env)
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

    # 1. freeze python exes (deepcode.exe + bridge.exe) into the intermediate
    #    build/_pydist; the GUI bundles bridge.exe from there.
    run([sys.executable, "-m", "PyInstaller", "build/deepcode.spec",
         "--noconfirm", "--distpath", PYDIST, "--workpath", "build/_work"],
        cwd=ROOT)

    # 2. build the GUI portable exe (bundles build/_pydist/bridge.exe).
    # Disable code signing: we ship an UNSIGNED portable exe. Without this,
    # electron-builder downloads winCodeSign and tries to extract macOS .dylib
    # SYMLINKS, which fail on Windows without admin/Developer Mode ("client
    # lacks required privilege") and abort the build.
    npm = "npm.cmd" if os.name == "nt" else "npm"
    env = dict(os.environ)
    env["CSC_IDENTITY_AUTO_DISCOVERY"] = "false"
    run([npm, "run", "dist"], cwd=APP, shell=(os.name == "nt"), env=env)

    # 3. collect the two FINAL exes into dist/ — the only folder users touch.
    if os.path.isdir(DIST):
        shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)
    shutil.copy2(os.path.join(PYDIST, "deepcode.exe"),
                 os.path.join(DIST, "DeepCodeCLI.exe"))
    gui_src = os.path.join(GUI_OUT, "DeepCodeGUI.exe")
    if os.path.isfile(gui_src):
        shutil.copy2(gui_src, os.path.join(DIST, "DeepCodeGUI.exe"))
    else:
        print(f"!!! GUI exe not found at {gui_src} — check electron-builder output")

    # ship the installer scripts + README/disclaimer next to the exes
    for f in ("install.bat", "uninstall.bat", "RELEASE.md"):
        src = os.path.join(ROOT, "installer", f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST, f))

    print("\n=== done — final builds in dist/ ===")
    for f in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, f)
        print(f"  dist/{f}   ({os.path.getsize(p)//1_000_000} MB)")


if __name__ == "__main__":
    main()
