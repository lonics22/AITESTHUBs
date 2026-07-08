<template>
  <div class="ai-import-wizard">
    <div class="page-header">
      <h2>{{ $t('apiTesting.aiImport.title') }}</h2>
    </div>

    <!-- Steps indicator -->
    <el-steps :active="currentStep" align-center class="wizard-steps">
      <el-step :title="$t('apiTesting.aiImport.stepUpload')" />
      <el-step :title="$t('apiTesting.aiImport.stepConfig')" />
      <el-step :title="$t('apiTesting.aiImport.stepAgent')" />
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
      </div>

      <!-- Step 2: Agent Chat -->
      <div v-show="currentStep === 2" class="step-panel agent-chat-panel">
        <el-card class="chat-card">
          <div class="chat-messages" ref="chatRef">
            <div v-for="(msg, idx) in chatMessages" :key="idx"
                 :class="['message', msg.role === 'agent' ? 'agent-message' : 'user-message']">
              <div class="message-bubble">
                <div class="message-avatar">
                  <el-avatar :size="32" :icon="msg.role === 'agent' ? 'Monitor' : 'UserFilled'" />
                </div>
                <div class="message-content">
                  <div class="message-text">{{ msg.content }}</div>
                </div>
              </div>
            </div>
            <div v-if="agentLoading" class="message agent-message">
              <div class="message-bubble">
                <div class="message-avatar">
                  <el-avatar :size="32" icon="Monitor" />
                </div>
                <div class="message-content">
                  <el-progress :percentage="100" :stroke-width="4" indeterminate />
                  <span class="thinking-text">Agent 思考中...</span>
                </div>
              </div>
            </div>
          </div>

          <div class="chat-input-area">
            <el-input
              v-model="userInput"
              type="textarea"
              :rows="2"
              :placeholder="$t('apiTesting.aiImport.chatPlaceholder')"
              :disabled="agentLoading"
              @keyup.enter.ctrl="sendAgentMessage"
            />
            <el-button
              type="primary"
              :loading="agentLoading"
              :disabled="!userInput.trim()"
              @click="sendAgentMessage"
            >
              {{ $t('apiTesting.common.send') }}
            </el-button>
          </div>
        </el-card>
      </div>

      <!-- Step 3: Results -->
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
        v-if="currentStep > 0 && currentStep < 4"
        @click="prevStep"
      >
        {{ $t('apiTesting.aiImport.prev') }}
      </el-button>
      <el-button
        v-if="currentStep < 3"
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
import { UploadFilled, ArrowRight, Delete, SuccessFilled, Monitor, UserFilled } from '@element-plus/icons-vue'
import {
  uploadImportDocument,
  configureImport,
  saveImportRequests,
  getAgentState,
  sendAgentReply
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

// Agent Chat state
const chatMessages = ref([])
const userInput = ref('')
const agentLoading = ref(false)
const chatRef = ref(null)

// Saved results
const savedCount = ref(0)
const savedRequests = ref([])

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
      return true
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

// Agent Chat
const loadAgentState = async () => {
  if (!taskId.value) return
  try {
    agentLoading.value = true
    const res = await getAgentState(taskId.value)
    if (res.messages && res.messages.length > 0) {
      chatMessages.value = res.messages
    } else {
      chatMessages.value = [{
        role: 'agent',
        content: `已解析文档并完成参数分析。${res.classification_summary?.manual_params > 0
          ? `发现 ${res.classification_summary.manual_params} 个参数需要您确认，请回复提供这些参数的值。`
          : '所有参数均可自动生成，将直接为您生成测试用例。'}`
      }]
    }
  } catch (e) {
    ElMessage.error('获取 Agent 状态失败')
  } finally {
    agentLoading.value = false
  }
}

const sendAgentMessage = async () => {
  const message = userInput.value.trim()
  if (!message || !taskId.value) return

  chatMessages.value.push({ role: 'user', content: message })
  userInput.value = ''
  agentLoading.value = true

  try {
    const res = await sendAgentReply(taskId.value, { message })
    if (res.messages) {
      chatMessages.value = res.messages
    }
    if (res.status === 'completed') {
      currentStep.value = 3
      const saveRes = await saveImportRequests(taskId.value)
      savedCount.value = saveRes.requests_created?.length || saveRes.count || 0
      savedRequests.value = saveRes.requests_details || []
    }
  } catch (e) {
    ElMessage.error('Agent 处理失败')
    chatMessages.value.push({
      role: 'agent',
      content: '处理出错，请稍后重试'
    })
  } finally {
    agentLoading.value = false
  }
}

// 进入 Step 2 时自动加载 Agent 状态
watch(currentStep, (step) => {
  if (step === 2) {
    loadAgentState()
  }
})

// Results actions
const viewResults = () => {
  router.push('/api-testing/interfaces')
}

const resetWizard = () => {
  currentStep.value = 0
  taskId.value = null
  uploading.value = false
  uploadResult.value = null
  fileList.value = []
  chatMessages.value = []
  userInput.value = ''
  savedCount.value = 0
  savedRequests.value = []
  configForm.project_id = null
  configForm.auto_structure = true
  configForm.collection_id = null
}

// Lifecycle
onMounted(() => {
  loadProjects()
})

onUnmounted(() => {
  // cleanup if needed
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

/* Analysis step — full-width, bigger loading */
.analysis-panel {
  max-width: 100%;
}

.analysis-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 80px 0;
}

.analysis-text {
  margin-top: 28px;
  font-size: 18px;
  color: #606266;
}

/* Structure radio group — vertical stack to avoid text overflow */
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

/* Agent Chat */
.agent-chat-panel {
  max-width: 900px;
  margin: 0 auto;
}

.chat-card {
  min-height: 400px;
  display: flex;
  flex-direction: column;
}

.chat-messages {
  flex: 1;
  max-height: 500px;
  overflow-y: auto;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 8px;
  margin-bottom: 16px;
}

.message {
  margin-bottom: 16px;
}

.message-bubble {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.user-message .message-bubble {
  flex-direction: row-reverse;
}

.message-text {
  padding: 8px 16px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid #e4e7ed;
  max-width: 70%;
  white-space: pre-wrap;
  line-height: 1.5;
}

.user-message .message-text {
  background: #409eff;
  color: #fff;
  border-color: #409eff;
}

.chat-input-area {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.chat-input-area .el-textarea {
  flex: 1;
}

.thinking-text {
  margin-left: 8px;
  color: #909399;
  font-size: 13px;
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
