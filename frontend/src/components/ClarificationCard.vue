<script setup lang="ts">
// 澄清卡片：schema_curator 检出口径歧义 → interrupt 挂起 → 人回答经 /ask/resume 回到状态机。
// human-in-the-loop 第一种形态：人类的答案作为参数穿过协议边界。
import { ref } from 'vue'

defineProps<{ question: string; options: string[] }>()
const emit = defineEmits<{ (e: 'answer', v: string): void }>()
const custom = ref('')

function submit(text: string) {
  const t = text.trim()
  if (t) emit('answer', t)
}
</script>

<template>
  <div class="card interrupt clarify rise">
    <div class="head">
      <span class="tag warn">⏸ 人工介入 · 口径澄清</span>
      <span class="faint interrupt-note">interrupt() 已挂起状态机，等待你的回答</span>
    </div>
    <p class="q">{{ question }}</p>
    <div class="options">
      <button v-for="o in options" :key="o" class="btn" @click="submit(o)">{{ o }}</button>
    </div>
    <div class="custom">
      <input v-model="custom" placeholder="或输入你的口径定义…" @keyup.enter="submit(custom); custom = ''" />
      <button class="btn btn-primary" :disabled="!custom.trim()" @click="submit(custom); custom = ''">回答并继续</button>
    </div>
  </div>
</template>

<style scoped>
.clarify { border-color: rgba(251, 191, 36, 0.45); background: linear-gradient(180deg, rgba(251,191,36,0.05), var(--panel)); }
.head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
.interrupt-note { font-size: 12px; font-family: var(--mono); }
.q { font-size: 15.5px; margin: 12px 0 14px; font-weight: 600; }
.options { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.custom { display: flex; gap: 8px; }
</style>
