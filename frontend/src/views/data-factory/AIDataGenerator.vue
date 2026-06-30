<template>
  <div class="ai-data-generator">
    <!-- Step 1: Field Configuration -->
    <el-card class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">1</el-tag>
          <span class="step-title">{{ $t('dataFactory.ai.configStep') }}</span>
          <el-button size="small" type="success" @click="addField">
            + {{ $t('dataFactory.ai.addField') }}
          </el-button>
        </div>
      </template>

      <div v-for="(field, index) in fieldDefs" :key="index" class="field-row">
        <el-row :gutter="10" align="middle">
          <el-col :span="6">
            <el-input v-model="field.name" :placeholder="$t('dataFactory.ai.fieldName')" size="small" />
          </el-col>
          <el-col :span="5">
            <el-select v-model="field.type" size="small" style="width:100%">
              <el-option label="string" value="string" />
              <el-option label="email" value="email" />
              <el-option label="phone" value="phone" />
              <el-option label="name" value="name" />
              <el-option label="address" value="address" />
              <el-option label="id_card" value="id_card" />
              <el-option label="integer" value="integer" />
              <el-option label="date" value="date" />
              <el-option label="company" value="company" />
              <el-option label="bank_card" value="bank_card" />
              <el-option label="url" value="url" />
            </el-select>
          </el-col>
          <el-col :span="10">
            <el-input v-model="field.description" :placeholder="$t('dataFactory.ai.fieldDescription')" size="small" />
          </el-col>
          <el-col :span="3">
            <el-button size="small" type="danger" :icon="Delete" circle @click="removeField(index)" />
          </el-col>
        </el-row>
      </div>
    </el-card>

    <!-- Step 2: Config -->
    <el-card class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">2</el-tag>
          <span class="step-title">{{ $t('dataFactory.ai.generateCount') }}</span>
        </div>
      </template>
      <el-row :gutter="20">
        <el-col :span="6">
          <el-form-item :label="$t('dataFactory.ai.generateCount')">
            <el-input-number v-model="count" :min="1" :max="50" />
          </el-form-item>
        </el-col>
        <el-col :span="6">
          <el-form-item :label="$t('dataFactory.ai.outputFormat')">
            <el-select v-model="outputFormat">
              <el-option label="JSON" value="json" />
              <el-option label="SQL" value="sql" />
              <el-option label="CSV" value="csv" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
    </el-card>

    <!-- Step 3: Classification Result -->
    <el-card v-if="classification" class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="primary">3</el-tag>
          <span>{{ $t('dataFactory.ai.classificationResult') }}</span>
        </div>
      </template>

      <el-tag v-for="item in classification.classification" :key="item.field"
        :type="item.type === 'auto' ? 'success' : (item.type === 'manual' ? 'warning' : 'info')"
        class="classification-tag">
        {{ item.field }}: {{ item.type === 'auto' ? $t('dataFactory.ai.autoField') :
          (item.type === 'manual' ? $t('dataFactory.ai.manualField') : $t('dataFactory.ai.contextField')) }}
      </el-tag>
    </el-card>

    <!-- Step 4: Manual Fields Input -->
    <el-card v-if="manualFields.length > 0 && !generated" class="step-card">
      <template #header>
        <div class="step-header">
          <el-tag type="warning">!</el-tag>
          <span>{{ $t('dataFactory.ai.fillManualFields') }}</span>
        </div>
      </template>

      <el-form label-width="160px">
        <el-form-item v-for="field in manualFields" :key="field.field" :label="field.field">
          <el-input v-model="userInputs[field.field]"
            :placeholder="field.prompt || $t('dataFactory.ai.fillManualFields')" />
          <span class="manual-hint">{{ field.prompt }}</span>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- Project Context -->
    <el-card v-if="projectContext" class="step-card context-card">
      <template #header>
        <span>{{ $t('dataFactory.ai.projectContext') }}</span>
      </template>
      <div v-if="projectContext.available_ids?.length" class="context-section">
        <span class="context-label">{{ $t('dataFactory.ai.retrievedContext') }}:</span>
        <el-tag v-for="id in projectContext.available_ids" :key="id" size="small">{{ id }}</el-tag>
      </div>
      <el-empty v-else :description="$t('dataFactory.ai.noContext')" :image-size="40" />
    </el-card>

    <!-- Generate Button -->
    <div class="generate-actions">
      <el-button type="primary" size="large" :loading="loading"
        :disabled="fieldDefs.length === 0" @click="generate">
        {{ loading ? $t('dataFactory.ai.generating') : $t('dataFactory.ai.aiGenerate') }}
      </el-button>
    </div>

    <!-- Result -->
    <el-card v-if="generatedData.length > 0" class="result-card">
      <template #header>
        <div class="step-header">
          <span>{{ $t('dataFactory.ai.generatedResult') }} ({{ generatedData.length }})</span>
          <div>
            <el-button size="small" @click="copyAll">{{ $t('dataFactory.ai.copyAll') }}</el-button>
            <el-button size="small" type="primary" @click="downloadJSON">{{ $t('dataFactory.ai.downloadJSON') }}</el-button>
          </div>
        </div>
      </template>
      <el-input type="textarea" :rows="10" :model-value="formatResult" readonly />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { aiClassifyFields, aiGenerateData } from '@/api/data-factory'

