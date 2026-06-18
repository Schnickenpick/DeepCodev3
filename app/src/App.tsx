import { useEffect, useRef, useState, useCallback } from "react";

// --- types mirroring the bridge's event protocol -----------------------------
type WsEvent =
  | { type: "thinking" }
  | { type: "delta"; text: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean }
  | { type: "tokens"; count: number }
  | { type: "turn_done"; text: string }
  | { type: "error"; text: string };

type ToolCard = { name: string; args: Record<string, unknown>; ok?: boolean };
type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tools: ToolCard[]; streaming: boolean };

type ModelInfo = { id: string; name: string; provider: string; tier: string };

const BRIDGE: string = (window as any).deepcode?.bridgeUrl ?? "ws://127.0.0.1:8765/ws";
const API: string = (window as any).deepcode?.apiUrl ?? "http://127.0.0.1:8765";

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // --- websocket connection (auto-reconnect) ---------------------------------
  // React StrictMode double-invokes effects in dev: without a guard that opens
  // TWO sockets, both stream the same turn and the deltas interleave (doubled
  // text). We keep exactly one live socket via wsRef and only reconnect when
  // the CURRENT socket closes.
  useEffect(() => {
    let alive = true;
    let retry: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (!alive) return;
      // don't open a second socket if one is already open/connecting
      const existing = wsRef.current;
      if (existing && (existing.readyState === WebSocket.OPEN || existing.readyState === WebSocket.CONNECTING)) {
        return;
      }
      const ws = new WebSocket(BRIDGE);
      wsRef.current = ws;
      ws.onopen = () => alive && setConnected(true);
      ws.onclose = () => {
        if (!alive || wsRef.current !== ws) return; // ignore stale sockets
        setConnected(false);
        retry = setTimeout(connect, 1000);
      };
      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return;            // drop msgs from stale sockets
        handleEvent(JSON.parse(ev.data) as WsEvent);
      };
    };
    connect();
    return () => {
      alive = false;
      clearTimeout(retry);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- model list ------------------------------------------------------------
  useEffect(() => {
    fetch(`${API}/api/models`)
      .then((r) => r.json())
      .then((d) => {
        setModels(d.models);
        setModel(d.default);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking]);

  // --- event handling: mutate the last (streaming) assistant message ---------
  const handleEvent = useCallback((ev: WsEvent) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      const ensureAssistant = (): Msg & { role: "assistant" } => {
        if (last && last.role === "assistant" && last.streaming) {
          return last as Msg & { role: "assistant" };
        }
        const fresh = { role: "assistant" as const, text: "", tools: [], streaming: true };
        next.push(fresh);
        return fresh;
      };

      switch (ev.type) {
        case "thinking":
          setThinking(true);
          break;
        case "delta": {
          const a = ensureAssistant();
          a.text += ev.text;
          setThinking(false);
          break;
        }
        case "tool_call": {
          const a = ensureAssistant();
          a.tools.push({ name: ev.name, args: ev.args });
          setThinking(false);
          break;
        }
        case "tool_result": {
          const a = ensureAssistant();
          for (let i = a.tools.length - 1; i >= 0; i--) {
            if (a.tools[i].name === ev.name && a.tools[i].ok === undefined) {
              a.tools[i].ok = ev.ok;
              break;
            }
          }
          break;
        }
        case "turn_done": {
          const a = ensureAssistant();
          if (ev.text && !a.text) a.text = ev.text;
          a.streaming = false;
          setThinking(false);
          setBusy(false);
          break;
        }
        case "error": {
          const a = ensureAssistant();
          a.text += `\n\n[error] ${ev.text}`;
          a.streaming = false;
          setThinking(false);
          setBusy(false);
          break;
        }
      }
      return next;
    });
  }, []);

  const send = () => {
    const text = input.trim();
    if (!text || !connected || busy) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setBusy(true);
    setThinking(true);
    wsRef.current?.send(JSON.stringify({ type: "chat", text, model }));
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="flex h-full flex-col bg-ink text-[15px]">
      {/* top bar */}
      <header className="flex items-center gap-3 border-b border-edge px-4 py-2.5">
        <Logo className="h-6 w-6" />
        <span className="font-semibold tracking-tight">DeepCode</span>
        <span
          className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-green-500" : "bg-yellow-500 animate-pulse"}`}
          title={connected ? "bridge connected" : "connecting…"}
        />
        <div className="ml-auto">
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="rounded-lg bg-panel px-2.5 py-1 text-sm text-gray-300 outline-none border border-edge hover:border-accent/60 transition-colors"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {/* message stream */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.length === 0 && (
            <div className="mt-24 flex flex-col items-center text-gray-500">
              <Logo className="h-16 w-16" />
              <div className="mt-4 text-lg font-medium text-gray-300">DeepCode</div>
              <div className="mt-1 text-sm">Ask anything, or @mention a file to attach it.</div>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageView key={i} msg={m} />
          ))}
          {thinking && (
            <div className="flex items-center gap-2 text-gray-400">
              <Spinner /> Thinking…
            </div>
          )}
        </div>
      </div>

      {/* input bar */}
      <footer className="border-t border-edge p-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-xl border border-edge bg-panel p-2 focus-within:border-accent">
          <span className="px-1 pb-1 font-mono text-accent">❯</span>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKey}
            rows={1}
            placeholder={connected ? "Message DeepCode…  (Enter to send, Shift+Enter for newline)" : "Connecting to bridge…"}
            className="max-h-40 flex-1 resize-none bg-transparent py-1 outline-none placeholder:text-gray-600"
          />
          <button
            onClick={send}
            disabled={!connected || busy || !input.trim()}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-30"
          >
            Send
          </button>
        </div>
      </footer>
    </div>
  );
}

