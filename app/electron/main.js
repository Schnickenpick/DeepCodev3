import { app, BrowserWindow } from "electron";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isDev = !!process.env.ELECTRON_DEV;
const REPO_ROOT = path.resolve(__dirname, "..", "..");

let bridge = null;
let win = null;

function startBridge() {
  // Launch the Python websocket bridge (server/). It serves on 127.0.0.1:8765;
  // the renderer connects to it. Killed on app quit.
  //
  // dev: run `python -m server` from the repo (needs Python installed).
  // packaged: run the frozen bridge.exe shipped as an extraResource (PyInstaller
  //   freeze of server/ + the deepcodev3 core), so the single-exe build needs
  //   no system Python. process.resourcesPath points at the bundled resources.
  let cmd, args, cwd;
  if (app.isPackaged) {
    const exe = process.platform === "win32" ? "bridge.exe" : "bridge";
    cmd = path.join(process.resourcesPath, "bridge", exe);
    args = [];
    cwd = path.dirname(cmd);
  } else {
    cmd = process.platform === "win32" ? "python" : "python3";
    args = ["-m", "server"];
    cwd = REPO_ROOT;
  }
  // pipe stdio so bridge crashes are visible in the dev terminal; windowsHide
  // keeps the Python console window from flashing (the GUI parent owns no
  // console for the child to attach to).
  bridge = spawn(cmd, args, {
    cwd,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    stdio: isDev ? "inherit" : "ignore",
    windowsHide: true,
  });
  bridge.on("error", (e) => console.error("[bridge] failed to start:", e));
  bridge.on("exit", (code) => console.log("[bridge] exited", code));
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0c0c0f",
    title: "DeepCode",
    show: false, // reveal on ready-to-show to avoid the white-flash / blank wait
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.once("ready-to-show", () => win.show());
  if (isDev) {
    win.loadURL("http://localhost:5173");
    if (process.env.DEEPCODE_DEVTOOLS) win.webContents.openDevTools({ mode: "detach" });
  } else {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

app.whenReady().then(() => {
  startBridge();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  if (bridge && !bridge.killed) bridge.kill();
});
