import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildModelConfigPayload,
  mapModelConfigResponse,
  normalizeReasoningEffort,
} from '../src/utils/modelConfig.js'

test('配置响应只映射密钥状态，输入框始终为空', () => {
  const mapped = mapModelConfigResponse({
    api_key: 'legacy-secret-that-must-be-ignored',
    api_key_configured: true,
    api_key_last4: 'abcd',
    base_url: 'https://example.test/v1',
    model_name: 'model-a',
    enable_thinking: false,
    reasoning_effort: 'high',
    thinking_timeout_seconds: 180,
    prompt_max_chars: 64000,
    rag_top_k: 11,
  })

  assert.equal(mapped.form.api_key, '')
  assert.deepEqual(mapped.secret, { configured: true, last4: 'abcd' })
  assert.equal(mapped.form.enable_thinking, false)
  assert.equal(mapped.form.reasoning_effort, 'high')
  assert.equal(mapped.form.prompt_max_chars, 64000)
  assert.equal(mapped.form.rag_top_k, 11)
})

test('留空保存时载荷不含 api_key 或尾四位', () => {
  const mapped = mapModelConfigResponse({
    api_key_configured: true,
    api_key_last4: 'wxyz',
    base_url: 'https://example.test/v1',
    model_name: 'model-a',
  })
  const payload = buildModelConfigPayload(mapped.form)

  assert.equal(Object.hasOwn(payload, 'api_key'), false)
  assert.equal(JSON.stringify(payload).includes('wxyz'), false)
  assert.equal(payload.thinking_timeout_seconds, 600)
})

test('只有管理员输入的新密钥会进入保存载荷', () => {
  const payload = buildModelConfigPayload({
    api_key: '  sk-new-secret  ',
    base_url: ' https://example.test/v1 ',
    model_name: ' model-a ',
    enable_thinking: true,
    thinking_timeout_seconds: 900,
    prompt_max_chars: 96000,
    rag_top_k: 24,
  })

  assert.equal(payload.api_key, 'sk-new-secret')
  assert.equal(payload.base_url, 'https://example.test/v1')
  assert.equal(payload.model_name, 'model-a')
  assert.equal(payload.thinking_timeout_seconds, 600)
  assert.equal(payload.prompt_max_chars, 96000)
  assert.equal(payload.rag_top_k, 20)
})

test('思考强度支持五档枚举，未设置时显式发送 null', () => {
  for (const effort of ['low', 'medium', 'high', 'xhigh', 'max']) {
    assert.equal(normalizeReasoningEffort(` ${effort.toUpperCase()} `), effort)
  }
  assert.equal(normalizeReasoningEffort('unsupported'), null)
  assert.equal(normalizeReasoningEffort(null), null)

  const payload = buildModelConfigPayload({
    base_url: 'https://example.test/v1',
    model_name: 'model-a',
    reasoning_effort: null,
    prompt_max_chars: 120001,
  })

  assert.equal(payload.reasoning_effort, null)
  assert.equal(payload.prompt_max_chars, 120000)
})

test('保存响应缺少思考强度时保留当前选择，但显式 null 仍表示清空', () => {
  const current = { reasoning_effort: 'xhigh' }
  const withoutEffort = mapModelConfigResponse(
    {
      base_url: 'https://example.test/v1',
      model_name: 'model-a',
    },
    current,
  )
  assert.equal(withoutEffort.form.reasoning_effort, 'xhigh')

  const cleared = mapModelConfigResponse({ reasoning_effort: null }, current)
  assert.equal(cleared.form.reasoning_effort, null)
})