const fieldDefs = ref([{ name: '', type: 'string', description: '' }])
const count = ref(5)
const outputFormat = ref('json')
const loading = ref(false)
const classification = ref(null)
const manualFields = ref([])
const userInputs = ref({})
const projectContext = ref(null)
const generatedData = ref([])
const generated = ref(false)

const formatResult = computed(() => {
  if (outputFormat.value === 'json') {
    return JSON.stringify(generatedData.value, null, 2)
  }
  return generatedData.value.map(r => r.raw || JSON.stringify(r)).join('\n')
})

function addField() {
  fieldDefs.value.push({ name: '', type: 'string', description: '' })
}

function removeField(index) {
  fieldDefs.value.splice(index, 1)
}

async function generate() {
  const validDefs = fieldDefs.value.filter(f => f.name.trim())
  if (validDefs.length === 0) {
    ElMessage.warning('请至少配置一个字段')
    return
  }

  loading.value = true
  generatedData.value = []
  generated.value = false

  try {
    const projectId = 1  // TODO: get from actual project context

    // Step 1: Classify fields
    const classifyRes = await aiClassifyFields({
      project_id: projectId,
      field_defs: validDefs,
      api_info: {}
    })

    classification.value = classifyRes
    manualFields.value = classifyRes.manual_fields || []

    // If has manual fields, wait for user input
    if (manualFields.value.length > 0 && Object.keys(userInputs.value).length === 0) {
      loading.value = false
      ElMessage.info('请填写需要手动输入的字段')
      return
    }

    // Step 2: Generate (SSE)
    const cancel = aiGenerateData({
      project_id: projectId,
      field_defs: validDefs,
      api_info: {},
      user_inputs: userInputs.value,
      classification: classifyRes,
      count: count.value,
      output_format: outputFormat.value,
      language: '中文',
    }, (event) => {
      if (event.status === 'context_retrieved') {
        projectContext.value = event.context
      } else if (event.status === 'record') {
        generatedData.value.push(event.record)
      } else if (event.status === 'completed') {
        generated.value = true
        loading.value = false
        ElMessage.success(`成功生成 ${event.total} 条数据`)
      } else if (event.status === 'error') {
        loading.value = false
        ElMessage.error(event.message || '生成失败')
      }
    }, (error) => {
      loading.value = false
      ElMessage.error(error || '网络请求失败')
    })
  } catch (error) {
    loading.value = false
    ElMessage.error(error.response?.data?.error || 'AI 生成失败')
  }
}

function copyAll() {
  navigator.clipboard.writeText(formatResult.value)
  ElMessage.success('已复制到剪贴板')
}

function downloadJSON() {
  const blob = new Blob([formatResult.value], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `test-data-${Date.now()}.${outputFormat.value}`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.ai-data-generator { padding: 16px; }
.step-card { margin-bottom: 16px; }
.step-header { display: flex; align-items: center; gap: 12px; }
.field-row { margin-bottom: 12px; }
.classification-tag { margin: 4px; }
.generate-actions { text-align: center; margin: 24px 0; }
.manual-hint { color: #909399; font-size: 12px; margin-left: 8px; }
.context-section { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.context-label { font-weight: 500; }
.result-card { margin-top: 16px; }
</style>
