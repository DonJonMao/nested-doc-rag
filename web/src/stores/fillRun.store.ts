import { ref } from 'vue'
import { defineStore } from 'pinia'
import { cancelFillRun, createSimpleFillRun, getFillRun, listFillRuns } from '@/api/fillRuns.api'
import { uploadForm } from '@/api/forms.api'
import type { FillRun, FillRunDetail, FillRunListItem, FormFile } from '@/api/types'

export const useFillRunStore = defineStore('fillRun', () => {
  const runs = ref<FillRunListItem[]>([])
  const current = ref<FillRun | FillRunDetail | null>(null)
  const detail = ref<FillRunDetail | null>(null)
  const uploadedForm = ref<FormFile | null>(null)
  const loading = ref(false)

  async function upload(workspaceId: string, file: File) {
    uploadedForm.value = await uploadForm(workspaceId, file)
    return uploadedForm.value
  }

  async function createSimple(workspaceId: string, knowledgeBaseId: string, formFileId: string, name: string, roomContext: string) {
    current.value = await createSimpleFillRun({
      workspace_id: workspaceId,
      knowledge_base_id: knowledgeBaseId,
      form_file_id: formFileId,
      name,
      room_context: roomContext,
    })
    return current.value
  }

  async function loadRuns(workspaceId: string, status?: string) {
    loading.value = true
    try {
      runs.value = await listFillRuns(workspaceId, status, true)
    } finally {
      loading.value = false
    }
  }

  async function loadRun(runId: string) {
    detail.value = await getFillRun(runId)
    current.value = detail.value
    return detail.value
  }

  async function cancel(runId: string) {
    const data = await cancelFillRun(runId)
    current.value = data.fill_run
    return data
  }

  return { runs, current, detail, uploadedForm, loading, upload, createSimple, loadRuns, loadRun, cancel }
})
