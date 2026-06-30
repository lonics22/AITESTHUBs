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
      <el-step :title="$t('apiTesting.aiImport.stepQuestions')" />
      <el-step :title="$t('apiTesting.aiImport.stepGenerate')" />
      <el-step :title="$t('apiTesting.aiImport.stepResults')" />
    </el-steps>

    <!-- Step content area -->
    <div class="step-content">
      <!-- Step 0: Upload -->
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

      <!-- Step 1: Config -->
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
              <el-radio-group v-model="configForm.auto_structure">
                <el-radio :value="true" border>
                  <div>
                    <strong>{{ $t('apiTesting.aiImport.autoStructureLabel') }}</strong>
                    <p class="radio-tip">{{ $t('apiTesting.aiImport.autoStructureTip') }}</p>
                  </div>
                </el-radio>
                <el-radio :value="false" border>
                  <div>
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
      </div>

      <!-- Step 2: Analysis (auto) -->
      <div v-show="currentStep === 2" class="step-panel">
        <el-card>
          <div class="analysis-loading">
            <el-progress type="circle" :percentage="50" :width="120" :stroke-width="6" indeterminate />
            <p class="analysis-text">{{ $t('apiTesting.aiImport.analyzing') }}</p>
          </div>
        </el-card>
      </div>

      <!-- Step 3: Questions -->
      <div v-show="currentStep === 3" class="step-panel">
        <el-alert
          :title="$t('apiTesting.aiImport.questionsHint')"
          type="info"
          :closable="false"
          show-icon
          class="questions-hint"
        />
        <div class="questions-list">
          <el-card
            v-for="(question, qIndex) in questions"
            :key="qIndex"
            class="question-card"
          >
            <template #header>
              <div class="question-header">
                <span class="question-title">{{ question.title }}</span>
                <el-tag size="small" type="info">{{ question.field_type }}</el-tag>
              </div>
              <div v-if="question.description" class="question-desc">
                {{ question.description }}
              </div>
            </template>

            <!-- string type -->
            <el-input
              v-if="question.field_type === 'string'"
              v-model="answers[question.id]"
              :placeholder="$t('apiTesting.aiImport.inputPlaceholder')"
              clearable
            />

            <!-- select type -->
            <el-select
              v-else-if="question.field_type === 'select'"
              v-model="answers[question.id]"
              :placeholder="$t('apiTesting.common.pleaseSelect')"
              style="width: 100%"
              filterable
              allow-create
              clearable
            >
              <el-option
                v-for="opt in question.options"
                :key="opt.value || opt"
                :label="opt.label || opt"
                :value="opt.value || opt"
              />
            </el-select>

            <!-- multi_param type -->
            <div v-else-if="question.field_type === 'multi_param'" class="multi-param-table">
              <el-table :data="question.options || []" size="small" max-height="400">
                <el-table-column :label="$t('apiTesting.aiImport.paramName')" prop="param_name" width="160" />
                <el-table-column :label="$t('apiTesting.aiImport.location')" prop="location" width="100">
                  <template #default="{ row }">
                    <el-tag size="small" :type="locationTagType(row.location)">
                      {{ row.location }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('apiTesting.aiImport.endpoint')" prop="endpoint" min-width="200">
                  <template #default="{ row }">
                    <span class="endpoint-method">{{ row.method }}</span>
                    <span class="endpoint-path">{{ row.endpoint || row.path }}</span>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('apiTesting.aiImport.description')" prop="description" min-width="160" />
                <el-table-column :label="$t('apiTesting.aiImport.value')" width="200">
                  <template #default="{ row }">
                    <el-input
                      v-model="row.user_value"
                      :placeholder="$t('apiTesting.aiImport.inputPlaceholder')"
                      size="small"
                      clearable
                    />
                  </template>
                </el-table-column>
              </el-table>
            </div>

            <!-- env_var_mapping type -->
            <div v-else-if="question.field_type === 'env_var_mapping'" class="env-var-mapping">
              <div
                v-for="(envVar, evIndex) in question.variables || []"
                :key="evIndex"
                class="env-var-row"
              >
                <el-input
                  :model-value="envVar.original_value"
                  disabled
                  size="small"
                  style="width: 200px"
                >
                  <template #prepend>{{ $t('apiTesting.aiImport.originalValue') }}</template>
                </el-input>
                <el-icon class="arrow-icon"><ArrowRight /></el-icon>
                <el-input
                  v-model="envVar.var_name"
                  :placeholder="$t('apiTesting.aiImport.varNamePlaceholder')"
                  size="small"
                  style="width: 200px"
                >
                  <template #prepend><span>&#123;&#123;</span></template>
                  <template #append><span>&#125;&#125;</span></template>
                </el-input>
                <el-button
                  size="small"
                  type="danger"
                  :icon="Delete"
                  circle
                  @click="removeEnvVar(question, evIndex)"
                />
              </div>
              <el-button
                size="small"
                type="primary"
                plain
                @click="addEnvVar(question)"
              >
                {{ $t('apiTesting.aiImport.addVariable') }}
              </el-button>
            </div>
          </el-card>
        </div>
      </div>

      <!-- Step 4: Generate -->
      <div v-show="currentStep === 4" class="step-panel">
        <el-card>
          <div class="generate-container">
            <el-progress
              :percentage="generateProgress"
              :status="generateProgress >= 100 ? 'success' : undefined"
              :stroke-width="12"
              :duration="1"
            />
            <p class="generate-text">
              {{ generateProgress >= 100
                ? $t('apiTesting.aiImport.generateComplete')
                : $t('apiTesting.aiImport.generateProgress')
              }}
            </p>
            <p v-if="generateMessage" class="generate-message">{{ generateMessage }}</p>
          </div>
        </el-card>
      </div>

      <!-- Step 5: Results -->
      <div v-show="currentStep === 5" class="result-panel">
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
            <el-table-column :label="$t('apiTesting.aiImport.method')" width="90">
              <template #default="{ row }">
                <el-tag :type="methodTagType(row.method || row.method_display)" size="small">
                  {{ row.method || row.method_display }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('apiTesting.aiImport.name')" prop="name" min-width="200" />
            <el-table-column :label="$t('apiTesting.aiImport.path')" prop="path" min-width="250" show-overflow-tooltip />
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
      <el-button
        v-if="currentStep > 0 && currentStep < 5"
        :disabled="currentStep === 2"
        @click="prevStep"
      >
        {{ $t('apiTesting.aiImport.prev') }}
      </el-button>
      <el-button
        v-if="currentStep < 4"
        type="primary"
        :loading="stepLoading"
        :disabled="!canProceed"
        @click="nextStep"
      >
        {{ $t('apiTesting.aiImport.next') }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { UploadFilled, ArrowRight, Delete, SuccessFilled } from '@element-plus/icons-vue'
import {
  uploadImportDocument,
  configureImport,
  getImportQuestions,
  submitImportAnswers,
  saveImportRequests,
  subscribeImportProgress
} from '@/api/api-testing-import'
import { getApiProjects, getApiCollections } from '@/api/api-testing'

const { t } = useI18n()
const router = useRouter()

// Wizard state
const currentStep = ref(0)
const stepLoading = ref(false)
const taskId = ref(null)
const uploadRef = ref(null)
const fileList = ref([])
const uploadResult = ref(null)
const uploading = ref(false)

// File format tag colors
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

const methodTagMap = { GET: '', POST: 'success', PUT: 'warning', PATCH: 'warning', DELETE: 'danger' }
const methodTagType = (method) => methodTagMap[method] || ''

// Step 0: Upload handling
const handleFileChange = async (file) => {
  fileList.value = [file]
  uploading.value = true
  stepLoading.value = true
  try {
    const formData = new FormData()
    formData.append('file', file.raw || file)
    const res = await uploadImportDocument(formData)
    taskId.value = res.data.task_id || res.data.id
    uploadResult.value = {
      message: res.data.message || t('apiTesting.common.success'),
      detected_format: res.data.doc_type || 'unknown',
      endpoint_count: (res.data.parsed_endpoints && res.data.parsed_endpoints.length) || 0
    }
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.response?.data?.error || t('apiTesting.messages.error.loadFailed'))
    uploadResult.value = null
    fileList.value = []
  } finally {
    uploading.value = false
    stepLoading.value = false
  }
}

// Step 1: Config
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
    // Transform flat list to tree if needed
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

// Step 2-3: Questions
const questions = ref([])
const answers = ref({})

const loadQuestions = async () => {
  if (!taskId.value) return
  try {
    const res = await getImportQuestions(taskId.value)
    questions.value = res.data.questions || res.data.results || []
    // Initialize answers
    const initAnswers = {}
    const envVars = {}
    questions.value.forEach((q) => {
      if (q.field_type === 'string' || q.field_type === 'select') {
        initAnswers[q.id] = q.default_value || ''
      } else if (q.field_type === 'multi_param') {
        q.options = (q.options || []).map((p) => ({
          ...p,
          user_value: p.default_value || p.user_value || ''
        }))
      } else if (q.field_type === 'env_var_mapping') {
        q.variables = (q.variables || q.env_vars || []).map((v) => ({
          original_value: v.original_value || v.originalValue || '',
          var_name: v.var_name || v.varName || ''
        }))
      }
    })
    answers.value = initAnswers
    return true
  } catch (error) {
    ElMessage.error(t('apiTesting.messages.error.loadFailed'))
    return false
  }
}

// Step 3 helpers
const locationTagType = (location) => {
  const map = {
    query: 'info',
    header: 'warning',
    path: 'danger',
    body: 'success'
  }
  return map[location] || 'info'
}

const addEnvVar = (question) => {
  if (!question.variables) {
    question.variables = []
  }
  question.variables.push({ original_value: '', var_name: '' })
}

const removeEnvVar = (question, index) => {
  question.variables.splice(index, 1)
}

// Step 4: Generate
const generateProgress = ref(0)
const generateMessage = ref('')
const savedCount = ref(0)
const savedRequests = ref([])
let unsubscribeSSE = null

// Computed: can proceed to next step
const canProceed = computed(() => {
  switch (currentStep.value) {
    case 0:
      return !!taskId.value
    case 1:
      if (!configForm.project_id) return false
      if (!configForm.auto_structure && !configForm.collection_id) return false
      return true
    case 2:
      return false // auto step
    case 3:
      return questions.value.length > 0
    default:
      return true
  }
})

// Navigation
const nextStep = async () => {
  stepLoading.value = true
  try {
    switch (currentStep.value) {
      case 0:
        if (taskId.value) {
          currentStep.value = 1
        }
        break

      case 1:
        await handleConfigure()
        currentStep.value = 2
        // Auto start analysis
        await nextTick()
        await handleAutoAnalysis()
        break

      case 2:
        // Handled by auto analysis
        break

      case 3:
        await handleSubmitAnswers()
        currentStep.value = 4
        break
    }
  } finally {
    stepLoading.value = false
  }
}

const prevStep = () => {
  if (currentStep.value > 0) {
    currentStep.value--
  }
}

const handleConfigure = async () => {
  if (!taskId.value) return
  await configureImport(taskId.value, {
    project_id: configForm.project_id,
    auto_structure: configForm.auto_structure,
    target_collection_id: configForm.collection_id
  })
}

const handleAutoAnalysis = async () => {
  const success = await loadQuestions()
  if (success && questions.value.length > 0) {
    currentStep.value = 3
  } else if (success) {
    // No questions to answer, go straight to generate
    currentStep.value = 4
    await handleGenerate()
  } else {
    // Analysis failed — let the user retry from config
    ElMessage.warning(t('apiTesting.messages.error.loadFailed'))
    currentStep.value = 1
  }
}

const handleSubmitAnswers = async () => {
  if (!taskId.value) return
  // Collect answers from all question types
  const userAnswers = { ...answers.value }
  const environmentVars = []

  questions.value.forEach((q) => {
    if (q.field_type === 'multi_param') {
      // Each param's value is embedded in the row
      userAnswers[q.id] = (q.options || []).map((p) => ({
        param_name: p.param_name,
        location: p.location,
        value: p.user_value || p.value
      }))
    } else if (q.field_type === 'env_var_mapping') {
      environmentVars.push(...(q.variables || []).map((v) => ({
        original_value: v.original_value,
        var_name: v.var_name
      })))
    }
  })

  // 转换为后端期望的字典格式
  const envVarDict = {}
  environmentVars.forEach(v => { envVarDict[v.original_value] = v.var_name })

  await submitImportAnswers(taskId.value, {
    user_answers: userAnswers,
    environment_vars: envVarDict
  })

  await handleGenerate()
}

const handleGenerate = async () => {
  if (!taskId.value) return
  generateProgress.value = 0
  generateMessage.value = ''

  // Start SSE subscription
  const unsubscribe = subscribeImportProgress(
    taskId.value,
    (data) => {
      if (data.progress !== undefined) {
        generateProgress.value = Math.round(data.progress)
      }
      if (data.message) {
        generateMessage.value = data.message
      }
      if (data.status === 'completed' || data.progress >= 100) {
        handleSaveResults()
      }
    },
    (error) => {
      ElMessage.error(error || t('apiTesting.messages.error.loadFailed'))
    }
  )
  unsubscribeSSE = unsubscribe

  // Fallback: poll for completion if SSE doesn't trigger
  setTimeout(async () => {
    if (currentStep.value === 4 && generateProgress.value < 100) {
      try {
        const saveRes = await saveImportRequests(taskId.value)
        savedCount.value = saveRes.data?.requests_created?.length || 0
        savedRequests.value = saveRes.data?.requests_created || []
        generateProgress.value = 100
        currentStep.value = 5
      } catch (error) {
        ElMessage.error(error.response?.data?.detail || t('apiTesting.messages.error.saveFailed'))
      }
    }
  }, 30000) // 30s fallback
}

const handleSaveResults = async () => {
  if (!taskId.value) return
  try {
    const res = await saveImportRequests(taskId.value)
    savedCount.value = res.data?.requests_created?.length || 0
    savedRequests.value = res.data?.requests_created || []
    generateProgress.value = 100
    currentStep.value = 5
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || t('apiTesting.messages.error.saveFailed'))
  }
}

// Step 5: Results actions
const viewResults = () => {
  router.push('/api-testing/interfaces')
}

const resetWizard = () => {
  currentStep.value = 0
  taskId.value = null
  uploading.value = false
  uploadResult.value = null
  fileList.value = []
  questions.value = []
  answers.value = {}
  generateProgress.value = 0
  generateMessage.value = ''
  savedCount.value = 0
  savedRequests.value = []
  configForm.project_id = null
  configForm.auto_structure = true
  configForm.collection_id = null
  if (unsubscribeSSE) {
    unsubscribeSSE()
    unsubscribeSSE = null
  }
}

// Lifecycle
onMounted(() => {
  loadProjects()
})

onUnmounted(() => {
  if (unsubscribeSSE) {
    unsubscribeSSE()
    unsubscribeSSE = null
  }
})
</script>

<style scoped>
.ai-import-wizard {
  padding: 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 500;
}

.wizard-steps {
  margin-bottom: 24px;
}

.step-content {
  flex: 1;
  overflow-y: auto;
  margin-bottom: 20px;
}

.step-panel {
  max-width: 800px;
  margin: 0 auto;
}

/* Upload result */
.upload-result {
  margin-top: 16px;
}

.result-details {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}

.endpoint-count {
  font-size: 16px;
  font-weight: 500;
  color: #409eff;
}

/* Upload loading overlay */
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

/* Config radio tips */
.radio-tip {
  margin: 4px 0 0;
  font-size: 12px;
  color: #909399;
  font-weight: normal;
}

/* Analysis step */
.analysis-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 0;
}

