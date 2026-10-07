import { computed, reactive, type ComputedRef } from 'vue'
import { createHealthMonitor, type HealthMonitor, type HealthState } from '@/services/health'
import { API_BASE } from '@/services/api'

// ===== App 级健康探测：全局唯一实例 =====
// 事故背景：App 外壳曾自带一套 setInterval 打 /health，useAsk 又持一套状态机
// 探测——两套探测互不知情，"灯绿着门关着 / 门开着灯红"都出现过。侧栏状态灯
// 与提问门控必须是同一个事实源，所以收敛到这一个模块级单例。
//
// 响应式桥接：纯模块（health.ts）按原始引用改对象，直接 reactive(state) 不触发
// （代理 setter 不拦原生赋值——实测踩坑，回归见 tests/health.reactive.test.ts）。
// 正解：单例创建时一次性挂 onStateChange → Object.assign 到 Vue 自持的镜像。

const HEALTH_INTERVAL_MS = 15_000
const HEALTH_TIMEOUT_MS = 6_000

interface Singleton {
  monitor: HealthMonitor
  mirror: HealthState // Vue 侧自持的响应式镜像
}

let singleton: Singleton | null = null

function probe(signal: AbortSignal): Promise<boolean> {
  // 经 API_BASE：生产模式 VITE_API_BASE 指向网关时，探测要打到同一个后端
  return fetch(API_BASE + '/health', { signal })
    .then((r) => r.ok)
    .catch(() => false)
}

function ensureSingleton(): Singleton {
  if (singleton) return singleton
  // 必须先包 reactive 再让 onStateChange 闭包持有 proxy——
  // 若闭包持有原始对象，Object.assign 走原生赋值，代理 setter 不触发，
  // 界面永远停在"检测中"（health.reactive.test.ts 锁死的同款坑，别踩第二次）
  const mirror = reactive<HealthState>({ status: 'probing', since: Date.now(), fails: 0 })
  const monitor = createHealthMonitor({
    probe,
    intervalMs: HEALTH_INTERVAL_MS,
    probeTimeoutMs: HEALTH_TIMEOUT_MS,
    onStateChange: (s) => Object.assign(mirror, s), // 只桥接一次，别迟到
  })
  monitor.start() // 首个消费者出现即开始探测，之后 15s 周期由状态机自持
  singleton = { monitor, mirror }
  return singleton
}

export interface UseHealth {
  /** 只读响应式状态：probing / ok / unreachable */
  state: Readonly<HealthState>
  /** 后端不可达（含探测中）——提问门控用这个，不要各自判断。
   *  computed Ref：解构传递的是 Ref 本体、模板自动解包，保持响应式。
   *  曾经的坑（实机复现）：这里写普通 getter，被 const { isDown } 解构的那一刻
   *  值就固化了（status 从 probing→ok 也不再变），表现为"后端恢复了但报错
   *  横幅永不消、提问按钮永远禁用"。判定必须来自响应式源。 */
  isDown: ComputedRef<boolean>
  /** 立即探测一次（如用户手动重试） */
  probeNow: () => Promise<void>
}

export function useHealth(): UseHealth {
  const state = ensureSingleton().mirror
  return {
    state,
    isDown: computed(() => state.status !== 'ok'),
    probeNow: async () => {
      await ensureSingleton().monitor.probeNow()
    },
  }
}
