# -*- coding: utf-8 -*-
"""Tests for apps.api_testing.doc_parser."""

import json
import copy
import pytest

from apps.api_testing.doc_parser import (
    ParsedEndpoint,
    ParsedParameter,
    detect_format,
    parse_document,
    _resolve_refs,
    _simple_dereference,
    _parse_swagger2,
    _parse_openapi3,
    _parse_postman,
    _parse_har,
)


# ============================================================================
# Mini Test Fixtures
# ============================================================================

SWAGGER2_FIXTURE = {
    "swagger": "2.0",
    "info": {"title": "Pet Store", "version": "1.0.0"},
    "basePath": "/v2",
    "host": "petstore.example.com",
    "paths": {
        "/pet": {
            "post": {
                "tags": ["pet"],
                "summary": "Add a new pet",
                "operationId": "addPet",
                "parameters": [
                    {
                        "name": "body",
                        "in": "body",
                        "description": "Pet object",
                        "required": True,
                        "schema": {"$ref": "#/definitions/Pet"},
                    }
                ],
                "responses": {
                    "200": {"description": "successful operation"},
                    "405": {"description": "Invalid input"},
                },
                "security": [{"petstore_auth": ["write:pets", "read:pets"]}],
            }
        },
        "/pet/{petId}": {
            "get": {
                "tags": ["pet"],
                "summary": "Find pet by ID",
                "operationId": "getPetById",
                "parameters": [
                    {
                        "name": "petId",
                        "in": "path",
                        "description": "ID of pet",
                        "required": True,
                        "type": "integer",
                        "format": "int64",
                    }
                ],
                "responses": {
                    "200": {"description": "successful operation"},
                    "404": {"description": "Pet not found"},
                },
            }
        },
    },
    "definitions": {
        "Pet": {
            "type": "object",
            "required": ["name"],
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "tag": {"type": "string"},
            },
        }
    },
}

OPENAPI3_FIXTURE = {
    "openapi": "3.0.0",
    "info": {"title": "Store API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "tags": ["items"],
                "summary": "List all items",
                "operationId": "listItems",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "description": "Max items",
                        "required": False,
                        "schema": {"type": "integer", "default": 20},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "An array of items",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Item"}
                            }
                        },
                    }
                },
            },
            "post": {
                "tags": ["items"],
                "summary": "Create an item",
                "operationId": "createItem",
                "requestBody": {
                    "description": "Item to create",
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/NewItem"}
                        }
                    },
                },
                "responses": {
                    "201": {"description": "Created"},
                },
            },
        },
    },
    "components": {
        "schemas": {
            "Item": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                },
            },
            "NewItem": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price": {"type": "number"},
                },
            },
        }
    },
}

POSTMAN_FIXTURE = {
    "info": {
        "name": "Example Collection",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": [
        {
            "name": "Users",
            "item": [
                {
                    "name": "Get Users",
                    "request": {
                        "method": "GET",
                        "url": {
                            "raw": "https://api.example.com/users?page=1",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["users"],
                            "query": [
                                {
                                    "key": "page",
                                    "value": "1",
                                    "description": "Page number",
                                }
                            ],
                        },
                    },
                    "response": [
                        {"code": 200, "name": "Success"},
                    ],
                },
            ],
        },
        {
            "name": "Create User",
            "request": {
                "method": "POST",
                "url": {
                    "raw": "https://api.example.com/users",
                    "protocol": "https",
                    "host": ["api", "example", "com"],
                    "path": ["users"],
                },
                "header": [
                    {"key": "Content-Type", "value": "application/json"},
                ],
                "body": {
                    "mode": "raw",
                    "raw": '{"name": "John"}',
                    "options": {"raw": {"language": "json"}},
                },
            },
        },
    ],
}

HAR_FIXTURE = {
    "log": {
        "version": "1.2",
        "entries": [
            {
                "request": {
                    "method": "GET",
                    "url": "https://api.example.com/users?page=1",
                    "queryString": [
                        {"name": "page", "value": "1"},
                    ],
                    "headers": [
                        {"name": "Accept", "value": "application/json"},
                    ],
                },
                "response": {
                    "status": 200,
                    "statusText": "OK",
                    "content": {
                        "mimeType": "application/json",
                        "text": '{"data": []}',
                    },
                },
            },
            {
                "request": {
                    "method": "POST",
                    "url": "https://api.example.com/users",
                    "headers": [
                        {"name": "Content-Type", "value": "application/json"},
                    ],
                    "postData": {
                        "mimeType": "application/json",
                        "text": '{"name": "John"}',
                    },
                },
                "response": {
                    "status": 201,
                    "statusText": "Created",
                    "content": {
                        "mimeType": "application/json",
                        "text": '{"id": 1, "name": "John"}',
                    },
                },
            },
        ],
    }
}

