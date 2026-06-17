/**
 * "Unlimited" provider — routes Claude Code's normal Anthropic SDK traffic
 * through the user's own Cloudflare Worker (unlimited-ai-proxy), which only
 * exposes a plain text-streaming `/api/chat` endpoint (no native tool-calling).
 *
 * Strategy: install a custom `fetch` on the Anthropic SDK client. That fetch
 * intercepts the normal `/v1/messages` request (standard Anthropic Messages
 * API body: system/messages/tools/stream), translates it into a single
 * ReAct-style text prompt (system + tool schemas + conversation history,
 * model emits `<tool>{"name":...,"args":{...}}</tool>` tags), sends it to the
 * worker, and re-encodes the worker's plain-text SSE stream back into real
 * Anthropic streaming events (message_start/content_block_*/message_delta/
 * message_stop), including synthetic `tool_use` blocks parsed from `<tool>`
 * tags. The rest of Claude Code (queryModel, tool execution loop, UI) is
 * untouched — it just sees a normal Anthropic stream.
 */

import { randomUUID } from 'crypto'

const WORKER_BASE = 'https://unlimited-ai-proxy.sportsmoments97.workers.dev'
const WORKER_HEADERS: Record<string, string> = {
  'Content-Type': 'application/json',
  Accept: '*/*',
  Origin: 'https://unlimited-ai-2jw.pages.dev',
  Referer: 'https://unlimited-ai-2jw.pages.dev/',
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
}

const TOOL_TAG_RE = /<tool>\s*(\{[\s\S]*?\})\s*<\/tool>/

export function isUnlimitedEnabled(): boolean {
  return !!process.env.DEEPCODE_UNLIMITED_MODEL
}

export function getUnlimitedModelId(): string {
  return process.env.DEEPCODE_UNLIMITED_MODEL || 'gateway-claude-opus-4-7'
}

// ---------------------------------------------------------------------------
// Request -> prompt translation
// ---------------------------------------------------------------------------

type AnthropicTextBlock = { type: 'text'; text: string }
type AnthropicToolUseBlock = {
  type: 'tool_use'
  id: string
  name: string
  input: unknown
}
type AnthropicToolResultBlock = {
  type: 'tool_result'
  tool_use_id: string
  content: unknown
  is_error?: boolean
}
type AnthropicContentBlock =
  | AnthropicTextBlock
  | AnthropicToolUseBlock
  | AnthropicToolResultBlock
  | { type: string; [key: string]: unknown }

type AnthropicMessage = {
  role: 'user' | 'assistant'
  content: string | AnthropicContentBlock[]
}

type AnthropicTool = {
  name: string
  description?: string
  input_schema?: unknown
}

type AnthropicRequestBody = {
  model?: string
  system?: string | { type: 'text'; text: string }[]
  messages?: AnthropicMessage[]
  tools?: AnthropicTool[]
  max_tokens?: number
  [key: string]: unknown
}

function systemToText(
  system: AnthropicRequestBody['system'],
): string {
  if (!system) return ''
  if (typeof system === 'string') return system
  return system.map(s => s.text).join('\n\n')
}

function blockToText(block: AnthropicContentBlock): string {
  switch (block.type) {
    case 'text':
      return (block as AnthropicTextBlock).text
    case 'tool_use': {
      const b = block as AnthropicToolUseBlock
      return `<tool>${JSON.stringify({ name: b.name, args: b.input })}</tool>`
    }
    case 'tool_result': {
      const b = block as AnthropicToolResultBlock
      const content =
        typeof b.content === 'string'
          ? b.content
          : Array.isArray(b.content)
            ? b.content
                .map(c =>
                  typeof c === 'object' && c && 'text' in c
                    ? (c as { text: string }).text
                    : JSON.stringify(c),
                )
                .join('\n')
            : JSON.stringify(b.content)
      return `Tool result: ${content}`
    }
    default:
      return ''
  }
}

function messageContentToText(content: AnthropicMessage['content']): string {
  if (typeof content === 'string') return content
  return content
    .map(blockToText)
    .filter(Boolean)
    .join('\n')
}

function toolsToText(tools: AnthropicTool[] | undefined): string {
  if (!tools || tools.length === 0) return ''
  const lines = tools.map(t => {
    const schema = t.input_schema
      ? JSON.stringify(t.input_schema)
      : '{}'
    return `- ${t.name}: ${t.description ?? ''}\n  input_schema: ${schema}`
  })
  return (
    '\n\n# Available tools\n' +
    'To call a tool, output a single tag in this exact format and nothing else in that turn after it:\n' +
    '<tool>{"name": "<tool_name>", "args": { ... }}</tool>\n\n' +
    'Tool definitions:\n' +
    lines.join('\n')
  )
}

