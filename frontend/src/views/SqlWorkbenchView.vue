<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import RetrievedTablesSummary from '../components/RetrievedTablesSummary.vue'
import { generateSql, getDbDefinitions, submitSqlFeedback } from '../api'
import { extractError } from '../utils/errors'
import { formatDbType } from '../utils/format'

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
const requestId = ref('')
const promptVersion = ref('')
const policyVersion = ref('')
const noSqlCode = ref('')
const validationStatus = ref('not_run')
const validationErrors = ref([])
const validationWarnings = ref([])
const assumptions = ref([])
const retrievedEvidence = ref([])
const retrievedTerms = ref([])
const modelCalls = ref(0)
const generationElapsedSeconds = ref(0)
const generationHistoryId = ref(null)
const feedbackType = ref('')
const correctedSql = ref('')
const submittingFeedback = ref(false)
const feedbackSubmitted = ref(false)
const resultPanelRef = ref(null)

const form = reactive({
  db_id: null,
  target_db_type: 'mysql',
  natural_text: '',
})

const selectedDb = computed(
  () => dbDefinitions.value.find((item) => item.id === form.db_id) || null,
)
const isNoSql = computed(() => sqlResult.value === 'NO_SQL')
const canCopySql = computed(
  () => Boolean(sqlResult.value && !isNoSql.value && validationStatus.value === 'passed'),
)
const canProvideFeedback = computed(
  () => Boolean(generationHistoryId.value && canCopySql.value && !feedbackSubmitted.value),
)
const dialectLabel = computed(() => formatDbType(selectedDb.value?.db_type || form.target_db_type))
const validationTag = computed(() => {
  if (validationStatus.value === 'passed') {
    return { type: 'success', label: '本地校验通过' }
  }
  if (validationStatus.value === 'failed') {
    return { type: 'danger', label: '本地校验失败' }
  }
  return { type: 'info', label: '未执行本地校验' }
})
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
  requestId.value = ''
  promptVersion.value = ''
  policyVersion.value = ''
  noSqlCode.value = ''
  validationStatus.value = 'not_run'
  validationErrors.value = []
  validationWarnings.value = []
  assumptions.value = []
  retrievedEvidence.value = []
  retrievedTerms.value = []
  modelCalls.value = 0
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
    requestId.value = data.request_id || ''
    promptVersion.value = data.prompt_version || ''
    policyVersion.value = data.policy_version || ''
    noSqlCode.value = data.no_sql_code || ''
    validationStatus.value = data.validation_status || 'not_run'
    validationErrors.value = Array.isArray(data.validation_errors) ? data.validation_errors : []
    validationWarnings.value = Array.isArray(data.warnings) ? data.warnings : []
    assumptions.value = Array.isArray(data.assumptions) ? data.assumptions : []
    retrievedEvidence.value = Array.isArray(data.retrieved_evidence) ? data.retrieved_evidence : []
    retrievedTerms.value = Array.isArray(data.retrieved_terms) ? data.retrieved_terms : []
    modelCalls.value = Number.isFinite(Number(data.model_calls)) ? Number(data.model_calls) : 0
    generationHistoryId.value = data.history_id || null
    await revealResultPanel()
    if (data.sql === 'NO_SQL') {
      ElMessage.warning(noSqlReason.value || '当前 schema 无法准确生成 SQL。')
    } else if (validationStatus.value === 'passed') {
      ElMessage.success('SQL 生成成功。')
    } else {
      ElMessage.error('候选 SQL 未通过本地校验，已禁止复制。')
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
  if (!canCopySql.value) {
    ElMessage.warning('只有通过本地校验的 SQL 才能复制。')
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

async function revealResultPanel() {
  await nextTick()
  resultPanelRef.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(loadDbDefs)
onBeforeUnmount(stopGenerationTimer)
</script>

<template>
  <div class="page-stack sql-workbench">
    <div class="workbench-layout">
      <el-card class="panel-card prompt-panel" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>SQL 生成工作台</h2>
            <p>选择 Schema 定义后，系统自动使用其方言生成并静态校验只读 SQL。</p>
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
        <el-form-item class="workbench-form-item" label="目标数据库定义">
          <el-select v-model="form.db_id" placeholder="请选择数据库定义" style="width: 100%">
            <el-option
              v-for="item in dbDefinitions"
              :key="item.id"
              :label="`${item.name} (${item.db_type})`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item class="workbench-form-item" label="目标 SQL 方言">
          <div class="readonly-dialect" aria-label="由数据库定义自动确定的 SQL 方言">
            <el-tag effect="plain" size="large">{{ dialectLabel }}</el-tag>
            <span class="muted-text">由所选数据库定义自动确定，不可手动更改。</span>
          </div>
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
              <el-tag type="info" effect="plain">处理中 {{ generationElapsedText }}</el-tag>
              <span class="muted-text">本地校验失败时最多自动修复一次；总调用次数不超过两次。</span>
            </div>
            <el-button type="primary" size="large" :loading="generating" @click="handleGenerate">
              生成 SQL
            </el-button>
          </div>
        </div>
      </div>
    </el-card>

      <div ref="resultPanelRef" class="result-panel-anchor">
        <el-card class="panel-card result-panel" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>生成结果</h2>
            <p>结果供人工复核和后续使用；SQLGenie 不连接或执行目标数据库。</p>
          </div>
          <el-button plain :disabled="!canCopySql" @click="copySql">复制 SQL</el-button>
        </div>
      </template>

      <div v-if="generating" class="generation-alert">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          title="SQL 生成中"
          :description="`正在检索 Schema、调用远端模型并执行本地静态校验，已等待 ${generationElapsedText}。`"
        />
      </div>

      <template v-if="sqlResult">
        <section class="primary-result" aria-label="SQL 主结果">
          <div class="primary-result-bar">
            <div class="primary-result-title">
              <span class="result-eyebrow">SQL 结果</span>
              <el-tag :type="isNoSql ? 'warning' : validationTag.type" effect="plain">
                {{ isNoSql ? '需要澄清' : validationTag.label }}
              </el-tag>
            </div>
            <span class="muted-text">{{ dialectLabel }}</span>
          </div>
          <el-alert
            v-if="isNoSql"
            type="warning"
            show-icon
            :closable="false"
            title="未能生成 SQL"
            :description="noSqlReason || '当前 schema 无法准确支持该需求。'"
          />
          <pre v-else class="code-panel">{{ sqlResult }}</pre>
        </section>
      </template>

      <el-empty
        v-if="!sqlResult && !generating"
        description="SQL 结果会显示在这里。"
      />

      <section v-if="sqlResult" class="secondary-result-details" aria-label="生成详情">
      <div class="trace-grid" aria-label="生成追踪信息">
        <div class="trace-item">
          <span>校验状态</span>
          <el-tag :type="validationTag.type">{{ validationTag.label }}</el-tag>
        </div>
        <div class="trace-item">
          <span>请求 ID</span>
          <code :title="requestId">{{ requestId || '—' }}</code>
        </div>
        <div class="trace-item">
          <span>提示词版本</span>
          <strong>{{ promptVersion || '—' }}</strong>
        </div>
        <div class="trace-item">
          <span>策略版本</span>
          <strong>{{ policyVersion || '—' }}</strong>
        </div>
        <div class="trace-item">
          <span>模型调用</span>
          <strong>{{ modelCalls }} 次</strong>
        </div>
        <div class="trace-item">
          <span>未生成代码</span>
          <el-tag v-if="noSqlCode" type="warning" effect="plain">{{ noSqlCode }}</el-tag>
          <strong v-else>—</strong>
        </div>
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

      <section v-if="retrievedEvidence.length" class="result-section" aria-label="Schema 命中证据">
        <h3>Schema 命中证据</h3>
        <div class="evidence-grid">
          <article v-for="evidence in retrievedEvidence" :key="evidence.table_name" class="evidence-card">
            <div class="evidence-title">
              <strong>{{ evidence.table_name }}</strong>
              <el-tag type="success" effect="plain">
                综合 {{ Number(evidence.evidence_score || 0).toFixed(2) }}
              </el-tag>
            </div>
            <div class="tag-row">
              <el-tag v-for="reason in (evidence.reasons || [])" :key="reason" size="small" effect="plain">
                {{ reason }}
              </el-tag>
              <span v-if="evidence.expanded_from" class="muted-text">
                由 {{ evidence.expanded_from }} 关系扩展
              </span>
            </div>
            <div class="tag-row evidence-metrics">
              <el-tag v-if="Number(evidence.keyword_score || 0) > 0" size="small" type="info" effect="plain">
                关键词 {{ Number(evidence.keyword_score).toFixed(2) }}
              </el-tag>
              <el-tag v-if="evidence.vector_similarity != null" size="small" type="warning" effect="plain">
                向量 {{ Number(evidence.vector_similarity).toFixed(3) }}
              </el-tag>
              <el-tag v-if="evidence.vector_margin != null" size="small" type="warning" effect="plain">
                首位差 {{ Number(evidence.vector_margin).toFixed(3) }}
              </el-tag>
            </div>
            <p v-if="evidence.matched_terms?.length">命中：{{ evidence.matched_terms.join('、') }}</p>
          </article>
        </div>
      </section>

      <section v-if="retrievedTerms.length" class="result-section" aria-label="HIS 命中术语">
        <h3>HIS 命中术语</h3>
        <div class="term-list">
          <el-tag v-for="term in retrievedTerms" :key="term.id ?? `${term.term}-${term.scope}`" effect="plain">
            {{ term.term }} · {{ term.category || '未分类' }} · {{ term.scope || '全局' }} · {{ Number(term.score || 0).toFixed(2) }}
          </el-tag>
        </div>
      </section>

      <section v-if="assumptions.length" class="result-section" aria-label="生成假设">
        <h3>生成假设</h3>
        <ul class="issue-list assumptions-list">
          <li v-for="(item, index) in assumptions" :key="index">{{ item }}</li>
        </ul>
      </section>

      <section v-if="validationWarnings.length" class="result-section" aria-label="本地校验告警">
        <h3>告警</h3>
        <el-alert
          v-for="(warning, index) in validationWarnings"
          :key="`${warning.code || 'warning'}-${index}`"
          class="issue-alert"
          type="warning"
          show-icon
          :closable="false"
          :title="warning.code || 'WARNING'"
          :description="warning.message || String(warning)"
        />
      </section>

      <section v-if="validationErrors.length" class="result-section" aria-label="本地校验错误">
        <h3>校验错误</h3>
        <el-alert
          v-for="(issue, index) in validationErrors"
          :key="`${issue.code || 'error'}-${index}`"
          class="issue-alert"
          type="error"
          show-icon
          :closable="false"
          :title="issue.code || 'VALIDATION_FAILED'"
          :description="issue.message || String(issue)"
        />
      </section>

      </section>

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
        </el-card>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sql-workbench {
  gap: 0;
}

.workbench-layout {
  display: grid;
  grid-template-columns: minmax(340px, 0.88fr) minmax(0, 1.12fr);
  align-items: start;
  gap: 20px;
}

.prompt-panel,
.result-panel {
  min-width: 0;
}

.sql-workbench .form-grid {
  align-items: start;
}

.workbench-form-item {
  display: grid;
  min-width: 0;
  margin-bottom: 0;
  gap: 8px;
  align-items: start;
  align-content: start;
}

.workbench-form-item :deep(.el-form-item__label) {
  width: auto;
  min-height: 0;
  padding: 0;
  color: var(--text-primary);
  font-weight: 600;
  line-height: 1.5;
  text-align: left;
}

.workbench-form-item :deep(.el-form-item__content) {
  width: 100%;
  min-width: 0;
  min-height: 0;
  line-height: normal;
}

.workbench-form-item :deep(.el-select) {
  width: 100% !important;
  min-width: 0;
}

.workbench-form-item .readonly-dialect {
  width: 100%;
}

.result-panel-anchor {
  min-width: 0;
  align-self: start;
  position: sticky;
  top: 20px;
  scroll-margin-top: 20px;
}

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

.primary-result {
  display: grid;
  gap: 12px;
  margin-bottom: 18px;
}

.primary-result-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.82);
}

.primary-result-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.result-eyebrow {
  color: var(--text-primary);
  font-weight: 700;
}

.primary-result .code-panel {
  min-height: 260px;
  max-height: min(58vh, 620px);
}

.secondary-result-details {
  margin-top: 4px;
  padding-top: 18px;
  border-top: 1px solid var(--card-border);
}

.readonly-dialect {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 40px;
  flex-wrap: wrap;
}

.trace-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.trace-item {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.8);
}

.trace-item > span {
  color: var(--text-secondary);
  font-size: 0.82rem;
}

.trace-item code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-section {
  margin: 18px 0;
}

.result-section h3 {
  margin: 0 0 10px;
  font-size: 0.98rem;
}

.evidence-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.evidence-card {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.8);
}

.evidence-card p {
  margin: 10px 0 0;
  color: var(--text-secondary);
  font-size: 0.88rem;
  word-break: break-word;
}

.evidence-metrics {
  margin-top: 8px;
}

.evidence-title {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  word-break: break-word;
}

.term-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.issue-list {
  margin: 0;
  padding: 14px 18px 14px 34px;
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.86);
}

.issue-alert + .issue-alert {
  margin-top: 8px;
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

@media (max-width: 1100px) {
  .workbench-layout {
    grid-template-columns: 1fr;
  }

  .result-panel-anchor {
    position: static;
  }
}

@media (max-width: 860px) {
  .trace-grid,
  .evidence-grid {
    grid-template-columns: 1fr;
  }

  .sql-actions > .el-button {
    width: 100%;
  }

  .primary-result-bar {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
