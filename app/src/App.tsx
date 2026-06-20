import { useEffect, useRef, useState, useCallback } from "react";
import ReactMarkdown from "react-markdown";

// --- bridge event protocol ----------------------------------------------------
type WsEvent =
  | { type: "thinking" }
  | { type: "reasoning"; level: string }
  | { type: "reasoning_stage"; stage: string; label: string; step: number; total_steps: number }
  | { type: "reasoning_delta"; stage: string; text: string }
  | { type: "delta"; text: string }
  | { type: "tool_call"; name: string; args: Record<string, unknown> }
  | { type: "tool_result"; name: string; ok: boolean }
  | { type: "permission_request"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tokens"; count: number }
  | { type: "turn_done"; text: string; sessionId?: string }
  | { type: "cancelled" }
  | { type: "loaded"; session: StoredSession }
  | { type: "error"; text: string }
  | UltraCodeStatus;

type UltraCodeAgent = { agent_id: string; name: string; status: string; action: string; tokens: number; tool_calls: number; elapsed: number };
type UltraCodeGroup = { id: string; role: string; goal: string; depends_on: string[]; status: string; agents: UltraCodeAgent[] };
type UltraCodeStatus = { type: "ultracode_status"; id: string; task: string; finished: boolean; failed: boolean; error: string; groups: UltraCodeGroup[] };

type PermissionRequest = { id: string; name: string; args: Record<string, unknown> };

type ToolCard = { name: string; args: Record<string, unknown>; ok?: boolean };
type Msg =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; tools: ToolCard[]; streaming: boolean };

