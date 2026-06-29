# -*- coding: utf-8 -*-
"""Tests for apps.api_testing.ai_import_service."""

import pytest
from typing import Any, Dict, List

from apps.api_testing.ai_import_service import (
    AIQuestion,
    _apply_heuristic,
    _extract_body_params,
    _hybrid_classify_params,
    _generate_auto_value,
    _lookup_user_value,
    _collect_manual_params,
    _extract_domains,
    _replace_env_vars,
    _build_auth,
    _assign_param,
    analyze_endpoints,
    generate_questions,
    generate_requests,
)
from apps.api_testing.doc_parser import ParsedEndpoint, ParsedParameter


# ============================================================================
# Fixtures – sample parsed endpoints
# ============================================================================

@pytest.fixture
def sample_endpoint_list() -> List[ParsedEndpoint]:
    return [
        {
            "path": "/users",
            "method": "GET",
            "summary": "List users",
            "description": "Returns a paginated list of users.",
            "tags": ["users"],
            "operation_id": "listUsers",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer",
                 "description": "Page number", "required": False},
                {"name": "limit", "location": "query", "type": "integer",
                 "description": "Items per page", "required": False},
                {"name": "search", "location": "query", "type": "string",
                 "description": "Search keyword", "required": False},
                {"name": "Authorization", "location": "header", "type": "string",
                 "description": "Bearer token", "required": True},
            ],
            "request_body": None,
            "responses": {"200": {"description": "OK"}},
            "security": [],
            "deprecated": False,
        },
        {
            "path": "/users",
            "method": "POST",
            "summary": "Create user",
            "description": "Creates a new user.",
            "tags": ["users"],
            "operation_id": "createUser",
            "parameters": [
                {"name": "Authorization", "location": "header", "type": "string",
                 "description": "Bearer token", "required": True},
            ],
            "request_body": {
                "description": "User object",
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["name", "email"],
                            "properties": {
                                "name": {"type": "string",
                                         "description": "User's full name"},
                                "email": {"type": "string",
                                          "description": "User's email address"},
                                "age": {"type": "integer",
                                        "description": "User's age"},
                            },
                        }
                    }
                },
            },
            "responses": {"201": {"description": "Created"}},
            "security": [],
            "deprecated": False,
        },
        {
            "path": "/users/{userId}",
            "method": "GET",
            "summary": "Get user by ID",
            "description": "Returns a single user.",
            "tags": ["users"],
            "operation_id": "getUserById",
            "parameters": [
                {"name": "userId", "location": "path", "type": "integer",
                 "description": "User ID", "required": True},
            ],
            "request_body": None,
            "responses": {"200": {"description": "OK"}},
            "security": [],
            "deprecated": False,
        },
    ]


# ============================================================================
# Test: Heuristic rules
# ============================================================================

class TestHeuristicRules:
    """Test _apply_heuristic for various param names."""

    # --- Auto patterns ---
    @pytest.mark.parametrize("name", [
        "page", "page_num", "page_index", "offset", "page_size",
        "limit", "per_page", "perpage", "timestamp", "format",
        "locale", "callback", "_t", "_timestamp", "_dc",
    ])
    def test_auto_params(self, name: str) -> None:
        assert _apply_heuristic(name) == "auto", f"{name!r} should be auto"

    # --- Context ref patterns ---
    @pytest.mark.parametrize("name", [
        "id", "user_id", "product_id", "order_id", "token",
        "access_token", "api_key", "apikey", "name", "username",
    ])
    def test_context_ref_params(self, name: str) -> None:
        assert _apply_heuristic(name) == "context_ref", \
            f"{name!r} should be context_ref"

    # --- Uncertain (None) ---
    @pytest.mark.parametrize("name", [
        "search", "q", "filter", "sort", "order", "status",
        "type", "category", "title", "content", "price",
        "email", "phone", "address",
    ])
    def test_uncertain_params(self, name: str) -> None:
        assert _apply_heuristic(name) is None, \
            f"{name!r} should be uncertain"

    # --- Edge cases ---
    def test_empty_name(self) -> None:
        assert _apply_heuristic("") is None

    def test_case_insensitive(self) -> None:
        assert _apply_heuristic("PAGE") == "auto"
        assert _apply_heuristic("User_Id") == "context_ref"
        assert _apply_heuristic("TOKEN") == "context_ref"


