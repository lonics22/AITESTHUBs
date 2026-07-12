<template>
  <div class="ai-import-wizard">
    <div class="page-header">
      <h2>{{ $t('apiTesting.aiImport.title') }}</h2>
    </div>

    <!-- Steps indicator -->
    <el-steps :active="currentStep" align-center class="wizard-steps">
      <el-step :title="$t('apiTesting.aiImport.stepUpload')" />
      <el-step :title="$t('apiTesting.aiImport.stepConfig')" />
      <el-step :title="$t('apiTesting.aiImport.stepAnalysis')" />
      <el-step :title="$t('apiTesting.aiImport.stepResults')" />
    </el-steps>

    <!-- Step content area -->
    <div class="step-content">
      <!-- ==================== Step 0: Upload ==================== -->
      <div v-show="currentStep === 0" class="step-panel">
        <el-card>
          <el-upload
            ref="uploadRef"
            drag
            :auto-upload="false"
            :show-file-list="true"
            accept=".json,.yaml,.yml"
            :limit="1"
            :disabled="uploading"
            :on-change="handleFileChange"
            :file-list="fileList"
          >
            <el-icon class="el-icon--upload" :size="48">
              <UploadFilled />
            </el-icon>
            <div class="el-upload__text">
              {{ $t('apiTesting.aiImport.dropFileHint') }}
            </div>
            <template #tip>
              <div class="el-upload__tip">
                {{ $t('apiTesting.aiImport.supportedFormats') }}
              </div>
            </template>
          </el-upload>

          <div v-if="uploading" class="upload-loading-overlay">
            <el-progress type="circle" :percentage="50" :width="80" :stroke-width="5" indeterminate />
            <p class="upload-loading-text">{{ $t('apiTesting.aiImport.analyzing') }}</p>
          </div>
        </el-card>

        <div v-if="uploadResult && !uploading" class="upload-result">
          <el-alert
            :title="uploadResult.message"
            type="success"
            :closable="false"
            show-icon
          />
          <div class="result-details">
            <el-tag :type="formatTagType" size="large">
              {{ uploadResult.detected_format }}
            </el-tag>
            <span class="endpoint-count">
              {{ uploadResult.endpoint_count }}{{ $t('apiTesting.aiImport.endpointsFound') }}
            </span>
          </div>
        </div>
      </div>

      <!-- ==================== Step 1: Config + Endpoints ==================== -->
      <div v-show="currentStep === 1" class="step-panel">
        <el-card>
          <template #header>
            <span>{{ $t('apiTesting.aiImport.stepConfig') }}</span>
          </template>
          <el-form label-width="140px">
            <el-form-item :label="$t('apiTesting.aiImport.selectProject')" required>
              <el-select
                v-model="configForm.project_id"
                :placeholder="$t('apiTesting.common.selectProject')"
                style="width: 100%"
                filterable
                @change="handleProjectChange"
              >
                <el-option
                  v-for="project in projects"
                  :key="project.id"
                  :label="project.name"
                  :value="project.id"
                />
              </el-select>
            </el-form-item>

            <el-form-item :label="$t('apiTesting.aiImport.structureMode')">
              <el-radio-group v-model="configForm.auto_structure" class="structure-radio-group">
                <el-radio :value="true" border>
                  <div class="radio-content">
                    <strong>{{ $t('apiTesting.aiImport.autoStructureLabel') }}</strong>
                    <p class="radio-tip">{{ $t('apiTesting.aiImport.autoStructureTip') }}</p>
                  </div>
                </el-radio>
                <el-radio :value="false" border>
                  <div class="radio-content">
                    <strong>{{ $t('apiTesting.aiImport.specificCollectionLabel') }}</strong>
                    <p class="radio-tip">{{ $t('apiTesting.aiImport.selectCollection') }}</p>
                  </div>
                </el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item
              v-if="!configForm.auto_structure"
              :label="$t('apiTesting.aiImport.selectCollection')"
              required
            >
              <el-tree-select
                v-model="configForm.collection_id"
                :data="collections"
                :props="treeProps"
                :placeholder="$t('apiTesting.aiImport.selectCollection')"
                style="width: 100%"
                filterable
                check-strictly
              />
            </el-form-item>
          </el-form>
        </el-card>

        <!-- Endpoint list preview -->
        <el-card v-if="parsedEndpoints.length > 0" class="endpoint-preview-card">
          <template #header>
            <span>{{ $t('apiTesting.aiImport.endpointsFound').replace('个端点', '') }}{{ parsedEndpoints.length }} 个端点</span>
          </template>
          <el-collapse v-model="activeEndpointNames" class="endpoint-collapse">
            <el-collapse-item
              v-for="(ep, idx) in parsedEndpoints"
              :key="idx"
              :name="String(idx)"
              :title="`${ep.method} ${ep.summary || ep.path}`"
            >
              <div class="endpoint-detail">
                <span :class="['method-badge', ep.method?.toLowerCase()]">{{ ep.method }}</span>
                <code class="endpoint-path">{{ ep.path }}</code>
              </div>
              <div v-if="ep.tags && ep.tags.length" class="endpoint-tags">
                <el-tag v-for="tag in ep.tags" :key="tag" size="small" type="info">{{ tag }}</el-tag>
              </div>
              <div v-if="ep.description" class="endpoint-desc">{{ ep.description }}</div>
            </el-collapse-item>
          </el-collapse>
        </el-card>
      </div>

      <!-- ==================== Step 2: AI Analysis Results ==================== -->
      <div v-show="currentStep === 2" class="step-panel analysis-panel">
        <Transition name="analysis-fade" mode="out-in">
          <!-- Loading skeleton + SSE progress -->
          <div v-if="analysisLoading" key="loading" class="analysis-loading-skeleton">
            <div class="skeleton-status-bar">
              <el-icon v-if="currentPhase !== 'complete'" class="is-loading" :size="20"><Loading /></el-icon>
              <el-icon v-else :size="20" color="#67c23a"><SuccessFilled /></el-icon>
              <span class="skeleton-status-text">
                {{ sseConnected ? phaseText : $t('apiTesting.aiImport.analysisLoading') }}
              </span>
              <el-tag
                v-if="reviewScore !== null"
                :type="scoreTagType"
                size="small"
                effect="dark"
                class="score-badge"
              >
                {{ reviewScore }}分
              </el-tag>
            </div>
            <div class="skeleton-card">
              <div class="skeleton-card-header">
                <el-skeleton :rows="1" animated />
              </div>
              <div class="skeleton-card-body">
                <el-skeleton :rows="4" animated />
              </div>
            </div>
            <div class="skeleton-card">
              <div class="skeleton-card-header">
                <el-skeleton :rows="1" animated />
              </div>
              <div class="skeleton-card-body">
                <el-skeleton :rows="3" animated />
              </div>
            </div>
            <div class="skeleton-card skeleton-card-last">
              <div class="skeleton-card-header">
                <el-skeleton :rows="1" animated />
              </div>
              <div class="skeleton-card-body">
                <el-skeleton :rows="5" animated />
              </div>
            </div>
          </div>

          <!-- Cards list -->
          <div v-else key="content" class="analysis-content">
            <!-- Stats bar -->
            <div class="analysis-stats">
              <el-alert
                :title="statsText"
                type="info"
                :closable="false"
                show-icon
              />
            </div>

            <!-- Grouped by endpoint -->
            <div v-for="(group, gIdx) in analysisGroups" :key="gIdx" class="endpoint-group">
              <div class="endpoint-group-header" @click="toggleGroup(gIdx)">
                <span
                  class="group-toggle"
                  :class="{ expanded: groupExpanded[gIdx] !== false }"
                >&#9654;</span>
                <el-tag :type="methodTagType(group.method)" size="small" effect="dark">{{ group.method }}</el-tag>
                <span class="endpoint-group-path">{{ group.path }}</span>
                <span class="endpoint-group-summary">{{ group.cases.length }} 个用例</span>
              </div>
              <Transition name="group-body">
                <div v-show="groupExpanded[gIdx] !== false" class="group-body">
                  <TestCaseCard
                    v-for="(tc, cIdx) in group.cases"
                    :key="cIdx"
                    :test-case="tc"
                    :case-index="globalCaseIndex(gIdx, cIdx)"
                    @update:model-value="(val) => updateCase(gIdx, cIdx, val)"
                  />
                </div>
              </Transition>
            </div>

            <div v-if="analysisCases.length === 0 && !analysisLoading" class="empty-state">
              <el-empty :description="$t('apiTesting.common.noData')" />
            </div>
          </div>
        </Transition>
      </div>

      <!-- ==================== Step 3: Results ==================== -->
      <div v-show="currentStep === 3" class="result-panel">
        <div class="result-header">
          <el-icon :size="48" color="#67c23a">
            <SuccessFilled />
          </el-icon>
          <h2>{{ $t('apiTesting.aiImport.saveSuccess', { count: savedCount }) }}</h2>
        </div>

        <el-card class="result-list-card">
          <template #header>
            <span>{{ $t('apiTesting.aiImport.generatedRequests') }}</span>
          </template>
          <el-table :data="savedRequests" max-height="400" stripe :empty-text="$t('apiTesting.common.noData')">
            <el-table-column type="expand" width="40">
              <template #default="{ row }">
                <div class="request-detail-json">
                  <div v-if="row.body && Object.keys(row.body).length" class="json-section">
                    <strong class="json-section-title">Body</strong>
                    <pre class="json-pre">{{ formatJSON(row.body) }}</pre>
                  </div>
                  <div v-if="row.headers && Object.keys(row.headers).length" class="json-section">
                    <strong class="json-section-title">Headers</strong>
                    <pre class="json-pre">{{ formatJSON(row.headers) }}</pre>
                  </div>
                  <div v-if="row.assertions && row.assertions.length" class="json-section">
                    <strong class="json-section-title">Assertions</strong>
                    <pre class="json-pre">{{ formatJSON(row.assertions) }}</pre>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column :label="$t('apiTesting.aiImport.method')" width="90">
              <template #default="{ row }">
                <el-tag :type="methodTagType(row.method || row.method_display)" size="small">
                  {{ row.method || row.method_display }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('apiTesting.aiImport.name')" prop="name" min-width="200" />
            <el-table-column :label="$t('apiTesting.aiImport.path')" prop="path" min-width="250" show-overflow-tooltip />
            <el-table-column :label="$t('apiTesting.aiImport.body')" min-width="180">
              <template #default="{ row }">
                <span v-if="row.body && Object.keys(row.body).length" class="body-preview">{{ bodyPreview(row.body) }}</span>
                <span v-else class="body-empty">-</span>
              </template>
            </el-table-column>
            <el-table-column :label="$t('apiTesting.aiImport.collection')" min-width="150">
              <template #default="{ row }">
                {{ row.collection_name || row.collection || '-' }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <div class="result-actions">
          <el-button type="primary" size="large" @click="viewResults">{{ $t('apiTesting.aiImport.viewResult') }}</el-button>
          <el-button size="large" @click="resetWizard">{{ $t('apiTesting.aiImport.importMore') }}</el-button>
        </div>
      </div>
    </div>

    <!-- Bottom navigation buttons -->
    <div class="step-actions">
      <!-- Previous: Step 1 only (Step 2 has its own prev button in content) -->
      <el-button v-if="currentStep === 1" @click="prevStep">
        {{ $t('apiTesting.aiImport.prev') }}
      </el-button>

      <!-- Step 0: Next -->
      <el-button
        v-if="currentStep === 0"
        type="primary"
        :disabled="!canProceed"
        @click="nextStep"
      >
        {{ $t('apiTesting.aiImport.next') }}
      </el-button>

      <!-- Step 1: AI Analyze -->
      <el-button
        v-if="currentStep === 1"
        type="primary"
        :loading="analyzing"
        :disabled="!canProceed"
        @click="startAnalysis"
      >
        {{ $t('apiTesting.aiImport.aiAnalyze') }}
      </el-button>

      <!-- Step 2: Save & Back buttons inside the panel -->
      <div v-if="currentStep === 2 && !analysisLoading" class="step-actions-inner">
        <el-button @click="prevStep">
          {{ $t('apiTesting.aiImport.prev') }}
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="analysisCases.length === 0"
          @click="confirmSave"
        >
          {{ $t('apiTesting.aiImport.confirmSave') }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { UploadFilled, SuccessFilled, Loading } from '@element-plus/icons-vue'
import {
  uploadImportDocument,
  configureImport,
  analyzeImport,
  saveImport,
  getAnalyzeStreamUrl,
  getImportTask
} from '@/api/api-testing-import'
import { getApiProjects, getApiCollections } from '@/api/api-testing'
import TestCaseCard from '@/components/TestCaseCard.vue'

const { t } = useI18n()
const router = useRouter()

// ======================== Wizard State ========================
const currentStep = ref(0)
const taskId = ref(null)
const uploadRef = ref(null)
const fileList = ref([])
const uploadResult = ref(null)
const uploading = ref(false)
const parsedEndpoints = ref([])
const activeEndpointNames = ref([])

// ======================== SSE Streaming State (Phase 2) ========================
const currentPhase = ref('')
const reviewScore = ref(null)
const sseConnected = ref(false)

const phaseText = computed(() => {
  const map = {
    generating: t('apiTesting.aiImport.phaseGenerating'),
    reviewing: t('apiTesting.aiImport.phaseReviewing'),
    retrying: t('apiTesting.aiImport.phaseRetrying'),
    complete: t('apiTesting.aiImport.phaseComplete')
  }
  return map[currentPhase.value] || currentPhase.value
})

const scoreTagType = computed(() => {
  if (reviewScore.value === null) return 'info'
  if (reviewScore.value >= 90) return 'success'
  if (reviewScore.value >= 60) return 'warning'
  return 'danger'
})

// ======================== Tag Helpers ========================
const formatTagMap = {
  swagger: 'primary',
  openapi: 'success',
  postman: 'warning',
  har: 'info'
}
const formatTagType = computed(() => {
  if (!uploadResult.value) return 'info'
  const fmt = (uploadResult.value.detected_format || '').toLowerCase()
  return formatTagMap[fmt] || 'info'
})

const methodTagMap = { GET: 'success', POST: 'primary', PUT: 'warning', PATCH: 'warning', DELETE: 'danger' }
const methodTagType = (method) => methodTagMap[method?.toUpperCase()] || 'info'

// ======================== Step 0: Upload ========================
const handleFileChange = async (file) => {
  fileList.value = [file]
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.raw || file)
    const res = await uploadImportDocument(formData)
    taskId.value = res.data.task_id || res.data.id
    const endpoints = res.data.parsed_endpoints || []
    parsedEndpoints.value = endpoints
    // Auto-expand endpoint collapse
    activeEndpointNames.value = endpoints.map((_, i) => String(i))
    uploadResult.value = {
      message: res.data.message || t('apiTesting.common.success'),
      detected_format: res.data.doc_type || 'unknown',
      endpoint_count: endpoints.length
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.response?.data?.error || t('apiTesting.messages.error.loadFailed'))
    uploadResult.value = null
    fileList.value = []
    parsedEndpoints.value = []
  } finally {
    uploading.value = false
  }
}

// ======================== Step 1: Config ========================
const projects = ref([])
const collections = ref([])
const configForm = reactive({
  project_id: null,
  auto_structure: true,
  collection_id: null
})
const treeProps = {
  children: 'children',
  label: 'name',
  value: 'id',
  disabled: 'disabled'
}

const loadProjects = async () => {
  try {
    const res = await getApiProjects()
    projects.value = res.data.results || res.data
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadProjects'))
  }
}

const handleProjectChange = async (projectId) => {
  configForm.collection_id = null
  if (!projectId) {
    collections.value = []
    return
  }
  if (!configForm.auto_structure) {
    await loadCollections(projectId)
  }
}

const loadCollections = async (projectId) => {
  try {
    const res = await getApiCollections({ project_id: projectId })
    const items = res.data.results || res.data
    collections.value = buildCollectionTree(items)
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadCollections'))
  }
}

const buildCollectionTree = (items) => {
  const map = {}
  const roots = []
  items.forEach((item) => {
    map[item.id] = { ...item, children: [] }
  })
  items.forEach((item) => {
    if (item.parent && map[item.parent]) {
      map[item.parent].children.push(map[item.id])
    } else {
      roots.push(map[item.id])
    }
  })
  return roots
}

// ======================== Step 2: AI Analysis ========================
const analysisCases = ref([])
const analysisLoading = ref(false)
const analyzing = ref(false)
const saving = ref(false)
const groupExpanded = ref({})

// Group analysis cases by endpoint
const analysisGroups = computed(() => {
  const groups = {}
  for (const tc of analysisCases.value) {
    const key = `${tc.method || '?'} ${tc.url || tc.path || ''}`
    if (!groups[key]) {
      groups[key] = {
        endpoint: key,
        method: tc.method,
        path: tc.url || tc.path,
        cases: []
      }
    }
    groups[key].cases.push(tc)
  }
  return Object.values(groups)
})

// Global case index across all groups
const globalCaseIndex = (gIdx, cIdx) => {
  let idx = 1
  for (let g = 0; g < gIdx; g++) {
    idx += analysisGroups.value[g].cases.length
  }
  return idx + cIdx
}

// Toggle endpoint group collapse
const toggleGroup = (gIdx) => {
  const current = groupExpanded.value[gIdx]
  groupExpanded.value[gIdx] = current === false ? true : false
}

// Stats text
const statsText = computed(() => {
  const normal = analysisCases.value.filter(tc => tc._case_type === 'normal').length
  const error = analysisCases.value.filter(tc => tc._case_type === 'error').length
  const endpoints = analysisGroups.value.length
  return t('apiTesting.aiImport.analysisStats', { normal, error, endpoints })
})

// Update a specific case in the flat analysisCases array
const updateCase = (gIdx, cIdx, updatedValue) => {
  let globalIdx = 0
  for (let g = 0; g < gIdx; g++) {
    globalIdx += analysisGroups.value[g].cases.length
  }
  globalIdx += cIdx
  if (globalIdx < analysisCases.value.length) {
    analysisCases.value[globalIdx] = updatedValue
  }
}

// ======================== SSE Streaming (Phase 2) ========================

/** 使用 EventSource 打开 SSE 流，返回 Promise */
const startAnalysisStream = (taskId) => {
  return new Promise((resolve, reject) => {
    sseConnected.value = true
    reviewScore.value = null
    currentPhase.value = 'generating'

    const url = getAnalyzeStreamUrl(taskId)
    const eventSource = new EventSource(url)

    // 5 分钟超时
    const timeout = setTimeout(() => {
      eventSource.close()
      sseConnected.value = false
      reject(new Error(t('apiTesting.aiImport.sseTimeout')))
    }, 300000)

    eventSource.addEventListener('phase', (e) => {
      try {
        currentPhase.value = JSON.parse(e.data).phase
      } catch { /* ignore */ }
    })

    eventSource.addEventListener('review_result', (e) => {
      try {
        const data = JSON.parse(e.data)
        reviewScore.value = data.total_score != null ? data.total_score : (data.scores?.total_score ?? null)
      } catch { /* ignore */ }
    })

    eventSource.addEventListener('complete', () => {
      clearTimeout(timeout)
      eventSource.close()
      sseConnected.value = false
      resolve()
    })

    eventSource.addEventListener('error', () => {
      // 服务端发来的 error 事件 —— 不断开连接，继续等
    })

    eventSource.onerror = () => {
      clearTimeout(timeout)
      eventSource.close()
      sseConnected.value = false
      reject(new Error(t('apiTesting.aiImport.sseConnectionFailed')))
    }
  })
}

/** 轮询回溯：SSE 断开后定时查询任务状态 */
const startPolling = (taskId) => {
  const pollInterval = setInterval(async () => {
    try {
      const res = await getImportTask(taskId)
      const task = res.data
      if (task.status === 'completed') {
        clearInterval(pollInterval)
        const cases = task.generated_summary?.generated_cases || []
        analysisCases.value = cases.map(tc => JSON.parse(JSON.stringify(tc)))
        analysisLoading.value = false
      } else if (task.status === 'failed') {
        clearInterval(pollInterval)
        ElMessage.error(task.error_message || t('apiTesting.aiImport.errorAnalysisFailed'))
        analysisLoading.value = false
        currentStep.value = 1
      }
    } catch (err) {
      clearInterval(pollInterval)
      analysisLoading.value = false
      currentStep.value = 1
    }
  }, 5000)
}

// Start AI analysis — try SSE first, fallback to POST
const startAnalysis = async () => {
  if (!taskId.value) return
  analyzing.value = true
  analysisLoading.value = true
  currentStep.value = 2 // Show step 2 loading

  try {
    // Step 1: Configure project first
    await configureImport(taskId.value, {
      project_id: configForm.project_id,
      auto_structure: configForm.auto_structure,
      target_collection_id: configForm.collection_id
    })

    // Step 2: Try SSE first
    try {
      await startAnalysisStream(taskId.value)
    } catch (sseError) {
      console.warn('SSE failed, falling back to POST analyze', sseError)
      // Fallback to POST
      const res = await analyzeImport(taskId.value)
      const cases = res.data.generated_cases || res.data.cases || []
      analysisCases.value = cases.map(tc => JSON.parse(JSON.stringify(tc)))
      await nextTick()
      return
    }

    // Step 3: SSE succeeded — fetch generated cases from task
    const res = await getImportTask(taskId.value)
    const cases = res.data.generated_summary?.generated_cases || []
    analysisCases.value = cases.map(tc => JSON.parse(JSON.stringify(tc)))
  } catch (error) {
    const msg = error.response?.data?.detail || error.message || t('apiTesting.aiImport.errorAnalysisFailed')
    ElMessage.error(msg)
    currentStep.value = 1 // Go back to config
  } finally {
    analyzing.value = false
    analysisLoading.value = false
  }
}

// Confirm and save
const confirmSave = async () => {
  if (!taskId.value) return
  saving.value = true

  try {
    // Collect all cases from groups (they may have been modified by user)
    const allCases = []
    for (const group of analysisGroups.value) {
      allCases.push(...group.cases)
    }

    const res = await saveImport(taskId.value, {
      cases: allCases,
      collection_id: configForm.auto_structure ? null : configForm.collection_id,
      auto_structure: configForm.auto_structure
    })

    const data = res.data || res
    savedCount.value = data.requests_created?.length || data.count || allCases.length
    savedRequests.value = data.requests_details || []
    currentStep.value = 3
    ElMessage.success(t('apiTesting.aiImport.saveSuccess', { count: savedCount.value }))
  } catch (error) {
    const msg = error.response?.data?.detail || error.message || t('apiTesting.aiImport.errorSaveFailed')
    ElMessage.error(msg)
  } finally {
    saving.value = false
  }
}

// Format JSON string for display
const formatJSON = (data) => {
  if (!data) return ''
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

// Short body preview for table cell
const bodyPreview = (data) => {
  if (!data || typeof data !== 'object') return '-'
  const keys = Object.keys(data)
  if (keys.length === 0) return '-'
  const preview = JSON.stringify(data)
  return preview.length > 60 ? preview.slice(0, 60) + '...' : preview
}

// ======================== Step 3: Results ========================
const savedCount = ref(0)
const savedRequests = ref([])

const viewResults = () => {
  router.push('/api-testing/interfaces')
}

const resetWizard = () => {
  currentStep.value = 0
  taskId.value = null
  uploading.value = false
  uploadResult.value = null
  fileList.value = []
  parsedEndpoints.value = []
  activeEndpointNames.value = []
  analysisCases.value = []
  analysisLoading.value = false
  analyzing.value = false
  saving.value = false
  savedCount.value = 0
  savedRequests.value = []
  configForm.project_id = null
  configForm.auto_structure = true
  configForm.collection_id = null
}

// ======================== Navigation ========================
const canProceed = computed(() => {
  switch (currentStep.value) {
    case 0:
      return !!taskId.value
    case 1:
      if (!configForm.project_id) return false
      if (!configForm.auto_structure && !configForm.collection_id) return false
      return true
    default:
      return true
  }
})

const nextStep = () => {
  if (currentStep.value === 0 && taskId.value) {
    currentStep.value = 1
  }
}

const prevStep = () => {
  if (currentStep.value > 0) {
    currentStep.value--
  }
}

// ======================== Lifecycle ========================
onMounted(() => {
  loadProjects()
})
</script>

<style scoped>
.ai-import-wizard {
  padding: 24px 32px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  min-height: calc(100vh - 60px);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: #1d2129;
}

.wizard-steps {
  margin-bottom: 28px;
  background: #fff;
  padding: 20px 40px;
  border-radius: 8px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.step-content {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 20px;
}

/* ============= Step Container ============= */
.step-panel {
  max-width: 840px;
  margin: 0 auto;
  width: 100%;
}

/* ============= Upload Step ============= */
.upload-result {
  margin-top: 16px;
}

.result-details {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 12px;
  padding: 16px 20px;
  background: #f0f9ff;
  border-radius: 8px;
}

.format-badge {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
}

.endpoint-count {
  font-size: 16px;
  font-weight: 500;
  color: #409eff;
}

.upload-loading-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 0;
}

.upload-loading-text {
  margin-top: 20px;
  font-size: 15px;
  color: #606266;
}

/* ============= Config Step ============= */
.config-card {
  margin-bottom: 16px;
}

.radio-tip {
  margin: 4px 0 0;
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}

.structure-radio-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.structure-radio-group .el-radio {
  width: 100%;
  margin-right: 0;
  height: auto;
  padding: 12px 16px;
}

.radio-content {
  line-height: 1.5;
}

/* Endpoint preview card */
.endpoint-preview-card {
  margin-top: 16px;
}

.endpoint-preview-card .el-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.endpoint-preview-card .el-card__header span {
  font-weight: 600;
}

.endpoint-preview-card .el-card__header .ep-count-badge {
  font-weight: 400;
  font-size: 12px;
  color: #909399;
}

.endpoint-collapse {
  border-top: none;
}

.endpoint-detail {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.method-badge {
  display: inline-block;
  padding: 0 8px;
  border-radius: 4px;
  font-weight: 700;
  font-size: 11px;
  line-height: 22px;
  color: #fff;
  min-width: 52px;
  text-align: center;
  letter-spacing: 0.5px;
}

.method-badge.get { background: linear-gradient(135deg, #67c23a, #85ce61); }
.method-badge.post { background: linear-gradient(135deg, #409eff, #6ab0ff); }
.method-badge.put { background: linear-gradient(135deg, #e6a23c, #ebb563); }
.method-badge.patch { background: linear-gradient(135deg, #e6a23c, #ebb563); }
.method-badge.delete { background: linear-gradient(135deg, #f56c6c, #f78989); }

.endpoint-path {
  font-family: 'Courier New', Courier, monospace;
  font-size: 13px;
  color: #606266;
}

.endpoint-tags {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.endpoint-desc {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

/* ============= Analysis Step 2 ============= */
.analysis-panel {
  max-width: 100%;
}

.analysis-loading-skeleton {
  max-width: 840px;
  margin: 0 auto;
  width: 100%;
}

.skeleton-status-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px 0 24px;
  color: #606266;
}

.skeleton-status-text {
  font-size: 16px;
  color: #909399;
}

.score-badge {
  margin-left: 8px;
  font-weight: 600;
}

.skeleton-card {
  margin-bottom: 20px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.skeleton-card-header {
  padding: 14px 20px;
  background: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

.skeleton-card-body {
  padding: 16px 20px;
}

.skeleton-card-last {
  opacity: 0.6;
}

/* Transition between loading skeleton and content */
.analysis-fade-enter-active,
.analysis-fade-leave-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}
.analysis-fade-enter-from {
  opacity: 0;
  transform: translateY(10px);
}
.analysis-fade-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

.analysis-stats {
  margin-bottom: 24px;
}

.analysis-stats .el-alert {
  border-radius: 8px;
}

/* Endpoint group card */
.endpoint-group {
  margin-bottom: 28px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  overflow: hidden;
}

.endpoint-group-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  background: linear-gradient(135deg, #f5f7fa, #f0f2f5);
  border-bottom: 1px solid #ebeef5;
  font-size: 14px;
  cursor: default;
}

.endpoint-group-header .group-toggle {
  cursor: pointer;
  transition: transform 0.2s;
  color: #909399;
  font-size: 12px;
  user-select: none;
}

.endpoint-group-header .group-toggle.expanded {
  transform: rotate(90deg);
}

.endpoint-group-path {
  font-family: 'Courier New', Courier, monospace;
  font-weight: 500;
  color: #303133;
  flex: 1;
  font-size: 14px;
}

.endpoint-group-summary {
  font-size: 12px;
  color: #909399;
}

/* Cards inside group */
.endpoint-group .test-case-card-wrapper {
  padding: 12px 16px 4px;
}

.endpoint-group .test-case-card-wrapper:last-child {
  padding-bottom: 16px;
}

/* Collapsible group body */
.group-body-enter-active,
.group-body-leave-active {
  transition: all 0.25s ease;
}

.group-body-enter-from,
.group-body-leave-to {
  opacity: 0;
  max-height: 0;
}

.empty-state {
  padding: 80px 0;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

/* ============= Results Step ============= */
.result-panel {
  max-width: 840px;
  margin: 0 auto;
  width: 100%;
}

.result-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 24px 0 16px;
  gap: 12px;
}

.result-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 500;
  color: #303133;
}

.result-header .result-subtitle {
  color: #909399;
  font-size: 14px;
  margin: 0;
}

.result-list-card {
  margin-top: 20px;
  border-radius: 10px;
}

.result-actions {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-top: 28px;
}

/* ============= Bottom Navigation ============= */
.step-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  padding: 20px 0;
  border-top: 1px solid #ebeef5;
  flex-wrap: wrap;
  background: #fff;
  border-radius: 0 0 8px 8px;
  margin-top: auto;
}

.step-actions-inner {
  display: flex;
  gap: 12px;
}

/* Step card styling */
.step-panel > .el-card,
.step-panel .config-card {
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

/* Hover effects */
.endpoint-preview-card .el-card__body {
  padding: 8px 16px;
}

/* Transition between steps */
.step-panel {
  animation: fadeInUp 0.3s ease;
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Project selector */
.step-panel .el-select {
  width: 100%;
}

/* JSON detail expand styles */
.request-detail-json {
  padding: 12px 20px;
  background: #fafafa;
}

.json-section {
  margin-bottom: 12px;
}

.json-section:last-child {
  margin-bottom: 0;
}

.json-section-title {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: #606266;
}

.json-pre {
  margin: 0;
  padding: 10px 14px;
  background: #1d2129;
  color: #e6e6e6;
  border-radius: 6px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre;
}

.body-preview {
  font-family: 'Courier New', Courier, monospace;
  font-size: 12px;
  color: #606266;
  cursor: default;
}

.body-empty {
  color: #c0c4cc;
}
</style>

<style>
/* Unscoped: targets teleported el-select-dropdown items outside component tree */
.ai-import-wizard .el-select-dropdown__item {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
