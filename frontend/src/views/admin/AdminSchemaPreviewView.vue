<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getDbDefinitions, getTableSchema } from '../../api'
import { extractError } from '../../utils/errors'
import { formatDbType } from '../../utils/format'
import { createEmptySchema } from '../../utils/schema'

defineOptions({
  name: 'AdminSchemaPreviewView',
})

const loadingDbDefs = ref(false)
const loadingSchema = ref(false)
const dbDefinitions = ref([])
const selectedDbId = ref(null)
const selectedTableName = ref('')
const schema = ref({
  db_id: null,
  tables: [],
  relations: [],
})

const filters = reactive({
  keyword: '',
  sort_by: 'name',
  table_page: 1,
  table_page_size: 5,
  column_page: 1,
  column_page_size: 20,
})

const selectedDb = computed(
  () => dbDefinitions.value.find((item) => item.id === selectedDbId.value) || null,
)

const tableRelationSummary = computed(() => {
  const summaryMap = new Map()

  for (const table of schema.value.tables) {
    summaryMap.set(table.table_name, {
      outgoing: [],
      incoming: [],
    })
  }

  for (const relation of schema.value.relations) {
    if (summaryMap.has(relation.from_table)) {
      summaryMap.get(relation.from_table).outgoing.push(relation)
    }
    if (summaryMap.has(relation.to_table)) {
      summaryMap.get(relation.to_table).incoming.push(relation)
    }
  }

  return summaryMap
})

const filteredTables = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  let items = schema.value.tables.filter((table) => {
    if (!keyword) {
      return true
    }
    const inTable = table.table_name.toLowerCase().includes(keyword)
      || (table.table_comment || '').toLowerCase().includes(keyword)
    const inColumn = table.columns.some(
      (column) =>
        column.column_name.toLowerCase().includes(keyword)
        || (column.column_comment || '').toLowerCase().includes(keyword),
    )
    return inTable || inColumn
  })

  if (filters.sort_by === 'columns') {
    items = [...items].sort((a, b) => b.columns.length - a.columns.length || a.table_name.localeCompare(b.table_name))
  } else if (filters.sort_by === 'relations') {
    items = [...items].sort((a, b) => {
      const relationDiff = getRelationCount(b.table_name) - getRelationCount(a.table_name)
      return relationDiff || a.table_name.localeCompare(b.table_name)
    })
  } else {
    items = [...items].sort((a, b) => a.table_name.localeCompare(b.table_name))
  }

  return items
})

const selectedTable = computed(
  () => filteredTables.value.find((item) => item.table_name === selectedTableName.value)
    || schema.value.tables.find((item) => item.table_name === selectedTableName.value)
    || null,
)

const pagedTables = computed(() => {
  const start = (filters.table_page - 1) * filters.table_page_size
  return filteredTables.value.slice(start, start + filters.table_page_size)
})

const selectedTableSummary = computed(
  () => tableRelationSummary.value.get(selectedTableName.value) || { outgoing: [], incoming: [] },
)

const pagedColumns = computed(() => {
  if (!selectedTable.value) {
    return []
  }
  const start = (filters.column_page - 1) * filters.column_page_size
  return selectedTable.value.columns.slice(start, start + filters.column_page_size)
})

const relationOverview = computed(() => {
  const sorted = [...schema.value.relations].sort((a, b) => {
    if (a.from_table !== b.from_table) {
      return a.from_table.localeCompare(b.from_table)
    }
    if (a.to_table !== b.to_table) {
      return a.to_table.localeCompare(b.to_table)
    }
    return a.from_column.localeCompare(b.from_column)
  })
  return sorted.slice(0, 30)
})

function getRelationCount(tableName) {
  const summary = tableRelationSummary.value.get(tableName)
  if (!summary) {
    return 0
  }
  return summary.incoming.length + summary.outgoing.length
}

function resetColumnPager() {
  filters.column_page = 1
}

function resetTablePager() {
  filters.table_page = 1
}

function selectTable(tableName) {
  selectedTableName.value = tableName
  resetColumnPager()
}

