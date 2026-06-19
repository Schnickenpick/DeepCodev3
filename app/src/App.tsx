import { useEffect, useRef, useState, useCallback } from "react";

// --- bridge event protocol ----------------------------------------------------
type WsEvent =
  | { type: "thinking" }
  | { type: "reasoning"; level: string }
  | { type: "delta"; text: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean }
  | { type: "tokens"; count: number }
  | { type: "turn_done"; text: string; sessionId?: string }
  | { type: "loaded"; session: StoredSession }
  | { type: "error"; text: string };

type ToolCard = { name: string; args: Record<string, unknown>; ok?: boolean };
type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tools: ToolCard[]; streaming: boolean };

type ModelInfo = { id: string; name: string; provider: string; tier: string };
type SessionMeta = { id: string; title: string; model: string; created: string; count: number };
type StoredSession = { id: string; title?: string; model?: string; messages: { role: string; content: string }[] };

const BRIDGE: string = (window as any).deepcode?.bridgeUrl ?? "ws://127.0.0.1:8765/ws";
const API: string = (window as any).deepcode?.apiUrl ?? "http://127.0.0.1:8765";

const REASONING_LEVELS = ["off", "low", "middle", "high", "ultra"];

export default function App() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // toggles — Agent defaults OFF: tools (shell/file-write) require an explicit
  // opt-in. Read-only until the user turns Agent on.
  const [agent, setAgent] = useState(false);
  const [reasoning, setReasoning] = useState("off");

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // --- websocket (single socket; no StrictMode) ------------------------------
  useEffect(() => {
    let alive = true;
    let retry: ReturnType<typeof setTimeout>;
    const connect = () => {
      if (!alive) return;
      const ex = wsRef.current;
      if (ex && (ex.readyState === WebSocket.OPEN || ex.readyState === WebSocket.CONNECTING)) return;
      const ws = new WebSocket(BRIDGE);
      wsRef.current = ws;
      ws.onopen = () => alive && setConnected(true);
      ws.onclose = () => {
        if (!alive || wsRef.current !== ws) return;
        setConnected(false);
        retry = setTimeout(connect, 1000);
      };
      ws.onmessage = (ev) => {
        if (wsRef.current !== ws) return;
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

  const refreshSessions = useCallback(() => {
    fetch(`${API}/api/sessions`)
      .then((r) => r.json())
      .then((d) => setSessions(d.sessions ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`${API}/api/models`).then((r) => r.json()).then((d) => {
      setModels(d.models);
      setModel(d.default);
    }).catch(() => {});
    fetch(`${API}/api/config`).then((r) => r.json()).then((d) => {
      setAgent(d.agent ?? true);
      setReasoning(d.reasoning ?? "off");
    }).catch(() => {});
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking]);

  // --- event handling --------------------------------------------------------
  const handleEvent = useCallback((ev: WsEvent) => {
    if (ev.type === "loaded") {
      const s = ev.session;
      setActiveId(s.id);
      setMessages(
        (s.messages ?? [])
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) =>
            m.role === "user"
              ? { role: "user", text: m.content }
              : { role: "assistant", text: m.content, tools: [], streaming: false }
          )
      );
      setThinking(false);
      setBusy(false);
      return;
    }
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      const ensure = (): Msg & { role: "assistant" } => {
        if (last && last.role === "assistant" && last.streaming) return last as any;
        const fresh = { role: "assistant" as const, text: "", tools: [], streaming: true };
        next.push(fresh);
        return fresh;
      };
      switch (ev.type) {
        case "thinking":
        case "reasoning":
          setThinking(true);
          break;
        case "delta":
          ensure().text += ev.text;
          setThinking(false);
          break;
        case "tool_call":
          ensure().tools.push({ name: ev.name, args: ev.args });
          setThinking(false);
          break;
        case "tool_result": {
          const a = ensure();
          for (let i = a.tools.length - 1; i >= 0; i--)
            if (a.tools[i].name === ev.name && a.tools[i].ok === undefined) {
              a.tools[i].ok = ev.ok;
              break;
            }
          break;
        }
        case "turn_done": {
          const a = ensure();
          if (ev.text && !a.text) a.text = ev.text;
          a.streaming = false;
          setThinking(false);
          setBusy(false);
          if (ev.sessionId) setActiveId(ev.sessionId);
          setTimeout(refreshSessions, 200);
          break;
        }
        case "error": {
          const a = ensure();
          a.text += `\n\n[error] ${ev.text}`;
          a.streaming = false;
          setThinking(false);
          setBusy(false);
          break;
        }
      }
      return next;
    });
  }, [refreshSessions]);

  const send = () => {
    const text = input.trim();
    if (!text || !connected || busy) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setBusy(true);
    setThinking(true);
    wsRef.current?.send(JSON.stringify({
      type: "chat",
      text,
      opts: { model, agent, reasoning },
    }));
  };

  const newChat = () => {
    wsRef.current?.send(JSON.stringify({ type: "new" }));
    setMessages([]);
    setActiveId(null);
  };
  const loadChat = (id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "load", id }));
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="flex h-full bg-ink text-[15px] text-gray-200">
      {/* sidebar */}
      <aside className={`${sidebarOpen ? "w-64" : "w-0"} flex flex-col overflow-hidden border-r border-edge bg-[#0f0f13] transition-[width] duration-150`}>
        <div className="flex items-center gap-2 px-3 py-3">
          <Logo className="h-6 w-6 shrink-0" />
          <span className="font-semibold">DeepCode</span>
        </div>
        <button
          onClick={newChat}
          className="mx-3 mb-2 flex items-center justify-center gap-2 rounded-lg border border-edge py-2 text-sm text-gray-300 hover:border-accent/60 hover:text-white transition-colors"
        >
          <span className="text-accent">+</span> New chat
        </button>
        <div className="mt-1 flex-1 overflow-y-auto px-2">
          <div className="px-2 py-1 text-xs uppercase tracking-wide text-gray-600">Recent</div>
          {sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => loadChat(s.id)}
              className={`mb-0.5 w-full truncate rounded-lg px-2 py-1.5 text-left text-sm transition-colors ${
                s.id === activeId ? "bg-accent/15 text-white" : "text-gray-400 hover:bg-panel"
              }`}
              title={s.title}
            >
              {s.title}
            </button>
          ))}
          {sessions.length === 0 && (
            <div className="px-2 py-2 text-sm text-gray-600">No conversations yet.</div>
          )}
        </div>
      </aside>

      {/* main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-edge px-4 py-2.5">
          <button onClick={() => setSidebarOpen((v) => !v)} className="text-gray-500 hover:text-white" title="Toggle sidebar">
            ☰
          </button>
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
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          </div>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            {messages.length === 0 && (
              <div className="mt-24 flex flex-col items-center text-gray-500">
                <Logo className="h-16 w-16" />
                <div className="mt-4 text-lg font-medium text-gray-300">How can I help?</div>
                <div className="mt-1 text-sm">Ask anything, or @mention a file to attach it.</div>
                <div className="mt-6 max-w-md text-center text-xs text-gray-600">
                  Read-only until you enable <span className="text-gray-400">Agent</span>. With Agent on,
                  DeepCode can run commands and edit files — use at your own risk; you're responsible for what it does.
                </div>
              </div>
            )}
            {messages.map((m, i) => <MessageView key={i} msg={m} />)}
            {thinking && (
              <div className="flex items-center gap-2 text-gray-400"><Spinner /> Thinking…</div>
            )}
          </div>
        </div>

        {/* composer + toggles */}
        <footer className="px-4 pb-4">
          <div className="mx-auto max-w-3xl">
            <div className="rounded-2xl border border-edge bg-panel p-2 focus-within:border-accent/60 transition-colors">
              <div className="flex items-end gap-2">
                <span className="px-1 pb-1.5 font-mono text-accent">❯</span>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKey}
                  rows={1}
                  placeholder={connected ? "Message DeepCode…" : "Connecting to bridge…"}
                  className="max-h-48 flex-1 resize-none bg-transparent py-1.5 outline-none placeholder:text-gray-600"
                />
                <button
                  onClick={send}
                  disabled={!connected || busy || !input.trim()}
                  className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-30"
                >
                  Send
                </button>
              </div>
              {/* toggle chip row */}
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 px-1">
                <Chip active={agent} onClick={() => setAgent((v) => !v)} label={agent ? "⏵⏵ Agent" : "Agent off"} />
                <ReasoningChip level={reasoning} onChange={setReasoning} />
              </div>
            </div>
            <div className="mt-1 text-center text-xs text-gray-600">Enter to send · Shift+Enter for newline</div>
          </div>
        </footer>
      </div>
    </div>
  );
}

