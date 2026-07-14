<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import RetrievedTablesSummary from '../components/RetrievedTablesSummary.vue'
import { generateSql, getDbDefinitions, submitSqlFeedback } from '../api'
import { extractError } from '../utils/errors'

defineOptions({
  name: 'SqlWorkbenchView',
})

const loadingDbDefs = ref(false)
const generating = ref(false)
const dbDefinitions = ref([])
const sqlResult = ref('')
const noSqlReason = ref('')
const retrievedTables = ref([])
const retrievalMode = ref('')
const generationElapsedSeconds = ref(0)
const generationHistoryId = ref(null)
const feedbackType = ref('')
const correctedSql = ref('')
const submittingFeedback = ref(false)
const feedbackSubmitted = ref(false)

const form = reactive({
  db_id: null,
  target_db_type: 'mysql',
  natural_text: '',
})

const selectedDb = computed(
  () => dbDefinitions.value.find((item) => item.id === form.db_id) || null,
)
const isNoSql = computed(() => sqlResult.value === 'NO_SQL')
const canProvideFeedback = computed(
  () => Boolean(generationHistoryId.value && sqlResult.value && !isNoSql.value && !feedbackSubmitted.value),
)
const generationElapsedText = computed(() => {
  const minutes = Math.floor(generationElapsedSeconds.value / 60)
  const seconds = generationElapsedSeconds.value % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
})

let generationTimerId = 0
let generationStartedAt = 0

watch(
  () => form.db_id,
  (dbId) => {
    const matched = dbDefinitions.value.find((item) => item.id === dbId)
    if (matched) {
      form.target_db_type = matched.db_type
    }
  },
)

function resetGenerationState() {
  sqlResult.value = ''
  noSqlReason.value = ''
  retrievedTables.value = []
  retrievalMode.value = ''
  generationHistoryId.value = null
  feedbackType.value = ''
  correctedSql.value = ''
  feedbackSubmitted.value = false
}

function startGenerationTimer() {
  stopGenerationTimer()
  generationStartedAt = Date.now()
  generationElapsedSeconds.value = 0
  generationTimerId = window.setInterval(() => {
    generationElapsedSeconds.value = Math.floor((Date.now() - generationStartedAt) / 1000)
  }, 1000)
}

function stopGenerationTimer() {
  if (generationTimerId) {
    window.clearInterval(generationTimerId)
    generationTimerId = 0
  }
}

async function loadDbDefs() {
  loadingDbDefs.value = true
  try {
    const data = await getDbDefinitions()
    dbDefinitions.value = data
    if (data.length && !form.db_id) {
      form.db_id = data[0].id
      form.target_db_type = data[0].db_type
    }
  } catch (error) {
    ElMessage.error(extractError(error, '加载数据库定义失败。'))
  } finally {
    loadingDbDefs.value = false
  }
}

async function handleGenerate() {
  if (!form.db_id) {
    ElMessage.warning('请先选择数据库定义。')
    return
  }
  if (!form.natural_text.trim()) {
    ElMessage.warning('请先输入自然语言需求。')
    return
  }

  generating.value = true
  resetGenerationState()
  startGenerationTimer()
  try {
    const data = await generateSql({
      db_id: form.db_id,
      target_db_type: form.target_db_type,
      natural_text: form.natural_text.trim(),
    })
    sqlResult.value = data.sql
    noSqlReason.value = data.no_sql_reason || ''
    retrievedTables.value = data.retrieved_tables || []
    retrievalMode.value = data.retrieval_mode || ''
    generationHistoryId.value = data.history_id || null
    if (data.sql === 'NO_SQL') {
      ElMessage.warning(noSqlReason.value || '当前 schema 无法准确生成 SQL。')
    } else {
      ElMessage.success('SQL 生成成功。')
    }
  } catch (error) {
    resetGenerationState()
    ElMessage.error(extractError(error, 'SQL 生成失败。'))
  } finally {
    stopGenerationTimer()
    generating.value = false
  }
}

async function submitFeedback() {
  if (!feedbackType.value) {
    ElMessage.warning('请选择反馈类型后再提交。')
    return
  }
  if (feedbackType.value === 'modified' && !correctedSql.value.trim()) {
    ElMessage.warning('请填写修正后的 SQL。')
    return
  }

  submittingFeedback.value = true
  try {
    await submitSqlFeedback({
      history_id: generationHistoryId.value,
      feedback_type: feedbackType.value,
      corrected_sql: feedbackType.value === 'modified' ? correctedSql.value.trim() : undefined,
    })
    feedbackSubmitted.value = true
    ElMessage.success('反馈已保存。')
  } catch (error) {
    ElMessage.error(extractError(error, '保存反馈失败。'))
  } finally {
    submittingFeedback.value = false
  }
}

async function copySql() {
  if (!sqlResult.value || isNoSql.value) {
    ElMessage.warning('当前没有可复制的 SQL。')
    return
  }

  try {
    await navigator.clipboard.writeText(sqlResult.value)
    ElMessage.success('SQL 已复制到剪贴板。')
  } catch (error) {
    ElMessage.error('复制失败，请手动复制。')
  }
}

function fillDemoPrompt() {
  form.natural_text =
    '统计最近30天每个用户的订单数量和订单总金额，按订单总金额从高到低排序。'
}