function ensureSelectedTableVisible() {
  if (!filteredTables.value.length) {
    selectedTableName.value = ''
    return
  }

  if (!filteredTables.value.some((item) => item.table_name === selectedTableName.value)) {
    selectedTableName.value = filteredTables.value[0].table_name
  }

  const selectedIndex = filteredTables.value.findIndex((item) => item.table_name === selectedTableName.value)
  if (selectedIndex < 0) {
    return
  }

  const nextPage = Math.floor(selectedIndex / filters.table_page_size) + 1
  if (filters.table_page !== nextPage) {
    filters.table_page = nextPage
  }
}

async function loadDbDefinitions(preferredId = selectedDbId.value) {
  loadingDbDefs.value = true
  try {
    const data = await getDbDefinitions()
    dbDefinitions.value = data

    if (!data.length) {
      selectedDbId.value = null
      schema.value = createEmptySchema(selectedDbId.value)
      return
    }

    const nextId = data.some((item) => item.id === preferredId) ? preferredId : data[0].id
    await selectDb(nextId)
  } catch (error) {
    ElMessage.error(extractError(error, '加载数据库定义失败。'))
  } finally {
    loadingDbDefs.value = false
  }
}

async function selectDb(id) {
  const matched = dbDefinitions.value.find((item) => item.id === id)
  if (!matched) {
    return
  }

  selectedDbId.value = matched.id
  await loadSchema()
}

async function loadSchema() {
  if (!selectedDbId.value) {
    schema.value = createEmptySchema(selectedDbId.value)
    selectedTableName.value = ''
    return
  }

  loadingSchema.value = true
  try {
    schema.value = await getTableSchema(selectedDbId.value)
    if (!schema.value.tables.length) {
      selectedTableName.value = ''
      return
    }
    resetTablePager()
    ensureSelectedTableVisible()
    resetColumnPager()
  } catch (error) {
    schema.value = createEmptySchema(selectedDbId.value)
    selectedTableName.value = ''
    ElMessage.error(extractError(error, '加载表结构失败。'))
  } finally {
    loadingSchema.value = false
  }
}

function handleFilterChange() {
  resetTablePager()
  ensureSelectedTableVisible()
}

function handleTablePageChange(page) {
  filters.table_page = page
  if (pagedTables.value.length && !pagedTables.value.some((item) => item.table_name === selectedTableName.value)) {
    selectedTableName.value = pagedTables.value[0].table_name
    resetColumnPager()
  }
}

function handleTablePageSizeChange(pageSize) {
  filters.table_page_size = pageSize
  resetTablePager()
  ensureSelectedTableVisible()
}

function handleColumnPageChange(page) {
  filters.column_page = page
}

function handleColumnPageSizeChange(pageSize) {
  filters.column_page_size = pageSize
  filters.column_page = 1
}

