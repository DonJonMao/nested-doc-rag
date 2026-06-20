import { ref } from 'vue'
import { defineStore } from 'pinia'
import { deleteKnowledgeDocument, listKnowledgeDocuments, listKnowledgeOptions, uploadKnowledgeDocument } from '@/api/knowledge.api'
import type { IngestionJob, KnowledgeBase, KnowledgeDocument } from '@/api/types'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const options = ref<KnowledgeBase[]>([])
  const selectedId = ref('')
  const documents = ref<KnowledgeDocument[]>([])
  const latestIngestion = ref<IngestionJob | null>(null)
  const loading = ref(false)

  async function loadOptions(workspaceId: string) {
    loading.value = true
    try {
      options.value = await listKnowledgeOptions(workspaceId)
      if (!selectedId.value && options.value[0]) selectedId.value = options.value[0].id
    } finally {
      loading.value = false
    }
  }

  async function loadDocuments(kbId = selectedId.value) {
    if (!kbId) return
    documents.value = await listKnowledgeDocuments(kbId)
  }

  async function uploadDocument(kbId: string, file: File) {
    const result = await uploadKnowledgeDocument(kbId, file, true)
    if ('document' in result) {
      latestIngestion.value = result.ingestion_job || null
    }
    await loadDocuments(kbId)
    return result
  }

  async function deleteDocument(docId: string) {
    const result = await deleteKnowledgeDocument(docId, true)
    latestIngestion.value = result.ingestion_job || null
    await loadDocuments()
    return result
  }

  return { options, selectedId, documents, latestIngestion, loading, loadOptions, loadDocuments, uploadDocument, deleteDocument }
})
