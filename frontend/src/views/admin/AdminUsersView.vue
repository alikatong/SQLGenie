<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createUser, deleteUser, getUsers, resetUserPassword, updateUserRole } from '../../api'
import { formatDateTime } from '../../utils/datetime'
import { extractError } from '../../utils/errors'

defineOptions({
  name: 'AdminUsersView',
})

const DEFAULT_ADMIN_USERNAME = 'admin'

const loading = ref(false)
const saving = ref(false)
const users = ref([])

const form = reactive({
  username: '',
  password: '',
  role: 'user',
})

const passwordDialogVisible = ref(false)
const passwordForm = reactive({
  userId: null,
  username: '',
  role: '',
  password: '',
})

const adminUsers = computed(() => users.value.filter((item) => item.role === 'admin'))
const normalUsers = computed(() => users.value.filter((item) => item.role === 'user'))

function isProtectedAdmin(row) {
  return row.role === 'admin' && row.username === DEFAULT_ADMIN_USERNAME
}

function canManageAdmin(row) {
  return row.role === 'admin' && !isProtectedAdmin(row)
}

async function loadUsers() {
  loading.value = true
  try {
    users.value = await getUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '加载用户列表失败。'))
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.username = ''
  form.password = ''
  form.role = 'user'
}

async function handleCreateUser() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入用户名和密码。')
    return
  }

  saving.value = true
  try {
    await createUser({
      username: form.username.trim(),
      password: form.password,
      role: 'user',
    })
    ElMessage.success(form.role === 'admin' ? '管理员账号已创建。' : '普通用户已创建。')
    resetForm()
    await loadUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '创建用户失败。'))
  } finally {
    saving.value = false
  }
}

function openResetPasswordDialog(row) {
  passwordForm.userId = row.id
  passwordForm.username = row.username
  passwordForm.role = row.role
  passwordForm.password = ''
  passwordDialogVisible.value = true
}

async function handleResetPassword() {
  if (!passwordForm.password) {
    ElMessage.warning('请输入新密码。')
    return
  }

  saving.value = true
  try {
    await resetUserPassword(passwordForm.userId, {
      password: passwordForm.password,
    })
    ElMessage.success('密码已重置。')
    passwordDialogVisible.value = false
    passwordForm.password = ''
    await loadUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '重置密码失败。'))
  } finally {
    saving.value = false
  }
}

async function handleDeleteUser(row) {
  const userTypeLabel = row.role === 'admin' ? '管理员' : '普通用户'

  try {
    await ElMessageBox.confirm(
      `确定删除${userTypeLabel}“${row.username}”吗？该账号的 7 天内提问历史也会一并删除。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  loading.value = true
  try {
    await deleteUser(row.id)
    ElMessage.success(`${userTypeLabel}已删除。`)
    await loadUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '删除账号失败。'))
  } finally {
    loading.value = false
  }
}

async function handleDowngradeAdmin(row) {
  try {
    await ElMessageBox.confirm(
      `确定将管理员“${row.username}”降级为普通用户吗？降级后将失去后台管理权限。`,
      '角色修改确认',
      {
        type: 'warning',
        confirmButtonText: '确认降级',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  loading.value = true
  try {
    await updateUserRole(row.id, { role: 'user' })
    ElMessage.success('管理员已降级为普通用户。')
    await loadUsers()
  } catch (error) {
    ElMessage.error(extractError(error, '修改角色失败。'))
  } finally {
    loading.value = false
  }
}

onMounted(loadUsers)
</script>

<template>
  <div class="page-stack">
    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>创建账号</h2>
            <p>管理员可以在这里创建普通用户或新的管理员账号。</p>
          </div>
        </div>
      </template>

      <div class="form-grid">
        <el-form-item label="用户名">
          <el-input v-model="form.username" placeholder="请输入用户名" />
        </el-form-item>

        <el-form-item label="初始密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入初始密码"
          />
        </el-form-item>

        <el-form-item label="账号角色">
          <el-select v-model="form.role" style="width: 100%" disabled>
            <el-option label="普通用户" value="user" />
            <el-option label="管理员" value="admin" />
          </el-select>
        </el-form-item>

        <div class="form-grid-full form-actions">
          <el-button @click="resetForm">重置</el-button>
          <el-button type="primary" :loading="saving" @click="handleCreateUser">创建账号</el-button>
        </div>
      </div>
    </el-card>

    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>管理员账号</h2>
            <p>初始管理员 `admin` 受保护；其他管理员可重置密码、删除或降级为普通用户。</p>
          </div>
          <el-button @click="loadUsers" :loading="loading">刷新</el-button>
        </div>
      </template>

      <el-table :data="adminUsers" stripe v-loading="loading">
        <el-table-column prop="id" label="ID" width="90" />
        <el-table-column prop="username" label="用户名" min-width="180" />
        <el-table-column label="状态" width="160">
          <template #default="{ row }">
            <el-tag v-if="isProtectedAdmin(row)" type="warning">初始管理员</el-tag>
            <el-tag v-else type="success">可管理管理员</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="280">
          <template #default="{ row }">
            <div class="row-actions">
              <template v-if="canManageAdmin(row)">
                <el-button link type="primary" @click="openResetPasswordDialog(row)">重置密码</el-button>
                <el-button link type="warning" @click="handleDowngradeAdmin(row)">降级为普通用户</el-button>
                <el-button link type="danger" @click="handleDeleteUser(row)">删除</el-button>
              </template>
              <span v-else class="muted-text">初始管理员受保护，不可删除、不可降级、不可改密</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="panel-card" shadow="never">
      <template #header>
        <div class="section-header">
          <div>
            <h2>普通用户账号</h2>
            <p>普通用户可重置密码或删除。</p>
          </div>
        </div>
      </template>

      <el-table :data="normalUsers" stripe v-loading="loading">
        <el-table-column prop="id" label="ID" width="90" />
        <el-table-column prop="username" label="用户名" min-width="180" />
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" @click="openResetPasswordDialog(row)">重置密码</el-button>
              <el-button link type="danger" @click="handleDeleteUser(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="passwordDialogVisible" title="重置账号密码" width="420px">
      <el-form label-position="top">
        <el-form-item label="用户名">
          <el-input :model-value="passwordForm.username" disabled />
        </el-form-item>

        <el-form-item label="账号角色">
          <el-input :model-value="passwordForm.role === 'admin' ? '管理员' : '普通用户'" disabled />
        </el-form-item>

        <el-form-item label="新密码">
          <el-input
            v-model="passwordForm.password"
            type="password"
            show-password
            placeholder="请输入新密码"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <div class="dialog-actions">
          <el-button @click="passwordDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="handleResetPassword">保存</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.row-actions,
.dialog-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