# ============================================================================
# Test: _extract_body_params
# ============================================================================

class TestExtractBodyParams:
    def test_none_body(self) -> None:
        assert _extract_body_params(None) == []

    def test_empty_dict(self) -> None:
        assert _extract_body_params({}) == []

    def test_object_properties(self) -> None:
        rb = {
            "description": "",
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string",
                                     "description": "Full name"},
                            "age": {"type": "integer",
                                    "description": "Age"},
                        },
                    }
                }
            },
        }
        params = _extract_body_params(rb)
        assert len(params) == 2

        name_param = next(p for p in params if p["name"] == "name")
        assert name_param["location"] == "body"
        assert name_param["type"] == "string"
        assert name_param["required"] is True

        age_param = next(p for p in params if p["name"] == "age")
        assert age_param["type"] == "integer"
        assert age_param["required"] is False

    def test_example_based_schema(self) -> None:
        rb = {
            "content": {
                "application/json": {
                    "schema": {
                        "example": {"username": "john", "role": "admin"},
                    }
                }
            }
        }
        params = _extract_body_params(rb)
        assert len(params) == 2
        names = {p["name"] for p in params}
        assert names == {"username", "role"}

    def test_no_properties_no_example(self) -> None:
        rb = {
            "content": {
                "application/json": {
                    "schema": {"type": "string"},
                }
            }
        }
        assert _extract_body_params(rb) == []

    def test_array_schema(self) -> None:
        rb = {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                }
            }
        }
        assert _extract_body_params(rb) == []


# ============================================================================
# Test: _hybrid_classify_params
# ============================================================================

