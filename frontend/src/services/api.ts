// 后端 HTTP 客户端：ask / resume 的 SSE 流 + 带鉴权的文件下载。
// 鉴权走 X-API-Key 头（与 API 的 require_api_key 一致），key 运行时传入——
// 界面可直接改，不需要重新构建。图表经 fetch 拿 blob，因为 <img src> 带不了自定义头。
import { createSseParser, type SseFrame } from './sse'
import type { AskEvent } from '@/types/events'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

const BASE: string = import.meta.env.VITE_API_BASE ?? ''

function authHeaders(apiKey: string): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (apiKey) h['X-API-Key'] = apiKey
  return h
}

/** 通用的 POST SSE：把响应流转成结构化事件序列。 */
export async function* postSse(
  path: string,
  body: unknown,
  apiKey: string,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: authHeaders(apiKey),
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await safeText(res))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const queue: AskEvent[] = []
  let done = false
  let wake: (() => void) | null = null
  const notify = () => { if (wake) { const w2 = wake; wake = null; w2() } }

  const parser = createSseParser((frame: SseFrame) => {
    if (!frame.data) return
    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(frame.data)
    } catch {
      return // 非 JSON 帧（心跳/注释）忽略
    }
    // datacrew 帧带 event: 头；没有时退回 payload.event
    queue.push({ ...payload, event: frame.event ?? payload.event } as AskEvent)
    notify()
  })

  try {
    while (!done) {
      const { value, done: streamDone } = await reader.read()
      done = streamDone
      if (value) parser.feed(decoder.decode(value, { stream: true }))
      while (queue.length > 0) {
        yield queue.shift() as AskEvent
      }
      if (!done) {
        await new Promise<void>((resolve) => { wake = resolve })
      }
    }
    const tail = decoder.decode()
    if (tail) parser.feed(tail)
    while (queue.length > 0) {
      yield queue.shift() as AskEvent
    }
  } finally {
    reader.cancel().catch(() => undefined)
  }
}

export function askStream(
  question: string,
  sessionId: string,
  apiKey: string,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  return postSse('/ask', { question, session_id: sessionId }, apiKey, signal)
}

/** 恢复执行：澄清传字符串；审批传 {approved: boolean}。 */
export function resumeStream(
  sessionId: string,
  value: string | { approved: boolean },
  apiKey: string,
  signal?: AbortSignal,
): AsyncGenerator<AskEvent> {
  return postSse('/ask/resume', { session_id: sessionId, value }, apiKey, signal)
}

/** 图表下载（/files 需要 X-API-Key，<img> 做不到，只能 fetch + blob）。 */
export async function fetchFileObjectUrl(
  filePath: string,
  apiKey: string,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch(BASE + filePath, { headers: authHeaders(apiKey), signal })
  if (!res.ok) throw new ApiError(res.status, await safeText(res))
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text()
  } catch {
    return res.statusText
  }
}