onMounted(() => {
  loadDbDefinitions()
})
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>结构总览</h2>
            <p>面向大体量表结构的检索式预览，先找表，再看字段，再看关系。</p>
          </div>
          <div class="section-actions">
            <el-select
              v-model="selectedDbId"
              placeholder="请选择数据库定义"
              style="min-width: 280px"
              :loading="loadingDbDefs"
              @change="selectDb"
            >
              <el-option
                v-for="item in dbDefinitions"
                :key="item.id"
                :label="`${item.name} (${formatDbType(item.db_type)})`"
                :value="item.id"
              />
            </el-select>
            <el-button @click="loadDbDefinitions" :loading="loadingDbDefs">刷新定义</el-button>
            <el-button @click="loadSchema" :loading="loadingSchema" :disabled="!selectedDbId">
              刷新结构
            </el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!dbDefinitions.length && !loadingDbDefs" description="当前还没有数据库定义。" />

      <template v-else-if="selectedDb">
        <div class="overview-grid">
          <div class="overview-stat">
            <span class="overview-label">当前数据库定义</span>
            <strong>{{ selectedDb.name }}</strong>
            <span class="overview-subtext">{{ formatDbType(selectedDb.db_type) }}</span>
          </div>
          <div class="overview-stat">
            <span class="overview-label">表数量</span>
            <strong>{{ schema.tables.length }}</strong>
            <span class="overview-subtext">适合先搜索后查看</span>
          </div>
          <div class="overview-stat">
            <span class="overview-label">字段总数</span>
            <strong>{{ schema.tables.reduce((sum, table) => sum + table.columns.length, 0) }}</strong>
            <span class="overview-subtext">避免全量展开卡顿</span>
          </div>
          <div class="overview-stat">
            <span class="overview-label">关系数量</span>
            <strong>{{ schema.relations.length }}</strong>
            <span class="overview-subtext">支持按单表查看上下游</span>
          </div>
        </div>
      </template>
    </el-card>

    <el-empty
      v-if="selectedDb && !schema.tables.length && !schema.relations.length && !loadingSchema"
      description="当前定义还没有导入表结构。"
    />

    <template v-else-if="selectedDb">
      <div class="preview-layout" v-loading="loadingSchema">
        <el-card class="panel-card preview-side" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>表目录</h2>
                <p>按表名、注释、字段名搜索，并按规模排序。</p>
              </div>
            </div>
          </template>

          <div class="side-filters">
            <el-input
              v-model="filters.keyword"
              placeholder="搜索表名 / 注释 / 字段名"
              clearable
              @input="handleFilterChange"
              @clear="handleFilterChange"
            />
            <el-select v-model="filters.sort_by" @change="handleFilterChange">
              <el-option label="按表名排序" value="name" />
              <el-option label="按字段数排序" value="columns" />
              <el-option label="按关系数排序" value="relations" />
            </el-select>
          </div>

          <div class="table-directory">
            <div
              v-for="table in pagedTables"
              :key="table.id"
              class="directory-item"
              :class="{ active: table.table_name === selectedTableName }"
              @click="selectTable(table.table_name)"
            >
              <div>
                <strong>{{ table.table_name }}</strong>
                <p>{{ table.table_comment || '暂无表注释' }}</p>
              </div>
              <div class="directory-tags">
                <el-tag effect="plain">{{ table.columns.length }} 字段</el-tag>
                <el-tag type="success" effect="plain">{{ getRelationCount(table.table_name) }} 关系</el-tag>
              </div>
            </div>
          </div>

          <div class="directory-pagination">
            <el-pagination
              small
              background
              layout="total, sizes, prev, pager, next"
              :total="filteredTables.length"
              :current-page="filters.table_page"
              :page-size="filters.table_page_size"
              :page-sizes="[5, 10]"
              @current-change="handleTablePageChange"
              @size-change="handleTablePageSizeChange"
            />
          </div>
        </el-card>

        <el-card class="panel-card preview-main" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>{{ selectedTable ? selectedTable.table_name : '单表详情' }}</h2>
                <p>
                  {{
                    selectedTable
                      ? (selectedTable.table_comment || '暂无表注释')
                      : '请先从左侧表目录选择一个数据表。'
                  }}
                </p>
              </div>
              <div class="section-actions" v-if="selectedTable">
                <el-tag effect="plain">{{ selectedTable.columns.length }} 个字段</el-tag>
                <el-tag type="success" effect="plain">{{ selectedTableSummary.outgoing.length }} 发出关系</el-tag>
                <el-tag type="warning" effect="plain">{{ selectedTableSummary.incoming.length }} 被引用关系</el-tag>
              </div>
            </div>
          </template>

          <el-empty
            v-if="!selectedTable"
            description="没有匹配的表，请调整左侧搜索条件。"
          />

          <template v-else>
            <div class="column-toolbar">
              <span class="muted-text">字段分页展示，避免上百字段一次性展开造成阅读压力。</span>
            </div>

            <el-table :data="pagedColumns" stripe max-height="640">
              <el-table-column prop="column_name" label="字段名" min-width="220" />
              <el-table-column prop="data_type" label="类型" min-width="180" />
              <el-table-column prop="column_comment" label="注释" min-width="240" />
            </el-table>

            <div class="detail-pagination">
              <el-pagination
                small
                background
                layout="total, sizes, prev, pager, next"
                :total="selectedTable.columns.length"
                :current-page="filters.column_page"
                :page-size="filters.column_page_size"
                :page-sizes="[20, 50, 100]"
                @current-change="handleColumnPageChange"
                @size-change="handleColumnPageSizeChange"
              />
            </div>
          </template>
        </el-card>

        <el-card class="panel-card preview-side" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>关系侧栏</h2>
                <p>先看当前表上下游，再看全局关系样本。</p>
              </div>
            </div>
          </template>

          <div v-if="selectedTable" class="relation-stack">
            <div class="relation-block">
              <div class="relation-block-title">当前表发出的关系</div>
              <el-empty
                v-if="!selectedTableSummary.outgoing.length"
                description="没有发出关系"
              />
              <div v-else class="relation-list">
                <div
                  v-for="relation in selectedTableSummary.outgoing"
                  :key="`out-${relation.id}`"
                  class="relation-item outgoing"
                >
                  <strong>{{ relation.from_column }}</strong>
                  <span>{{ relation.relation_type }}</span>
                  <strong>{{ relation.to_table }}.{{ relation.to_column }}</strong>
                </div>
              </div>
            </div>

            <div class="relation-block">
              <div class="relation-block-title">指向当前表的关系</div>
              <el-empty
                v-if="!selectedTableSummary.incoming.length"
                description="没有被其他表引用"
              />
              <div v-else class="relation-list">
                <div
                  v-for="relation in selectedTableSummary.incoming"
                  :key="`in-${relation.id}`"
                  class="relation-item incoming"
                >
                  <strong>{{ relation.from_table }}.{{ relation.from_column }}</strong>
                  <span>{{ relation.relation_type }}</span>
                  <strong>{{ relation.to_column }}</strong>
                </div>
              </div>
            </div>
          </div>

          <div class="relation-block global-block">
            <div class="relation-block-title">全局关系样本（前 30 条）</div>
            <el-empty
              v-if="!relationOverview.length"
              description="当前结构还没有定义表关系。"
            />
            <div v-else class="relation-list">
              <div
                v-for="relation in relationOverview"
                :key="relation.id"
                class="relation-item compact"
              >
                <strong>{{ relation.from_table }}</strong>
                <span>{{ relation.from_column }} -> {{ relation.to_table }}.{{ relation.to_column }}</span>
              </div>
            </div>
          </div>
        </el-card>
      </div>
    </template>
  </div>
