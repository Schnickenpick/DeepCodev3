// Minimal preload. The renderer talks to the Python bridge directly over a
// websocket (ws://127.0.0.1:8765), so we only expose small static info here.
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("deepcode", {
  bridgeUrl: "ws://127.0.0.1:8765/ws",
  apiUrl: "http://127.0.0.1:8765",
});
