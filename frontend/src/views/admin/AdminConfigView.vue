<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getConfig, updateConfig } from '../../api'
import { extractError } from '../../utils/errors'

defineOptions({
  name: 'AdminConfigView',
})

const loading = ref(false)
const saving = ref(false)

const form = reactive({
  api_key: '',
  base_url: '',
  model_name: '',
  enable_thinking: true,
  thinking_timeout_seconds: 120,
})

async function loadConfig() {
  loading.value = true
  try {
    const data = await getConfig()
    form.api_key = data.api_key || ''
    form.base_url = data.base_url || ''
    form.model_name = data.model_name || ''
    form.enable_thinking = data.enable_thinking ?? true
    form.thinking_timeout_seconds = Number(data.thinking_timeout_seconds || 120)
  } catch (error) {
    ElMessage.error(extractError(error, '加载系统配置失败。'))
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (!form.api_key.trim() || !form.base_url.trim() || !form.model_name.trim()) {
    ElMessage.warning('请完整填写 API Key、Base URL 和模型名。')
    return
  }

  if (!Number.isFinite(form.thinking_timeout_seconds) || form.thinking_timeout_seconds < 10) {
    ElMessage.warning('最大等待秒数不能小于 10 秒。')
    return
  }

  saving.value = true
  try {
    await updateConfig({
      api_key: form.api_key.trim(),
      base_url: form.base_url.trim(),
      model_name: form.model_name.trim(),
      enable_thinking: form.enable_thinking,
      thinking_timeout_seconds: Math.trunc(form.thinking_timeout_seconds),
    })
    ElMessage.success('系统配置已保存。')
  } catch (error) {
    ElMessage.error(extractError(error, '保存系统配置失败。'))
  } finally {
    saving.value = false
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
            <p>配置 OpenAI 兼容接口参数，并控制 SQL 生成时是否启用深度思考以及最长等待时间。</p>
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
            <el-input
              v-model="form.api_key"
              type="password"
              show-password
              placeholder="请输入 OpenAI 兼容接口的 API Key"
            />
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

          <el-form-item label="启用深度思考">
            <div class="thinking-row">
              <el-switch v-model="form.enable_thinking" />
              <span class="muted-text">开启后会先生成，再做一次审核修正，通常更准，但更慢。</span>
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

.config-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
