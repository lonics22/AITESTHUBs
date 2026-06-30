# 任务目标
分析 API 接口测试的入参字段，判断每个字段的数据是否能由 AI 生成。

# 接口信息
{{api_info}}

# 分类规则
将每个字段分为以下三类：
1. auto — AI 可以独立生成的字段，如：姓名、邮箱、手机号、地址、随机整数、时间戳
2. manual — 必须用户提供的字段，如：已存在的用户名、系统已有订单ID、token、session
3. context_ref — 可从项目已有测试用例中复用的字段，如 auth_token、已注册的用户

# 判断依据
- 字段名含 id、token、session、existing 等关键词 → manual
- 类型为 email、phone、name、address、date 等个人/随机数据 → auto
- 描述中提到"已注册"、"已创建"、"已存在" → manual
- 描述中提到"登录后获取"、"上一步返回" → context_ref

# 输出格式
```json
{
  "classification": [
    {"field": "字段名", "type": "auto|manual|context_ref", "reason": "判断理由"}
  ],
  "manual_fields": [
    {"field": "字段名", "prompt": "给用户的填写提示"}
  ],
  "context_fields": [
    {"field": "字段名", "prompt": "需要从项目中检索什么样的数据"}
  ]
}
```
