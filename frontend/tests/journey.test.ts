// @vitest-environment jsdom
// UI 端到端旅程：AskView 把事件渲染成正确的界面。
// 与 tests/api.stream.test.ts 的分工：那个锁"流式管道收敛"，这个锁"AskView 编排"。
// 两块回归：
//   ① 澄清卡 → 人类回答 → 结果卡，全程无错误横幅（30s 看门狗不得误报）；
//   ② 页面在后端不可达时打开 → 后端恢复 → 横幅消失、提问解锁
//      （曾经的坑：isDown 是普通 getter，解构即固化，"后端恢复了横幅永不消"）。
// 用手工 reader 桩：jsdom 的 Response 不支持流构造，必须绕开。
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AskView from '@/views/AskView.vue'

// ---- 后端真实帧序（event: + data: JSON，与 app/api/main.py 一致）----
function frame(event: string, payload = ''): string {
  return `event: ${event}\ndata: {"event":"${event}"${payload ? ',' + payload : ''}}\n\n`
}
const ASK_CHUNKS = [
  frame('clarification', '"question":"您说的销售额是指 GMV 还是实付销售额？","options":["GMV","实付销售额"]'),
]
const RESUME_CHUNKS = [
  frame('node_done', '"node":"schema_curator","latency_ms":12'),
  frame('node_done', '"node":"sql_generator","latency_ms":8') + frame('node_done', '"node":"executor","latency_ms":20'),
  frame('result', '"summary":"app 渠道占比最高。","sql":"SELECT 1","columns":["c"],"rows":[["1"]],"row_count":1'),
]

// 可控后端：健康灯可开关；/ask 与 /ask/resume 按块交付真实帧
function stubBackend(opts: { healthOk: () => boolean }) {
  const enc = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const u = String(url)
    if (u.endsWith('/health')) return { ok: opts.healthOk() }
    const chunks = u.endsWith('/ask/resume') ? RESUME_CHUNKS : ASK_CHUNKS
    let i = 0
    return {
      ok: true,
      body: {
        getReader: () => ({
          read: async () => {
            if (i < chunks.length) {
              const value = enc.encode(chunks[i++])
              await new Promise((r) => setTimeout(r, 2))
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

describe('AskView 旅程', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('回归：页面在后端不可达时打开，后端恢复后横幅消失、提问解锁', async () => {
    let healthOk = false
    stubBackend({ healthOk: () => healthOk })
    const w = mount(AskView)
    await flushPromises()
    expect(w.text()).toContain('后端不可达') // 探测中即视为不可达：门控先行

    await w.find('.health-banner button').trigger('click') // 立即重试（仍失败）
    await flushPromises()

    healthOk = true
    await w.find('.health-banner button').trigger('click')
    await vi.waitFor(() => expect(w.text()).not.toContain('后端不可达'))
  })

  it('提问出澄清卡；点选项回答后出结果卡，全程无错误横幅', async () => {
    stubBackend({ healthOk: () => true })
    const w = mount(AskView)
    await flushPromises()

    await w.find('textarea').setValue('上个月各渠道实付销售额')
    await w.find('textarea').trigger('keydown.enter')
    await vi.waitFor(() => expect(w.text()).toContain('GMV'))
    expect(w.find('.clarify').exists()).toBe(true)

    const opts = w.findAll('.clarify .options button')
    expect(opts.length).toBeGreaterThan(0)
    await opts[0].trigger('click')

    await vi.waitFor(() => expect(w.text()).toContain('查询完成'))
    expect(w.text()).not.toContain('无新事件')
    expect(w.text()).not.toContain('连接中断')
  })
})
