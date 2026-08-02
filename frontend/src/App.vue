<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authState, clearSession } from './stores/auth'

const route = useRoute()
const router = useRouter()

const openTabs = ref([])
const activeTabPath = ref('')

const isLoggedIn = computed(() => Boolean(authState.token))
const isAdmin = computed(() => authState.role === 'admin')
const isLoginPage = computed(() => route.path === '/login')
const keepAliveNames = computed(() =>
  openTabs.value.map((tab) => tab.cacheName).filter(Boolean),
)

function buildTab(targetRoute) {
  if (!targetRoute.meta?.requiresAuth || targetRoute.path === '/login') {
    return null
  }

  return {
    path: targetRoute.path,
    title: targetRoute.meta.title || targetRoute.name || targetRoute.path,
    cacheName: targetRoute.meta.componentName || '',
  }
}

function syncRouteTab(targetRoute) {
  const nextTab = buildTab(targetRoute)
  if (!nextTab) {
    activeTabPath.value = ''
    return
  }

  const existingTab = openTabs.value.find((tab) => tab.path === nextTab.path)
  if (existingTab) {
    existingTab.title = nextTab.title
    existingTab.cacheName = nextTab.cacheName
  } else {
    openTabs.value = [...openTabs.value, nextTab]
  }

  activeTabPath.value = nextTab.path
}

function handleTabChange(path) {
  const nextPath = String(path || '')
  if (nextPath && nextPath !== route.path) {
    router.push(nextPath)
  }
}

function handleTabRemove(path) {
  if (openTabs.value.length <= 1) {
    return
  }

  const closingPath = String(path || '')
  const tabIndex = openTabs.value.findIndex((tab) => tab.path === closingPath)
  if (tabIndex < 0) {
    return
  }

  const closingCurrent = activeTabPath.value === closingPath
  const remainingTabs = openTabs.value.filter((tab) => tab.path !== closingPath)
  openTabs.value = remainingTabs

  if (!closingCurrent) {
    return
  }

  const fallbackPath =
    remainingTabs[tabIndex]?.path || remainingTabs[tabIndex - 1]?.path || remainingTabs[0]?.path || ''

  activeTabPath.value = fallbackPath
  if (fallbackPath && fallbackPath !== route.path) {
    router.push(fallbackPath)
  }
}

function logout() {
  openTabs.value = []
  activeTabPath.value = ''
  clearSession()
  router.push('/login')
}

watch(
  () => [isLoggedIn.value, route.path],
  () => {
    if (!isLoggedIn.value || route.path === '/login') {
      openTabs.value = []
      activeTabPath.value = ''
      return
    }

    syncRouteTab(route)
  },
  { immediate: true },
)
</script>

<template>
  <div class="app-shell">
    <router-view v-if="isLoginPage || !isLoggedIn" />

    <el-container v-else class="app-container">
      <el-aside width="220px" class="aside">
        <div class="brand">
          <div class="brand-mark">SG</div>
          <div>
            <div class="brand-name">sqlGenie</div>
            <div class="brand-subtitle">NL to SQL Workspace</div>
          </div>
        </div>

        <el-menu
          router
          :default-active="route.path"
          class="nav-menu"
          background-color="transparent"
          text-color="rgba(232, 244, 247, 0.78)"
          active-text-color="#f5f8f9"
        >
          <el-menu-item index="/sql">SQL 生成</el-menu-item>
          <el-menu-item v-if="isAdmin" index="/admin/users">用户管理</el-menu-item>
          <el-sub-menu v-if="isAdmin" index="/admin/schema">
            <template #title>表结构中心</template>
            <el-menu-item index="/admin/metadata">表结构管理</el-menu-item>
            <el-menu-item index="/admin/schema-preview">表结构预览</el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else index="/admin/schema-preview">表结构预览</el-menu-item>
          <el-menu-item index="/admin/history">提问历史</el-menu-item>
          <el-menu-item v-if="isAdmin" index="/admin/config">系统配置</el-menu-item>
          <el-menu-item v-if="isAdmin" index="/admin/his-semantics">HIS 语义目录</el-menu-item>
          <el-menu-item v-if="isAdmin" index="/admin/feedback-rag">反馈 RAG 管理</el-menu-item>
        </el-menu>
      </el-aside>

      <el-container>
        <el-header class="header">
          <div>
            <div class="header-title">{{ route.meta.title || 'sqlGenie' }}</div>
            <div class="header-subtitle">本地化自然语言转 SQL 工具</div>
          </div>

          <div class="header-actions">
            <div class="user-chip">
              <span>{{ authState.username || '当前用户' }}</span>
              <span>{{ authState.role }}</span>
            </div>
            <el-button plain @click="logout">退出登录</el-button>
          </div>
        </el-header>

        <el-main class="main-content">
          <el-tabs
            v-if="openTabs.length"
            v-model="activeTabPath"
            type="card"
            class="workspace-tabs"
            @tab-change="handleTabChange"
            @tab-remove="handleTabRemove"
          >
            <el-tab-pane
              v-for="tab in openTabs"
              :key="tab.path"
              :label="tab.title"
              :name="tab.path"
              :closable="openTabs.length > 1"
            />
          </el-tabs>

          <router-view v-slot="{ Component, route: currentRoute }">
            <keep-alive :include="keepAliveNames">
              <component :is="Component" :key="currentRoute.path" />
            </keep-alive>
          </router-view>
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>
