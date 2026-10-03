<script setup lang="ts">
// 安全渲染 Markdown：marked 转 HTML → DOMPurify 消毒（LLM 输出按不可信内容处理）。
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()
const html = computed(() =>
  DOMPurify.sanitize(marked.parse(props.content || '', { async: false }) as string),
)
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="markdown" v-html="html" />
</template>

<style scoped>
.markdown :deep(table) { border-collapse: collapse; margin: 12px 0; display: block; overflow: auto; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid var(--border); padding: 6px 14px; }
.markdown :deep(th) { background: var(--card-2); color: var(--text-dim); font-family: var(--mono); font-size: 12.5px; }
.markdown :deep(h1), .markdown :deep(h2), .markdown :deep(h3) { margin: 16px 0 6px; }
.markdown :deep(p) { margin: 8px 0; line-height: 1.8; }
.markdown :deep(code) { background: #0a0f1c; padding: 1px 6px; border-radius: 5px; color: var(--cyan); font-family: var(--mono); font-size: 12.5px; }
.markdown :deep(pre) { background: #0a0f1c; }
.markdown :deep(pre code) { background: none; padding: 0; }
.markdown :deep(ul) { margin: 6px 0; padding-left: 22px; }
.markdown :deep(li) { margin: 3px 0; }
.markdown :deep(strong) { color: var(--text); }
</style>