type ModelInfo = { id: string; name: string; provider: string; tier: string };
type SessionMeta = { id: string; title: string; model: string; created: string; count: number; snippet?: string };
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
  const [thinkingLabel, setThinkingLabel] = useState("Thinking…");
  const [thinkingText, setThinkingText] = useState("");
  const [thinkingOpen, setThinkingOpen] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [model, setModel] = useState("");
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // toggles — Agent defaults OFF: tools (shell/file-write) require an explicit
  // opt-in. Read-only until the user turns Agent on.
  const [agent, setAgent] = useState(false);
  const [reasoning, setReasoning] = useState("off");
  const [permRequest, setPermRequest] = useState<PermissionRequest | null>(null);
  const permQueueRef = useRef<PermissionRequest[]>([]);
  const [projectDir, setProjectDir] = useState<string>("");

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SessionMeta[]>([]);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [cmdOpen, setCmdOpen] = useState(false);
  const [ultracode, setUltracode] = useState<UltraCodeStatus | null>(null);

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
    (window as any).deepcode?.getProjectDir?.().then((d: string) => d && setProjectDir(d)).catch(() => {});
  }, [refreshSessions]);

  const pickProject = async () => {
    const dir = await (window as any).deepcode?.pickProjectDir?.();
    if (dir) {
      setProjectDir(dir);
      // The bridge process gets restarted (main.js) against the new folder.
      // Its old socket connection drops; the websocket effect's
      // reconnect-on-close logic picks it back up once it's listening again.
      // Clear local state since the new bridge process has no in-memory
      // conversation for us yet (it's a brand-new process).
      setMessages([]);
      setActiveId(null);
      refreshSessions();
    }
  };

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, thinking]);

  // --- event handling --------------------------------------------------------
  const handleEvent = useCallback((ev: WsEvent) => {
    if (ev.type === "ultracode_status") {
      setUltracode(ev);
      setBusy(!ev.finished);
      setThinking(false);
      return;
    }
    if (ev.type === "permission_request") {
      // Queue rather than clobber: a turn can fire multiple tool calls before
      // the user answers the first prompt.
      permQueueRef.current.push({ id: ev.id, name: ev.name, args: ev.args });
      setPermRequest((cur) => cur ?? permQueueRef.current.shift() ?? null);
      return;
    }
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
          setThinking(true);
          break;
        case "reasoning":
          setThinking(true);
          setThinkingLabel(`Reasoning (${ev.level})…`);
          break;
        case "reasoning_stage":
          setThinking(true);
          setThinkingLabel(ev.label ? `${ev.label}… (${ev.step}/${ev.total_steps})` : "Thinking…");
          setThinkingText((t) => (t ? t + "\n\n" : "") + `── ${ev.label} ──\n`);
          break;
        case "reasoning_delta":
          setThinkingText((t) => t + ev.text);
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
          // Backend's cleaned text is authoritative — it has tool-call JSON
          // and quiz tags stripped out; streamed deltas may still contain
          // an in-progress tag's leading text that didn't get caught.
          if (ev.text) a.text = ev.text;
          a.streaming = false;
          setThinking(false);
          setBusy(false);
          if (ev.sessionId) setActiveId(ev.sessionId);
          setTimeout(refreshSessions, 200);
          break;
        }
        case "cancelled": {
          const a = ensure();
          a.streaming = false;
          setThinking(false);
          setBusy(false);
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
    if (text.startsWith("/ultracode ")) {
      runUltracode(text.slice("/ultracode ".length).trim());
      return;
    }
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setBusy(true);
    setThinking(true);
    setThinkingLabel("Thinking…");
    setThinkingText("");
    setThinkingOpen(false);
    setUltracode(null);
    wsRef.current?.send(JSON.stringify({
      type: "chat",
      text,
      opts: { model, agent, reasoning, interactive: true },
    }));
  };

  const runUltracode = (task: string) => {
    if (!task || !connected || busy) return;
    setMessages((m) => [...m, { role: "user", text: `/ultracode ${task}` }]);
    setInput("");
    setBusy(true);
    setThinking(false);
    setUltracode({ type: "ultracode_status", id: "", task, finished: false, failed: false, error: "", groups: [] });
    wsRef.current?.send(JSON.stringify({ type: "ultracode", text: task, opts: { model } }));
  };

  const stop = () => {
    wsRef.current?.send(JSON.stringify({ type: "stop" }));
  };

  const respondPermission = (decision: "allow" | "allow_always" | "deny" | "deny_always") => {
    if (!permRequest) return;
    wsRef.current?.send(JSON.stringify({
      type: "permission_response",
      id: permRequest.id,
      decision,
    }));
    setPermRequest(permQueueRef.current.shift() ?? null);
  };

  const newChat = () => {
    wsRef.current?.send(JSON.stringify({ type: "new" }));
    setMessages([]);
    setActiveId(null);
    setUltracode(null);
  };
  const loadChat = (id: string) => {
    wsRef.current?.send(JSON.stringify({ type: "load", id }));
    setUltracode(null);
    setSearchOpen(false);
  };

  const startRename = (s: SessionMeta) => {
    setRenamingId(s.id);
    setRenameValue(s.title);
  };
  const commitRename = async () => {
    if (!renamingId) return;
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title) return;
    await fetch(`${API}/api/sessions/${renamingId}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).catch(() => {});
    refreshSessions();
  };

  useEffect(() => {
    if (!searchOpen || !searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    const t = setTimeout(() => {
      fetch(`${API}/api/sessions/search?q=${encodeURIComponent(searchQuery.trim())}`)
        .then((r) => r.json())
        .then((d) => setSearchResults(d.sessions ?? []))
        .catch(() => {});
    }, 200);
    return () => clearTimeout(t);
  }, [searchOpen, searchQuery]);

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="relative flex h-full bg-ink text-[15px] text-gray-200">
      {permRequest && <PermissionDialog request={permRequest} onChoose={respondPermission} />}
      {searchOpen && (
        <SearchDialog
          query={searchQuery}
          onQuery={setSearchQuery}
          results={searchResults}
          onPick={loadChat}
          onClose={() => { setSearchOpen(false); setSearchQuery(""); }}
        />
      )}
      {/* sidebar */}
      <aside className={`${sidebarOpen ? "w-64" : "w-0"} flex flex-col overflow-hidden border-r border-edge bg-[#0f0f13] transition-[width] duration-150`}>
        <div className="flex items-center gap-2 px-3 py-3">
          <Logo className="h-6 w-6 shrink-0" />
          <span className="font-semibold">DeepCode</span>
        </div>
        <div className="mx-3 mb-2 flex gap-1.5">
          <button
            onClick={newChat}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg border border-edge py-2 text-sm text-gray-300 hover:border-accent/60 hover:text-white transition-colors"
          >
            <span className="text-accent">+</span> New chat
          </button>
          <button
            onClick={() => setSearchOpen(true)}
            title="Search chats"
            className="flex items-center justify-center rounded-lg border border-edge px-2.5 text-gray-400 hover:border-accent/60 hover:text-white transition-colors"
          >
            🔍
          </button>
        </div>
        <div className="mt-1 flex-1 overflow-y-auto px-2">
          <div className="px-2 py-1 text-xs uppercase tracking-wide text-gray-600">Recent</div>
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`group mb-0.5 flex items-center rounded-lg px-2 py-1.5 text-sm transition-colors ${
                s.id === activeId ? "bg-accent/15 text-white" : "text-gray-400 hover:bg-panel"
              }`}
            >
              {renamingId === s.id ? (
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                  className="w-full bg-transparent outline-none border-b border-accent/50"
                />
              ) : (
                <>
                  <button
                    onClick={() => loadChat(s.id)}
                    onDoubleClick={() => startRename(s)}
                    className="flex-1 truncate text-left"
                    title={s.title}
                  >
                    {s.title}
                  </button>
                  <button
                    onClick={() => startRename(s)}
                    title="Rename"
                    className="ml-1 shrink-0 opacity-0 group-hover:opacity-100 text-gray-500 hover:text-white"
                  >
                    ✎
                  </button>
                </>
              )}
            </div>
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
          <button
            onClick={pickProject}
            title="Choose which folder the agent operates on"
            className="flex items-center gap-1.5 truncate rounded-lg border border-edge px-2.5 py-1 text-sm text-gray-400 hover:border-accent/60 hover:text-gray-200 transition-colors max-w-xs"
          >
            <span className="text-accent">📁</span>
            <span className="truncate">{projectDir || "Choose project folder…"}</span>
          </button>
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
              <div className="flex flex-col gap-2">
                <button
                  onClick={() => setThinkingOpen((v) => !v)}
                  disabled={!thinkingText}
                  className="flex items-center gap-2 text-gray-400 hover:text-gray-200 disabled:cursor-default disabled:hover:text-gray-400"
                  title={thinkingText ? "Show/hide reasoning" : undefined}
                >
                  <Spinner /> {thinkingLabel}
                  {thinkingText && <span className="text-xs text-gray-600">{thinkingOpen ? "▲" : "▼"}</span>}
                </button>
                {thinkingOpen && thinkingText && (
                  <div className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-edge bg-panel/60 px-3 py-2 font-mono text-xs text-gray-500">
                    {thinkingText}
                  </div>
                )}
              </div>
            )}
            {ultracode && <UltraCodePanel status={ultracode} />}
          </div>
        </div>

        {/* composer + toggles */}
        <footer className="relative px-4 pb-4">
          <div className="mx-auto max-w-3xl">
            {cmdOpen && (
              <CommandPalette
                onClose={() => setCmdOpen(false)}
                onUltracode={() => {
                  setCmdOpen(false);
                  setInput("/ultracode ");
                }}
                onNewChat={() => { setCmdOpen(false); newChat(); }}
                onToggleAgent={() => { setCmdOpen(false); setAgent((v) => !v); }}
                onReasoning={(l) => { setCmdOpen(false); setReasoning(l); }}
                agent={agent}
              />
            )}
            <div className="rounded-2xl border border-edge bg-panel p-2 focus-within:border-accent/60 transition-colors">
              <div className="flex items-end gap-2">
                <button
                  onClick={() => setCmdOpen((v) => !v)}
                  title="Commands"
                  className="rounded-lg px-1.5 pb-1.5 text-lg text-gray-500 hover:text-accent transition-colors"
                >
                  +
                </button>
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKey}
                  rows={1}
                  placeholder={connected ? "Message DeepCode…" : "Connecting to bridge…"}
                  className="max-h-48 flex-1 resize-none bg-transparent py-1.5 outline-none placeholder:text-gray-600"
                />
                {busy ? (
                  <button
                    onClick={stop}
                    className="rounded-lg border border-red-500/50 bg-red-500/15 px-3 py-1.5 text-sm font-medium text-red-400 transition-opacity hover:opacity-90"
                    title="Stop the current turn"
                  >
                    ◾ Stop
                  </button>
                ) : (
                  <button
                    onClick={send}
                    disabled={!connected || !input.trim()}
                    className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-30"
                  >
                    Send
                  </button>
                )}
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
        <div className="markdown leading-relaxed text-gray-200">
          <ReactMarkdown>{msg.text}</ReactMarkdown>
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

