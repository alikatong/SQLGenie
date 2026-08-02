<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createHisTerm,
  deleteHisTerm,
  getDbDefinitions,
  getHisTerms,
  getTableSchema,
  updateHisTerm,
} from '../../api'
import { formatDateTime } from '../../utils/datetime'
import { extractError } from '../../utils/errors'

defineOptions({
  name: 'AdminHisSemanticsView',
})

const CATEGORIES = [
  { value: 'entity', label: '实体' },
  { value: 'event', label: '事件' },
  { value: 'time', label: '时间' },
  { value: 'status', label: '状态' },
  { value: 'metric', label: '指标' },
  { value: 'relation', label: '关系' },
]

const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const dbDefinitions = ref([])
const schemaTables = ref([])
const terms = ref([])
const total = ref(0)

const filters = reactive({
  scope: 'all',
  category: '',
  enabled: '',
  keyword: '',
  page: 1,
  page_size: 10,
})

const form = reactive({
  id: null,
  db_id: null,
  term: '',
  synonyms: [],
  definition: '',
  category: 'entity',
  bindings: [],
  sql_hint: '',
  enabled: true,
})

const filteredTerms = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return terms.value.filter((item) => {
    const scopeMatches = filters.scope === 'all'
      || (filters.scope === 'global' && item.db_id == null)
      || Number(filters.scope) === Number(item.db_id)
    const categoryMatches = !filters.category || item.category === filters.category
    const enabledMatches = filters.enabled === '' || item.enabled === filters.enabled
    const keywordMatches = !keyword
      || item.term.toLowerCase().includes(keyword)
      || item.synonyms.some((synonym) => synonym.toLowerCase().includes(keyword))
      || (item.definition || '').toLowerCase().includes(keyword)
      || (item.sql_hint || '').toLowerCase().includes(keyword)
    return scopeMatches && categoryMatches && enabledMatches && keywordMatches
  })
})

const displayedTerms = computed(() => {
  if (filters.scope !== 'global') {
    return filteredTerms.value
  }
  const start = (filters.page - 1) * filters.page_size
  return filteredTerms.value.slice(start, start + filters.page_size)
})

const visibleTotal = computed(() => {
  if (total.value > terms.value.length && filters.scope !== 'global') {
    return total.value
  }
  return filteredTerms.value.length
})

