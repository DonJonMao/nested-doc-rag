import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { listWorkspaces } from '@/api/workspace.api'
import type { Workspace } from '@/api/types'

export const useWorkspaceStore = defineStore('workspace', () => {
  const workspaces = ref<Workspace[]>([])
  const currentWorkspaceId = ref(localStorage.getItem('gongkan.workspace_id') || '')
  const loading = ref(false)

  const currentWorkspace = computed(() => workspaces.value.find((item) => item.id === currentWorkspaceId.value) || workspaces.value[0])

  async function load() {
    loading.value = true
    try {
      workspaces.value = await listWorkspaces()
      if (!currentWorkspaceId.value && workspaces.value[0]) {
        currentWorkspaceId.value = workspaces.value[0].id
      }
      if (currentWorkspaceId.value) {
        localStorage.setItem('gongkan.workspace_id', currentWorkspaceId.value)
      }
    } finally {
      loading.value = false
    }
  }

  function select(id: string) {
    currentWorkspaceId.value = id
    localStorage.setItem('gongkan.workspace_id', id)
  }

  return { workspaces, currentWorkspaceId, currentWorkspace, loading, load, select }
})
