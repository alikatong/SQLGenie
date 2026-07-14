<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createDbDefinition,
  deleteDbDefinition,
  deleteSingleTableSchema,
  getDbDefinitions,
  getTableSchema,
  updateDbDefinition,
  uploadSingleTableSchema,
  uploadTableSchema,
} from '../../api'
import { extractError } from '../../utils/errors'
import { formatDbType } from '../../utils/format'
import { createEmptySchema } from '../../utils/schema'

defineOptions({
  name: 'AdminMetadataView',
})

const DEFAULT_SCHEMA_JSON = `{
  "tables": [
    {
      "table_name": "users",
      "table_comment": "用户表",
      "columns": [
        { "column_name": "id", "data_type": "int", "column_comment": "主键" },
        { "column_name": "username", "data_type": "varchar(50)", "column_comment": "用户名" },
        { "column_name": "created_at", "data_type": "datetime", "column_comment": "创建时间" }
      ]
    },
    {
      "table_name": "orders",
      "table_comment": "订单表",
      "columns": [
        { "column_name": "id", "data_type": "int", "column_comment": "主键" },
        { "column_name": "user_id", "data_type": "int", "column_comment": "下单用户ID" },
        { "column_name": "amount", "data_type": "decimal(10,2)", "column_comment": "订单金额" },
        { "column_name": "created_at", "data_type": "datetime", "column_comment": "创建时间" }
      ]
    }
  ],
  "relations": [
    {
      "from_table": "users",
      "from_column": "id",
      "to_table": "orders",
      "to_column": "user_id",
      "relation_type": "one_to_many"
    }
  ]
}`

const DEFAULT_SINGLE_TABLE_JSON = `{
  "table": {
    "table_name": "users",
    "table_comment": "用户表",
    "columns": [
      { "column_name": "id", "data_type": "int", "column_comment": "主键" },
      { "column_name": "username", "data_type": "varchar(50)", "column_comment": "用户名" },
      { "column_name": "created_at", "data_type": "datetime", "column_comment": "创建时间" }
    ]
  },
  "relations": []
}`

const RELATION_EXAMPLE_JSON = `[
  {
    "from_table": "users",
    "from_column": "id",
    "to_table": "orders",
    "to_column": "user_id",
    "relation_type": "one_to_many"
  }
]`

const DELETE_CONFIRM_PHRASE = 'DELETE'

const loadingDbDefs = ref(false)
const loadingSchema = ref(false)
const savingDbDef = ref(false)
const importingSchema = ref(false)
const savingSingleTable = ref(false)
const deletingSingleTable = ref(false)
const dbDefinitions = ref([])
const selectedDbId = ref(null)
const schema = ref({
  db_id: null,
  tables: [],
  relations: [],
})
const bulkUploadText = ref(DEFAULT_SCHEMA_JSON)
const singleTableUploadText = ref(DEFAULT_SINGLE_TABLE_JSON)
const selectedTableName = ref('')
const singleTableFilters = reactive({
  keyword: '',
  sortBy: 'name',
  page: 1,
  pageSize: 10,
})

const dbForm = reactive({
  id: null,
  name: '',
  db_type: 'mysql',
})

const selectedDb = computed(
  () => dbDefinitions.value.find((item) => item.id === selectedDbId.value) || null,
)

const selectedTable = computed(
  () => schema.value.tables.find((item) => item.table_name === selectedTableName.value) || null,
)

const selectedTableRelations = computed(() =>
  schema.value.relations.filter(
    (item) => item.from_table === selectedTableName.value || item.to_table === selectedTableName.value,
  ),
)

const filteredSingleTables = computed(() => {
  const keyword = singleTableFilters.keyword.trim().toLowerCase()
  let items = schema.value.tables.filter((table) => {
    if (!keyword) {
      return true
    }

    const matchesTable =
      table.table_name.toLowerCase().includes(keyword)
      || (table.table_comment || '').toLowerCase().includes(keyword)
    const matchesColumn = table.columns.some(
      (column) =>
        column.column_name.toLowerCase().includes(keyword)
        || (column.column_comment || '').toLowerCase().includes(keyword),
    )
    return matchesTable || matchesColumn
  })

  if (singleTableFilters.sortBy === 'columns') {
    items = [...items].sort((a, b) => b.columns.length - a.columns.length || a.table_name.localeCompare(b.table_name))
  } else if (singleTableFilters.sortBy === 'updated') {
    items = [...items].sort((a, b) => b.id - a.id)
  } else {
    items = [...items].sort((a, b) => a.table_name.localeCompare(b.table_name))
  }

  return items
})