function parseJsonArray(value) {
  if (Array.isArray(value)) {
    return value
  }
  if (typeof value !== 'string' || !value.trim()) {
    return []
  }
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function normalizeTerm(item) {
  return {
    ...item,
    db_id: item.db_id == null ? null : Number(item.db_id),
    synonyms: parseJsonArray(item.synonyms ?? item.synonyms_json).map(String),
    bindings: parseJsonArray(item.bindings ?? item.bindings_json),
    enabled: item.enabled === true || item.enabled === 1,
  }
}

function normalizeTermPage(data) {
  if (Array.isArray(data)) {
    return { items: data.map(normalizeTerm), total: data.length }
  }
  const items = Array.isArray(data?.items) ? data.items.map(normalizeTerm) : []
  return {
    items,
    total: Number.isFinite(Number(data?.total)) ? Number(data.total) : items.length,
  }
}

function categoryLabel(value) {
  return CATEGORIES.find((item) => item.value === value)?.label || value
}

function databaseName(dbId) {
  if (dbId == null) {
    return '全局'
  }
  return dbDefinitions.value.find((item) => item.id === dbId)?.name || `数据库 #${dbId}`
}

function resetForm() {
  Object.assign(form, {
    id: null,
    db_id: null,
    term: '',
    synonyms: [],
    definition: '',
    category: 'entity',
    bindings: [],
    sql_hint: '',
    enabled: true,
  })
  schemaTables.value = []
}

async function loadDbDefinitions() {
  try {
    dbDefinitions.value = await getDbDefinitions()
  } catch (error) {
    ElMessage.error(extractError(error, '加载数据库定义失败。'))
  }
}

async function loadTerms() {
  loading.value = true
  try {
    const params = {
      page: filters.scope === 'global' ? 1 : filters.page,
      page_size: filters.scope === 'global' ? 100 : filters.page_size,
    }
    if (filters.scope !== 'all' && filters.scope !== 'global') {
      params.db_id = Number(filters.scope)
    }
    if (filters.category) {
      params.category = filters.category
    }
    if (filters.enabled !== '') {
      params.enabled = filters.enabled
    }
    if (filters.keyword.trim()) {
      params.search = filters.keyword.trim()
    }

    const firstPage = normalizeTermPage(await getHisTerms(params))
    const allItems = [...firstPage.items]
    if (filters.scope === 'global') {
      const pageCount = Math.ceil(firstPage.total / 100)
      for (let pageNumber = 2; pageNumber <= pageCount; pageNumber += 1) {
        const nextPage = normalizeTermPage(await getHisTerms({ ...params, page: pageNumber }))
        allItems.push(...nextPage.items)
      }
    }
    terms.value = allItems
    total.value = firstPage.total
  } catch (error) {
    terms.value = []
    total.value = 0
    ElMessage.error(extractError(error, '加载 HIS 语义目录失败。'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  filters.page = 1
  loadTerms()
}

async function loadBindingSchema(dbId, keepBindings = false) {
  schemaTables.value = []
  if (!dbId) {
    form.bindings = []
    return
  }
  try {
    const schema = await getTableSchema(dbId)
    schemaTables.value = Array.isArray(schema?.tables) ? schema.tables : []
    if (!keepBindings) {
      form.bindings = []
    }
  } catch (error) {
    form.bindings = []
    ElMessage.error(extractError(error, '加载绑定选择器所需的表结构失败。'))
  }
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

async function openEdit(row) {
  resetForm()
  Object.assign(form, {
    id: row.id,
    db_id: row.db_id,
    term: row.term,
    synonyms: [...row.synonyms],
    definition: row.definition || '',
    category: row.category,
    bindings: row.bindings.map((binding) => ({
      table: binding.table || '',
      columns: Array.isArray(binding.columns) ? [...binding.columns] : [],
      role: binding.role || '',
    })),
    sql_hint: row.sql_hint || '',
    enabled: row.enabled,
  })
  dialogVisible.value = true
  await loadBindingSchema(row.db_id, true)
}

async function handleScopeChange(dbId) {
  await loadBindingSchema(dbId, false)
}

function addBinding() {
  form.bindings.push({ table: '', columns: [], role: '' })
}

function removeBinding(index) {
  form.bindings.splice(index, 1)
}

function bindingColumns(binding) {
  return schemaTables.value.find((table) => table.table_name === binding.table)?.columns || []
}

function handleBindingTableChange(binding) {
  binding.columns = []
}

function buildPayload(source = form) {
  return {
    db_id: source.db_id == null ? null : Number(source.db_id),
    term: String(source.term || '').trim(),
    synonyms: [...new Set((source.synonyms || []).map((item) => String(item).trim()).filter(Boolean))],
    definition: String(source.definition || '').trim(),
    category: source.category,
    bindings: source.db_id == null
      ? []
      : (source.bindings || []).map((binding) => ({
        table: binding.table,
        columns: [...new Set(binding.columns || [])],
        ...(String(binding.role || '').trim() ? { role: String(binding.role).trim() } : {}),
      })),
    sql_hint: String(source.sql_hint || '').trim(),
    enabled: Boolean(source.enabled),
  }
}

function validateForm() {
  if (!form.term.trim() || !form.definition.trim()) {
    ElMessage.warning('请填写标准术语和业务定义。')
    return false
  }
  if (form.term.trim().length > 100 || form.synonyms.some((item) => String(item).trim().length > 100)) {
    ElMessage.warning('术语和每个同义词最多 100 个字符。')
    return false
  }
  if (form.synonyms.length > 20) {
    ElMessage.warning('同义词最多 20 个。')
    return false
  }
  if (form.definition.length > 2000 || form.sql_hint.length > 2000) {
    ElMessage.warning('定义和 SQL 提示各最多 2000 个字符。')
    return false
  }
  if (form.bindings.length > 20 || form.bindings.some((item) => !item.table || !item.columns.length)) {
    ElMessage.warning('每组绑定必须选择表和至少一个字段，绑定最多 20 组。')
    return false
  }
  return true
}

async function saveTerm() {
  if (!validateForm()) {
    return
  }
  saving.value = true
  try {
    const payload = buildPayload()
    if (form.id) {
      await updateHisTerm(form.id, payload)
      ElMessage.success('HIS 术语已更新。')
    } else {
      await createHisTerm(payload)
      ElMessage.success('HIS 术语已创建。')
    }
    dialogVisible.value = false
    await loadTerms()
  } catch (error) {
    ElMessage.error(extractError(error, '保存 HIS 术语失败。'))
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(row) {
  const nextEnabled = !row.enabled
  try {
    await updateHisTerm(row.id, buildPayload({ ...row, enabled: nextEnabled }))
    row.enabled = nextEnabled
    ElMessage.success(nextEnabled ? '术语已启用。' : '术语已停用。')
  } catch (error) {
    ElMessage.error(extractError(error, '切换启用状态失败。'))
  }
}

async function confirmDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除术语“${row.term}”吗？删除后不会再参与语义检索。`,
      '删除 HIS 术语',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }

  try {
    await deleteHisTerm(row.id)
    ElMessage.success('HIS 术语已删除。')
    await loadTerms()
  } catch (error) {
    ElMessage.error(extractError(error, '删除 HIS 术语失败。'))
  }
}

function handlePageChange(page) {
  filters.page = page
  if (filters.scope !== 'global') {
    loadTerms()
  }
}

function handlePageSizeChange(pageSize) {
  filters.page_size = pageSize
  filters.page = 1
  if (filters.scope !== 'global') {
    loadTerms()
  }
}

onMounted(async () => {
  await loadDbDefinitions()
  await loadTerms()
})
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>HIS 语义目录</h2>
            <p>维护标准术语、口径、同义词与 Schema 绑定；自由文本仅作为不可信模型上下文。</p>
          </div>
          <div class="section-actions">
            <el-button :loading="loading" @click="loadTerms">刷新</el-button>
            <el-button type="primary" @click="openCreate">新增术语</el-button>
          </div>
        </div>
      </template>

      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="全局术语不可绑定表字段；数据库专属术语使用下方选择器建立结构化绑定。"
      />

      <div class="term-filters">
        <el-select v-model="filters.scope" aria-label="数据库范围" @change="applyFilters">
          <el-option label="全部数据库范围" value="all" />
          <el-option label="仅全局术语" value="global" />
          <el-option
            v-for="item in dbDefinitions"
            :key="item.id"
            :label="item.name"
            :value="String(item.id)"
          />
        </el-select>
        <el-select v-model="filters.category" clearable placeholder="全部类别" @change="applyFilters">
          <el-option v-for="item in CATEGORIES" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="filters.enabled" placeholder="全部状态" @change="applyFilters">
          <el-option label="全部状态" value="" />
          <el-option label="已启用" :value="true" />
          <el-option label="已停用" :value="false" />
        </el-select>
        <el-input
          v-model="filters.keyword"
          clearable
          placeholder="搜索术语或同义词"
          @keyup.enter="applyFilters"
          @clear="applyFilters"
        />
        <el-button @click="applyFilters">搜索</el-button>
      </div>

      <div class="desktop-term-table">
        <el-table :data="displayedTerms" stripe v-loading="loading" class="term-table">
          <el-table-column prop="term" label="标准术语" min-width="160" show-overflow-tooltip />
          <el-table-column label="类别" width="90">
            <template #default="{ row }"><el-tag effect="plain">{{ categoryLabel(row.category) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="范围" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ databaseName(row.db_id) }}</template>
          </el-table-column>
          <el-table-column label="同义词" width="90">
            <template #default="{ row }">{{ row.synonyms.length }} 个</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-switch :model-value="row.enabled" @change="toggleEnabled(row)" />
            </template>
          </el-table-column>
          <el-table-column label="更新时间" min-width="170">
            <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="mobile-term-list" v-loading="loading">
        <article v-for="row in displayedTerms" :key="row.id" class="mobile-term-card">
          <div class="mobile-term-title">
            <strong>{{ row.term }}</strong>
            <el-tag effect="plain">{{ categoryLabel(row.category) }}</el-tag>
          </div>
          <span class="muted-text">{{ databaseName(row.db_id) }} · {{ row.synonyms.length }} 个同义词</span>
          <div class="mobile-term-actions">
            <el-switch :model-value="row.enabled" active-text="启用" @change="toggleEnabled(row)" />
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="confirmDelete(row)">删除</el-button>
          </div>
        </article>
        <el-empty v-if="!displayedTerms.length && !loading" description="没有匹配的 HIS 术语。" />
      </div>

      <div class="pagination-wrap">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="visibleTotal"
          :current-page="filters.page"
          :page-size="filters.page_size"
          :page-sizes="[10, 20]"
          @current-change="handlePageChange"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="dialogVisible"
      :title="form.id ? '编辑 HIS 术语' : '新增 HIS 术语'"
      width="min(880px, 94vw)"
      destroy-on-close
      append-to-body
    >
      <el-form label-position="top" class="term-form">
        <div class="term-form-grid">
          <el-form-item label="标准术语" required>
            <el-input v-model="form.term" maxlength="100" show-word-limit />
          </el-form-item>
          <el-form-item label="类别" required>
            <el-select v-model="form.category" style="width: 100%">
              <el-option v-for="item in CATEGORIES" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item label="数据库范围">
            <el-select
              v-model="form.db_id"
              clearable
              placeholder="全局术语"
              style="width: 100%"
              @change="handleScopeChange"
            >
              <el-option v-for="item in dbDefinitions" :key="item.id" :label="item.name" :value="item.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="启用">
            <el-switch v-model="form.enabled" />
          </el-form-item>
        </div>

        <el-form-item label="同义词">
          <el-select
            v-model="form.synonyms"
            multiple
            filterable
            allow-create
            default-first-option
            :multiple-limit="20"
            placeholder="输入后按回车添加，最多 20 个"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="业务定义" required>
          <el-input v-model="form.definition" type="textarea" :rows="5" maxlength="2000" show-word-limit />
        </el-form-item>

        <el-form-item label="SQL 提示">
          <el-input
            v-model="form.sql_hint"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="可选。仅作为不可信上下文，不作为 Schema 强证据。"
          />
        </el-form-item>

        <section v-if="form.db_id" class="bindings-panel">
          <div class="section-header">
            <div>
              <h3>结构化表字段绑定</h3>
              <p>只有经当前 Schema 验证的绑定可参与强证据判定。</p>
            </div>
            <el-button @click="addBinding">添加绑定</el-button>
          </div>

          <el-empty v-if="!form.bindings.length" description="尚未添加绑定。" :image-size="56" />
          <div v-else class="binding-list">
            <div v-for="(binding, index) in form.bindings" :key="index" class="binding-row">
              <el-select
                v-model="binding.table"
                filterable
                placeholder="选择表"
                @change="handleBindingTableChange(binding)"
              >
                <el-option
                  v-for="table in schemaTables"
                  :key="table.table_name"
                  :label="table.table_name"
                  :value="table.table_name"
                />
              </el-select>
              <el-select v-model="binding.columns" multiple filterable collapse-tags placeholder="选择字段">
                <el-option
                  v-for="column in bindingColumns(binding)"
                  :key="column.column_name"
                  :label="column.column_name"
                  :value="column.column_name"
                />
              </el-select>
              <el-input v-model="binding.role" maxlength="100" placeholder="可选角色" />
              <el-button type="danger" plain @click="removeBinding(index)">移除</el-button>
            </div>
          </div>
        </section>
      </el-form>

      <template #footer>
        <div class="dialog-actions">
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="saveTerm">保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.term-filters {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) minmax(130px, 0.8fr) minmax(130px, 0.8fr) minmax(220px, 1.5fr) auto;
  gap: 12px;
  margin: 18px 0;
}

.term-table {
  width: 100%;
}

.mobile-term-list {
  display: none;
}

.term-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 18px;
}

.bindings-panel {
  padding: 16px;
  border: 1px solid var(--card-border);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.82);
}

.binding-list {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.binding-row {
  display: grid;
  grid-template-columns: 1fr 1.4fr 1fr auto;
  gap: 10px;
  align-items: start;
}

.dialog-actions,
.mobile-term-actions,
.mobile-term-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dialog-actions {
  justify-content: flex-end;
}

.mobile-term-title {
  justify-content: space-between;
}

.mobile-term-actions {
  flex-wrap: wrap;
}

@media (max-width: 1080px) {
  .term-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .term-filters > :last-child {
    width: 100%;
  }

  .binding-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .term-filters,
  .term-form-grid,
  .binding-row {
    grid-template-columns: 1fr;
  }

  .desktop-term-table {
    display: none;
  }

  .mobile-term-list {
    display: grid;
    gap: 12px;
  }

  .mobile-term-card {
    display: grid;
    gap: 10px;
    min-width: 0;
    padding: 14px;
    border: 1px solid var(--card-border);
    border-radius: 16px;
    background: rgba(248, 250, 252, 0.82);
    word-break: break-word;
  }

  .pagination-wrap {
    overflow-x: auto;
    justify-content: flex-start;
  }
}
</style>
