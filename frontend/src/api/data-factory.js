import request from '@/utils/api'

// 获取工具分类
export function getCategories() {
  return request({
    url: '/data-factory/categories/',
    method: 'get'
  })
}

// 执行工具
export function executeTool(data) {
  return request({
    url: '/data-factory/',
    method: 'post',
    data
  })
}

// 获取历史记录
export function getHistory(params) {
  return request({
    url: '/data-factory/',
    method: 'get',
    params
  })
}

// 获取统计信息
export function getStatistics() {
  return request({
    url: '/data-factory/statistics/',
    method: 'get'
  })
}

// 删除记录
export function deleteRecord(id) {
  return request({
    url: `/data-factory/${id}/`,
    method: 'delete'
  })
}

// 批量生成
export function batchGenerate(data) {
  return request({
    url: '/data-factory/batch_generate/',
    method: 'post',
    data
  })
}

// 获取变量函数列表（用于变量助手）
export function getVariableFunctions() {
  return request({
    url: '/data-factory/variable_functions/',
    method: 'get'
  })
}

// AI 字段分类
export function aiClassifyFields(data) {
  return request({
    url: '/data-factory/ai_classify/',
    method: 'post',
    data
  })
}

// AI 获取项目上下文
export function getAIContext(projectId) {
  return request({
    url: '/data-factory/ai_context/',
    method: 'get',
    params: { project_id: projectId }
  })
}

// AI 生成测试数据（SSE）
export function aiGenerateData(data, onMessage, onError) {
  const url = '/api/data-factory/ai_generate/'
  const xhr = new XMLHttpRequest()

  xhr.open('POST', url, true)
  xhr.setRequestHeader('Content-Type', 'application/json')

  xhr.onprogress = () => {
    const lines = xhr.responseText.split('\n')
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const event = JSON.parse(line.slice(6))
          onMessage(event)
        } catch (e) {
          // skip malformed lines
        }
      }
    }
  }

  xhr.onerror = () => onError?.('网络请求失败')
  xhr.send(JSON.stringify(data))

  return () => xhr.abort()  // return cancel function
}
