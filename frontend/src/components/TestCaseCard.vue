<template>
  <el-card shadow="hover" class="test-case-card">
    <template #header>
      <div class="card-header">
        <div class="card-header-left">
          <span class="case-index">#{{ caseIndex }}</span>
          <span class="case-name">{{ localCase.name || 'Unnamed' }}</span>
          <el-tag v-if="localCase._case_type === 'normal'" size="small" type="success" effect="light">正常</el-tag>
          <el-tag v-else-if="localCase._case_type === 'error'" size="small" type="danger" effect="light">错误</el-tag>
        </div>
        <el-tag v-if="localCase.method" :type="methodTagType(localCase.method)" size="small" effect="dark">
          {{ localCase.method }}
        </el-tag>
      </div>
      <div class="card-url">
        <span class="url-text">{{ localCase.url || localCase.path || '-' }}</span>
      </div>
    </template>

    <div class="card-body">
      <!-- Request Headers -->
      <FieldSection
        v-if="hasKeys(localCase.headers)"
        title="Headers"
        :fields="localCase.headers"
        :readonly="isErrorCase"
        @input="emitUpdate"
      />

      <!-- Request Params -->
      <FieldSection
        v-if="hasKeys(localCase.params)"
        title="Params"
        :fields="localCase.params"
        :readonly="isErrorCase"
        @input="emitUpdate"
      />

      <!-- Request Body -->
      <BodySection
        v-if="hasKeys(localCase.body)"
        :fields="localCase.body"
        :readonly="isErrorCase"
        @input="emitUpdate"
      />

      <!-- Assertions (readonly) -->
      <div v-if="localCase.assertions && localCase.assertions.length" class="field-section">
        <h4 class="section-title">断言</h4>
        <div v-for="(assertion, i) in localCase.assertions" :key="i" class="assertion-row">
          <template v-if="isModeObject(assertion)">
            <span class="ai-value">{{ assertion.value }}</span>
            <span v-if="assertion._mode === 'ai_generated'" class="ai-badge">(AI自动生成)</span>
            <span v-else-if="assertion._mode === 'user_input'" class="assertion-pending">[待填写] {{ assertion._label }}</span>
          </template>
          <span v-else class="plain-value">{{ assertion }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { reactive, watch, computed, defineComponent, h } from 'vue'
import { ElInput, ElTooltip, ElTag } from 'element-plus'

// =================== Sub-components ===================

/**
 * FieldSection — renders a flat key-value object where each value may have _mode markers.
 * Used by Headers and Params sections.
 */
const FieldSection = defineComponent({
  name: 'FieldSection',
  props: {
    title: String,
    fields: Object,
    readonly: Boolean
  },
  emits: ['input'],
  setup(props, { emit }) {
    return () => {
      const entries = Object.entries(props.fields || {})
      if (entries.length === 0) return null

      const children = [
        h('h4', { class: 'section-title' }, props.title)
      ]

      for (const [key, val] of entries) {
        children.push(renderFieldRow(key, val, props.readonly, 0, () => emit('input')))
      }

      return h('div', { class: 'field-section' }, children)
    }
  }
})

/**
 * BodySection — like FieldSection but supports one level of nested objects.
 */
const BodySection = defineComponent({
  name: 'BodySection',
  props: {
    fields: Object,
    readonly: Boolean
  },
  emits: ['input'],
  setup(props, { emit }) {
    return () => {
      const fields = props.fields || {}

      // Handle {type, data} wrapper format from LLM-generated body
      let displayFields = fields
      if (fields.type && fields.data && typeof fields.data === 'object' && !Array.isArray(fields.data)) {
        displayFields = fields.data
      }

      const entries = Object.entries(displayFields)
      if (entries.length === 0) return null

      const children = [
        h('h4', { class: 'section-title' }, 'Body')
      ]

      for (const [key, val] of entries) {
        // Nested object
        if (val && typeof val === 'object' && !Array.isArray(val) && !val._mode) {
          const nestedChildren = [h('h5', { class: 'nested-title' }, key)]
          for (const [subKey, subVal] of Object.entries(val)) {
            nestedChildren.push(renderFieldRow(subKey, subVal, props.readonly, 1, () => emit('input')))
          }
          children.push(h('div', { class: 'nested-group' }, nestedChildren))
        } else {
          children.push(renderFieldRow(key, val, props.readonly, 0, () => emit('input')))
        }
      }

      return h('div', { class: 'field-section' }, children)
    }
  }
})

/**
 * renderFieldRow — creates a VNode for a single field row based on _mode.
 */
function renderFieldRow(key, val, readonly, indent, onChange) {
  const padLeft = indent > 0 ? `${indent * 20}px` : '0'

  // Plain value (no _mode)
  if (!val || typeof val !== 'object' || Array.isArray(val) || !val._mode) {
    return h('div', { class: 'field-row', style: { paddingLeft: padLeft } }, [
      h('label', { class: 'field-label' }, [key]),
      h('div', { class: 'field-control' }, [
        h('span', { class: 'plain-value' }, [String(val ?? '')])
      ])
    ])
  }

  const mode = val._mode

  // ---- ai_generated ----
  if (mode === 'ai_generated') {
    const labelNodes = [key]
    if (val._label) {
      labelNodes.push(h('span', { class: 'label-hint' }, ` (${val._label})`))
    }
    return h('div', { class: 'field-row', style: { paddingLeft: padLeft } }, [
      h('label', { class: 'field-label' }, labelNodes),
      h('div', { class: 'field-control' }, [
        h('span', { class: 'ai-value' }, [String(val.value ?? '')]),
        h('span', { class: 'ai-badge' }, '(AI自动生成)')
      ])
    ])
  }

  // ---- user_input ----
  if (mode === 'user_input') {
    const input = h(ElInput, {
      modelValue: val.value ?? '',
      'onUpdate:modelValue': (v) => { val.value = v; onChange?.() },
      placeholder: '请输入',
      size: 'small',
      disabled: readonly,
      class: 'user-input-field'
    })
    return h('div', { class: 'field-row', style: { paddingLeft: padLeft } }, [
      h('label', { class: 'field-label' }, [
        h('span', { class: 'label-text' }, [val._label || key]),
        h('span', { class: 'required-mark' }, '*')
      ]),
      h('div', { class: 'field-control' }, [input])
    ])
  }

  // ---- ask_user ----
  if (mode === 'ask_user') {
    const placeholder = val._ai_suggestion || '请确认此参数'
    const askIcon = h(ElTooltip, { content: '此参数 AI 不确定，请确认', placement: 'top' }, {
      default: () => h('span', { class: 'ask-icon' }, '?')
    })
    const input = h(ElInput, {
      modelValue: val.value ?? '',
      'onUpdate:modelValue': (v) => { val.value = v; onChange?.() },
      placeholder,
      size: 'small',
      disabled: readonly,
      class: 'ask-user-field'
    }, { prefix: () => askIcon })
    return h('div', { class: 'field-row', style: { paddingLeft: padLeft } }, [
      h('label', { class: 'field-label' }, [
        h('span', { class: 'label-text' }, [val._label || key]),
        h('span', { class: 'required-mark' }, '*')
      ]),
      h('div', { class: 'field-control' }, [input])
    ])
  }

  // Fallback for unknown mode
  return h('div', { class: 'field-row', style: { paddingLeft: padLeft } }, [
    h('label', { class: 'field-label' }, [key]),
    h('div', { class: 'field-control' }, [
      h('span', { class: 'plain-value' }, [JSON.stringify(val)])
    ])
  ])
}

// =================== Main component logic ===================

const props = defineProps({
  testCase: { type: Object, required: true },
  caseIndex: { type: Number, default: 0 }
})

const emit = defineEmits(['update:modelValue'])

// Deep clone to avoid mutating the prop directly
const localCase = reactive({})

const initFromProp = () => {
  const fresh = JSON.parse(JSON.stringify(props.testCase || {}))
  for (const key of Object.keys(localCase)) {
    delete localCase[key]
  }
  Object.assign(localCase, fresh)
}

initFromProp()

watch(() => props.testCase, () => {
  initFromProp()
}, { deep: true })

const isErrorCase = computed(() => localCase._case_type === 'error')

const methodTagMap = { GET: 'success', POST: 'primary', PUT: 'warning', PATCH: 'warning', DELETE: 'danger' }
const methodTagType = (method) => methodTagMap[method?.toUpperCase()] || 'info'

const hasKeys = (obj) => {
  return obj && typeof obj === 'object' && !Array.isArray(obj) && Object.keys(obj).length > 0
}

const isModeObject = (val) => {
  return val && typeof val === 'object' && !Array.isArray(val) && val._mode
}

const emitUpdate = () => {
  emit('update:modelValue', JSON.parse(JSON.stringify(localCase)))
}
</script>

<style scoped>
.test-case-card {
  margin-bottom: 12px;
  border-radius: 8px;
  border: 1px solid #ebeef5;
  transition: all 0.2s ease;
}

.test-case-card:hover {
  border-color: #d9e1ec;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.test-case-card :deep(.el-card__header) {
  padding: 12px 16px;
  border-bottom: 1px solid #f5f6f8;
  background: #fafbfc;
  border-radius: 8px 8px 0 0;
}

.test-case-card :deep(.el-card__body) {
  padding: 14px 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.case-index {
  font-weight: 600;
  color: #bcc0c8;
  min-width: 28px;
  font-size: 13px;
}

.case-name {
  font-weight: 600;
  font-size: 14px;
  color: #1d2129;
}

.card-url {
  margin-top: 5px;
  font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
  font-size: 12px;
  color: #86909c;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-body {
  padding: 0;
}

/* Section */
.field-section {
  margin-bottom: 10px;
}

.field-section:last-child {
  margin-bottom: 0;
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #86909c;
  margin: 0 0 6px;
  padding-bottom: 3px;
  border-bottom: 1px solid #f0f2f5;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Field row */
.field-row {
  display: flex;
  align-items: flex-start;
  padding: 3px 0;
  gap: 8px;
  min-height: 30px;
}

.field-label {
  flex: 0 0 130px;
  font-size: 13px;
  color: #4e5969;
  line-height: 28px;
  text-align: right;
  padding-right: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.label-text {
  color: #1d2129;
}

.label-hint {
  color: #86909c;
  font-size: 12px;
}

.field-control {
  flex: 1;
  min-width: 0;
}

.required-mark {
  color: #f56c6c;
  margin-left: 2px;
}

/* user_input field */
:deep(.user-input-field) {
  max-width: 380px;
}

:deep(.user-input-field .el-input__wrapper) {
  background-color: #fffbe6;
  border: 1px solid #f0ddaa;
  box-shadow: none;
  border-radius: 4px;
}

:deep(.user-input-field .el-input__wrapper:hover) {
  border-color: #e6c87c;
  box-shadow: none;
}

:deep(.user-input-field .el-input__wrapper.is-focus) {
  border-color: #d4b06a;
  box-shadow: 0 0 0 2px rgba(230, 200, 124, 0.2);
}

/* ask_user field */
:deep(.ask-user-field) {
  max-width: 380px;
}

:deep(.ask-user-field .el-input__wrapper) {
  background-color: #f0f9ff;
  border: 1px solid #b3d8f0;
  box-shadow: none;
  border-radius: 4px;
}

:deep(.ask-user-field .el-input__wrapper:hover) {
  border-color: #8cc5e8;
}

:deep(.ask-user-field .el-input__wrapper.is-focus) {
  border-color: #6eb3dc;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.15);
}

.ask-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: help;
  line-height: 1;
}

/* AI generated value */
.ai-value {
  color: #86909c;
  font-size: 13px;
  line-height: 28px;
  font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
}

.ai-badge {
  display: inline-block;
  font-size: 11px;
  color: #a9aeb8;
  background: #f5f6f7;
  padding: 0 8px;
  border-radius: 3px;
  margin-left: 6px;
  line-height: 20px;
  vertical-align: middle;
}

.plain-value {
  font-size: 13px;
  color: #4e5969;
  line-height: 28px;
  font-family: 'JetBrains Mono', 'Courier New', Courier, monospace;
}

/* Nested group */
.nested-group {
  padding: 6px 0 2px 16px;
  margin-left: 0;
  border-left: 2px solid #e8eaed;
}

.nested-title {
  font-size: 12px;
  font-weight: 600;
  color: #86909c;
  margin: 0 0 2px;
  padding: 2px 0;
}

/* Assertions */
.assertion-row {
  padding: 3px 0;
  font-size: 13px;
  line-height: 24px;
}

.assertion-pending {
  color: #e6a23c;
  font-style: italic;
  font-size: 12px;
}
</style>
