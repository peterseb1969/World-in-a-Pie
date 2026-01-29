<script setup lang="ts">
import TerminologyList from '@/components/TerminologyList.vue'
</script>

<template>
  <div class="terminology-list-view">
    <TerminologyList />
  </div>
</template>

<style scoped>
.terminology-list-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}
</style>
