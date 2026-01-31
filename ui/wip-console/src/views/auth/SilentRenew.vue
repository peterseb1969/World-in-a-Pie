<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores'

const authStore = useAuthStore()

onMounted(async () => {
  await authStore.handleSilentRenewCallback()
})
</script>

<template>
  <!-- This page is loaded in a hidden iframe for silent token renewal -->
  <div></div>
</template>
