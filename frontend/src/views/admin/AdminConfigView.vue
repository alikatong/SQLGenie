<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getConfig,
  initializeEmbeddingRag,
  pickEmbeddingModelDirectory,
  updateConfig,
} from '../../api'
import { extractError } from '../../utils/errors'
import { buildModelConfigPayload, mapModelConfigResponse } from '../../utils/modelConfig'

defineOptions({
  name: 'AdminConfigView',
})

const loading = ref(false)
const saving = ref(false)
const selectingEmbeddingModel = ref(false)
const initializingEmbedding = ref(false)
const initializationResult = ref(null)
const initializationSummary = computed(() => {
  const result = initializationResult.value
  if (!result) {
    return ''
  }
  return `Processed ${result.database_count ?? 0} databases, ${result.schema_table_count ?? 0} schema tables, and ${result.feedback_example_count ?? 0} verified SQL examples.`
})
const secret = reactive({
  configured: false,
  last4: '',
})
const retrieval = reactive({
  embeddingModel: '',
  expandDepth: null,
})

const form = reactive({
  api_key: '',
  base_url: '',
  model_name: '',
  enable_thinking: true,
  reasoning_effort: null,
  thinking_timeout_seconds: 600,
  prompt_max_chars: 60000,
  rag_top_k: 8,
  embedding_model_path: '',
})

