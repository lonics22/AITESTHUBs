"""Agent 提示词系统

注意：AGENT_SYSTEM_PROMPT 将在 P1 中重新引入，届时通过实际 LLM 路由使用。
"""

ENDPOINT_GENERATION_PROMPT = """你是一个 API 测试用例生成专家。请为以下 API 端点生成测试用例：

端点: {method} {path}
名称: {summary}
描述: {description}
参数: {parameters}

## 生成要求
每条生成记录必须包含以下字段：
- name: 请求名称（必填，最长 200 字符）
- method: GET/POST/PUT/DELETE/PATCH 之一
- url: 请求路径（必填，路径参数使用 {{{{param}}}} 语法）
- headers: 请求头键值对
- params: URL 查询参数键值对
- body: 请求体对象
- auth: 认证信息，必须有 type 字段
- assertions: 断言列表
- pre_request_script: 请求前脚本
- post_request_script: 请求后脚本

## 输出格式约束
{output_schema}
"""

OUTPUT_SCHEMA_CONSTRAINT = """必须严格按以下 JSON 结构输出，字段类型与示例一致：

{{
  "name": "获取用户列表",
  "method": "GET",
  "url": "/api/users",
  "headers": {{"Authorization": "Bearer {{{{API_TOKEN}}}}"}},
  "params": {{"page": "1", "page_size": "10"}},
  "body": {{}},
  "auth": {{"type": "none"}},
  "assertions": [
    {{"type": "status_code", "expected": 200}}
  ],
  "pre_request_script": "",
  "post_request_script": ""
}}

约束规则：
- name 必填，最长 200 字符
- method 必须是 GET/POST/PUT/DELETE/PATCH 之一
- url 必填，路径参数使用 {{{{param}}}} 语法
- headers/params 必须是键值对对象（不能是数组）
- auth 必须有 type 字段
"""
