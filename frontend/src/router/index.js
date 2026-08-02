import { createRouter, createWebHistory } from 'vue-router'
import { authState } from '../stores/auth'

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { guestOnly: true, title: '登录', componentName: 'LoginView' },
  },
  {
    path: '/',
    redirect: '/sql',
  },
  {
    path: '/sql',
    name: 'sql-workbench',
    component: () => import('../views/SqlWorkbenchView.vue'),
    meta: { requiresAuth: true, title: 'SQL 生成', componentName: 'SqlWorkbenchView' },
  },
  {
    path: '/admin/metadata',
    name: 'admin-metadata',
    component: () => import('../views/admin/AdminMetadataView.vue'),
    meta: {
      requiresAuth: true,
      requiresAdmin: true,
      title: '表结构管理',
      componentName: 'AdminMetadataView',
    },
  },
  {
    path: '/admin/schema-preview',
    name: 'admin-schema-preview',
    component: () => import('../views/admin/AdminSchemaPreviewView.vue'),
    meta: {
      requiresAuth: true,
      title: '表结构预览',
      componentName: 'AdminSchemaPreviewView',
    },
  },
  {
    path: '/admin/users',
    name: 'admin-users',
    component: () => import('../views/admin/AdminUsersView.vue'),
    meta: {
      requiresAuth: true,
      requiresAdmin: true,
      title: '用户管理',
      componentName: 'AdminUsersView',
    },
  },
  {
    path: '/admin/history',
    name: 'admin-history',
    component: () => import('../views/admin/AdminHistoryView.vue'),
    meta: {
      requiresAuth: true,
      title: '提问历史',
      componentName: 'AdminHistoryView',
    },
  },
  {
    path: '/admin/config',
    name: 'admin-config',
    component: () => import('../views/admin/AdminConfigView.vue'),
    meta: {
      requiresAuth: true,
      requiresAdmin: true,
      title: '系统配置',
      componentName: 'AdminConfigView',
    },
  },
  {
    path: '/admin/feedback-rag',
    name: 'admin-feedback-rag',
    component: () => import('../views/admin/AdminFeedbackRagView.vue'),
    meta: {
      requiresAuth: true,
      requiresAdmin: true,
      title: '反馈 RAG 管理',
      componentName: 'AdminFeedbackRagView',
    },
  },
  {
    path: '/admin/his-semantics',
    name: 'admin-his-semantics',
    component: () => import('../views/admin/AdminHisSemanticsView.vue'),
    meta: {
      requiresAuth: true,
      requiresAdmin: true,
      title: 'HIS 语义目录',
      componentName: 'AdminHisSemanticsView',
    },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/sql',
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !authState.token) {
    return '/login'
  }

  if (to.meta.requiresAdmin && authState.role !== 'admin') {
    return '/sql'
  }

  if (to.meta.guestOnly && authState.token) {
    return authState.role === 'admin' ? '/admin/metadata' : '/sql'
  }

  return true
})

export default router