</template>

<style scoped>
.overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.overview-stat {
  display: grid;
  gap: 6px;
  padding: 18px;
  border-radius: 20px;
  background:
    radial-gradient(circle at top right, rgba(20, 184, 166, 0.14), transparent 38%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.92), rgba(241, 245, 249, 0.88));
  border: 1px solid rgba(15, 118, 110, 0.1);
}

.overview-label {
  font-size: 0.82rem;
  color: var(--text-secondary);
}

.overview-stat strong {
  font-size: 1.6rem;
  line-height: 1.1;
}

.overview-subtext {
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.preview-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr) 340px;
  gap: 20px;
  align-items: start;
}

.preview-side,
.preview-main {
  min-height: 760px;
}

.side-filters {
  display: grid;
  gap: 12px;
  margin-bottom: 16px;
}

.table-directory,
.relation-stack,
.relation-list {
  display: grid;
  gap: 12px;
}

.directory-item,
.relation-item {
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(248, 250, 252, 0.9);
}

.directory-item {
  cursor: pointer;
  transition: all 0.2s ease;
}

.directory-item.active {
  border-color: rgba(15, 118, 110, 0.34);
  background:
    linear-gradient(135deg, rgba(20, 184, 166, 0.1), rgba(251, 146, 60, 0.08));
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.directory-item p {
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.directory-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.column-toolbar {
  margin-bottom: 12px;
}

.detail-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.directory-pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.relation-block + .relation-block {
  margin-top: 18px;
}

.relation-block-title {
  margin-bottom: 12px;
  font-size: 0.92rem;
  font-weight: 700;
}

.relation-item {
  display: grid;
  gap: 4px;
}

.relation-item span {
  color: var(--text-secondary);
  font-size: 0.9rem;
  word-break: break-word;
}

.relation-item.outgoing {
  background: rgba(236, 253, 245, 0.9);
}

.relation-item.incoming {
  background: rgba(255, 247, 237, 0.92);
}

.relation-item.compact strong {
  font-size: 0.92rem;
}

.global-block {
  margin-top: 22px;
}

@media (max-width: 1440px) {
  .preview-layout {
    grid-template-columns: 300px minmax(0, 1fr);
  }

  .preview-side:last-child {
    grid-column: 1 / -1;
    min-height: auto;
  }
}

@media (max-width: 1080px) {
  .overview-grid,
  .preview-layout {
    grid-template-columns: 1fr;
  }

  .preview-side,
  .preview-main {
    min-height: auto;
  }
}
</style>