function buildPrompt(body: AnthropicRequestBody): string {
  const parts: string[] = []
  const sys = systemToText(body.system)
  if (sys) parts.push(sys)
  parts.push(toolsToText(body.tools))

  parts.push(
    '\n\n# Conversation\n' +
      'Respond as the assistant. If you need to use a tool, emit exactly one ' +
      '<tool>{"name":...,"args":{...}}</tool> tag. Otherwise reply normally with no tags.',
  )

  for (const msg of body.messages ?? []) {
    const text = messageContentToText(msg.content)
    if (!text.trim()) continue
    const role = msg.role === 'user' ? 'User' : 'Assistant'
    parts.push(`\n\n${role}: ${text}`)
  }

  parts.push('\n\nAssistant:')
  return parts.join('')
}

// ---------------------------------------------------------------------------
// Worker stream -> Anthropic SSE translation
// ---------------------------------------------------------------------------

function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/**
 * Extracts the first complete `<tool>{...}</tool>` block from `text` using
 * brace counting (handles nested JSON). Returns the match span and parsed
 * object, or null if no complete tag is present yet.
 */
function extractToolCall(
  text: string,
): { start: number; end: number; name: string; args: unknown } | null {
  const tagStart = text.indexOf('<tool>')
  if (tagStart === -1) return null
  const braceStart = text.indexOf('{', tagStart)
  if (braceStart === -1) return null

  let depth = 0
  let inString = false
  let escape = false
  for (let i = braceStart; i < text.length; i++) {
    const ch = text[i]
    if (escape) {
      escape = false
      continue
    }
    if (ch === '\\' && inString) {
      escape = true
      continue
    }
    if (ch === '"') {
      inString = !inString
      continue
    }
    if (inString) continue
    if (ch === '{') depth++
    else if (ch === '}') {
      depth--
      if (depth === 0) {
        const jsonStr = text.slice(braceStart, i + 1)
        const closeTag = text.indexOf('</tool>', i + 1)
        const end = closeTag !== -1 ? closeTag + '</tool>'.length : i + 1
        try {
          const obj = JSON.parse(jsonStr) as { name?: string; args?: unknown }
          if (obj && typeof obj.name === 'string') {
            return { start: tagStart, end, name: obj.name, args: obj.args ?? {} }
          }
        } catch {
          // fall through — malformed JSON, give up on this tag
        }
        return null
      }
    }
  }
  return null
}

async function* streamWorkerDeltas(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<string> {
  const reader = response.body?.getReader()
  if (!reader) return
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let newlineIdx: number
      // eslint-disable-next-line no-cond-assign
      while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, newlineIdx).trim()
        buffer = buffer.slice(newlineIdx + 1)
        if (!line.startsWith('data: ')) continue
        const raw = line.slice('data: '.length).trim()
        if (!raw) continue
        try {
          const chunk = JSON.parse(raw) as {
            delta?: string
            done?: boolean
            error?: string
          }
          if (chunk.error) throw new Error(chunk.error)
          if (chunk.delta) yield chunk.delta
          if (chunk.done) return
        } catch {
          // ignore malformed lines
        }
      }
    }
  } finally {
    try {
      reader.cancel()
    } catch {
      // ignore
    }
  }
}

/**
 * Builds the SSE body stream for the synthetic Anthropic response. Consumes
 * the worker's text stream, emits text content_block deltas as they arrive,
 * and — once a complete `<tool>...</tool>` tag is seen — switches to a
 * tool_use content block fed via input_json_delta, then stops (Claude Code's
 * loop only needs one tool_use per turn from this provider).
 */
