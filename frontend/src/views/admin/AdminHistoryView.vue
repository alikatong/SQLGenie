<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import RetrievedTablesSummary from '../../components/RetrievedTablesSummary.vue'
import { getSqlHistory, getUsers } from '../../api'
import { authState } from '../../stores/auth'
import { formatDateTime } from '../../utils/datetime'
import { extractError } from '../../utils/errors'

defineOptions({
  name: 'AdminHistoryView',
})

const loading = ref(false)
const historyRows = ref([])
const total = ref(0)
const users = ref([])
const filters = reactive({
  user_id: null,
  date_from: '',
  date_to: '',
  page: 1,
  page_size: 10,
})

const isAdmin = computed(() => authState.role === 'admin')

function parseRetrievedTables(rawValue) {
  if (!rawValue) {
    return []
  }

  try {
    const parsed = JSON.parse(rawValue)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

async function loadHistory() {
  loading.value = true
  try {
    const response = await getSqlHistory({
      user_id: filters.user_id || undefined,
      date_from: filters.date_from || undefined,
      date_to: filters.date_to || undefined,
      page: filters.page,
      page_size: filters.page_size,
    })
    historyRows.value = response.items.map((item) => ({
      ...item,
      retrieved_tables: parseRetrievedTables(item.retrieved_tables_json),
    }))
    total.value = response.total
  } catch (error) {
    ElMessage.error(extractError(error, '加载提问历史失败。'))
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  if (!isAdmin.value) {
    return
  }

  try {
    users.value = await getUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '加载用户列表失败。'))
  }
}

function resetFilters() {
  filters.user_id = null
  filters.date_from = ''
  filters.date_to = ''
  filters.page = 1
  filters.page_size = 10
}

async function handleSearch() {
  filters.page = 1
  await loadHistory()
}

async function handlePageChange(page) {
  filters.page = page
  await loadHistory()
}

async function handlePageSizeChange(pageSize) {
  filters.page_size = pageSize
  filters.page = 1
  await loadHistory()
}

onMounted(() => {
  Promise.all([loadUsers(), loadHistory()])
})
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>{{ isAdmin ? '提问历史' : '我的提问历史' }}</h2>
            <p>
              {{
                isAdmin
                  ? '历史记录只保留最近 7 天，支持按用户、日期筛选，并按页查看。'
                  : '这里展示你最近 7 天内的提问历史，并支持按页查看。'
              }}
            </p>
          </div>
        </div>
      </template>

      <div class="history-filters">
        <el-form class="form-grid">
          <el-form-item v-if="isAdmin" label="普通用户">
            <el-select v-model="filters.user_id" clearable placeholder="请选择普通用户" style="width: 100%">
              <el-option
                v-for="user in users.filter((item) => item.role === 'user')"
                :key="user.id"
                :label="user.username"
                :value="user.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="开始日期">
            <el-date-picker
              v-model="filters.date_from"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="请选择开始日期"
              style="width: 100%"
            />
          </el-form-item>

          <el-form-item label="结束日期">
            <el-date-picker
              v-model="filters.date_to"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="请选择结束日期"
              style="width: 100%"
            />
          </el-form-item>

          <div class="form-grid-full filter-actions">
            <el-button @click="resetFilters">重置条件</el-button>
            <el-button type="primary" :loading="loading" @click="handleSearch">查询历史</el-button>
          </div>
        </el-form>
      </div>

      <div class="history-summary">
        <span>共 {{ total }} 条记录</span>
        <span>当前第 {{ filters.page }} 页</span>
      </div>

      <el-table :data="historyRows" stripe v-loading="loading">
        <el-table-column prop="id" label="ID" width="90" />
        <el-table-column prop="username" label="提问用户" width="140" />
        <el-table-column prop="db_name" label="数据库定义" min-width="180" />
        <el-table-column prop="target_db_type" label="方言" width="120" />
        <el-table-column prop="natural_text" label="自然语言提问" min-width="260" show-overflow-tooltip />
        <el-table-column label="RAG 命中表" min-width="220">
          <template #default="{ row }">
            <div v-if="row.retrieved_tables.length" class="history-hit-cell">
              <RetrievedTablesSummary
                :tables="row.retrieved_tables"
                :inline-limit="0"
                tag-size="small"
                button-size="small"
              />
            </div>
            <span v-else class="history-empty-hit">无</span>
          </template>
        </el-table-column>
        <el-table-column prop="generated_sql" label="生成 SQL" min-width="320" show-overflow-tooltip />
        <el-table-column label="时间" min-width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
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
          :page-sizes="[10, 20]"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.history-filters {
  margin-bottom: 18px;
}

.filter-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.history-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  color: var(--text-secondary);
  font-size: 0.92rem;
  flex-wrap: wrap;
}

.history-hit-cell {
  min-width: 0;
}

.history-empty-hit {
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
</style>
