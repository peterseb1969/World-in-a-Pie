import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ToastMessage {
  severity: 'success' | 'info' | 'warn' | 'error'
  summary: string
  detail?: string
  life?: number
}

export const useUiStore = defineStore('ui', () => {
  const toastMessages = ref<ToastMessage[]>([])
  const globalLoading = ref(false)
  const sidebarVisible = ref(true)

  function showToast(message: ToastMessage) {
    toastMessages.value.push({
      ...message,
      life: message.life ?? 3000
    })
  }

  function showSuccess(summary: string, detail?: string) {
    showToast({ severity: 'success', summary, detail })
  }

  function showError(summary: string, detail?: string) {
    // Suppress error toasts for auth-related errors (handled by session expired toast)
    if (detail && (detail === 'Session expired. Please log in again.' || detail === 'Authentication required')) {
      return
    }
    showToast({ severity: 'error', summary, detail, life: 5000 })
  }

  function showInfo(summary: string, detail?: string) {
    showToast({ severity: 'info', summary, detail })
  }

  function showWarn(summary: string, detail?: string) {
    showToast({ severity: 'warn', summary, detail })
  }

  function clearToasts() {
    toastMessages.value = []
  }

  function consumeToast(): ToastMessage | undefined {
    return toastMessages.value.shift()
  }

  function setGlobalLoading(loading: boolean) {
    globalLoading.value = loading
  }

  function toggleSidebar() {
    sidebarVisible.value = !sidebarVisible.value
  }

  return {
    toastMessages,
    globalLoading,
    sidebarVisible,
    showToast,
    showSuccess,
    showError,
    showInfo,
    showWarn,
    clearToasts,
    consumeToast,
    setGlobalLoading,
    toggleSidebar
  }
})
