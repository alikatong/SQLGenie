<script setup>
import { computed } from 'vue'

const props = defineProps({
  tables: {
    type: Array,
    default: () => [],
  },
  inlineLimit: {
    type: Number,
    default: 6,
  },
  tagType: {
    type: String,
    default: 'success',
  },
  tagSize: {
    type: String,
    default: 'default',
  },
  buttonSize: {
    type: String,
    default: 'default',
  },
})

const previewTables = computed(() => props.tables.slice(0, Math.max(props.inlineLimit, 0)))
const hiddenCount = computed(() => Math.max(props.tables.length - previewTables.value.length, 0))
const detailButtonLabel = computed(() =>
  hiddenCount.value > 0 ? `查看全部（+${hiddenCount.value}）` : '查看全部',
)
</script>

<template>
  <div v-if="tables.length" class="retrieved-table-summary">
    <el-tag :size="tagSize" :type="tagType" effect="dark">
      命中 {{ tables.length }} 张表
    </el-tag>

    <div v-if="previewTables.length" class="retrieved-table-preview">
      <el-tag
        v-for="table in previewTables"
        :key="table"
        :size="tagSize"
        :type="tagType"
        effect="plain"
      >
        {{ table }}
      </el-tag>
    </div>

    <el-popover placement="bottom-start" :width="380" trigger="click">
      <template #reference>
        <el-button link type="primary" :size="buttonSize">
          {{ detailButtonLabel }}
        </el-button>
      </template>

      <div class="retrieved-table-popover">
        <strong>RAG 命中表</strong>
        <p>共 {{ tables.length }} 张相关表 schema。</p>

        <div class="retrieved-table-popover-list">
          <el-tag
            v-for="table in tables"
            :key="table"
            :size="tagSize"
            :type="tagType"
            effect="plain"
          >
            {{ table }}
          </el-tag>
        </div>
      </div>
    </el-popover>
  </div>
</template>

<style scoped>
.retrieved-table-summary {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  flex-wrap: wrap;
}

.retrieved-table-preview,
.retrieved-table-popover-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.retrieved-table-popover p {
  margin: 6px 0 12px;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.retrieved-table-popover-list {
  max-height: 260px;
  overflow: auto;
  padding-right: 4px;
}
</style>
