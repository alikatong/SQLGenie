<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login } from '../api'
import { setSession } from '../stores/auth'
import { extractError } from '../utils/errors'

const router = useRouter()
const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
})

async function handleLogin() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入用户名和密码。')
    return
  }

  loading.value = true
  try {
    const data = await login({
      username: form.username.trim(),
      password: form.password,
    })
    setSession({
      ...data,
      username: form.username.trim(),
    })
    ElMessage.success('登录成功。')
    router.push(data.role === 'admin' ? '/admin/metadata' : '/sql')
  } catch (error) {
    ElMessage.error(extractError(error, '登录失败，请检查账号密码。'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-hero">
      <div class="login-copy">
        <div class="eyebrow">sqlGenie</div>
        <h1>把自然语言变成可信 SQL</h1>
        <p>
          管理员维护结构元数据，普通用户只需要描述需求，就能在 MySQL、PostgreSQL、Oracle
          方言之间快速生成查询语句。
        </p>
      </div>

      <el-card class="login-card panel-card" shadow="never">
        <template #header>
          <div class="section-header">
            <div>
              <h2>登录系统</h2>
              <p>请输入管理员或普通用户账号登录 sqlGenie。</p>
            </div>
          </div>
        </template>

        <el-form label-position="top" @submit.prevent="handleLogin">
          <el-form-item label="用户名">
            <el-input v-model="form.username" placeholder="请输入用户名" />
          </el-form-item>

          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              placeholder="请输入密码"
              @keyup.enter="handleLogin"
            />
          </el-form-item>

          <el-button type="primary" :loading="loading" size="large" class="login-button" @click="handleLogin">
            登录并进入 sqlGenie
          </el-button>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px;
}

.login-hero {
  width: min(1080px, 100%);
  display: grid;
  grid-template-columns: 1.15fr 0.95fr;
  gap: 28px;
  align-items: stretch;
}

.login-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 36px;
  border-radius: 32px;
  background:
    radial-gradient(circle at top left, rgba(20, 184, 166, 0.18), transparent 26%),
    radial-gradient(circle at bottom right, rgba(251, 146, 60, 0.2), transparent 24%),
    linear-gradient(135deg, rgba(8, 20, 32, 0.96), rgba(11, 45, 63, 0.9));
  color: #f4fbff;
  box-shadow: 0 26px 70px rgba(8, 20, 32, 0.22);
}

.eyebrow {
  display: inline-flex;
  width: fit-content;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(20, 184, 166, 0.18);
  color: #9ae6d8;
  font-size: 0.88rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.login-copy h1 {
  margin: 22px 0 14px;
  font-size: clamp(2.2rem, 5vw, 3.6rem);
  line-height: 1.05;
}

.login-copy p {
  margin: 0;
  max-width: 46ch;
  color: rgba(235, 247, 252, 0.82);
  line-height: 1.8;
  font-size: 1rem;
}

.login-card {
  align-self: center;
}

.login-button {
  width: 100%;
}

@media (max-width: 900px) {
  .login-page {
    padding: 18px;
  }

  .login-hero {
    grid-template-columns: 1fr;
  }

  .login-copy {
    padding: 26px;
  }
}
</style>