function CommandPalette({
  onClose,
  onUltracode,
  onNewChat,
  onToggleAgent,
  onReasoning,
  agent,
}: {
  onClose: () => void;
  onUltracode: () => void;
  onNewChat: () => void;
  onToggleAgent: () => void;
  onReasoning: (level: string) => void;
  agent: boolean;
}) {
  return (
    <div className="absolute inset-0 z-40" onClick={onClose}>
      <div
        className="absolute bottom-full left-0 mb-2 w-64 rounded-xl border border-edge bg-panel p-1.5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button onClick={onUltracode} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-gray-300 hover:bg-ink/60">
          <span className="text-accent">⟁</span> UltraCode swarm…
        </button>
        <button onClick={onToggleAgent} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-gray-300 hover:bg-ink/60">
          <span className="text-accent">⏵⏵</span> {agent ? "Turn Agent off" : "Turn Agent on"}
        </button>
        <div className="px-2.5 pt-1.5 pb-1 text-xs uppercase tracking-wide text-gray-600">Reasoning</div>
        {REASONING_LEVELS.map((l) => (
          <button
            key={l}
            onClick={() => onReasoning(l)}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm text-gray-300 hover:bg-ink/60"
          >
            🧠 {l === "off" ? "Off" : l}
          </button>
        ))}
        <div className="my-1 border-t border-edge" />
        <button onClick={onNewChat} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-gray-300 hover:bg-ink/60">
          <span className="text-accent">+</span> New chat
        </button>
      </div>
    </div>
  );
}