function Chip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
        active
          ? "border-accent/50 bg-accent/15 text-accent"
          : "border-edge text-gray-500 hover:text-gray-300"
      }`}
    >
      {label}
    </button>
  );
}

function ReasoningChip({ level, onChange }: { level: string; onChange: (l: string) => void }) {
  const active = level !== "off";
  return (
    <div className={`flex items-center rounded-full border text-xs ${active ? "border-accent/50 bg-accent/15" : "border-edge"}`}>
      <span className={`pl-2.5 pr-1 py-1 ${active ? "text-accent" : "text-gray-500"}`}>🧠</span>
      <select
        value={level}
        onChange={(e) => onChange(e.target.value)}
        className={`cursor-pointer rounded-r-full bg-transparent py-1 pr-2 outline-none ${active ? "text-accent" : "text-gray-500"}`}
      >
        {REASONING_LEVELS.map((l) => (
          <option key={l} value={l} className="bg-panel text-gray-200">
            {l === "off" ? "Reasoning off" : `Reasoning: ${l}`}
          </option>
        ))}
      </select>
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
      {msg.tools.map((t, i) => <ToolView key={i} tool={t} />)}
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
  const dot = tool.ok === undefined ? "text-gray-500" : tool.ok ? "text-green-500" : "text-red-500";
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
function Spinner() {
  return <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />;
}

// terminal "shell" window framing a >_ prompt — matches the TUI logo.
function Logo({ className = "" }: { className?: string }) {
  const c = "var(--accent, #d77757)";
  return (
    <svg viewBox="0 0 48 48" className={className} fill="none" aria-label="DeepCode">
      <rect x="3" y="6" width="42" height="36" rx="7" stroke={c} strokeWidth="2.5" />
      <line x1="3" y1="16" x2="45" y2="16" stroke={c} strokeWidth="2.5" />
      <circle cx="9.5" cy="11" r="1.4" fill={c} />
      <circle cx="15" cy="11" r="1.4" fill={c} />
      <circle cx="20.5" cy="11" r="1.4" fill={c} />
      <path d="M13 24 L20 29.5 L13 35" stroke={c} strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="24" y1="35" x2="35" y2="35" stroke={c} strokeWidth="2.8" strokeLinecap="round" />
    </svg>
  );
}
