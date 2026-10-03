<script setup lang="ts">
// 澄清卡片：schema_curator 发现口径歧义 → interrupt 挂起 → 人回答后 /ask/resume。
// 这是 human-in-the-loop 的第一种形态：答案经简历参数穿过协议边界回到状态机。
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
  <div class="card clarify">
    <div class="head"><span class="tag warn">澄清追问</span></div>
    <p class="q">{{ question }}</p>
    <div class="options">
      <button v-for="o in options" :key="o" class="btn" @click="submit(o)">{{ o }}</button>
    </div>
    <div class="custom">
      <input v-model="custom" placeholder="或输入自定义回答…" @keyup.enter="submit(custom); custom = ''" />
      <button class="btn btn-primary" :disabled="!custom.trim()" @click="submit(custom); custom = ''">回答</button>
    </div>
  </div>
</template>

<style scoped>
.head { margin-bottom: 6px; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
.q { font-size: 15px; margin: 4px 0 12px; }
.options { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.custom { display: flex; gap: 8px; }
.custom input {
  flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 7px 11px; font: inherit;
}
.clarify { border-color: var(--warn); }
</style>