UNKNOWN_CONTENT = {"foo": "bar"}


# ============================================================================
# Test: Format Detection
# ============================================================================

class TestDetectFormat:
    def test_swagger2(self):
        assert detect_format(SWAGGER2_FIXTURE) == "swagger2"

    def test_openapi3(self):
        assert detect_format(OPENAPI3_FIXTURE) == "openapi3"

    def test_postman(self):
        assert detect_format(POSTMAN_FIXTURE) == "postman"

    def test_har(self):
        assert detect_format(HAR_FIXTURE) == "har"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unable to detect"):
            detect_format(UNKNOWN_CONTENT)

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            detect_format([])  # type: ignore[arg-type]

    def test_empty_dict_raises(self):
        with pytest.raises(ValueError):
            detect_format({})


# ============================================================================
# Test: $ref Resolution
# ============================================================================

class TestRefResolution:
    def test_simple_dereference_swagger(self):
        """Resolve #/definitions/Pet from Swagger fixture."""
        resolved = _simple_dereference(SWAGGER2_FIXTURE, SWAGGER2_FIXTURE)
        # The /pet POST body parameter schema should be resolved
        pet_post = resolved["paths"]["/pet"]["post"]
        body_param = pet_post["parameters"][0]
        assert "$ref" not in body_param["schema"]
        assert body_param["schema"]["type"] == "object"
        assert "name" in body_param["schema"]["required"]

    def test_simple_dereference_openapi(self):
        """Resolve #/components/schemas/Item from OpenAPI fixture."""
        resolved = _simple_dereference(OPENAPI3_FIXTURE, OPENAPI3_FIXTURE)
        items_get = resolved["paths"]["/items"]["get"]
        resp_schema = items_get["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" not in resp_schema
        assert resp_schema["type"] == "object"

    def test_resolve_refs_fallback(self):
        """_resolve_refs should work (fallback) when prance is not installed."""
        result = _resolve_refs(SWAGGER2_FIXTURE)
        assert isinstance(result, dict)


# ============================================================================
# Test: Swagger 2.0 Parsing
# ============================================================================

class TestSwagger2Parser:
    def test_parse_swagger2_endpoints(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_swagger2_path(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        paths = {ep["path"] for ep in endpoints}
        assert "/v2/pet" in paths
        assert "/v2/pet/{petId}" in paths

    def test_parse_swagger2_methods(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        by_path = {ep["path"]: ep["method"] for ep in endpoints}
        assert by_path["/v2/pet"] == "POST"
        assert by_path["/v2/pet/{petId}"] == "GET"

    def test_parse_swagger2_tags(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        for ep in endpoints:
            assert ep["tags"] == ["pet"]

    def test_parse_swagger2_summary(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        summaries = {ep["path"]: ep["summary"] for ep in endpoints}
        assert summaries["/v2/pet"] == "Add a new pet"
        assert summaries["/v2/pet/{petId}"] == "Find pet by ID"

    def test_parse_swagger2_operation_id(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        ops = {ep["path"]: ep["operation_id"] for ep in endpoints}
        assert ops["/v2/pet"] == "addPet"
        assert ops["/v2/pet/{petId}"] == "getPetById"

    def test_parse_swagger2_parameters(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        # The GET endpoint has a path parameter
        get_ep = [ep for ep in endpoints if ep["method"] == "GET"][0]
        assert len(get_ep["parameters"]) == 1
        param = get_ep["parameters"][0]
        assert param["name"] == "petId"
        assert param["location"] == "path"
        assert param["type"] == "integer"
        assert param["required"] is True

    def test_parse_swagger2_body_parameter(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        assert post_ep["request_body"] is not None
        assert post_ep["request_body"]["required"] is True
        assert "application/json" in post_ep["request_body"]["content"]

    def test_parse_swagger2_responses(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        assert "200" in post_ep["responses"]
        assert "405" in post_ep["responses"]
        assert post_ep["responses"]["200"]["description"] == "successful operation"

    def test_parse_swagger2_security(self):
        endpoints = _parse_swagger2(SWAGGER2_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        assert len(post_ep["security"]) > 0

    def test_parse_swagger2_empty_paths(self):
        result = _parse_swagger2({"swagger": "2.0", "paths": {}})
        assert result == []


# ============================================================================
# Test: OpenAPI 3.0 Parsing
# ============================================================================

class TestOpenAPI3Parser:
    def test_parse_openapi3_endpoints(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_openapi3_methods(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        methods = {ep["method"] for ep in endpoints}
        assert methods == {"GET", "POST"}

    def test_parse_openapi3_parameters(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["method"] == "GET"][0]
        assert len(get_ep["parameters"]) == 1
        param = get_ep["parameters"][0]
        assert param["name"] == "limit"
        assert param["location"] == "query"
        assert param["type"] == "integer"
        assert param["default"] == 20

    def test_parse_openapi3_request_body(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        assert post_ep["request_body"] is not None
        assert post_ep["request_body"]["required"] is True
        assert "application/json" in post_ep["request_body"]["content"]

    def test_parse_openapi3_responses(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["method"] == "GET"][0]
        assert "200" in get_ep["responses"]

    def test_parse_openapi3_path(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        paths = {ep["path"] for ep in endpoints}
        assert "/items" in paths

    def test_parse_openapi3_tags(self):
        endpoints = _parse_openapi3(OPENAPI3_FIXTURE)
        for ep in endpoints:
            assert ep["tags"] == ["items"]

    def test_parse_openapi3_empty_paths(self):
        result = _parse_openapi3({"openapi": "3.0.0", "paths": {}})
        assert result == []


# ============================================================================
# Test: Postman Parsing
# ============================================================================

class TestPostmanParser:
    def test_parse_postman_endpoints(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_postman_folder_structure(self):
        """Requests inside a folder inherit the folder name as a tag."""
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        users_eps = [ep for ep in endpoints if "Users" in ep["tags"]]
        assert len(users_eps) == 1
        assert users_eps[0]["summary"] == "Get Users"

    def test_parse_postman_methods(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        methods = {ep["summary"]: ep["method"] for ep in endpoints}
        assert methods["Get Users"] == "GET"
        assert methods["Create User"] == "POST"

    def test_parse_postman_path(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        for ep in endpoints:
            assert "/users" in ep["path"]

    def test_parse_postman_query_params(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["summary"] == "Get Users"][0]
        query_params = [p for p in get_ep["parameters"] if p["location"] == "query"]
        assert len(query_params) == 1
        assert query_params[0]["name"] == "page"
        assert query_params[0]["default"] == "1"

    def test_parse_postman_headers(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["summary"] == "Create User"][0]
        header_params = [p for p in post_ep["parameters"] if p["location"] == "header"]
        assert len(header_params) >= 1

    def test_parse_postman_request_body(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["summary"] == "Create User"][0]
        assert post_ep["request_body"] is not None
        assert "application/json" in post_ep["request_body"]["content"]

    def test_parse_postman_responses(self):
        endpoints = _parse_postman(POSTMAN_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["summary"] == "Get Users"][0]
        assert "200" in get_ep["responses"]


# ============================================================================
# Test: HAR Parsing
# ============================================================================

class TestHARParser:
    def test_parse_har_endpoints(self):
        endpoints = _parse_har(HAR_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_har_methods(self):
        endpoints = _parse_har(HAR_FIXTURE)
        methods = {ep["method"] for ep in endpoints}
        assert methods == {"GET", "POST"}

    def test_parse_har_path(self):
        endpoints = _parse_har(HAR_FIXTURE)
        paths = {ep["path"] for ep in endpoints}
        assert "/users" in paths

    def test_parse_har_query_params(self):
        endpoints = _parse_har(HAR_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["method"] == "GET"][0]
        query_params = [p for p in get_ep["parameters"] if p["location"] == "query"]
        assert len(query_params) == 1
        assert query_params[0]["name"] == "page"

    def test_parse_har_headers(self):
        endpoints = _parse_har(HAR_FIXTURE)
        for ep in endpoints:
            header_params = [p for p in ep["parameters"] if p["location"] == "header"]
            assert len(header_params) >= 1

    def test_parse_har_post_data(self):
        endpoints = _parse_har(HAR_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        assert post_ep["request_body"] is not None
        assert "application/json" in post_ep["request_body"]["content"]

    def test_parse_har_responses(self):
        endpoints = _parse_har(HAR_FIXTURE)
        get_ep = [ep for ep in endpoints if ep["method"] == "GET"][0]
        assert "200" in get_ep["responses"]
        assert get_ep["responses"]["200"]["description"] == "OK"

    def test_parse_har_empty_entries(self):
        result = _parse_har({"log": {"entries": []}})
        assert result == []


# ============================================================================
# Test: parse_document (integration)
# ============================================================================

class TestParseDocument:
    def test_parse_document_swagger2(self):
        endpoints = parse_document(SWAGGER2_FIXTURE)
        assert len(endpoints) == 2
        assert all(ep["method"] in ("GET", "POST") for ep in endpoints)

    def test_parse_document_openapi3(self):
        endpoints = parse_document(OPENAPI3_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_document_postman(self):
        endpoints = parse_document(POSTMAN_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_document_har(self):
        endpoints = parse_document(HAR_FIXTURE)
        assert len(endpoints) == 2

    def test_parse_document_unknown_raises(self):
        with pytest.raises(ValueError, match="Unable to detect"):
            parse_document({"foo": "bar"})


# ============================================================================
# Test: Edge Cases
# ============================================================================

class TestEdgeCases:
    def test_swagger2_no_paths(self):
        result = _parse_swagger2({"swagger": "2.0", "paths": {}})
        assert result == []

    def test_openapi3_no_paths(self):
        result = _parse_openapi3({"openapi": "3.0.0", "paths": {}})
        assert result == []

    def test_postman_empty_items(self):
        result = _parse_postman({"item": []})
        assert result == []

    def test_har_no_entries(self):
        result = _parse_har({"log": {}})
        assert result == []

    def test_swagger2_path_level_parameters(self):
        """Path-level parameters should be merged with operation-level parameters."""
        content = {
            "swagger": "2.0",
            "paths": {
                "/test": {
                    "parameters": [
                        {
                            "name": "Authorization",
                            "in": "header",
                            "type": "string",
                            "required": True,
                        }
                    ],
                    "get": {
                        "summary": "Test endpoint",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "type": "integer",
                                "required": False,
                            }
                        ],
                        "responses": {"200": {"description": "OK"}},
                    },
                }
            },
        }
        endpoints = _parse_swagger2(content)
        assert len(endpoints) == 1
        assert len(endpoints[0]["parameters"]) == 2

    def test_swagger2_deprecated(self):
        content = {
            "swagger": "2.0",
            "paths": {
                "/old": {
                    "get": {
                        "deprecated": True,
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        endpoints = _parse_swagger2(content)
        assert endpoints[0]["deprecated"] is True

    def test_parse_document_with_ref_resolution(self):
        """Via parse_document, $ref should be resolved."""
        endpoints = parse_document(SWAGGER2_FIXTURE)
        post_ep = [ep for ep in endpoints if ep["method"] == "POST"][0]
        # The body parameter should have resolved schema (no $ref)
        rb = post_ep["request_body"]
        assert rb is not None
        schema = rb["content"]["application/json"]["schema"]
        assert "$ref" not in str(schema)
        assert schema.get("type") == "object" or "properties" in schema

    def test_postman_path_with_variables(self):
        """Postman path variables like :id should be converted to {id}."""
        content = {
            "info": {
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Get User",
                    "request": {
                        "method": "GET",
                        "url": {
                            "raw": "https://api.example.com/users/:id",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["users", ":id"],
                        },
                    },
                }
            ],
        }
        endpoints = _parse_postman(content)
        assert endpoints[0]["path"] == "/users/{id}"

    def test_postman_urlencoded_body(self):
        content = {
            "info": {
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Login",
                    "request": {
                        "method": "POST",
                        "url": {
                            "raw": "https://api.example.com/login",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["login"],
                        },
                        "body": {
                            "mode": "urlencoded",
                            "urlencoded": [
                                {"key": "username", "value": "admin", "description": "Username"},
                                {"key": "password", "value": "secret", "description": "Password"},
                            ],
                        },
                    },
                }
            ],
        }
        endpoints = _parse_postman(content)
        rb = endpoints[0]["request_body"]
        assert rb is not None
        assert "application/x-www-form-urlencoded" in rb["content"]

    def test_postman_formdata_body(self):
        content = {
            "info": {
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            },
            "item": [
                {
                    "name": "Upload",
                    "request": {
                        "method": "POST",
                        "url": {
                            "raw": "https://api.example.com/upload",
                            "protocol": "https",
                            "host": ["api", "example", "com"],
                            "path": ["upload"],
                        },
                        "body": {
                            "mode": "formdata",
                            "formdata": [
                                {"key": "file", "type": "file", "description": "File to upload"},
                                {"key": "name", "value": "test", "description": "File name"},
                            ],
                        },
                    },
                }
            ],
        }
        endpoints = _parse_postman(content)
        rb = endpoints[0]["request_body"]
        assert rb is not None
        assert "multipart/form-data" in rb["content"]