function buildAnthropicSSEStream(
  workerResponse: Response,
  model: string,
  signal?: AbortSignal,
): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  const messageId = `msg_unlimited_${randomUUID()}`

  return new ReadableStream<Uint8Array>({
    async start(controller) {
      const push = (s: string) => controller.enqueue(encoder.encode(s))

      push(
        sseEvent('message_start', {
          type: 'message_start',
          message: {
            id: messageId,
            type: 'message',
            role: 'assistant',
            model,
            content: [],
            stop_reason: null,
            stop_sequence: null,
            usage: {
              input_tokens: 0,
              output_tokens: 0,
              cache_creation_input_tokens: 0,
              cache_read_input_tokens: 0,
            },
          },
        }),
      )

      let fullText = ''
      let textBlockOpen = false
      let textEmitted = 0 // chars of fullText already emitted as text deltas
      let toolCall: ReturnType<typeof extractToolCall> | null = null
      let blockIndex = 0
      let stopReason: 'end_turn' | 'tool_use' = 'end_turn'

      try {
        for await (const delta of streamWorkerDeltas(workerResponse, signal)) {
          fullText += delta

          // Check whether a complete tool call has appeared.
          toolCall = extractToolCall(fullText)
          if (toolCall) break

          // Stream out text up to the start of a potential (incomplete) <tool> tag,
          // so we don't leak partial tags to the UI.
          const openTagIdx = fullText.indexOf('<tool>')
          const safeEnd = openTagIdx === -1 ? fullText.length : openTagIdx
          if (safeEnd > textEmitted) {
            if (!textBlockOpen) {
              push(
                sseEvent('content_block_start', {
                  type: 'content_block_start',
                  index: blockIndex,
                  content_block: { type: 'text', text: '' },
                }),
              )
              textBlockOpen = true
            }
            const piece = fullText.slice(textEmitted, safeEnd)
            push(
              sseEvent('content_block_delta', {
                type: 'content_block_delta',
                index: blockIndex,
                delta: { type: 'text_delta', text: piece },
              }),
            )
            textEmitted = safeEnd
          }
        }
      } catch (err) {
        push(
          sseEvent('error', {
            type: 'error',
            error: {
              type: 'api_error',
              message: err instanceof Error ? err.message : String(err),
            },
          }),
        )
        controller.close()
        return
      }

      if (textBlockOpen) {
        push(
          sseEvent('content_block_stop', {
            type: 'content_block_stop',
            index: blockIndex,
          }),
        )
        blockIndex++
      }

      if (toolCall) {
        stopReason = 'tool_use'
        const toolUseId = `toolu_unlimited_${randomUUID()}`
        push(
          sseEvent('content_block_start', {
            type: 'content_block_start',
            index: blockIndex,
            content_block: {
              type: 'tool_use',
              id: toolUseId,
              name: toolCall.name,
              input: {},
            },
          }),
        )
        push(
          sseEvent('content_block_delta', {
            type: 'content_block_delta',
            index: blockIndex,
            delta: {
              type: 'input_json_delta',
              partial_json: JSON.stringify(toolCall.args ?? {}),
            },
          }),
        )
        push(
          sseEvent('content_block_stop', {
            type: 'content_block_stop',
            index: blockIndex,
          }),
        )
      }

      push(
        sseEvent('message_delta', {
          type: 'message_delta',
          delta: { stop_reason: stopReason, stop_sequence: null },
          usage: { output_tokens: Math.ceil(fullText.length / 4) },
        }),
      )
      push(sseEvent('message_stop', { type: 'message_stop' }))
      controller.close()
    },
  })
}

// ---------------------------------------------------------------------------
// Public: fetch override
// ---------------------------------------------------------------------------

/**
 * Returns a `fetch`-compatible function that intercepts Anthropic SDK calls
 * to `/v1/messages` and serves them via the unlimited-ai worker. Any other
 * path is passed through to the real global fetch (used for things like the
 * SDK's API-key verification ping, which we just let fail/short-circuit).
 */
export function createUnlimitedFetch(): typeof fetch {
  const modelId = getUnlimitedModelId()

  return async function unlimitedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = input instanceof Request ? input.url : String(input)
    const isMessagesCall = url.includes('/v1/messages')

    if (!isMessagesCall) {
      // Nothing else should be called when this provider is active; return an
      // empty success response rather than hitting the real Anthropic API.
      return new Response('{}', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }

    const rawBody =
      input instanceof Request
        ? await input.clone().text()
        : typeof init?.body === 'string'
          ? init.body
          : ''
    const body = JSON.parse(rawBody || '{}') as AnthropicRequestBody

    const prompt = buildPrompt(body)
    const signal = (input instanceof Request ? input.signal : init?.signal) as
      | AbortSignal
      | undefined

    const workerResponse = await fetch(`${WORKER_BASE}/api/chat`, {
      method: 'POST',
      headers: WORKER_HEADERS,
      body: JSON.stringify({ message: prompt, model: modelId }),
      signal,
    })

    if (!workerResponse.ok || !workerResponse.body) {
      const errText = await workerResponse.text().catch(() => '')
      return new Response(
        JSON.stringify({
          type: 'error',
          error: {
            type: 'api_error',
            message: `unlimited-ai worker error ${workerResponse.status}: ${errText}`,
          },
        }),
        { status: workerResponse.status || 502, headers: { 'content-type': 'application/json' } },
      )
    }

    const sseStream = buildAnthropicSSEStream(
      workerResponse,
      body.model ?? modelId,
      signal,
    )

    return new Response(sseStream, {
      status: 200,
      headers: {
        'content-type': 'text/event-stream',
        'x-unlimited-provider': 'true',
      },
    })
  }
}
