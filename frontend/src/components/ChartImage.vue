<script setup lang="ts">
// 图表展示：chart_url 是 /files/... 相对路径，该路由带 X-API-Key 鉴权，
// <img src> 无法带自定义 header —— 所以 fetch 成 blob URL 再显示。
import { onUnmounted, ref, watch } from 'vue'
import { fetchFileObjectUrl } from '@/services/api'

const props = defineProps<{ url: string | null | undefined; apiKey: string }>()
const objectUrl = ref('')
const failed = ref(false)
const loading = ref(false)

watch(
  () => [props.url, props.apiKey],
  async ([url]) => {
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
    failed.value = false
    if (!url || typeof url !== 'string') return
    loading.value = true
    try {
      objectUrl.value = await fetchFileObjectUrl(url, props.apiKey)
    } catch {
      failed.value = true
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
})
</script>

<template>
  <div v-if="url" class="chart">
    <div v-if="objectUrl" class="chart-paper">
      <img :src="objectUrl" alt="查询结果图表" />
    </div>
    <div v-else-if="failed" class="chart-msg err">⚠ 图表加载失败：检查 API Key 或 storage 目录</div>
    <div v-else class="chart-msg pulse">图表加载中…</div>
  </div>
</template>

<style scoped>
.chart { min-height: 40px; }
.chart-paper {
  background: #fff; border-radius: 10px; padding: 14px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.35);
}
.chart-paper img { display: block; width: 100%; height: auto; }
.chart-msg { font-size: 13px; padding: 18px; text-align: center; color: var(--text-dim); font-family: var(--mono); }
.chart-msg.err { color: var(--err); }
</style>
