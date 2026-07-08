import pytest
from apps.api_testing.ai_agent.schema import validate_llm_output


class TestValidateLLMOutput:
    def test_valid_requests(self):
        raw = [
            {
                "name": "Get Users",
                "method": "GET",
                "url": "/api/users",
                "headers": {},
                "params": {"page": "1"},
                "body": {},
                "auth": {"type": "none"},
                "assertions": [],
                "pre_request_script": "",
                "post_request_script": "",
            }
        ]
        result = validate_llm_output(raw)
        assert len(result) == 1
        assert result[0]["name"] == "Get Users"

    def test_invalid_method(self):
        raw = [{"name": "Bad", "method": "INVALID", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="不是合法的 HTTP 方法"):
            validate_llm_output(raw)

    def test_empty_url(self):
        raw = [{"name": "No URL", "method": "GET", "url": "", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="url 不能为空"):
            validate_llm_output(raw)

    def test_empty_name(self):
        raw = [{"name": "", "method": "GET", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="name 不能为空"):
            validate_llm_output(raw)

    def test_auth_missing_type(self):
        raw = [{"name": "X", "method": "GET", "url": "/test", "headers": {}, "params": {}, "body": {}, "auth": {"token": "abc"}, "assertions": []}]
        with pytest.raises(ValueError, match="auth 必须有 type 字段"):
            validate_llm_output(raw)

    def test_headers_not_dict(self):
        raw = [{"name": "X", "method": "GET", "url": "/test", "headers": "invalid", "params": {}, "body": {}, "auth": {"type": "none"}, "assertions": []}]
        with pytest.raises(ValueError, match="字段 'requests.0.headers' 校验失败"):
            validate_llm_output(raw)

    def test_minimal_fields_get_defaults(self):
        """只有必填字段时，其他字段应填入合理的默认值"""
        raw = [{"name": "Minimal", "method": "POST", "url": "/api"}]
        result = validate_llm_output(raw)
        assert result[0]["method"] == "POST"
        assert result[0]["headers"] == {}
        assert result[0]["auth"] == {"type": "none"}
        assert result[0]["assertions"] == []
