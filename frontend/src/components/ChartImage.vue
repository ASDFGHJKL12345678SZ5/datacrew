<script setup lang="ts">
// 图表展示：chart_url 是 /files/... 相对路径，该路由带 X-API-Key 鉴权，
// <img src> 无法带自定义 header —— 所以 fetch 成 blob URL 再显示。
import { onUnmounted, ref, watch } from 'vue'
import { fetchFileObjectUrl } from '@/services/api'

const props = defineProps<{ url: string | null | undefined; apiKey: string }>()
const objectUrl = ref('')
const failed = ref(false)

watch(
  () => [props.url, props.apiKey],
  async ([url]) => {
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
    failed.value = false
    if (!url || typeof url !== 'string') return
    try {
      objectUrl.value = await fetchFileObjectUrl(url, props.apiKey)
    } catch {
      failed.value = true
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
    <img v-if="objectUrl" :src="objectUrl" alt="查询结果图表" />
    <span v-else-if="failed" class="muted">图表加载失败（检查 API Key 或 storage 目录）</span>
    <span v-else class="muted">图表加载中…</span>
  </div>
</template>

<style scoped>
.chart img { max-width: 100%; background: #fff; border-radius: 8px; padding: 10px; }
</style>
