import assert from 'node:assert/strict'
import test from 'node:test'

import apiClient, { generateSql, SQL_GENERATION_HTTP_TIMEOUT_MS } from '../src/api/index.js'

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
