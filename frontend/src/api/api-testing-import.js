import request from '@/utils/api'

export function uploadImportDocument(formData) {
  return request({
    url: '/api-testing/ai-import/upload/',
    method: 'post',
    data: formData,
    timeout: 120000,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

export function getImportTask(id) {
  return request({
    url: `/api-testing/ai-import/${id}/`,
    method: 'get'
  })
}

export function getImportQuestions(id) {
  return request({
    url: `/api-testing/ai-import/${id}/questions/`,
    method: 'get'
  })
}

export function configureImport(id, data) {
  return request({
    url: `/api-testing/ai-import/${id}/configure/`,
    method: 'post',
    data
  })
}

export function submitImportAnswers(id, data) {
  return request({
    url: `/api-testing/ai-import/${id}/answers/`,
    method: 'post',
    data
  })
}

export function previewImport(id) {
  return request({
    url: `/api-testing/ai-import/${id}/preview/`,
    method: 'get'
  })
}

export function saveImportRequests(id) {
  return request({
    url: `/api-testing/ai-import/${id}/save/`,
    method: 'post'
  })
}

export function getImportTaskList(params) {
  return request({
    url: '/api-testing/ai-import/list_tasks/',
    method: 'get',
    params
  })
}

// SSE subscription - uses raw XHR for streaming
export function subscribeImportProgress(taskId, onMessage, onError) {
  const url = `/api/api-testing/ai-import/${taskId}/logs/`
  const xhr = new XMLHttpRequest()
  xhr.open('GET', url, true)
  xhr.withCredentials = true
  xhr.onprogress = () => {
    const lines = xhr.responseText.split('\n')
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onMessage(JSON.parse(line.slice(6)))
        } catch (e) { /* skip malformed lines */ }
      }
    }
  }
  xhr.onerror = () => onError?.('SSE connection failed')
  xhr.send()
  return () => xhr.abort()
}
