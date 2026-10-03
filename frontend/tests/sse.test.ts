import { describe, expect, it } from 'vitest'
import { createSseParser, parseFrame, type SseFrame } from '@/services/sse'

function collect(chunks: string[]): SseFrame[] {
  const frames: SseFrame[] = []
  const parser = createSseParser((f) => frames.push(f))
  for (const c of chunks) parser.feed(c)
  return frames
}

describe('parseFrame', () => {
  it('解析 event + data 双行帧', () => {
    expect(parseFrame('event: node_done\ndata: {"node":"executor"}')).toEqual({
      event: 'node_done',
      data: '{"node":"executor"}',
    })
  })

  it('data 多行按 \n 拼接（SSE 规范）', () => {
    expect(parseFrame('data: {"a":1,\ndata: "b":2}')?.data).toBe('{"a":1,\n"b":2}')
  })

  it('忽略注释行与空行，无内容返回 null', () => {
    expect(parseFrame(': heartbeat')).toBeNull()
    expect(parseFrame('')).toBeNull()
  })
})

describe('createSseParser（网络切帧鲁棒性）', () => {
  it('整帧一次喂入', () => {
    const frames = collect(['event: node_done\ndata: {"node":"sql_generator"}\n\n'])
    expect(frames).toHaveLength(1)
    expect(frames[0].event).toBe('node_done')
  })

  it('帧被从中间切断：TCP 边界 ≠ 帧边界', () => {
    const frames = collect(['event: node_do', 'ne\ndata: {"n', 'ode":"executor"}\n\n'])
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ node: 'executor' })
  })

  it('一个 chunk 里粘多个帧', () => {
    const frames = collect([
      'event: a\ndata: {"i":1}\n\nevent: b\ndata: {"i":2}\n\nevent: c\ndata: {"i":3}\n\n',
    ])
    expect(frames.map((f) => f.event)).toEqual(['a', 'b', 'c'])
  })

  it('CRLF 换行（不同服务器/代理可能发 \r\n）', () => {
    const frames = collect(['event: x\r\ndata: {"ok":true}\r\n\r\n'])
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ ok: true })
  })

  it('不完整尾巴不出帧，补齐后才出', () => {
    const frames: SseFrame[] = []
    const parser = createSseParser((f) => frames.push(f))
    parser.feed('event: x\ndata: {"a":')
    expect(frames).toHaveLength(0)
    parser.feed('1}\n\n')
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data)).toEqual({ a: 1 })
  })
})
