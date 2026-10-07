<script setup lang="ts">
// 应用外壳：左侧驾驶舱（品牌/连接状态/会话）+ 右侧主工作区。
// 重构点：健康灯不再自带 setInterval 轮询——与提问门控共用 useHealth() 单例，
// "灯绿门关"之类两套探测各跑各的故事就此终结；侧栏会话 ID 直读 session 模块。
import { computed } from 'vue'
import { useHealth } from '@/composables/useHealth'
import { getSessionId } from '@/services/session'

const { state: health, probeNow } = useHealth()
const sessionId = computed(() => getSessionId())

const HEALTH_TEXT: Record<string, string> = {
  probing: '检测中',
  ok: 'API 已连接 · :8000',
  unreachable: 'API 不可达',
}
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
        <button class="health" :class="health.status" title="点击立即重新探测" @click="probeNow">
          <span class="dot" />
          <span>{{ HEALTH_TEXT[health.status] ?? health.status }}</span>
        </button>
        <p v-if="health.status === 'unreachable'" class="side-hint">
          后端无响应：<code>docker ps</code> 看容器是否 Paused（是则 <code>docker unpause</code> 恢复），
          或本地起 <code>python -m app.main</code>
        </p>
      </div>

      <div class="side-block">
        <div class="side-label">会话</div>
        <div class="session-chip mono" title="checkpointer thread_id：同一 ID 跨请求延续状态">{{ sessionId }}</div>
        <p class="side-hint">澄清 / 审批中断的恢复完全依赖此 ID，更换即开新会话线</p>
      </div>

      <div class="side-block">
        <div class="side-label">链路</div>
        <ul class="flow-list">
          <li><i class="dot-cyan" />SchemaCurator</li>
          <li><i class="dot-accent" />SQLGenerator</li>
          <li><i class="dot-ok" />Executor · 七道闸</li>
          <li><i class="dot-violet" />InsightWriter</li>
        </ul>
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
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
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
.health {
  display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--text-dim);
  font-family: var(--mono); background: none; border: none; padding: 0; text-align: left;
}
.health .dot { width: 8px; height: 8px; border-radius: 50%; background: #64748b; flex: none; }
.health.up .dot { background: var(--ok); box-shadow: 0 0 10px var(--ok); }
.health.down .dot { background: var(--err); box-shadow: 0 0 10px var(--err); }
.health.unreachable .dot { animation: pulse 1.4s ease-in-out infinite; }
.side-hint { font-size: 12px; color: var(--warn); margin: 0; line-height: 1.5; }
.side-hint code { background: var(--panel-2); padding: 1px 5px; border-radius: 4px; font-size: 11px; }
.session-chip {
  font-size: 12.5px; color: var(--cyan); background: var(--panel);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.flow-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 7px; }
.flow-list li { display: flex; align-items: center; gap: 9px; font-size: 12.5px; color: var(--text-dim); }
.flow-list i { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.dot-cyan { background: var(--cyan); }
.dot-accent { background: var(--accent); }
.dot-ok { background: var(--ok); }
.dot-violet { background: var(--accent-2); }

.side-foot { margin-top: auto; display: flex; gap: 8px; }

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
  .cockpit .side-block:nth-child(4), .cockpit .side-foot { display: none; }
  .ws-header { padding: 16px 18px 4px; }
}
</style>