function UltraCodePanel({ status }: { status: UltraCodeStatus }) {
  const statusDot = (s: string) =>
    s === "done" ? "text-green-500" : s === "failed" ? "text-red-500" : s === "running" ? "text-accent animate-pulse" : "text-gray-600";
  return (
    <div className="rounded-xl border border-accent/30 bg-panel/60 p-3">
      <div className="flex items-center gap-2 text-sm">
        {!status.finished && <Spinner />}
        <span className="font-medium text-accent">UltraCode</span>
        <span className="truncate text-gray-400">{status.task}</span>
        {status.finished && !status.failed && <span className="text-xs text-green-500">done</span>}
        {status.failed && <span className="text-xs text-red-500">{status.error || "failed"}</span>}
      </div>
      {status.groups.length === 0 && !status.finished && (
        <div className="mt-2 text-xs text-gray-500">Leader is planning the swarm…</div>
      )}
      <div className="mt-2 flex flex-col gap-1.5">
        {status.groups.map((g) => (
          <div key={g.id} className="rounded-lg border border-edge bg-ink/40 px-2.5 py-1.5">
            <div className="flex items-center gap-2 text-xs">
              <span className={statusDot(g.status)}>●</span>
              <span className="font-medium text-gray-300">{g.role || g.id}</span>
              <span className="text-gray-600">{g.status}</span>
            </div>
            {g.agents.length > 0 && (
              <div className="mt-1 flex flex-col gap-0.5 pl-4">
                {g.agents.map((a) => (
                  <div key={a.agent_id} className="flex items-center gap-1.5 truncate text-xs text-gray-500">
                    <span className={statusDot(a.status)}>●</span>
                    <span className="text-gray-400">{a.agent_id}</span>
                    <span className="truncate">{a.action}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SearchDialog({
  query,
  onQuery,
  results,
  onPick,
  onClose,
}: {
  query: string;
  onQuery: (q: string) => void;
  results: SessionMeta[];
  onPick: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <div className="absolute inset-0 z-50 flex items-start justify-center bg-black/60 pt-24" onClick={onClose}>
      <div
        className="w-full max-w-lg rounded-2xl border border-edge bg-panel p-3 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          autoFocus
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && onClose()}
          placeholder="Search chats…"
          className="w-full rounded-lg border border-edge bg-ink/60 px-3 py-2 text-sm outline-none focus:border-accent/60"
        />
        <div className="mt-2 max-h-80 overflow-y-auto">
          {query.trim() && results.length === 0 && (
            <div className="px-2 py-3 text-sm text-gray-600">No matches.</div>
          )}
          {results.map((s) => (
            <button
              key={s.id}
              onClick={() => onPick(s.id)}
              className="w-full rounded-lg px-2.5 py-2 text-left text-sm text-gray-300 hover:bg-ink/60"
            >
              <div className="truncate font-medium">{s.title}</div>
              {s.snippet && <div className="truncate text-xs text-gray-500">…{s.snippet}…</div>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function PermissionDialog({
  request,
  onChoose,
}: {
  request: PermissionRequest;
  onChoose: (decision: "allow" | "allow_always" | "deny" | "deny_always") => void;
}) {
  const arg =
    (request.args?.path as string) ??
    (request.args?.command as string) ??
    (request.args?.pattern as string) ??
    (request.args?.url as string) ??
    JSON.stringify(request.args ?? {}).slice(0, 200);
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-full max-w-md rounded-2xl border border-accent/40 bg-panel p-5 shadow-xl">
        <div className="mb-1 flex items-center gap-2 text-accent">
          <span>●</span>
          <span className="font-semibold">Permission requested</span>
        </div>
        <div className="mb-1 font-mono text-sm text-gray-200">{request.name}</div>
        <div className="mb-4 break-all rounded-lg bg-ink/60 px-3 py-2 font-mono text-xs text-gray-400">{arg}</div>
        <div className="grid grid-cols-2 gap-2">
          <button onClick={() => onChoose("allow")} className="rounded-lg bg-accent px-3 py-2 text-sm font-medium text-black hover:opacity-90">
            Allow once
          </button>
          <button onClick={() => onChoose("allow_always")} className="rounded-lg border border-accent/50 px-3 py-2 text-sm text-accent hover:bg-accent/10">
            Allow always
          </button>
          <button onClick={() => onChoose("deny")} className="rounded-lg border border-edge px-3 py-2 text-sm text-gray-300 hover:border-gray-500">
            Deny once
          </button>
          <button onClick={() => onChoose("deny_always")} className="rounded-lg border border-edge px-3 py-2 text-sm text-gray-300 hover:border-gray-500">
            Deny always
          </button>
        </div>
      </div>
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
