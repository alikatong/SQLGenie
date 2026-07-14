import axios from 'axios'
import { authState, clearSession } from '../stores/auth'

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

apiClient.interceptors.request.use((config) => {
  if (authState.token) {
    config.headers.Authorization = `Bearer ${authState.token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && authState.token) {
      clearSession()
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export function login(payload) {
  return apiClient.post('/login', payload).then((response) => response.data)
}

export function getUsers() {
  return apiClient.get('/users').then((response) => response.data)
}

export function createUser(payload) {
  return apiClient.post('/users', payload).then((response) => response.data)
}

export function resetUserPassword(id, payload) {
  return apiClient.put(`/users/${id}/password`, payload).then((response) => response.data)
}

export function updateUserRole(id, payload) {
  return apiClient.put(`/users/${id}/role`, payload).then((response) => response.data)
}

export function deleteUser(id) {
  return apiClient.delete(`/users/${id}`).then((response) => response.data)
}

export function getDbDefinitions() {
  return apiClient.get('/db-defs').then((response) => response.data)
}

export function createDbDefinition(payload) {
  return apiClient.post('/db-defs', payload).then((response) => response.data)
}

export function updateDbDefinition(id, payload) {
  return apiClient.put(`/db-defs/${id}`, payload).then((response) => response.data)
}

export function deleteDbDefinition(id, payload) {
  return apiClient.delete(`/db-defs/${id}`, { data: payload }).then((response) => response.data)
}

export function uploadTableSchema(dbId, payload) {
  return apiClient.post(`/db-defs/${dbId}/tables`, payload).then((response) => response.data)
}

export function getTableSchema(dbId) {
  return apiClient.get(`/db-defs/${dbId}/tables`).then((response) => response.data)
}

export function uploadSingleTableSchema(dbId, payload) {
  return apiClient.post(`/db-defs/${dbId}/single-table`, payload).then((response) => response.data)
}

export function deleteSingleTableSchema(dbId, tableName, payload) {
  return apiClient
    .delete(`/db-defs/${dbId}/single-table/${encodeURIComponent(tableName)}`, { data: payload })
    .then((response) => response.data)
}

export function generateSql(payload) {
  return apiClient
    .post('/generate-sql', payload, { timeout: 600000 })
    .then((response) => response.data)
}

export function submitSqlFeedback(payload) {
  return apiClient.post('/sql-feedback', payload).then((response) => response.data)
}

export function getFeedbackRagExamples(params) {
  return apiClient.get('/feedback-rag/examples', { params }).then((response) => response.data)
}

export function deleteFeedbackRagExample(id) {
  return apiClient.delete(`/feedback-rag/examples/${id}`)
}

export function approveFeedbackRagExample(id) {
  return apiClient.post(`/feedback-rag/examples/${id}/approve`)
}

export function getFeedbackRagConfig() {
  return apiClient.get('/feedback-rag/config').then((response) => response.data)
}

export function updateFeedbackRagConfig(payload) {
  return apiClient.put('/feedback-rag/config', payload).then((response) => response.data)
}

export function getSqlHistory(params) {
  return apiClient.get('/sql-history', { params }).then((response) => response.data)
}

export function getConfig() {
  return apiClient.get('/config').then((response) => response.data)
}

export function updateConfig(payload) {
  return apiClient.put('/config', payload).then((response) => response.data)
}

export default apiClient
