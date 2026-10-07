// postSse 流式客户端集成测试（node 环境：桩 fetch 用手工 reader，不依赖 jsdom 的 Response）。
// 核心回归：多分片 SSE 流下生成器必须收敛——2026-10-03 线上事故的根因是
// wake/notify 跨块信号量死锁：第一个块 yield 完后生成器永不退出，run() 的 for-await
// 挂住 → 轮次永不 settle → 30s 看门狗把已显示的结果误报成"无新事件"错误。
import { afterEach, describe, expect, it, vi } from 'vitest'
import { askStream, resumeStream } from '@/services/api'

// 把 SSE 文本按给定边界切成多块交付，块间留间隔——复刻真实 TCP 分片
function stubChunked(chunks: string[], gapMs = 2) {
  vi.stubGlobal('fetch', vi.fn(async () => {
    const enc = new TextEncoder()
    let i = 0
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => {
            if (i < chunks.length) {
              const value = enc.encode(chunks[i++])
              if (gapMs > 0) await new Promise((r) => setTimeout(r, gapMs))
              return { done: false, value }
            }
            return { done: true, value: undefined }
          },
          cancel: async () => undefined,
        }),
      },
    } as unknown as Response
  }))
}

async function collect<T>(gen: AsyncGenerator<T>, timeoutMs = 2000): Promise<T[]> {
  const out: T[] = []
  await Promise.race([
    (async () => {
      for await (const ev of gen) out.push(ev)
    })(),
    new Promise((_, rej) => setTimeout(() => rej(new Error('生成器未收敛（死锁回归！）')), timeoutMs)),
  ])
  return out
}

function frames(...parts: string[]): string {
  return parts.map((p) => `event: ${p}\ndata: {"event":"${p}"}\n\n`).join('')
}

describe('postSse 多分片流', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('回归：首块之后仍有数据（澄清→resume 终帧跨块）——必须全程收敛', async () => {
    stubChunked([
      frames('clarification'),            // 第 1 块：只有澄清（旧代码在这里死锁）
      frames('node_done', 'node_done'),   // 第 2 块：resume 的节点进度
      frames('result'),                   // 第 3 块：终态
    ])
    const evs = await collect(askStream('销售额是多少', 's1', 'dev-key-001'))
    expect(evs.map((e) => e.event)).toEqual(['clarification', 'node_done', 'node_done', 'result'])
  })

  it('一帧被拆在两个块中间（增量解析）', async () => {
    stubChunked([
      'event: clarifi', 'cation\ndata: {"event":"clarification"}\n',
      '\nevent: result\ndata: {"event":"result"}\n\n',
    ])
    const evs = await collect(askStream('q', 's1', 'k'))
    expect(evs.map((e) => e.event)).toEqual(['clarification', 'result'])
  })

  it('心跳/注释帧与非 JSON 帧被忽略，不影响收敛', async () => {
    stubChunked([': ping\n\n', 'event: node_done\ndata: not-json\n\n', frames('result')])
    const evs = await collect(askStream('q', 's1', 'k'))
    expect(evs.map((e) => e.event)).toEqual(['result'])
  })

  it('流以半帧结束：不产出假事件，生成器仍然收敛', async () => {
    stubChunked([frames('result'), 'event: node_done\ndata: {"event":"node_do'])
    const evs = await collect(askStream('q', 's1', 'k'))
    expect(evs.map((e) => e.event)).toEqual(['result'])
  })

  it('resume 流（/ask/resume）同样收敛', async () => {
    stubChunked([frames('node_done'), frames('node_done', 'result')])
    const evs = await collect(resumeStream('s1', '实付销售额', 'k'))
    expect(evs.map((e) => e.event)).toEqual(['node_done', 'node_done', 'result'])
  })

  it('单块内含多帧：同轮全部 yield', async () => {
    stubChunked([frames('node_done', 'node_done', 'node_done', 'result')])
    const evs = await collect(askStream('q', 's1', 'k'))
    expect(evs.map((e) => e.event)).toEqual(['node_done', 'node_done', 'node_done', 'result'])
  })
})
