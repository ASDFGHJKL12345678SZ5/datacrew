// @vitest-environment jsdom
// 组件测试：只测"展示契约"——组件的渲染规则是产品行为的一部分
// （时间线的重试徽标、Markdown 消毒、结果卡标签页），值得单测锁死。
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import EventTimeline from '@/components/EventTimeline.vue'
import MarkdownBlock from '@/components/MarkdownBlock.vue'
import ResultCard from '@/components/ResultCard.vue'
import type { ResultEvent } from '@/types/events'

const baseResult: ResultEvent = {
  event: 'result',
  summary: '实付销售额 **120 万**。',
  sql: 'SELECT channel, SUM(pay_amount) FROM biz.orders GROUP BY channel',
  columns: ['channel', 'pay_amount'],
  rows: [['app', 700000], ['mini', 500000]],
  row_count: 2,
  chart_url: '/files/charts/x.svg',
}

describe('EventTimeline', () => {
  it('空时间线不渲染（没有节点事件时不该占位）', () => {
    const w = mount(EventTimeline, { props: { entries: [] } })
    expect(w.find('.timeline').exists()).toBe(false)
  })

  it('渲染节点中文名 + 延迟 + 合计', () => {
    const w = mount(EventTimeline, {
      props: {
        entries: [
          { node: 'schema_curator', latency_ms: 12 },
          { node: 'sql_generator', latency_ms: 340 },
        ],
      },
    })
    expect(w.text()).toContain('Schema 检索')
    expect(w.text()).toContain('12ms')
    expect(w.text()).toContain('340ms')
    expect(w.text()).toContain('合计 352ms')
  })

  it('同节点第二次出现打 ×2 重试徽标（自愈可见）', () => {
    const w = mount(EventTimeline, {
      props: {
        entries: [
          { node: 'executor', latency_ms: 100 },
          { node: 'executor', latency_ms: 90 },
        ],
      },
    })
    const retry = w.findAll('.tl-retry')
    expect(retry).toHaveLength(1)
    expect(retry[0].text()).toBe('×2')
    expect(w.text()).toContain('SQL 执行')
  })

  it('detail 扁平化为 chip（含嵌套对象 JSON 摘要）', () => {
    const w = mount(EventTimeline, {
      props: { entries: [{ node: 'executor', detail: { n: 1000, meta: { retries: 1 } } }] },
    })
    expect(w.text()).toContain('n')
    expect(w.text()).toContain('1000')
    expect(w.text()).toContain('{"retries":1}')
  })
})

describe('MarkdownBlock（LLM 输出按不可信内容处理）', () => {
  it('渲染 Markdown 表格/加粗', () => {
    const w = mount(MarkdownBlock, { props: { content: '| a | b |\n|---|---|\n| 1 | 2 |' } })
    expect(w.find('table').exists()).toBe(true)
  })

  it('消毒脚本与事件属性（XSS 不执行）', () => {
    const w = mount(MarkdownBlock, {
      props: {
        content: '<img src=x onerror="window.__xss=1">\n\n<script>window.__xss2=1</script>',
      },
    })
    const html = w.html()
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('<script')
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined()
  })
})

describe('ResultCard', () => {
  const mountCard = (patch: Partial<ResultEvent> = {}) =>
    mount(ResultCard, { props: { result: { ...baseResult, ...patch }, apiKey: 'dev-key-001' } })

  it('默认落在结论页，展示统计', () => {
    const w = mountCard()
    expect(w.text()).toContain('查询完成')
    expect(w.text()).toContain('120 万')
    expect(w.find('.markdown').exists()).toBe(true)
  })

  it('图表 Tab 仅在 chart_url 存在时出现', () => {
    expect(mountCard().text()).toContain('图表')
    const noChart = mountCard({ chart_url: null })
    expect(noChart.text()).not.toContain('图表')
  })

  it('点击 Tab 切换标签页', async () => {
    const w = mountCard()
    const tabs = w.findAll('.tab')
    await tabs[2].trigger('click') // 数据
    expect(w.find('table').exists()).toBe(true)
    expect(w.text()).toContain('#') // 行号列
    await tabs[1].trigger('click') // SQL
    expect(w.find('pre').text()).toContain('SELECT')
  })
})
