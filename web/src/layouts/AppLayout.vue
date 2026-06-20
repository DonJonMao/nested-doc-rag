<script setup lang="ts">
import { onMounted } from 'vue'
import GlobalNav from '@/components/nav/GlobalNav.vue'
import { useWorkspaceStore } from '@/stores/workspace.store'

const workspace = useWorkspaceStore()

onMounted(() => {
  if (!workspace.workspaces.length) workspace.load()
})
</script>

<template>
  <div class="gk-page">
    <GlobalNav />
    <RouterView />
  </div>
</template>
