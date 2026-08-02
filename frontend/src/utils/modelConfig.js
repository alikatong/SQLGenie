const DEFAULT_TIMEOUT_SECONDS = 600
const DEFAULT_RAG_TOP_K = 8
const DEFAULT_PROMPT_MAX_CHARS = 60000
const MAX_PROMPT_MAX_CHARS = 120000
const REASONING_EFFORT_VALUES = new Set(['low', 'medium', 'high', 'xhigh', 'max'])

function normalizeTimeout(value) {
  const timeout = Number(value)
  return Number.isFinite(timeout) ? Math.min(600, Math.max(10, Math.trunc(timeout))) : DEFAULT_TIMEOUT_SECONDS
}

function normalizeRagTopK(value) {
  const topK = Number(value)
  return Number.isFinite(topK) ? Math.min(20, Math.max(1, Math.trunc(topK))) : DEFAULT_RAG_TOP_K
}

function normalizePromptMaxChars(value) {
  const maxChars = Number(value)
  return Number.isFinite(maxChars)
    ? Math.min(MAX_PROMPT_MAX_CHARS, Math.max(1000, Math.trunc(maxChars)))
    : DEFAULT_PROMPT_MAX_CHARS
}

export function normalizeReasoningEffort(value) {
  const normalized = typeof value === 'string' ? value.trim().toLowerCase() : ''
  return REASONING_EFFORT_VALUES.has(normalized) ? normalized : null
}

export function mapModelConfigResponse(data = {}, fallback = {}) {
  const last4 = typeof data.api_key_last4 === 'string' ? data.api_key_last4.trim().slice(-4) : ''
  const reasoningEffort = Object.hasOwn(data, 'reasoning_effort')
    ? normalizeReasoningEffort(data.reasoning_effort)
    : normalizeReasoningEffort(fallback.reasoning_effort)

  return {
    form: {
      // Never hydrate this field from a server response, including legacy api_key responses.
      api_key: '',
      base_url: typeof data.base_url === 'string' ? data.base_url : '',
      model_name: typeof data.model_name === 'string' ? data.model_name : '',
      enable_thinking: data.enable_thinking ?? true,
      reasoning_effort: reasoningEffort,
      thinking_timeout_seconds: normalizeTimeout(data.thinking_timeout_seconds),
      prompt_max_chars: normalizePromptMaxChars(data.prompt_max_chars),
      rag_top_k: normalizeRagTopK(data.rag_top_k),
      embedding_model_path: typeof data.embedding_model_path === 'string'
        ? data.embedding_model_path
        : (typeof data.rag_embedding_model === 'string' ? data.rag_embedding_model : ''),
    },
    secret: {
      configured: data.api_key_configured === true,
      last4,
    },
    retrieval: {
      embeddingModel: typeof data.rag_embedding_model === 'string' ? data.rag_embedding_model : '',
      embeddingFamily: typeof data.embedding_model_family === 'string' ? data.embedding_model_family : 'Qwen',
      expandDepth: Number.isFinite(Number(data.rag_expand_depth)) ? Number(data.rag_expand_depth) : null,
    },
  }
}

export function buildModelConfigPayload(form = {}) {
  const payload = {
    base_url: String(form.base_url || '').trim(),
    model_name: String(form.model_name || '').trim(),
    enable_thinking: Boolean(form.enable_thinking),
    reasoning_effort: normalizeReasoningEffort(form.reasoning_effort),
    thinking_timeout_seconds: normalizeTimeout(form.thinking_timeout_seconds),
    prompt_max_chars: normalizePromptMaxChars(form.prompt_max_chars),
    rag_top_k: normalizeRagTopK(form.rag_top_k),
    embedding_model_path: String(form.embedding_model_path || '').trim(),
  }
  const newApiKey = String(form.api_key || '').trim()

  if (newApiKey) {
    payload.api_key = newApiKey
  }

  return payload
}
