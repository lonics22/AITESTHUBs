import request from '@/utils/api'

// Upload API document (simplified — parse only, no pipeline)
export function uploadImportDocument(formData) {
  return request({
    url: '/api-testing/ai-import/upload/',
    method: 'post',
    data: formData,
    timeout: 120000,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

// Configure project and import structure
export function configureImport(id, data) {
  return request({
    url: `/api-testing/ai-import/${id}/configure/`,
    method: 'post',
    data
  })
}

// AI analyze endpoints and generate test cases with _mode markers
export function analyzeImport(taskId) {
  return request({
    url: `/api-testing/ai-import/${taskId}/analyze/`,
    method: 'post',
    timeout: 180000 // LLM 调用可能持续 30-90 秒
  })
}

// Save — submits the user-edited test cases to the backend
// data = { cases: [...], collection_id: null, auto_structure: true }
export function saveImport(taskId, data) {
  return request({
    url: `/api-testing/ai-import/${taskId}/save/`,
    method: 'post',
    data
  })
}

// Get import task details
export function getImportTask(id) {
  return request({
    url: `/api-testing/ai-import/${id}/`,
    method: 'get'
  })
}

// List import tasks
export function listImportTasks(params) {
  return request({
    url: '/api-testing/ai-import/list_tasks/',
    method: 'get',
    params
  })
}

// SSE streaming analysis URL (GET, used with EventSource)
export function getAnalyzeStreamUrl(taskId) {
  return `/api/api-testing/ai-import/${taskId}/analyze_stream/`
}

// SSE streaming analysis via fetch (fallback when EventSource not supported)
export async function fetchAnalyzeStream(taskId, onEvent, onComplete, onError) {
  const url = getAnalyzeStreamUrl(taskId)
  try {
    const response = await fetch(url)
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''  // Keep incomplete line

      let currentEvent = ''
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (currentEvent === 'complete') {
              onComplete(data)
            } else if (currentEvent === 'error') {
              onError(data)
            } else if (onEvent) {
              onEvent(currentEvent, data)
            }
          } catch (e) {
            // Skip malformed JSON
          }
        }
      }
    }
  } catch (e) {
    onError({ message: e.message })
  }
}