const pagedSingleTables = computed(() => {
  const start = (singleTableFilters.page - 1) * singleTableFilters.pageSize
  return filteredSingleTables.value.slice(start, start + singleTableFilters.pageSize)
})

function resetDbForm() {
  dbForm.id = null
  dbForm.name = ''
  dbForm.db_type = 'mysql'
}

function updateSingleTableEditorFromSelection() {
  if (!selectedTable.value) {
    singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
    return
  }

  singleTableUploadText.value = JSON.stringify(
    {
      table: {
        table_name: selectedTable.value.table_name,
        table_comment: selectedTable.value.table_comment || '',
        columns: selectedTable.value.columns.map((column) => ({
          column_name: column.column_name,
          data_type: column.data_type,
          column_comment: column.column_comment || '',
        })),
      },
      relations: selectedTableRelations.value.map((relation) => ({
        from_table: relation.from_table,
        from_column: relation.from_column,
        to_table: relation.to_table,
        to_column: relation.to_column,
        relation_type: relation.relation_type,
      })),
    },
    null,
    2,
  )
}

function resetSingleTablePager() {
  singleTableFilters.page = 1
}

function ensureSelectedTableVisible() {
  if (!filteredSingleTables.value.length) {
    selectedTableName.value = ''
    return
  }

  if (!filteredSingleTables.value.some((item) => item.table_name === selectedTableName.value)) {
    selectedTableName.value = filteredSingleTables.value[0].table_name
  }

  const selectedIndex = filteredSingleTables.value.findIndex((item) => item.table_name === selectedTableName.value)
  if (selectedIndex < 0) {
    return
  }

  const nextPage = Math.floor(selectedIndex / singleTableFilters.pageSize) + 1
  if (singleTableFilters.page !== nextPage) {
    singleTableFilters.page = nextPage
  }
}

function handleSingleTableFilterChange() {
  resetSingleTablePager()
  ensureSelectedTableVisible()
  updateSingleTableEditorFromSelection()
}

function handleSingleTablePageChange(page) {
  singleTableFilters.page = page
  const currentPageTables = pagedSingleTables.value
  if (currentPageTables.length && !currentPageTables.some((item) => item.table_name === selectedTableName.value)) {
    selectedTableName.value = currentPageTables[0].table_name
    updateSingleTableEditorFromSelection()
  }
}

function handleSingleTablePageSizeChange(pageSize) {
  singleTableFilters.pageSize = pageSize
  resetSingleTablePager()
  ensureSelectedTableVisible()
  updateSingleTableEditorFromSelection()
}

