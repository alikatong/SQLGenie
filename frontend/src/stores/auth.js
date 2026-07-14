import { reactive } from 'vue'

const TOKEN_KEY = 'sqlgenie_token'
const ROLE_KEY = 'sqlgenie_role'
const USERNAME_KEY = 'sqlgenie_username'

export const authState = reactive({
  token: '',
  role: '',
  username: '',
})

export function restoreSession() {
  authState.token = window.localStorage.getItem(TOKEN_KEY) || ''
  authState.role = window.localStorage.getItem(ROLE_KEY) || ''
  authState.username = window.localStorage.getItem(USERNAME_KEY) || ''
}

export function setSession({ access_token, role, username }) {
  authState.token = access_token
  authState.role = role
  authState.username = username
  window.localStorage.setItem(TOKEN_KEY, access_token)
  window.localStorage.setItem(ROLE_KEY, role)
  window.localStorage.setItem(USERNAME_KEY, username)
}

export function clearSession() {
  authState.token = ''
  authState.role = ''
  authState.username = ''
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(ROLE_KEY)
  window.localStorage.removeItem(USERNAME_KEY)
}

