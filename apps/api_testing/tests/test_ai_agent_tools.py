import pytest
from apps.api_testing.ai_agent.tools import (
    parse_document_tool,
    classify_parameters_tool,
    generate_test_cases_tool,
)


class TestParseDocumentTool:
    def test_swagger_2(self):
        """解析 Swagger 2.0 文档"""
        raw = {
            "swagger": "2.0",
            "info": {"title": "Pet Store", "version": "1.0"},
            "paths": {
                "/pets": {
                    "get": {
                        "summary": "List pets",
                        "parameters": [
                            {"name": "limit", "in": "query", "type": "integer"},
                        ],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        result = parse_document_tool.invoke({"raw_content": raw})
        assert len(result) >= 1
        # doc_parser normalizes methods to uppercase
        pet_endpoint = None
        for ep in result:
            if ep.get("path") == "/pets" and ep.get("method") == "GET":
                pet_endpoint = ep
                break
        assert pet_endpoint is not None, f"Could not find GET /pets in {result}"


class TestClassifyParametersTool:
    def test_classify_simple_endpoints(self):
        endpoints = [
            {
                "path": "/api/users",
                "method": "GET",
                "summary": "Get users",
                "parameters": [
                    {"name": "page", "in": "query", "type": "integer"},
                    {"name": "search", "in": "query", "type": "string"},
                ],
            }
        ]
        result = classify_parameters_tool.invoke({"endpoints": endpoints})
        assert result["endpoint_count"] == 1
        assert result["total_params"] == 2


class TestGenerateTestCasesTool:
    def test_happy_path(self):
        """生成测试用例 — 基本路径：纯逻辑，无需真实数据库"""
        endpoints = [
            {
                "path": "/api/users",
                "method": "GET",
                "summary": "Get users",
                "parameters": [],
            },
        ]
        classification = {
            "endpoint_count": 1,
            "total_params": 0,
            "classification": {
                "GET /api/users": {"auto": [], "manual": [], "context_ref": []},
            },
        }
        user_answers = {
            "environment_vars": {},
            "answers": {},
        }
        tester_prompt = "test"

        result = generate_test_cases_tool.invoke({
            "endpoints": endpoints,
            "classification": classification,
            "user_answers": user_answers,
            "tester_prompt": tester_prompt,
        })

        assert isinstance(result, list)
        assert len(result) == 1
        req = result[0]
        assert req["name"] == "Get users"
        assert req["method"] == "GET"
        assert req["url"] == "/api/users"
        assert isinstance(req["headers"], dict)
        assert isinstance(req["params"], dict)
        assert isinstance(req["body"], dict)
        assert isinstance(req["assertions"], list)

    def test_invalid_method_raises(self):
        """生成测试用例 — 非法 HTTP 方法应触发 ValueError"""
        endpoints = [
            {
                "path": "/api/users",
                "method": "INVALID",
                "summary": "Bad method",
                "parameters": [],
            },
        ]
        classification = {
            "endpoint_count": 1,
            "total_params": 0,
            "classification": {
                "INVALID /api/users": {"auto": [], "manual": [], "context_ref": []},
            },
        }
        user_answers = {
            "environment_vars": {},
            "answers": {},
        }

        with pytest.raises(ValueError, match="不是合法的 HTTP 方法"):
            generate_test_cases_tool.invoke({
                "endpoints": endpoints,
                "classification": classification,
                "user_answers": user_answers,
                "tester_prompt": "test",
            })