async function loadConfig() {
  loading.value = true
  try {
    const data = await getConfig()
    const mapped = mapModelConfigResponse(data)
    Object.assign(form, mapped.form)
    Object.assign(secret, mapped.secret)
    Object.assign(retrieval, mapped.retrieval)
  } catch (error) {
    ElMessage.error(extractError(error, '加载系统配置失败。'))
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (!form.base_url.trim() || !form.model_name.trim()) {
    ElMessage.warning('请完整填写 Base URL 和模型名。')
    return false
  }

  if (!secret.configured && !form.api_key.trim()) {
    ElMessage.warning('当前尚未配置 API Key，请输入新密钥。')
    return false
  }

  if (!Number.isFinite(form.thinking_timeout_seconds) || form.thinking_timeout_seconds < 10) {
    ElMessage.warning('最大等待秒数不能小于 10 秒。')
    return false
  }

  if (!Number.isFinite(form.rag_top_k) || form.rag_top_k < 1 || form.rag_top_k > 20) {
    ElMessage.warning('Schema 检索 Top K 必须在 1 到 20 之间。')
    return false
  }

  if (!Number.isFinite(form.prompt_max_chars) || form.prompt_max_chars < 1000 || form.prompt_max_chars > 120000) {
    ElMessage.warning('模型上下文上限必须在 1000 到 120000 字符之间。')
    return false
  }

  saving.value = true
  try {
    const payload = buildModelConfigPayload(form)
    const data = await updateConfig(payload)
    const mapped = mapModelConfigResponse(data, { reasoning_effort: payload.reasoning_effort })
    Object.assign(form, mapped.form)
    Object.assign(secret, mapped.secret)
    Object.assign(retrieval, mapped.retrieval)
    ElMessage.success('系统配置已保存。')
    return true
  } catch (error) {
    ElMessage.error(extractError(error, '保存系统配置失败。'))
    return false
  } finally {
    saving.value = false
  }
}

async function chooseEmbeddingModelDirectory() {
  selectingEmbeddingModel.value = true
  try {
    const result = await pickEmbeddingModelDirectory()
    if (result.selected && result.embedding_model_path) {
      form.embedding_model_path = result.embedding_model_path
      ElMessage.success('Qwen Embedding 模型目录已选择，请保存配置。')
    }
  } catch (error) {
    ElMessage.error(extractError(error, '选择 Embedding 模型目录失败。'))
  } finally {
    selectingEmbeddingModel.value = false
  }
}

async function initializeEmbedding() {
  if (!form.embedding_model_path.trim()) {
    ElMessage.warning('请先填写 Qwen Embedding 模型本地目录。')
    return
  }

  if (form.embedding_model_path.trim() !== retrieval.embeddingModel.trim()) {
    const saved = await saveConfig()
    if (!saved) {
      return
    }
  }

  initializingEmbedding.value = true
  initializationResult.value = null
  try {
    const result = await initializeEmbeddingRag()
    initializationResult.value = result
    if (result.failed_databases?.length) {
      ElMessage.warning(`Embedding 初始化完成，但有 ${result.failed_databases.length} 个数据库失败。`)
    } else {
      ElMessage.success('Embedding RAG 初始化完成。')
    }
  } catch (error) {
    ElMessage.error(extractError(error, 'Embedding RAG 初始化失败。'))
  } finally {
    initializingEmbedding.value = false
  }
}

onMounted(loadConfig)
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never" v-loading="loading">
      <template #header>
        <div class="section-header">
          <div>
            <h2>大模型配置</h2>
            <p>配置 OpenAI 兼容接口；候选 SQL 先经本地校验，失败时可自动修复一次。</p>
          </div>
        </div>
      </template>

      <el-alert
        type="info"
        show-icon
        :closable="false"
        title="配置会保存到本地 SQLite。保存后，新发起的 SQL 生成请求会立即使用最新配置。"
      />

      <div class="config-form">
        <el-form label-position="top">
          <el-form-item label="API Key">
            <div class="secret-field">
              <el-input
                v-model="form.api_key"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="留空表示不更换现有密钥"
              />
              <div class="secret-status" aria-live="polite">
                <el-tag :type="secret.configured ? 'success' : 'warning'">
                  {{ secret.configured ? '已配置' : '未配置' }}
                </el-tag>
                <span v-if="secret.configured && secret.last4" class="muted-text">
                  尾四位：{{ secret.last4 }}
                </span>
              </div>
            </div>
            <div class="field-help">系统不会回显现有密钥；状态和尾四位不会写入输入框或保存载荷。</div>
          </el-form-item>

          <el-form-item label="Qwen Embedding 模型本地目录">
            <div class="embedding-path-picker">
              <el-input
                v-model="form.embedding_model_path"
                readonly
                placeholder="点击右侧按钮选择 Qwen 模型目录"
              />
              <el-button
                type="primary"
                plain
                :loading="selectingEmbeddingModel"
                :disabled="saving || loading || initializingEmbedding"
                @click="chooseEmbeddingModelDirectory"
              >
                选择目录
              </el-button>
            </div>
            <div class="field-help">
              点击后打开本机系统目录选择器；仅支持包含 SentenceTransformers 文件和 config.json 的 Qwen 模型目录。
            </div>
          </el-form-item>

          <el-form-item label="Base URL">
            <el-input
              v-model="form.base_url"
              placeholder="例如：https://api.openai.com/v1"
            />
          </el-form-item>

          <el-form-item label="模型名">
            <el-input
              v-model="form.model_name"
              placeholder="例如：gpt-4o-mini"
            />
          </el-form-item>

          <el-form-item label="校验失败后自动修复">
            <div class="thinking-row">
              <el-switch v-model="form.enable_thinking" />
              <span class="muted-text">开启后，仅当本地校验失败时调用同一远端模型修复一次。</span>
            </div>
          </el-form-item>

          <el-form-item label="模型思考强度">
            <el-select
              v-model="form.reasoning_effort"
              clearable
              placeholder="跟随模型（未设置）"
              style="width: 220px"
            >
              <el-option label="Low" value="low" />
              <el-option label="Medium" value="medium" />
              <el-option label="High" value="high" />
              <el-option label="XHigh" value="xhigh" />
              <el-option label="Max" value="max" />
            </el-select>
            <div class="field-help">仅对支持 reasoning_effort 的模型发送；未设置时保持兼容请求格式。</div>
          </el-form-item>

          <div v-if="retrieval.embeddingModel || retrieval.expandDepth !== null" class="retrieval-config">
            <strong>Schema 检索运行参数</strong>
            <span v-if="retrieval.embeddingModel">Embedding：{{ retrieval.embeddingModel }}</span>
            <span v-if="retrieval.expandDepth !== null">关系扩展深度：{{ retrieval.expandDepth }}</span>
          </div>

          <el-form-item label="Schema 检索 Top K">
            <div class="thinking-row">
              <el-input-number
                v-model="form.rag_top_k"
                :min="1"
                :max="20"
                :step="1"
                :precision="0"
              />
              <span class="muted-text">控制每次请求进入模型上下文的 Schema 候选表数量。</span>
            </div>
          </el-form-item>

          <el-form-item label="模型上下文上限（字符）">
            <div class="thinking-row">
              <el-input-number
                v-model="form.prompt_max_chars"
                :min="1000"
                :max="120000"
                :step="1000"
                :precision="0"
              />
              <span class="muted-text">默认 60000；超过预算时会优先移除低优先级 Schema 候选表。</span>
            </div>
          </el-form-item>

          <el-form-item label="最大等待秒数">
            <div class="thinking-row">
              <el-input-number
                v-model="form.thinking_timeout_seconds"
                :min="10"
                :max="600"
                :step="10"
                :precision="0"
              />
              <span class="muted-text">超过这个时间仍未返回时，系统会明确提示为超时。</span>
            </div>
          </el-form-item>

          <div class="embedding-actions">
            <div>
              <strong>Schema / 正确 SQL RAG</strong>
              <span class="muted-text">
                使用当前 Qwen 模型重建所有数据库的 Schema 和已审核 SQL 向量索引。
              </span>
            </div>
            <el-button
              type="success"
              :loading="initializingEmbedding"
              :disabled="saving || loading"
              @click="initializeEmbedding"
            >
              初始化 Embedding
            </el-button>
          </div>
          <el-alert
            v-if="initializationResult"
            class="embedding-result"
            type="success"
            :closable="false"
            :title="initializationSummary"
          />

          <div class="config-actions">
            <el-button @click="loadConfig" :loading="loading">重新加载</el-button>
            <el-button type="primary" :loading="saving" @click="saveConfig">保存配置</el-button>
          </div>
        </el-form>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.config-form {
  margin-top: 20px;
}

.thinking-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.secret-field {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto;
  gap: 12px;
  width: 100%;
}

.embedding-path-picker {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  width: 100%;
}

.secret-status,
.retrieval-config {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.field-help {
  margin-top: 8px;
  color: var(--text-secondary);
  font-size: 0.86rem;
}

.retrieval-config {
  margin-bottom: 18px;
  padding: 14px 16px;
  border: 1px solid var(--card-border);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.82);
  color: var(--text-secondary);
  font-size: 0.9rem;
}

.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.embedding-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid var(--card-border);
}

.embedding-actions > div {
  display: grid;
  gap: 6px;
}

.embedding-result {
  margin-top: 14px;
}

@media (max-width: 720px) {
  .secret-field {
    grid-template-columns: 1fr;
  }

  .embedding-path-picker {
    grid-template-columns: 1fr;
  }

  .config-actions {
    justify-content: stretch;
  }

  .config-actions :deep(.el-button) {
    flex: 1;
    margin-left: 0;
  }

  .embedding-actions {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
