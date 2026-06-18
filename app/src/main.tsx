import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// NOTE: no <React.StrictMode>. StrictMode double-invokes effects in dev, which
// opens our websocket twice → two event streams interleave (doubled text). The
// single-socket guard in App also defends this, but skipping StrictMode removes
// the whole class of dev-only double-mount surprises for the live connection.
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
