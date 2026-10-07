// @vitest-environment jsdom
// App 外壳冒烟：真实挂载一次（路由 + 健康单例 + 会话模块全跑真），
// 拦"构建过但一挂载就炸"的运行时错（模块循环依赖/顶层副作用/模板绑定）。
// 注意：健康探测是异步状态机，用 waitFor 轮询等状态落定，不用一次性 flush。
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '@/App.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/', component: { template: '<div class="stub-view" />' } }],
})

describe('DataCrew App 外壳', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true })))
  })

  it('挂载即显示品牌、链路与健康灯（探测桩返回 ok → 已连接）', async () => {
    await router.push('/')
    const w = mount(App, { global: { plugins: [router] } })
    await router.isReady()
    await vi.waitFor(() => expect(w.text()).toContain('API 已连接'))
    expect(w.text()).toContain('DataCrew')
    expect(w.text()).toContain('问数工作台')
    expect(w.find('.stub-view').exists()).toBe(true)
  })

  it('探测失败（点灯重探）→ 显示不可达并给降级指引（不假装绿）', async () => {
    await router.push('/')
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('down'))))
    const w = mount(App, { global: { plugins: [router] } })
    await router.isReady()
    await w.find('.health').trigger('click') // 立即重探：不等 15s 周期
    await vi.waitFor(() => expect(w.text()).toContain('API 不可达'))
    expect(w.text()).toContain('docker unpause') // 降级指引跟着出现
  })
})
