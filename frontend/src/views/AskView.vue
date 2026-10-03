<script setup lang="ts">
// 问数主页面：接线层——所有判断逻辑在 reducer/api/解析器里（均有单测），
// 这里只负责状态分发与组件编排。
import AskForm from '@/components/AskForm.vue'
import SessionBar from '@/components/SessionBar.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import ClarificationCard from '@/components/ClarificationCard.vue'
import ApprovalCard from '@/components/ApprovalCard.vue'
import ResultCard from '@/components/ResultCard.vue'
import { useAsk } from '@/composables/useAsk'

const {
  state, running, errorMessage, apiKey, sessionId, setSessionId,
  ask, answerClarification, decideApproval, cancel,
} = useAsk()
</script>

<template>
  <div class="ask-view">
    <SessionBar
      :session-id="sessionId"
      :api-key="apiKey"
      :running="running"
      @update:session-id="setSessionId"
      @update:api-key="apiKey = $event"
    />

    <AskForm :disabled="running" @submit="ask" />

    <p v-if="errorMessage" class="error-banner">{{ errorMessage }}</p>

    <template v-if="state.phase !== 'idle'">
      <div class="question-line">
        <span class="tag">{{ state.question }}</span>
        <span v-if="running" class="running">运行中… <button class="link" @click="cancel">取消</button></span>
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

      <EventTimeline :timeline="state.timeline" />

      <div v-if="state.phase === 'error' && state.error" class="card error-card">
        <span class="tag err">{{ state.error }}</span>
      </div>
    </template>

    <div v-else class="empty">
      <p>输入业务问题开始。三条内置路径：</p>
      <ul>
        <li><b>正常问</b>：各渠道实付销售额（schema_curator → sql_generator → executor → insight_writer）</li>
        <li><b>澄清追问</b>：含糊口径触发中断，人类的回答经 <code>/ask/resume</code> 回到状态机</li>
        <li><b>人工审批</b>：大表无过滤查询被安全闸挂起，批准/拒绝以 <code>{approved}</code> 参数回传</li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.ask-view { display: flex; flex-direction: column; gap: 4px; }
.question-line { display: flex; gap: 10px; align-items: center; margin: 10px 0 4px; }
.running { color: var(--warn); font-size: 12px; }
.link { background: none; border: none; color: var(--accent); font-size: 12px; }
.error-banner {
  background: #f8717115; border: 1px solid var(--err); color: var(--err);
  border-radius: 10px; padding: 10px 14px; margin: 10px 0;
}
.error-card { border-color: var(--err); }
.tag.err { color: var(--err); border-color: var(--err); }
.empty { color: var(--text-muted); padding: 30px 6px; }
.empty ul { padding-left: 20px; }
.empty li { margin: 6px 0; }
</style>
