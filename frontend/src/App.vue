<script setup lang="ts">
// 应用外壳：左侧驾驶舱（品牌/连接状态/会话）+ 右侧主工作区。
import { onMounted, onUnmounted, ref } from 'vue'

const apiOk = ref<boolean | null>(null)
let timer: number | undefined

async function checkHealth() {
  try {
    // 6s 超时是必需的：容器被 docker pause 时 TCP 握手仍成功但永不响应，
    // 没有超时的 fetch 会一直挂起，健康灯假装还是绿的。
    const res = await fetch('/health', { signal: AbortSignal.timeout(6_000) })
    apiOk.value = res.ok
  } catch {
    apiOk.value = false
  }
}

onMounted(() => {
  checkHealth()
  // 容器重启过：健康灯要能自己恢复，不能刷新页面才变绿
  timer = window.setInterval(checkHealth, 15_000)
})
onUnmounted(() => { if (timer) window.clearInterval(timer) })
</script>

<template>
  <div class="app-shell">
    <aside class="cockpit">
      <div class="brand">
        <div class="logo">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5" /><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
          </svg>
        </div>
        <div class="brand-text">
          <h1>DataCrew</h1>
          <p>智能问数驾驶舱</p>
        </div>
      </div>

      <div class="side-block">
        <div class="side-label">连接</div>
        <div class="health" :class="apiOk === null ? 'unknown' : apiOk ? 'up' : 'down'">
          <span class="dot" />
          <span>{{ apiOk === null ? '检测中' : apiOk ? 'API 已连接 · :8000' : 'API 不可达' }}</span>
        </div>
        <p v-if="apiOk === false" class="side-hint">后端无响应：<code>docker ps</code> 看容器是否 Paused（是则 <code>docker unpause</code> 恢复），或本地起 <code>python -m app.main</code></p>
      </div>


      <div class="side-foot">
        <span class="tag">LLM: mock</span>
        <span class="tag">SSE v1</span>
      </div>
    </aside>

    <main class="workspace">
      <header class="ws-header">
        <div>
          <h2>问数工作台</h2>
          <p class="sub">用自然语言查业务库 · 全链路可审计（SQL / 闸后执行 / 自愈重试 / 人工介入）</p>
        </div>
      </header>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 264px 1fr; }

/* ---- 左侧驾驶舱 ---- */
.cockpit {
  background: linear-gradient(180deg, #0a1120, #070c17);
  border-right: 1px solid var(--border);
  padding: 22px 18px; display: flex; flex-direction: column; gap: 22px;
  position: sticky; top: 0; height: 100vh;
}
.brand { display: flex; gap: 12px; align-items: center; }
.logo {
  width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center; flex: none;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 4px 16px rgba(79, 140, 255, 0.4);
}
.brand-text h1 { font-size: 18px; margin: 0; letter-spacing: 0.5px; }
.brand-text p { margin: 0; font-size: 12px; color: var(--text-dim); }
.side-block { display: flex; flex-direction: column; gap: 8px; }
.side-label {
  font-size: 11px; letter-spacing: 2px; color: var(--text-faint);
  text-transform: uppercase; font-weight: 600;
}
.health { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-dim); font-family: var(--mono); }
.health .dot { width: 8px; height: 8px; border-radius: 50%; background: #64748b; flex: none; }
.health.up .dot { background: var(--ok); box-shadow: 0 0 10px var(--ok); }
.health.down .dot { background: var(--err); box-shadow: 0 0 10px var(--err); }
.side-hint { font-size: 12px; color: var(--warn); margin: 0; line-height: 1.5; }
.side-hint code { background: var(--panel-2); padding: 1px 5px; border-radius: 4px; font-size: 11px; }

/* ---- 右侧工作区 ---- */
.workspace { min-width: 0; display: flex; flex-direction: column; }
.ws-header {
  padding: 20px 32px 6px; display: flex; justify-content: space-between; align-items: flex-end;
}
.ws-header h2 { margin: 0; font-size: 20px; letter-spacing: 0.5px; }
.ws-header .sub { margin: 4px 0 0; font-size: 12.5px; color: var(--text-dim); }

@media (max-width: 920px) {
  .app-shell { grid-template-columns: 1fr; }
  .cockpit { position: static; height: auto; flex-direction: row; flex-wrap: wrap; align-items: center; }
  .cockpit .side-block:nth-child(3), .cockpit .side-foot { display: none; }
  .ws-header { padding: 16px 18px 4px; }
}
</style>
