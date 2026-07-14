import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import { restoreSession } from './stores/auth'
import './style.css'

restoreSession()

createApp(App).use(router).use(ElementPlus).mount('#app')