class TestHybridClassifyParams:
    def test_all_heuristic(self) -> None:
        """All params match heuristic rules — no LLM needed."""
        endpoint: ParsedEndpoint = {
            "path": "/test",
            "method": "GET",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
                {"name": "limit", "location": "query", "type": "integer"},
                {"name": "id", "location": "path", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }
        result = _hybrid_classify_params(endpoint, endpoint["parameters"])
        assert len(result["auto"]) == 2
        assert len(result["context_ref"]) == 1
        assert len(result["manual"]) == 0

    def test_mixed_heuristic_and_uncertain(self) -> None:
        """Mix of auto, context_ref, and uncertain params."""
        endpoint: ParsedEndpoint = {
            "path": "/test",
            "method": "POST",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
                {"name": "name", "location": "query", "type": "string"},
                {"name": "search", "location": "query", "type": "string"},
                {"name": "filter", "location": "query", "type": "string"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }
        # 2 uncertain (< 3), should default to manual
        result = _hybrid_classify_params(endpoint, endpoint["parameters"])
        assert len(result["auto"]) == 1  # page
        assert len(result["context_ref"]) == 1  # name
        assert len(result["manual"]) == 2  # search, filter

    def test_empty_params(self) -> None:
        endpoint: ParsedEndpoint = {
            "path": "/empty",
            "method": "GET",
            "parameters": [],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }
        result = _hybrid_classify_params(endpoint, [])
        assert len(result["auto"]) == 0
        assert len(result["manual"]) == 0
        assert len(result["context_ref"]) == 0


# ============================================================================
# Test: analyze_endpoints
# ============================================================================

class TestAnalyzeEndpoints:
    def test_basic_analysis(self, sample_endpoint_list) -> None:
        result = analyze_endpoints(sample_endpoint_list)

        assert result["endpoint_count"] == 3
        assert result["total_params"] > 0
        assert result["auto_params"] > 0
        assert result["manual_params"] > 0
        assert result["context_ref_params"] > 0

        classification = result["classification"]
        # GET /users: page, limit → auto; search, Authorization → manual
        get_users_key = "GET /users"
        assert get_users_key in classification
        assert len(classification[get_users_key]["auto"]) >= 2  # page, limit
        assert len(classification[get_users_key]["manual"]) >= 1  # search
        # Authorization header → context_ref or manual

        # POST /users: Authorization header, body params
        post_users_key = "POST /users"
        assert post_users_key in classification
        # body params: name → context_ref, email → ?, age → ?
        post_manual = classification[post_users_key]["manual"]
        post_auto = classification[post_users_key]["auto"]
        post_context = classification[post_users_key]["context_ref"]
        # name should be context_ref
        assert any(p["name"] == "name" for p in post_context)

        # GET /users/{userId}: user_id → context_ref (matches ^.*_id$)
        get_user_key = "GET /users/{userId}"
        assert get_user_key in classification

    def test_empty_endpoints(self) -> None:
        result = analyze_endpoints([])
        assert result["endpoint_count"] == 0
        assert result["total_params"] == 0
        assert result["auto_params"] == 0
        assert result["manual_params"] == 0
        assert result["context_ref_params"] == 0
        assert result["classification"] == {}

    def test_endpoint_no_params(self) -> None:
        endpoints: List[ParsedEndpoint] = [{
            "path": "/health",
            "method": "GET",
            "parameters": [],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        result = analyze_endpoints(endpoints)
        assert result["endpoint_count"] == 1
        assert result["total_params"] == 0

    def test_unknown_param_names_default_to_manual(self) -> None:
        """Params not matching any heuristic default to manual."""
        endpoints: List[ParsedEndpoint] = [{
            "path": "/data",
            "method": "GET",
            "parameters": [
                {"name": "foo", "location": "query", "type": "string"},
                {"name": "bar", "location": "query", "type": "string"},
                # 2 uncertain (< 3) → manual
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        result = analyze_endpoints(endpoints)
        ep_class = result["classification"]["GET /data"]
        assert len(ep_class["manual"]) == 2


# ============================================================================
# Test: _collect_manual_params & _extract_domains
# ============================================================================

class TestCollectManualParams:
    def test_collect_manual(self, sample_endpoint_list) -> None:
        classification = analyze_endpoints(sample_endpoint_list)
        manual = _collect_manual_params(classification, sample_endpoint_list)
        assert len(manual) > 0
        for p in manual:
            assert "name" in p
            assert "_method" in p
            assert "_path" in p


class TestExtractDomains:
    def test_no_domains(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/users",
            "method": "GET",
            "parameters": [],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        assert _extract_domains(eps) == []

    def test_with_domains(self) -> None:
        eps: List[ParsedEndpoint] = [
            {"path": "https://api.example.com/users", "method": "GET",
             "parameters": [], "request_body": None, "responses": {},
             "security": [], "deprecated": False},
            {"path": "https://api.example.com/posts", "method": "GET",
             "parameters": [], "request_body": None, "responses": {},
             "security": [], "deprecated": False},
        ]
        assert _extract_domains(eps) == ["api.example.com"]

    def test_multiple_domains(self) -> None:
        eps: List[ParsedEndpoint] = [
            {"path": "https://us.example.com/api", "method": "GET",
             "parameters": [], "request_body": None, "responses": {},
             "security": [], "deprecated": False},
            {"path": "https://eu.example.com/api", "method": "GET",
             "parameters": [], "request_body": None, "responses": {},
             "security": [], "deprecated": False},
        ]
        assert _extract_domains(eps) == ["eu.example.com", "us.example.com"]


# ============================================================================
# Test: AIQuestion TypedDict
# ============================================================================

class TestAIQuestionTypedDict:
    def test_minimal_structure(self) -> None:
        q: AIQuestion = {
            "id": "q_1",
            "category": "url_domain",
            "title": "Base URL",
            "description": "Enter base URL",
            "field_type": "string",
            "options": [],
            "related_endpoints": [],
            "related_params": [],
        }
        assert q["id"] == "q_1"
        assert q["category"] == "url_domain"
        assert q["field_type"] == "string"

    def test_full_structure(self) -> None:
        q: AIQuestion = {
            "id": "q_2",
            "category": "auth",
            "title": "Auth Method",
            "description": "Pick one",
            "field_type": "select",
            "options": [
                {"label": "Bearer", "value": "bearer"},
                {"label": "None", "value": "none"},
            ],
            "related_endpoints": ["GET /users"],
            "related_params": ["Authorization"],
        }
        assert len(q["options"]) == 2
        assert q["related_endpoints"] == ["GET /users"]


# ============================================================================
# Test: generate_questions
# ============================================================================

class TestGenerateQuestions:
    def test_empty_endpoints(self) -> None:
        assert generate_questions({}, []) == []

    def test_questions_created(self, sample_endpoint_list) -> None:
        classification = analyze_endpoints(sample_endpoint_list)
        questions = generate_questions(classification, sample_endpoint_list)

        # Should have url_domain, auth, param_value, env_var
        assert len(questions) >= 3
        categories = {q["category"] for q in questions}
        assert "url_domain" in categories
        assert "env_var" in categories

        # Check structure of each question
        for q in questions:
            assert "id" in q
            assert "field_type" in q
            assert isinstance(q["options"], list)
            assert isinstance(q["related_endpoints"], list)
            assert isinstance(q["related_params"], list)

    def test_auth_question_present(self, sample_endpoint_list) -> None:
        classification = analyze_endpoints(sample_endpoint_list)
        questions = generate_questions(classification, sample_endpoint_list)
        auth_qs = [q for q in questions if q["category"] == "auth"]
        assert len(auth_qs) == 1
        assert auth_qs[0]["field_type"] == "select"
        assert len(auth_qs[0]["options"]) > 0

    def test_param_value_question(self, sample_endpoint_list) -> None:
        classification = analyze_endpoints(sample_endpoint_list)
        questions = generate_questions(classification, sample_endpoint_list)
        param_qs = [q for q in questions if q["category"] == "param_value"]
        if param_qs:
            assert param_qs[0]["field_type"] == "multi_param"
            assert len(param_qs[0]["options"]) > 0
            for opt in param_qs[0]["options"]:
                assert "param_name" in opt
                assert "location" in opt

    def test_no_auth_params_no_auth_question(self) -> None:
        """Endpoints without auth headers should not produce an auth question."""
        eps: List[ParsedEndpoint] = [{
            "path": "/public",
            "method": "GET",
            "summary": "Public endpoint",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        questions = generate_questions(classification, eps)
        auth_qs = [q for q in questions if q["category"] == "auth"]
        assert len(auth_qs) == 0


# ============================================================================
# Test: Value generation helpers
# ============================================================================

class TestGenerateAutoValue:
    def test_page_param(self) -> None:
        assert _generate_auto_value({"name": "page", "type": "integer"}) == "1"

    def test_limit_param(self) -> None:
        assert _generate_auto_value({"name": "limit", "type": "integer"}) == "10"

    def test_format_param(self) -> None:
        assert _generate_auto_value({"name": "format", "type": "string"}) == "json"

    def test_locale_param(self) -> None:
        assert _generate_auto_value({"name": "locale", "type": "string"}) == "zh-CN"

    def test_timestamp_param(self) -> None:
        assert _generate_auto_value({"name": "timestamp", "type": "integer"}) == "1700000000"

    def test_string_default(self) -> None:
        assert _generate_auto_value({"name": "callback", "type": "string"}) == "jQueryCallback"

    def test_unknown_param_type_default(self) -> None:
        assert _generate_auto_value({"name": "some_param", "type": "string"}) == "test"

    def test_integer_type(self) -> None:
        assert _generate_auto_value({"name": "unknown_int", "type": "integer"}) == "1"


class TestReplaceEnvVars:
    def test_matches_env_var(self) -> None:
        env_vars = {"api.example.com": "BASE_URL"}
        assert _replace_env_vars("api.example.com", env_vars) == "{{BASE_URL}}"

    def test_no_match(self) -> None:
        env_vars = {"api.example.com": "BASE_URL"}
        assert _replace_env_vars("other.com", env_vars) == "other.com"

    def test_empty_env_vars(self) -> None:
        assert _replace_env_vars("hello", {}) == "hello"


class TestLookupUserValue:
    def test_flat_lookup(self) -> None:
        answers = {"search": "test_value"}
        assert _lookup_user_value("search", "GET /users", answers, {}) == "test_value"

    def test_param_value_section(self) -> None:
        answers = {"param_value": {"name": "john"}}
        assert _lookup_user_value("name", "POST /users", answers, {}) == "john"

    def test_keyed_lookup(self) -> None:
        answers = {"GET /users|search": "hello"}
        assert _lookup_user_value("search", "GET /users", answers, {}) == "hello"

    def test_env_var_replacement(self) -> None:
        answers = {"search": "api.example.com"}
        env_vars = {"api.example.com": "BASE_URL"}
        assert _lookup_user_value("search", "GET /users", answers, env_vars) == "{{BASE_URL}}"

    def test_not_found(self) -> None:
        assert _lookup_user_value("nonexistent", "GET /x", {}, {}) == ""


class TestBuildAuth:
    def test_no_auth_header(self) -> None:
        assert _build_auth({}, {}) == {"type": "none"}

    def test_matches_bearer_answer(self) -> None:
        result = _build_auth({}, {"q_2": "bearer"})
        assert result == {"type": "bearer"}

    def test_detects_bearer_header(self) -> None:
        result = _build_auth({"Authorization": "Bearer mytoken"}, {})
        assert result == {"type": "bearer", "token": "mytoken"}

    def test_detects_basic_header(self) -> None:
        result = _build_auth({"Authorization": "Basic base64creds"}, {})
        assert result == {"type": "basic", "credentials": "base64creds"}

    def test_detects_apikey_header(self) -> None:
        result = _build_auth({"X-Api-Key": "abc123"}, {})
        assert result == {"type": "apikey", "key": "abc123"}


class TestAssignParam:
    def test_header_location(self) -> None:
        headers, params, body = {}, {}, {}
        _assign_param({"name": "Auth", "location": "header"}, headers, params, body, "token123")
        assert headers == {"Auth": "token123"}
        assert params == {}
        assert body == {}

    def test_query_location(self) -> None:
        headers, params, body = {}, {}, {}
        _assign_param({"name": "page", "location": "query"}, headers, params, body, "1")
        assert params == {"page": "1"}

    def test_body_location(self) -> None:
        headers, params, body = {}, {}, {}
        _assign_param({"name": "email", "location": "body"}, headers, params, body, "a@b.com")
        assert body == {"email": "a@b.com"}

    def test_path_location_no_op(self) -> None:
        headers, params, body = {}, {}, {}
        _assign_param({"name": "id", "location": "path"}, headers, params, body, "5")
        assert headers == {}
        assert params == {}
        assert body == {}


# ============================================================================
# Test: generate_requests (Phase 3)
# ============================================================================

class TestGenerateRequests:
    def test_basic_request_generation(self, sample_endpoint_list) -> None:
        classification = analyze_endpoints(sample_endpoint_list)
        user_answers = {
            "param_value": {
                "search": "john",
                "name": "John Doe",
                "email": "john@example.com",
            },
        }
        env_vars = {}
        requests = generate_requests(
            sample_endpoint_list, classification, user_answers, env_vars,
        )

        assert len(requests) == 3

        # GET /users
        get_req = requests[0]
        assert get_req["name"] == "List users"
        assert get_req["method"] == "GET"
        assert "/users" in get_req["url"]
        assert "params" in get_req
        assert "headers" in get_req

        # POST /users
        post_req = requests[1]
        assert post_req["name"] == "Create user"
        assert post_req["method"] == "POST"
        assert "body" in post_req

        # GET /users/{userId}
        get_user_req = requests[2]
        assert get_user_req["name"] == "Get user by ID"
        assert get_user_req["method"] == "GET"
        assert "{{userId}}" in get_user_req["url"]

    def test_empty_endpoints(self) -> None:
        assert generate_requests([], {}, {}, {}) == []

    def test_no_classification_fallback(self) -> None:
        """Endpoints not in classification map should not crash."""
        eps: List[ParsedEndpoint] = [{
            "path": "/unknown",
            "method": "GET",
            "parameters": [],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        requests = generate_requests(eps, {}, {}, {})
        assert len(requests) == 1
        assert requests[0]["url"] == "/unknown"

    def test_path_param_template(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/items/{itemId}",
            "method": "GET",
            "parameters": [
                {"name": "itemId", "location": "path", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        requests = generate_requests(eps, classification, {}, {})
        # itemId → context_ref (matches *_id pattern)
        assert "{{itemId}}" in requests[0]["url"]

    def test_auto_values_in_request(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/items",
            "method": "GET",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
                {"name": "limit", "location": "query", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        requests = generate_requests(eps, classification, {}, {})
        assert requests[0]["params"].get("page") == "1"
        assert requests[0]["params"].get("limit") == "10"

    def test_user_answers_applied(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/search",
            "method": "GET",
            "parameters": [
                {"name": "q", "location": "query", "type": "string"},
                {"name": "filter", "location": "query", "type": "string"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        # Both 'q' and 'filter' are uncertain → manual (< 3 params)
        user_answers = {
            "q": "hello",
            "GET /search|filter": "active",
        }
        requests = generate_requests(eps, classification, user_answers, {})
        assert requests[0]["params"].get("q") == "hello"
        assert requests[0]["params"].get("filter") == "active"

    def test_name_no_summary(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/foo",
            "method": "PATCH",
            "parameters": [],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        requests = generate_requests(eps, {}, {}, {})
        assert "PATCH /foo" in requests[0]["name"]

    def test_env_var_substitution(self) -> None:
        eps: List[ParsedEndpoint] = [{
            "path": "/items",
            "method": "GET",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        env_vars = {"1": "DEFAULT_PAGE"}
        requests = generate_requests(eps, classification, {}, env_vars)
        # page → auto → value "1" → matches env var "1" → "{{DEFAULT_PAGE}}"
        assert requests[0]["params"]["page"] == "{{DEFAULT_PAGE}}"


# ============================================================================
# Test: Integration / edge cases
# ============================================================================

class TestEdgeCases:
    def test_no_manual_params(self) -> None:
        """All params are auto — no manual questions generated indirectly."""
        eps: List[ParsedEndpoint] = [{
            "path": "/paged",
            "method": "GET",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
                {"name": "limit", "location": "query", "type": "integer"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        questions = generate_questions(classification, eps)
        # Still have url_domain and env_var
        assert all(q["category"] in ("url_domain", "env_var") for q in questions)

    def test_body_params_extracted_and_classified(self) -> None:
        """Body properties should be extracted and classified."""
        eps: List[ParsedEndpoint] = [{
            "path": "/submit",
            "method": "POST",
            "parameters": [],
            "request_body": {
                "description": "",
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "timestamp": {"type": "integer"},
                            },
                        }
                    }
                },
            },
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        result = analyze_endpoints(eps)
        classification = result["classification"]
        ep_class = classification["POST /submit"]
        # id → context_ref, name → context_ref, timestamp → auto, description → manual
        assert any(p["name"] == "timestamp" for p in ep_class["auto"])
        assert any(p["name"] == "id" for p in ep_class["context_ref"])
        assert any(p["name"] == "name" for p in ep_class["context_ref"])

    def test_request_with_mixed_param_types(self) -> None:
        """Generate request with auto, manual, and context_ref params."""
        eps: List[ParsedEndpoint] = [{
            "path": "/users",
            "method": "POST",
            "parameters": [
                {"name": "page", "location": "query", "type": "integer"},
                {"name": "Authorization", "location": "header", "type": "string"},
                {"name": "name", "location": "body", "type": "string"},
                {"name": "role", "location": "body", "type": "string"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        classification = analyze_endpoints(eps)
        user_answers = {
            "name": "Alice",
            "role": "admin",
        }
        requests = generate_requests(eps, classification, user_answers, {})
        req = requests[0]
        # page → auto → "1"
        assert req["params"]["page"] == "1"
        # name → context_ref → "Alice" (from user_answers)
        assert req["body"].get("name") == "Alice"
        # role → manual → "admin"
        assert req["body"].get("role") == "admin"

    def test_large_number_uncertain_params(self) -> None:
        """3+ uncertain params would normally trigger LLM, but without DB it falls through to manual."""
        eps: List[ParsedEndpoint] = [{
            "path": "/search",
            "method": "GET",
            "parameters": [
                {"name": "q", "location": "query", "type": "string"},
                {"name": "filter", "location": "query", "type": "string"},
                {"name": "sort", "location": "query", "type": "string"},
                {"name": "status", "location": "query", "type": "string"},
            ],
            "request_body": None,
            "responses": {},
            "security": [],
            "deprecated": False,
        }]
        # 4 uncertain params → LLM would be called, but no DB → falls to manual
        result = analyze_endpoints(eps)
        classification = result["classification"]
        ep_class = classification["GET /search"]
        assert len(ep_class["manual"]) == 4

    def test_ai_question_all_fields(self) -> None:
        """AIQuestion TypedDict should accept all expected fields."""
        q: AIQuestion = {
            "id": "q_test",
            "category": "param_value",
            "title": "Test",
            "description": "Test desc",
            "field_type": "multi_param",
            "options": [{"param_name": "x", "type": "string"}],
            "related_endpoints": ["GET /test"],
            "related_params": ["x"],
        }
        assert isinstance(q, dict)
        assert q["field_type"] == "multi_param"
