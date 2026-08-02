import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/views/admin/AdminHistoryView.vue', import.meta.url), 'utf8')

test('提问历史页仅在管理员身份下加载用户列表', () => {
  assert.match(source, /async function loadUsers\(\)\s*\{[\s\S]*?if \(!isAdmin\.value\)\s*\{[\s\S]*?return\s*\}/)
})
