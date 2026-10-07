// 后端 HTTP 客户端：ask / resume 的 SSE 流 + 带鉴权的文件下载。
// 鉴权走 X-API-Key 头（与 API 的 require_api_key 一致），key 运行时传入——
// 界面可直接改（localStorage 持久化），不需要重新构建。
// 图表经 fetch 拿 blob，因为 <img src> 带不了自定义头。
import { createSseParser, type SseFrame } from './sse'
import type { AskEvent } from '@/types/events'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** API 基址：dev 留空走 Vite proxy；生产由 VITE_API_BASE 指向网关。
 *  导出它是为了让健康探测（useHealth）打到同一个后端，别各写各的。 */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

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
  const res = await fetch(API_BASE + path, {
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
  })

  // 朴素 read 循环：解析器是同步的，一个块里的多帧在同一轮 yield 完，块间靠 read() 天然同步。
  // 曾经的坑（线上事故，2026-10-03 实机复现）：初版用 wake/notify 做"跨块信号量"——
  // notify 只在 parser.feed 时触发，feed 只在 reader.read 之后发生，而循环却在等 wake
  // 才肯继续 read：第一个块的事件 yield 完后死锁，生成器永不收敛 → run() 的 for-await
  // 永不退出 → 轮次永不 settle → 30s 无事件看门狗把已经显示的结果误报成错误横幅。
  // 回归测试见 tests/api.stream.test.ts（多分片流 + 生成器必须收敛 + 迟到尾帧）。
  try {
    while (true) {
      const { value, done: streamDone } = await reader.read()
      if (value) parser.feed(decoder.decode(value, { stream: true }))
      while (queue.length > 0) {
        yield queue.shift() as AskEvent
      }
      if (streamDone) break
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
  const res = await fetch(API_BASE + filePath, { headers: authHeaders(apiKey), signal })
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
