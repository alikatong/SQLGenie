import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const source = fs.readFileSync(new URL('../src/views/SqlWorkbenchView.vue', import.meta.url), 'utf8')

test('SQL 工作台的数据库定义表单项使用顶部标签布局', () => {
  assert.match(source, /class="workbench-form-item"/)
  assert.match(source, /\.workbench-form-item\s*\{/)
  assert.match(source, /\.workbench-form-item\s+:deep\(\.el-form-item__label\)/)
})