function MessageView({ msg }: { msg: Msg }) {
  if (msg.role === "user") {
    return (
      <div className="self-end max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent/15 px-4 py-2 text-gray-100 border border-accent/30">
        {msg.text}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {msg.tools.map((t, i) => (
        <ToolView key={i} tool={t} />
      ))}
      {msg.text && (
        <div className="whitespace-pre-wrap leading-relaxed text-gray-200">
          {msg.text}
          {msg.streaming && <Caret />}
        </div>
      )}
    </div>
  );
}

function ToolView({ tool }: { tool: ToolCard }) {
  const arg =
    (tool.args?.path as string) ??
    (tool.args?.command as string) ??
    (tool.args?.pattern as string) ??
    JSON.stringify(tool.args ?? {}).slice(0, 80);
  const dot =
    tool.ok === undefined ? "text-gray-500" : tool.ok ? "text-green-500" : "text-red-500";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-edge bg-panel/60 px-3 py-1.5 font-mono text-sm">
      <span className={dot}>●</span>
      <span className="font-medium text-accent">{tool.name}</span>
      <span className="truncate text-gray-400">{arg}</span>
    </div>
  );
}

function Caret() {
  return <span className="ml-0.5 inline-block w-2 animate-pulse text-accent">▋</span>;
}

// The DeepCode mark: a terminal "shell" window (title-bar dots) framing a >_
// prompt — the same idea as the TUI's DEEPCODE_LOGO, in crisp SVG.
function Logo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} fill="none" aria-label="DeepCode">
      <rect
        x="3"
        y="6"
        width="42"
        height="36"
        rx="7"
        stroke="var(--accent, #d77757)"
        strokeWidth="2.5"
      />
      <line x1="3" y1="16" x2="45" y2="16" stroke="var(--accent, #d77757)" strokeWidth="2.5" />
      <circle cx="9.5" cy="11" r="1.4" fill="var(--accent, #d77757)" />
      <circle cx="15" cy="11" r="1.4" fill="var(--accent, #d77757)" />
      <circle cx="20.5" cy="11" r="1.4" fill="var(--accent, #d77757)" />
      <path
        d="M13 24 L20 29.5 L13 35"
        stroke="var(--accent, #d77757)"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <line
        x1="24"
        y1="35"
        x2="35"
        y2="35"
        stroke="var(--accent, #d77757)"
        strokeWidth="2.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
  );
}
