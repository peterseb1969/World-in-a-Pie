import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import Aura from '@primevue/themes/aura'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'

import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores'

import 'primeicons/primeicons.css'

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)

// Initialize auth store from localStorage
const authStore = useAuthStore()
authStore.initialize()

app.use(router)
app.use(PrimeVue, {
  theme: {
    preset: Aura,
    options: {
      darkModeSelector: '.dark-mode'
    }
  }
})
app.use(ConfirmationService)
app.use(ToastService)

app.mount('#app')
