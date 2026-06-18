// Frees TCP 5173 (and 8765) before `npm run dev`, so a Vite/bridge orphan from
// a previous run (window closed without Ctrl-C) doesn't block startup.
// Locale-proof: matches the port + a numeric PID, not the (localized) state word.
import { execSync } from "node:child_process";

const PORTS = [5173, 8765];

function killPort(port) {
  let out = "";
  try {
    out =
      process.platform === "win32"
        ? execSync("netstat -ano", { encoding: "utf8" })
        : execSync(`lsof -ti tcp:${port}`, { encoding: "utf8" });
  } catch {
    return;
  }
  const pids = new Set();
  if (process.platform === "win32") {
    for (const line of out.split("\n")) {
      if (!line.includes(`:${port}`)) continue;
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1];
      if (/^\d+$/.test(pid) && pid !== "0") pids.add(pid);
    }
  } else {
    out.split("\n").forEach((p) => p.trim() && pids.add(p.trim()));
  }
  for (const pid of pids) {
    try {
      execSync(
        process.platform === "win32" ? `taskkill /PID ${pid} /F /T` : `kill -9 ${pid}`,
        { stdio: "ignore" }
      );
      console.log(`[freeport] freed :${port} (pid ${pid})`);
    } catch {
      /* already gone */
    }
  }
}

PORTS.forEach(killPort);