onMounted(loadDbDefs)
onBeforeUnmount(stopGenerationTimer)
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>SQL 生成工作台</h2>
            <p>选择数据库定义和目标方言，然后用自然语言描述你的查询需求。</p>
          </div>
          <div class="tag-row">
            <el-tag effect="dark" type="success">支持 MySQL</el-tag>
            <el-tag effect="dark" type="warning">支持 PostgreSQL</el-tag>
            <el-tag effect="dark" type="info">支持 Oracle</el-tag>
          </div>
        </div>
      </template>

      <el-alert
        v-if="!dbDefinitions.length && !loadingDbDefs"
        type="warning"
        show-icon
        :closable="false"
        title="当前还没有数据库定义。请让管理员先去“表结构管理”页面创建并导入表结构。"
      />

      <div v-else class="form-grid">
        <el-form-item label="目标数据库定义">
          <el-select v-model="form.db_id" placeholder="请选择数据库定义" style="width: 100%">
            <el-option
              v-for="item in dbDefinitions"
              :key="item.id"
              :label="`${item.name} (${item.db_type})`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="目标 SQL 方言">
          <el-select v-model="form.target_db_type" placeholder="请选择 SQL 方言" style="width: 100%">
            <el-option label="MySQL" value="mysql" />
            <el-option label="PostgreSQL" value="pg" />
            <el-option label="Oracle" value="oracle" />
          </el-select>
        </el-form-item>

        <div class="form-grid-full">
          <div class="prompt-toolbar">
            <span class="muted-text">
              {{ selectedDb ? `当前数据库：${selectedDb.name}` : '请选择数据库定义' }}
            </span>
            <div class="section-actions">
              <el-button @click="loadDbDefs" :loading="loadingDbDefs">刷新定义</el-button>
              <el-button @click="fillDemoPrompt">填充示例</el-button>
            </div>
          </div>

          <el-input
            v-model="form.natural_text"
            type="textarea"
            :rows="8"
            resize="vertical"
            placeholder="例如：查询最近7天每个用户的订单总金额，并筛选金额大于1000的用户。"
          />
        </div>

        <div class="form-grid-full">
          <div class="sql-actions">
            <div v-if="generating" class="generation-status">
              <el-tag type="info" effect="plain">已思考 {{ generationElapsedText }}</el-tag>
              <span class="muted-text">如果最终失败，系统会区分是超时还是生成失败。</span>
            </div>
            <el-button type="primary" size="large" :loading="generating" @click="handleGenerate">
              生成 SQL
            </el-button>
          </div>
        </div>
      </div>
    </el-card>

    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>生成结果</h2>
            <p>大模型只返回 SQL 文本，便于直接复制到数据库客户端执行。</p>
          </div>
          <el-button plain :disabled="!sqlResult || isNoSql" @click="copySql">复制 SQL</el-button>
        </div>
      </template>

      <div v-if="generating" class="generation-alert">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          title="SQL 生成中"
          :description="`模型正在思考，已等待 ${generationElapsedText}。`"
        />
      </div>

      <div v-if="retrievedTables.length" class="retrieval-meta">
        <div class="retrieval-summary">
          <span class="muted-text">
            {{ retrievalMode ? `RAG: ${retrievalMode}` : 'RAG' }}
          </span>
          <span class="muted-text">命中表较多时已自动折叠展示。</span>
        </div>
        <RetrievedTablesSummary :tables="retrievedTables" :inline-limit="6" />
      </div>

      <el-empty
        v-if="!sqlResult && !generating"
        description="SQL 结果会显示在这里。"
      />

      <el-alert
        v-else-if="isNoSql"
        type="warning"
        show-icon
        :closable="false"
        title="未能生成 SQL"
        :description="noSqlReason || '当前 schema 无法准确支撑该需求。'"
      />

      <template v-else-if="sqlResult">
        <pre class="code-panel">{{ sqlResult }}</pre>

        <section v-if="canProvideFeedback" class="feedback-panel" aria-label="SQL 反馈">
          <span class="feedback-label">反馈</span>
          <el-radio-group v-model="feedbackType" :disabled="submittingFeedback">
            <el-radio-button label="correct">SQL 正确</el-radio-button>
            <el-radio-button label="modified">需要修改</el-radio-button>
          </el-radio-group>

          <el-input
            v-if="feedbackType === 'modified'"
            v-model="correctedSql"
            type="textarea"
            :rows="6"
            resize="vertical"
            placeholder="请填写修正后的 SQL"
          />

          <div class="feedback-actions">
            <el-button
              type="primary"
              :disabled="!feedbackType"
              :loading="submittingFeedback"
              @click="submitFeedback"
            >
              提交反馈
            </el-button>
          </div>
        </section>

        <el-alert
          v-else-if="feedbackSubmitted"
          class="feedback-confirmation"
          type="success"
          :closable="false"
          title="反馈已保存"
        />
      </template>
    </el-card>
  </div>
</template>

<style scoped>
.prompt-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.sql-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.generation-status {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.generation-alert {
  margin-bottom: 14px;
}

.retrieval-meta {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}

.retrieval-summary {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.feedback-panel {
  display: grid;
  gap: 12px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--card-border);
}

.feedback-label {
  font-weight: 600;
}

.feedback-actions {
  display: flex;
  justify-content: flex-end;
}

.feedback-confirmation {
  margin-top: 16px;
}
</style>
