<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approveFeedbackRagExample,
  deleteFeedbackRagExample,
  getDbDefinitions,
  getFeedbackRagConfig,
  getFeedbackRagExamples,
  updateFeedbackRagConfig,
} from '../../api'
import { formatDateTime } from '../../utils/datetime'
import { extractError } from '../../utils/errors'

defineOptions({ name: 'AdminFeedbackRagView' })

const loading = ref(false)
const saving = ref(false)
const deletingId = ref(null)
const approvingId = ref(null)
const rows = ref([])
const total = ref(0)
const databases = ref([])
const config = reactive({ top_k: 3 })
const filters = reactive({ db_id: null, approved: null, page: 1, page_size: 10 })

async function loadExamples() {
  loading.value = true
  try {
    const data = await getFeedbackRagExamples({
      db_id: filters.db_id || undefined,
      approved: filters.approved === null ? undefined : filters.approved,
      page: filters.page,
      page_size: filters.page_size,
    })
    rows.value = data.items
    total.value = data.total
  } catch (error) {
    ElMessage.error(extractError(error, '加载反馈示例失败。'))
  } finally {
    loading.value = false
  }
}

async function loadPageData() {
  try {
    const [dbDefinitions, ragConfig] = await Promise.all([getDbDefinitions(), getFeedbackRagConfig()])
    databases.value = dbDefinitions
    config.top_k = Number(ragConfig.top_k || 3)
  } catch (error) {
    ElMessage.error(extractError(error, '加载反馈 RAG 配置失败。'))
  }
  await loadExamples()
}

async function saveTopK() {
  saving.value = true
  try {
    const data = await updateFeedbackRagConfig({ top_k: Math.trunc(config.top_k) })
    config.top_k = data.top_k
    ElMessage.success('反馈 RAG 配置已保存。')
  } catch (error) {
    ElMessage.error(extractError(error, '保存反馈 RAG 配置失败。'))
  } finally {
    saving.value = false
  }
}

async function deleteExample(row) {
  try {
    await ElMessageBox.confirm(
      '删除后，该示例将不再参与后续 SQL 生成的检索。',
      '删除反馈示例',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  deletingId.value = row.id
  try {
    await deleteFeedbackRagExample(row.id)
    ElMessage.success('反馈示例已删除。')
    if (rows.value.length === 1 && filters.page > 1) {
      filters.page -= 1
    }
    await loadExamples()
  } catch (error) {
    ElMessage.error(extractError(error, '删除反馈示例失败。'))
  } finally {
    deletingId.value = null
  }
}

async function approveExample(row) {
  approvingId.value = row.id
  try {
    await approveFeedbackRagExample(row.id)
    ElMessage.success('Approved')
    await loadExamples()
  } catch (error) {
    ElMessage.error(extractError(error, 'Approval failed.'))
  } finally {
    approvingId.value = null
  }
}

async function handleFilterChange() {
  filters.page = 1
  await loadExamples()
}

async function handlePageChange(page) {
  filters.page = page
  await loadExamples()
}

async function handlePageSizeChange(pageSize) {
  filters.page_size = pageSize
  filters.page = 1
  await loadExamples()
}

onMounted(loadPageData)
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>反馈 RAG 配置</h2>
            <p>设置每次生成 SQL 时可提供给模型的已验证反馈示例数量。</p>
          </div>
        </div>
      </template>

      <div class="settings-row">
        <el-form-item label="反馈 RAG top_k">
          <el-input-number v-model="config.top_k" :min="1" :max="20" :step="1" :precision="0" />
        </el-form-item>
        <span class="muted-text">示例仅从当前所选数据库定义中检索。</span>
        <el-button type="primary" :loading="saving" @click="saveTopK">保存</el-button>
      </div>
    </el-card>

    <el-card class="panel-card" shadow="never" v-loading="loading">
      <template #header>
        <div class="section-header">
          <div>
            <h2>已验证的反馈示例</h2>
            <p>可删除不应再影响 SQL 生成的示例。</p>
          </div>
          <el-button @click="loadExamples" :loading="loading">刷新</el-button>
        </div>
      </template>

      <div class="filter-row">
        <el-select
          v-model="filters.db_id"
          clearable
          placeholder="按数据库筛选"
          aria-label="按数据库筛选反馈示例"
          @change="handleFilterChange"
        >
          <el-option v-for="database in databases" :key="database.id" :label="database.name" :value="database.id" />
        </el-select>
        <el-select
          v-model="filters.approved"
          clearable
          placeholder="Approval status"
          aria-label="Filter feedback examples by approval status"
          @change="handleFilterChange"
        >
          <el-option label="Pending" :value="false" />
          <el-option label="Approved" :value="true" />
        </el-select>
      </div>

      <el-table :data="rows" stripe>
        <el-table-column label="Status" width="140">
          <template #default="{ row }">
            <span>{{ row.approved ? 'Approved' : 'Pending' }}</span>
            <el-button
              v-if="!row.approved"
              type="primary"
              link
              :loading="approvingId === row.id"
              @click="approveExample(row)"
            >
              Approve
            </el-button>
          </template>
        </el-table-column>
        <el-table-column prop="db_name" label="数据库" min-width="140" />
        <el-table-column prop="username" label="用户" min-width="120" />
        <el-table-column label="反馈类型" width="120">
          <template #default="{ row }">
            {{ row.feedback_type === 'correct' ? 'SQL 正确' : '已修改' }}
          </template>
        </el-table-column>
        <el-table-column prop="natural_text" label="问题" min-width="220" show-overflow-tooltip />
        <el-table-column prop="corrected_sql" label="已验证 SQL" min-width="320" show-overflow-tooltip />
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" link :loading="deletingId === row.id" @click="deleteExample(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="filters.page"
          :page-size="filters.page_size"
          :page-sizes="[10, 20, 50]"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.settings-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.settings-row .el-form-item {
  margin-bottom: 0;
}

.filter-row {
  margin-bottom: 16px;
}

.filter-row .el-select {
  width: min(100%, 320px);
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
</style>