async function loadDbDefinitions(preferredId = selectedDbId.value) {
  loadingDbDefs.value = true
  try {
    const data = await getDbDefinitions()
    dbDefinitions.value = data

    if (!data.length) {
      selectedDbId.value = null
      selectedTableName.value = ''
      schema.value = createEmptySchema(selectedDbId.value)
      resetDbForm()
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
  dbForm.id = matched.id
  dbForm.name = matched.name
  dbForm.db_type = matched.db_type
  await loadSchema()
}

function startCreate() {
  selectedDbId.value = null
  selectedTableName.value = ''
  schema.value = createEmptySchema(selectedDbId.value)
  singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
  singleTableFilters.keyword = ''
  singleTableFilters.sortBy = 'name'
  resetSingleTablePager()
  resetDbForm()
}

async function saveDbDefinition() {
  if (!dbForm.name.trim()) {
    ElMessage.warning('请输入数据库定义名称。')
    return
  }

  savingDbDef.value = true
  try {
    let saved
    const payload = {
      name: dbForm.name.trim(),
      db_type: dbForm.db_type,
    }

    if (dbForm.id) {
      saved = await updateDbDefinition(dbForm.id, payload)
      ElMessage.success('数据库定义已更新。')
    } else {
      saved = await createDbDefinition(payload)
      ElMessage.success('数据库定义已创建。')
    }

    await loadDbDefinitions(saved.id)
  } catch (error) {
    ElMessage.error(extractError(error, '保存数据库定义失败。'))
  } finally {
    savingDbDef.value = false
  }
}

async function confirmDelete(row) {
  try {
    await ElMessageBox.confirm(
      `你将删除数据库定义“${row.name}”。该定义下的表、字段、关系和关联 SQL 历史都会被永久删除，且无法恢复。`,
      '第一次确认',
      {
        type: 'warning',
        confirmButtonText: '继续删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await ElMessageBox.prompt(
      `请输入数据库定义名称 “${row.name}” 以继续删除。`,
      '第二次确认',
      {
        inputPlaceholder: '请输入完整名称',
        confirmButtonText: '名称确认无误',
        cancelButtonText: '取消',
        inputValidator: (value) => {
          if (value.trim() !== row.name) {
            return '输入名称与当前数据库定义不一致。'
          }
          return true
        },
      },
    )
  } catch {
    return
  }

  let confirmPhrase = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `最后一步，请输入 ${DELETE_CONFIRM_PHRASE} 以执行不可逆删除。`,
      '最终确认',
      {
        inputPlaceholder: DELETE_CONFIRM_PHRASE,
        confirmButtonText: '确认永久删除',
        cancelButtonText: '取消',
        inputValidator: (value) => {
          if (value.trim() !== DELETE_CONFIRM_PHRASE) {
            return `请输入准确的 ${DELETE_CONFIRM_PHRASE}。`
          }
          return true
        },
      },
    )
    confirmPhrase = value.trim()
  } catch {
    return
  }

  loadingDbDefs.value = true
  try {
    await deleteDbDefinition(row.id, {
      confirm_name: row.name,
      confirm_phrase: confirmPhrase,
    })
    ElMessage.success('数据库定义已删除。')
    const nextPreferredId = selectedDbId.value === row.id ? null : selectedDbId.value
    await loadDbDefinitions(nextPreferredId)
  } catch (error) {
    ElMessage.error(extractError(error, '删除数据库定义失败。'))
  } finally {
    loadingDbDefs.value = false
  }
}

async function loadSchema() {
  if (!selectedDbId.value) {
    schema.value = createEmptySchema(selectedDbId.value)
    selectedTableName.value = ''
    singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
    singleTableFilters.keyword = ''
    resetSingleTablePager()
    return
  }

  loadingSchema.value = true
  try {
    schema.value = await getTableSchema(selectedDbId.value)
    if (!schema.value.tables.length) {
      selectedTableName.value = ''
      singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
      resetSingleTablePager()
      return
    }

    ensureSelectedTableVisible()
    updateSingleTableEditorFromSelection()
  } catch (error) {
    schema.value = createEmptySchema(selectedDbId.value)
    selectedTableName.value = ''
    singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
    ElMessage.error(extractError(error, '加载表结构失败。'))
  } finally {
    loadingSchema.value = false
  }
}

function selectTable(tableName) {
  selectedTableName.value = tableName
  ensureSelectedTableVisible()
  updateSingleTableEditorFromSelection()
}

function resetSingleTableEditor() {
  updateSingleTableEditorFromSelection()
}

function startSingleTableCreate() {
  selectedTableName.value = ''
  singleTableUploadText.value = DEFAULT_SINGLE_TABLE_JSON
}

async function importBulkSchema() {
  if (!selectedDbId.value) {
    ElMessage.warning('请先选中一个数据库定义。')
    return
  }

  let payload
  try {
    payload = JSON.parse(bulkUploadText.value)
  } catch {
    ElMessage.error('全量 JSON 格式不合法，请检查后重试。')
    return
  }

  if (!Array.isArray(payload.tables) || !Array.isArray(payload.relations || [])) {
    ElMessage.error('全量 JSON 需要包含 tables 和 relations 数组。')
    return
  }

  importingSchema.value = true
  try {
    schema.value = await uploadTableSchema(selectedDbId.value, {
      tables: payload.tables,
      relations: payload.relations || [],
    })
    if (schema.value.tables.length) {
      selectedTableName.value = schema.value.tables[0].table_name
      ensureSelectedTableVisible()
      updateSingleTableEditorFromSelection()
    }
    ElMessage.success('全量表结构导入成功。')
  } catch (error) {
    ElMessage.error(extractError(error, '全量表结构导入失败。'))
  } finally {
    importingSchema.value = false
  }
}

async function saveSingleTable() {
  if (!selectedDbId.value) {
    ElMessage.warning('请先选中一个数据库定义。')
    return
  }

  let payload
  try {
    payload = JSON.parse(singleTableUploadText.value)
  } catch {
    ElMessage.error('单表 JSON 格式不合法，请检查后重试。')
    return
  }

  if (!payload?.table || !Array.isArray(payload.table.columns) || !Array.isArray(payload.relations || [])) {
    ElMessage.error('单表 JSON 需要包含 table 对象、table.columns 数组和 relations 数组。')
    return
  }

  const tableName = payload.table.table_name.trim()
  const isExistingTable = schema.value.tables.some((item) => item.table_name === tableName)

  savingSingleTable.value = true
  try {
    schema.value = await uploadSingleTableSchema(selectedDbId.value, {
      table: payload.table,
      relations: payload.relations || [],
    })
    selectedTableName.value = tableName
    ensureSelectedTableVisible()
    updateSingleTableEditorFromSelection()
    ElMessage.success(isExistingTable ? '单表已更新。' : '单表已导入。')
  } catch (error) {
    ElMessage.error(extractError(error, '单表保存失败。'))
  } finally {
    savingSingleTable.value = false
  }
}

async function confirmDeleteTable(table) {
  try {
    await ElMessageBox.confirm(
      `你将删除数据表“${table.table_name}”。该表字段以及涉及该表的关系都会被移除。`,
      '删除数据表',
      {
        type: 'warning',
        confirmButtonText: '继续删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await ElMessageBox.prompt(
      `请输入数据表名称 “${table.table_name}” 以确认删除。`,
      '最终确认',
      {
        inputPlaceholder: '请输入完整表名',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        inputValidator: (value) => {
          if (value.trim() !== table.table_name) {
            return '输入表名与当前数据表不一致。'
          }
          return true
        },
      },
    )
  } catch {
    return
  }

  deletingSingleTable.value = true
  try {
    schema.value = await deleteSingleTableSchema(selectedDbId.value, table.table_name, {
      confirm_name: table.table_name,
    })
    ensureSelectedTableVisible()
    updateSingleTableEditorFromSelection()
    ElMessage.success('数据表已删除。')
  } catch (error) {
    ElMessage.error(extractError(error, '删除数据表失败。'))
  } finally {
    deletingSingleTable.value = false
  }
}

async function handleSchemaFileChange(uploadFile, targetRef, label) {
  if (!uploadFile.raw) {
    return
  }

  try {
    targetRef.value = await uploadFile.raw.text()
    ElMessage.success(`已读取${label} JSON 文件：${uploadFile.name}`)
  } catch {
    ElMessage.error(`读取${label}上传文件失败。`)
  }
}

async function handleBulkFileChange(uploadFile) {
  await handleSchemaFileChange(uploadFile, bulkUploadText, '全量')
}

async function handleSingleTableFileChange(uploadFile) {
  await handleSchemaFileChange(uploadFile, singleTableUploadText, '单表')
}

onMounted(() => {
  loadDbDefinitions()
})
</script>

<template>
  <div class="page-stack">
    <el-row :gutter="20">
      <el-col :xs="24" :lg="9">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>数据库定义</h2>
                <p>维护可供 SQL 生成页选择的数据库别名和默认方言。</p>
              </div>
              <div class="section-actions">
                <el-button @click="loadDbDefinitions" :loading="loadingDbDefs">刷新</el-button>
                <el-button type="primary" @click="startCreate">新增</el-button>
              </div>
            </div>
          </template>

          <el-alert
            type="warning"
            show-icon
            :closable="false"
            title="删除数据库定义将永久移除其表结构、关系和关联历史，请谨慎操作。"
            class="delete-alert"
          />

          <el-table
            :data="dbDefinitions"
            stripe
            highlight-current-row
            v-loading="loadingDbDefs"
            @row-click="(row) => selectDb(row.id)"
          >
            <el-table-column prop="name" label="名称" min-width="170" />
            <el-table-column label="方言" min-width="120">
              <template #default="{ row }">
                <el-tag>{{ formatDbType(row.db_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150">
              <template #default="{ row }">
                <div class="table-actions">
                  <el-button link type="primary" @click.stop="selectDb(row.id)">编辑</el-button>
                  <el-button link type="danger" @click.stop="confirmDelete(row)">删除</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="15">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>{{ dbForm.id ? '编辑数据库定义' : '新建数据库定义' }}</h2>
                <p>为一套表结构定义一个业务名称，并指定默认数据库方言。</p>
              </div>
            </div>
          </template>

          <div class="form-grid">
            <el-form-item label="数据库名称">
              <el-input v-model="dbForm.name" placeholder="例如：生产 Oracle 库" />
            </el-form-item>

            <el-form-item label="数据库类型">
              <el-select v-model="dbForm.db_type" style="width: 100%">
                <el-option label="MySQL" value="mysql" />
                <el-option label="PostgreSQL" value="pg" />
                <el-option label="Oracle" value="oracle" />
              </el-select>
            </el-form-item>

            <div class="form-grid-full form-actions">
              <el-button @click="startCreate">重置</el-button>
              <el-button type="primary" :loading="savingDbDef" @click="saveDbDefinition">
                {{ dbForm.id ? '保存修改' : '创建定义' }}
              </el-button>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20">
      <el-col :xs="24" :xl="9">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>单表维护</h2>
                <p>
                  {{
                    selectedDb
                      ? `当前数据库：${selectedDb.name}`
                      : '请先创建或选择一个数据库定义。'
                  }}
                </p>
              </div>
              <div class="section-actions" v-if="selectedDb">
                <el-button @click="loadSchema" :loading="loadingSchema">刷新表列表</el-button>
              </div>
            </div>
          </template>

          <el-empty
            v-if="!selectedDb"
            description="请选择数据库定义后维护单表。"
          />

          <template v-else>
            <el-alert
              type="success"
              show-icon
              :closable="false"
              title="推荐优先使用单表导入：失败影响更小，也更方便后续局部修改。"
              class="single-table-alert"
            />

            <el-empty
              v-if="!schema.tables.length && !loadingSchema"
              description="当前定义还没有导入任何数据表。"
            />

            <div v-else class="single-table-management" v-loading="loadingSchema">
              <div class="single-table-toolbar">
                <el-input
                  v-model="singleTableFilters.keyword"
                  placeholder="搜索表名 / 注释 / 字段名"
                  clearable
                  @input="handleSingleTableFilterChange"
                  @clear="handleSingleTableFilterChange"
                />
                <el-select v-model="singleTableFilters.sortBy" @change="handleSingleTableFilterChange">
                  <el-option label="按表名排序" value="name" />
                  <el-option label="按字段数排序" value="columns" />
                  <el-option label="按最新导入排序" value="updated" />
                </el-select>
              </div>

              <div class="single-table-summary">
                <span>共 {{ schema.tables.length }} 张表</span>
                <span>筛选后 {{ filteredSingleTables.length }} 张</span>
                <span>当前页 {{ pagedSingleTables.length }} 张</span>
              </div>

              <el-empty
                v-if="!filteredSingleTables.length"
                description="没有匹配的表，请调整搜索条件。"
              />

              <div v-else class="single-table-list">
              <div
                v-for="table in pagedSingleTables"
                :key="table.id"
                class="single-table-item"
                :class="{ active: table.table_name === selectedTableName }"
                @click="selectTable(table.table_name)"
              >
                <div>
                  <strong>{{ table.table_name }}</strong>
                  <p>{{ table.table_comment || '暂无表注释' }}</p>
                </div>
                <div class="single-table-meta">
                  <el-tag effect="plain">{{ table.columns.length }} 字段</el-tag>
                  <el-button link type="danger" @click.stop="confirmDeleteTable(table)">
                    删除
                  </el-button>
                </div>
              </div>

                <div class="single-table-pagination">
                  <el-pagination
                    small
                    background
                    layout="total, sizes, prev, pager, next"
                    :total="filteredSingleTables.length"
                    :current-page="singleTableFilters.page"
                    :page-size="singleTableFilters.pageSize"
                    :page-sizes="[10, 20, 50]"
                    @current-change="handleSingleTablePageChange"
                    @size-change="handleSingleTablePageSizeChange"
                  />
                </div>
              </div>
            </div>
          </template>
        </el-card>
      </el-col>

      <el-col :xs="24" :xl="15">
        <el-card class="panel-card" shadow="never">
          <template #header>
            <div class="section-header">
              <div>
                <h2>单表 JSON 导入 / 修改</h2>
                <p>
                  {{
                    selectedTable
                      ? `正在编辑：${selectedTable.table_name}`
                      : '填写一个表的 JSON，可以新增，也可以覆盖更新同名表。'
                  }}
                </p>
              </div>
              <div class="section-actions" v-if="selectedDb">
                <el-button type="primary" plain @click="startSingleTableCreate">新增单表</el-button>
                <el-upload
                  accept=".json"
                  :auto-upload="false"
                  :show-file-list="false"
                  :on-change="handleSingleTableFileChange"
                >
                  <el-button>读取单表 JSON</el-button>
                </el-upload>
                <el-button @click="resetSingleTableEditor">重置编辑器</el-button>
                <el-button type="primary" :loading="savingSingleTable" @click="saveSingleTable">
                  保存当前单表
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            v-if="!selectedDb"
            type="info"
            show-icon
            :closable="false"
            title="请先选择数据库定义，再进行单表导入或修改。"
          />

          <template v-else>
            <div class="import-tips">
              <div class="tip-item">
                <strong>新增或修改规则</strong>
                <span>同名表再次导入时，会只覆盖当前这张表及其关联关系，不影响其他表。</span>
              </div>
              <div class="tip-item">
                <strong>relations 规则</strong>
                <span>relations 中只需要填写和当前表相关的关系，且关系两端的表都必须已存在。</span>
              </div>
              <div class="tip-item">
                <strong>relations 方向</strong>
                <span>`from_table/from_column` 填被引用端，`to_table/to_column` 填外键端。例如一个用户对应多个订单时，写 `users.id -> orders.user_id`。</span>
              </div>
              <div class="tip-item">
                <strong>relations 示例</strong>
                <span>如果当前维护的是 `orders` 单表，relations 里仍然这样写，不需要把当前表强行写在前面。</span>
              </div>
            </div>

            <pre class="relation-example">{{ RELATION_EXAMPLE_JSON }}</pre>

            <el-input
              v-model="singleTableUploadText"
              type="textarea"
              :rows="22"
              resize="vertical"
              class="schema-json-editor"
            />
          </template>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>全量导入</h2>
            <p>保留整库覆盖导入，适合首次初始化；日常维护建议优先使用上方单表导入。</p>
          </div>
          <div class="section-actions" v-if="selectedDb">
            <el-upload
              accept=".json"
              :auto-upload="false"
              :show-file-list="false"
              :on-change="handleBulkFileChange"
            >
              <el-button>读取全量 JSON</el-button>
            </el-upload>
            <el-button type="primary" :loading="importingSchema" @click="importBulkSchema">
              执行全量覆盖导入
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="!selectedDb"
        type="info"
        show-icon
        :closable="false"
        title="左侧选中一个数据库定义后，再执行全量导入。"
      />

      <template v-else>
        <el-alert
          type="warning"
          show-icon
          :closable="false"
          title="全量导入会先清空当前数据库定义下的所有表、字段与关系，再整体重建。"
          class="delete-alert"
        />

        <el-input
          v-model="bulkUploadText"
          type="textarea"
          :rows="12"
          resize="vertical"
          class="schema-json-editor"
        />
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.table-actions {
  display: flex;
  gap: 8px;
}

.delete-alert,
.single-table-alert {
  margin-bottom: 16px;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.schema-json-editor :deep(textarea) {
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
}

.relation-example {
  margin: 0 0 16px;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(15, 23, 42, 0.92);
  color: #e2e8f0;
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, monospace;
  font-size: 0.9rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.import-tips {
  display: grid;
  gap: 12px;
  margin-bottom: 16px;
  padding: 16px 18px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(20, 184, 166, 0.08), rgba(251, 146, 60, 0.07));
}

.tip-item {
  display: grid;
  gap: 4px;
}

.tip-item strong {
  font-size: 0.96rem;
}

.tip-item span,
.single-table-item p {
  color: var(--text-secondary);
  font-size: 0.92rem;
  line-height: 1.6;
}

.single-table-list {
  display: grid;
  gap: 12px;
}

.single-table-management {
  display: grid;
  gap: 14px;
}

.single-table-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px;
  gap: 12px;
}

.single-table-summary {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--text-secondary);
  font-size: 0.92rem;
}

.single-table-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-radius: 18px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(248, 250, 252, 0.86);
  cursor: pointer;
  transition: all 0.2s ease;
}

.single-table-item.active {
  border-color: rgba(15, 118, 110, 0.36);
  background:
    linear-gradient(135deg, rgba(20, 184, 166, 0.1), rgba(251, 146, 60, 0.08));
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.single-table-item strong {
  font-size: 1rem;
}

.single-table-item p {
  margin: 6px 0 0;
}

.single-table-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.single-table-pagination {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .single-table-toolbar {
    grid-template-columns: 1fr;
  }
}
</style>
