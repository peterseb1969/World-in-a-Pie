import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import Aura from '@primevue/themes/aura'
import { definePreset } from '@primevue/themes'
import ConfirmationService from 'primevue/confirmationservice'
import ToastService from 'primevue/toastservice'
import Tooltip from 'primevue/tooltip'

import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores'

import 'primeicons/primeicons.css'

// Define WIP Blue theme preset based on Aura
const WipBlue = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{sky.50}',
      100: '{sky.100}',
      200: '{sky.200}',
      300: '{sky.300}',
      400: '{sky.400}',
      500: '{sky.500}',
      600: '{sky.600}',
      700: '{sky.700}',
      800: '{sky.800}',
      900: '{sky.900}',
      950: '{sky.950}'
    }
  }
})

const app = createApp(App)

const pinia = createPinia()
app.use(pinia)

// Initialize auth store from localStorage
const authStore = useAuthStore()
authStore.initialize()

app.use(router)
app.use(PrimeVue, {
  theme: {
    preset: WipBlue,
    options: {
      darkModeSelector: '.dark-mode'
    }
  }
})
app.use(ConfirmationService)
app.use(ToastService)
app.directive('tooltip', Tooltip)

app.mount('#app')
