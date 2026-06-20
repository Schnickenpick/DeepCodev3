// Minimal preload. The renderer talks to the Python bridge directly over a
// websocket (ws://127.0.0.1:8765), so we only expose small static info here,
// plus the project-folder picker (needs native dialog.showOpenDialog, which
// only the main process can call).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("deepcode", {
  bridgeUrl: "ws://127.0.0.1:8765/ws",
  apiUrl: "http://127.0.0.1:8765",
  getProjectDir: () => ipcRenderer.invoke("project:get"),
  pickProjectDir: () => ipcRenderer.invoke("project:pick"),
});
