// SSE 帧解析器（纯函数，零依赖）。职责单一：把任意切片的字节流转成完整帧。
// 关键性质：网络 chunk 边界与帧边界无关——一个 TCP 包里可能有半帧，一个帧也可能
// 跨多个包。解析器内部缓冲残片，只吐完整帧。tests/sse.test.ts 覆盖粘包/断帧场景。

export interface SseFrame {
  event?: string
  data: string
}

export function parseFrame(raw: string): SseFrame | null {
  let event: string | undefined
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
    else if (line.startsWith(':') || line === '') continue // 注释/心跳行
  }
  if (event === undefined && dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

export interface SseParser {
  feed(text: string): void
  reset(): void
}

export function createSseParser(onFrame: (frame: SseFrame) => void): SseParser {
  let buf = ''
  return {
    feed(text: string): void {
      buf += text.replace(/\r\n/g, '\n')
      let idx = buf.indexOf('\n\n')
      while (idx >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        const frame = parseFrame(raw)
        if (frame) onFrame(frame)
        idx = buf.indexOf('\n\n')
      }
    },
    reset(): void {
      buf = ''
    },
  }
}
