# PyInstaller spec — builds two onefile exes:
#   deepcode.exe  (terminal CLI)        from build/cli_entry.py
#   bridge.exe    (GUI websocket bridge) from build/bridge_entry.py
#
# Build:  pyinstaller build/deepcode.spec --noconfirm
# Outputs land in dist/ (dist/deepcode.exe, dist/bridge.exe).
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller = the dir holding this spec (build/).
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
CORE_SRC = os.path.join(ROOT, "deepcodev3", "src")   # the deepcodev3 package
# `server` lives at repo root and imports deepcodev3; both must be importable.
PATHS = [ROOT, CORE_SRC]

# tiktoken ships encodings as a namespace pkg (tiktoken_ext) + a data blob that
# PyInstaller won't find automatically — collect everything.
tk_datas, tk_bins, tk_hidden = collect_all("tiktoken")
tke_datas, tke_bins, tke_hidden = collect_all("tiktoken_ext")

# uvicorn loads its protocol/loop impls dynamically -> hidden imports.
UVICORN_HIDDEN = collect_submodules("uvicorn") + [
    "websockets", "websockets.legacy", "h11",
]

common = dict(
    pathex=PATHS,
    datas=tk_datas + tke_datas,
    binaries=tk_bins + tke_bins,
    hiddenimports=tk_hidden + tke_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

_BUILD = SPECPATH

# ---- terminal CLI -> deepcode.exe -------------------------------------------
cli_a = Analysis([os.path.join(_BUILD, "cli_entry.py")], **common)
cli_pyz = PYZ(cli_a.pure)
cli_exe = EXE(
    cli_pyz, cli_a.scripts, cli_a.binaries, cli_a.datas, [],
    name="deepcode", console=True, onefile=True,
    icon=os.path.join(ROOT, "app", "assets", "icon.ico"),
)

# ---- GUI bridge -> bridge.exe (windowless; spawned by Electron) -------------
br_common = dict(common)
br_common["hiddenimports"] = common["hiddenimports"] + UVICORN_HIDDEN
br_a = Analysis([os.path.join(_BUILD, "bridge_entry.py")], **br_common)
br_pyz = PYZ(br_a.pure)
br_exe = EXE(
    br_pyz, br_a.scripts, br_a.binaries, br_a.datas, [],
    name="bridge", console=False, onefile=True,
    icon=os.path.join(ROOT, "app", "assets", "icon.ico"),
)
