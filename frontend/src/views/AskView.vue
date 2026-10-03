<script setup lang="ts">
// 问数主页面：接线层——所有判断逻辑在 reducer/api/解析器里（均有单测），
// 这里只负责状态分发与组件编排。
import AskForm from '@/components/AskForm.vue'
import SessionBar from '@/components/SessionBar.vue'
import ClarificationCard from '@/components/ClarificationCard.vue'
import ApprovalCard from '@/components/ApprovalCard.vue'
import ResultCard from '@/components/ResultCard.vue'
import { useAsk } from '@/composables/useAsk'
import { computed, ref } from 'vue'

const {
  state, running, errorMessage, apiKey, sessionId, setSessionId,
  healthState, ask, answerClarification, decideApproval, cancel, reset,
} = useAsk()

// 健康门控：后端不可达时禁止提问（事故教训：朝暂停的容器开枪只会挂死）
const healthDown = computed(() => healthState.status !== 'ok')
const downSeconds = ref(0)
setInterval(() => {
  downSeconds.value = Math.max(0, Math.round((Date.now() - healthState.since) / 1000))
}, 1000)

// 状态徽标：让用户永远知道系统现在卡在哪一步（尤其 interrupt 挂起时）
const PHASE_META: Record<string, { label: string; tone: string }> = {
  idle: { label: '待命', tone: '' },
  running: { label: '执行中', tone: 'accent' },
  clarifying: { label: '等待人工澄清', tone: 'warn' },
  approving: { label: '等待人工审批', tone: 'warn' },
  done: { label: '已完成', tone: 'ok' },
  error: { label: '失败', tone: 'err' },
  cancelled: { label: '已取消', tone: '' },
}
function phaseLabel(): string {
  return PHASE_META[state.value.phase]?.label ?? state.value.phase
}
function phaseTone(): string {
  return PHASE_META[state.value.phase]?.tone ?? ''
}
</script>

<template>
  <div class="ask-view">
    <SessionBar
      :session-id="sessionId"
      :api-key="apiKey"
      :running="running"
      @update:session-id="setSessionId"
      @update:api-key="apiKey = $event"
      @reset="reset"
    />

    <p v-if="healthDown" class="health-banner rise">
  <b>后端不可达{{ healthState.status === 'probing' ? '（探测中）' : '已 ' + downSeconds + 's' }}</b>
  提问已临时禁用。<code>docker ps</code> 看容器是否 Paused（是就 <code>docker unpause</code> 恢复），
  或本地起 <code>python -m app.main</code>。
</p>

<AskForm :disabled="running || healthDown" @submit="ask" />

    <p v-if="errorMessage" class="error-banner rise">
      <b>出错了</b>{{ errorMessage }}
    </p>

    <template v-if="state.phase !== 'idle'">
      <div class="run-head rise">
        <span class="tag" :class="phaseTone()">{{ phaseLabel() }}</span>
        <span class="q-text">“{{ state.question }}”</span>
        <span v-if="running" class="running">
          <span class="pulse">●</span> 运行中
          <button class="btn btn-ghost" style="padding: 2px 10px; font-size: 12px;" @click="cancel">取消</button>
        </span>
      </div>

      <ClarificationCard
        v-if="state.phase === 'clarifying' && state.clarification"
        :question="state.clarification.question"
        :options="state.clarification.options"
        @answer="answerClarification"
      />

      <ApprovalCard
        v-if="state.phase === 'approving' && state.approval"
        :reason="state.approval.reason"
        :sql="state.approval.sql"
        @decide="decideApproval"
      />

      <ResultCard v-if="state.result" :result="state.result" :api-key="apiKey" />

      <p v-if="state.phase === 'cancelled'" class="cancel-banner rise">本轮已取消，没有产生结果。轨迹保留在上方供参考。</p>

      <div v-if="state.phase === 'error' && state.error" class="card error-card rise">
        <div class="card-title" style="color: var(--err);">本轮失败</div>
        <p class="err-text">{{ state.error }}</p>
      </div>
    </template>

    <div v-else class="onboard">
      <h3>三条内置路径</h3>
      <div class="paths">
        <div class="path ok">
          <div class="path-tag">正常问</div>
          <p>各渠道实付销售额</p>
          <span class="path-flow">schema_curator → sql_generator → executor → insight_writer</span>
        </div>
        <div class="path warn">
          <div class="path-tag">澄清追问</div>
          <p>含糊口径触发中断</p>
          <span class="path-flow">人的回答经 /ask/resume 回到状态机</span>
        </div>
        <div class="path accent">
          <div class="path-tag">人工审批</div>
          <p>大表无过滤查询被挂起</p>
          <span class="path-flow">{approved} 参数回传，批准后自动补 LIMIT</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ask-view {
  display: flex; flex-direction: column; gap: 16px;
  padding: 16px 32px 60px; max-width: 980px; width: 100%;
}
.error-banner {
  display: flex; gap: 10px; align-items: baseline;
  background: rgba(255, 107, 107, 0.08); border: 1px solid rgba(255, 107, 107, 0.4);
  color: var(--err); border-radius: 11px; padding: 11px 16px; margin: 0; font-size: 13px;
}
.error-banner b { flex: none; }
.run-head {
  display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
  padding: 2px 2px;
}
.q-text { color: var(--text); font-size: 14.5px; font-weight: 600; }
.running {
  margin-left: auto; display: inline-flex; gap: 10px; align-items: center;
  color: var(--accent); font-size: 12px; font-family: var(--mono);
}
.err-text { color: var(--err); font-size: 13px; margin: 0; }
.health-banner {
  display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
  background: rgba(255, 196, 84, 0.08); border: 1px solid rgba(255, 196, 84, 0.4);
  color: var(--warn); border-radius: 11px; padding: 11px 16px; margin: 0; font-size: 13px;
}
.health-banner code { font-family: var(--mono); font-size: 12px; }

/* ---- 空态：三条路径 ---- */
.onboard h3 { margin: 8px 0 12px; font-size: 14px; color: var(--text-dim); letter-spacing: 1px; }
.paths { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.path {
  background: var(--panel); border: 1px solid var(--border); border-radius: 13px;
  padding: 16px; display: flex; flex-direction: column; gap: 6px;
  transition: border-color .15s, transform .15s;
}
.path:hover { border-color: var(--border-strong); transform: translateY(-2px); }
.path p { margin: 0; font-size: 13.5px; font-weight: 600; }
.path-flow {
  font-family: var(--mono); font-size: 11px; color: var(--text-faint); line-height: 1.5;
}
.path-tag {
  align-self: flex-start; font-family: var(--mono); font-size: 10px; padding: 1px 8px;
  border-radius: 5px; background: var(--card-2); color: var(--text-dim);
}
.path.ok { border-top: 2px solid var(--ok); }
.path.ok .path-tag { color: var(--ok); }
.path.warn { border-top: 2px solid var(--warn); }
.path.warn .path-tag { color: var(--warn); }
.path.accent { border-top: 2px solid var(--accent); }
.path.accent .path-tag { color: var(--accent); }

@media (max-width: 760px) {
  .paths { grid-template-columns: 1fr; }
  .ask-view { padding: 14px 16px 40px; }
}
</style>
