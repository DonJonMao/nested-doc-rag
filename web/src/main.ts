import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tokens.css'
import './styles/element-overrides.css'
import './styles/layout.css'
import './styles/motion.css'
import App from './App.vue'
import { router } from './router'

createApp(App).use(createPinia()).use(router).use(ElementPlus).mount('#app')