.analysis-text {
  margin-top: 24px;
  font-size: 16px;
  color: #606266;
}

/* Questions */
.questions-hint {
  margin-bottom: 16px;
}

.questions-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.question-card {
  margin-bottom: 0;
}

.question-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.question-title {
  font-weight: 500;
  font-size: 15px;
}

.question-desc {
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}

/* Multi param table */
.multi-param-table :deep(.el-table) {
  font-size: 13px;
}

.endpoint-method {
  display: inline-block;
  padding: 0 6px;
  border-radius: 3px;
  font-weight: 600;
  font-size: 11px;
  margin-right: 6px;
  background-color: #ecf5ff;
  color: #409eff;
}

.endpoint-path {
  font-family: monospace;
  font-size: 12px;
  color: #606266;
}

/* Env var mapping */
.env-var-mapping {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.env-var-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.arrow-icon {
  color: #c0c4cc;
  font-size: 16px;
}

/* Generate step */
.generate-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 60px 40px;
}

.generate-text {
  margin-top: 24px;
  font-size: 16px;
  color: #606266;
}

.generate-message {
  margin-top: 8px;
  font-size: 14px;
  color: #909399;
}

/* Result step — step-panel-agnostic, full-width */
.result-panel {
  max-width: 100%;
}

.result-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 20px 0;
  gap: 12px;
}

.result-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 500;
  color: #303133;
}

.result-list-card {
  margin-top: 20px;
}

.result-actions {
  display: flex;
  justify-content: center;
  gap: 16px;
  margin-top: 24px;
}

/* Bottom Nav */
.step-actions {
  display: flex;
  justify-content: center;
  gap: 12px;
  padding: 16px 0;
  border-top: 1px solid #ebeef5;
}

/* Project selector overflow fix */
.step-panel .el-select {
  width: 100%;
}

:deep(.el-select-dropdown__item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
