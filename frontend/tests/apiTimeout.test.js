import assert from 'node:assert/strict'
import test from 'node:test'

import apiClient, {
  generateSql,
  pickEmbeddingModelDirectory,
  SQL_GENERATION_HTTP_TIMEOUT_MS,
} from '../src/api/index.js'

test('SQL 生成请求为后端十分钟模型预算保留处理余量', async () => {
  const originalAdapter = apiClient.defaults.adapter
  let requestConfig
  apiClient.defaults.adapter = async (config) => {
    requestConfig = config
    return {
      data: { sql: 'SELECT 1' },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }
  }

  try {
    await generateSql({ natural_text: 'test' })
  } finally {
    apiClient.defaults.adapter = originalAdapter
  }

  assert.equal(SQL_GENERATION_HTTP_TIMEOUT_MS, 660_000)
  assert.equal(requestConfig.timeout, SQL_GENERATION_HTTP_TIMEOUT_MS)
})

test('Embedding 模型目录选择请求使用管理员目录选择接口', async () => {
  const originalAdapter = apiClient.defaults.adapter
  let requestConfig
  apiClient.defaults.adapter = async (config) => {
    requestConfig = config
    return {
      data: { selected: true, embedding_model_path: 'C:\\models\\Qwen3-Embedding-0.6B' },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }
  }

  try {
    const result = await pickEmbeddingModelDirectory()
    assert.equal(result.selected, true)
  } finally {
    apiClient.defaults.adapter = originalAdapter
  }

  assert.equal(requestConfig.method, 'post')
  assert.equal(requestConfig.url, '/embedding-models/pick-directory')
  assert.equal(requestConfig.timeout, 30 * 60 * 1000)
})
