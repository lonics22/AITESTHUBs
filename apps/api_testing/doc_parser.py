# -*- coding: utf-8 -*-
"""
Multi-Format API Document Parser

Normalizes Swagger 2.0, OpenAPI 3.0, Postman Collection v2.1, and HAR 1.2
into a unified ``list[ParsedEndpoint]`` structure used by the AI import service.

Usage::

    from apps.api_testing.doc_parser import parse_document

    with open("swagger.json") as f:
        raw = json.load(f)

    endpoints = parse_document(raw)
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional

from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts – shared vocabulary for the entire import pipeline
# ---------------------------------------------------------------------------

class ParsedParameter(TypedDict, total=False):
    """A single parameter extracted from any source format."""
    name: str
    location: str  # query / header / path / body / formdata
    type: str       # string / integer / boolean / array / object / file
    description: str
    required: bool
    default: Any
    example: Any
    enum: List[Any]


class ParsedEndpoint(TypedDict, total=False):
    """A normalised API endpoint independent of the source format."""
    path: str
    method: str          # GET / POST / PUT / DELETE / PATCH / HEAD / OPTIONS
    summary: str
    description: str
    tags: List[str]
    operation_id: str
    parameters: List[ParsedParameter]
    request_body: Optional[Dict[str, Any]]
    responses: Dict[str, Any]
    security: List[Dict[str, Any]]
    deprecated: bool


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(content: dict) -> str:
    """Detect the API document format from its top-level keys.

    Returns one of ``"swagger2"``, ``"openapi3"``, ``"postman"``, ``"har"``.

    Raises ``ValueError`` when the format cannot be identified.
    """
    if not isinstance(content, dict):
        raise ValueError("Content must be a dict")

    # Swagger 2.0 -- the 'swagger' key holds the version string
    swagger_ver = content.get("swagger")
    if isinstance(swagger_ver, str) and swagger_ver.startswith("2"):
        return "swagger2"
    # Handle numeric swagger version (e.g. YAML unquoted "2.0" -> float 2.0)
    if isinstance(swagger_ver, (int, float)) and int(swagger_ver) == 2:
        return "swagger2"

    # OpenAPI 3.x -- the 'openapi' key holds the version string
    openapi_ver = content.get("openapi")
    if isinstance(openapi_ver, str) and openapi_ver.startswith("3"):
        return "openapi3"
    # Handle numeric openapi version
    if isinstance(openapi_ver, (int, float)) and int(openapi_ver) == 3:
        return "openapi3"

    # Postman Collection v2.1 -- info.schema points to getpostman.com
    info = content.get("info")
    if isinstance(info, dict):
        schema_url = info.get("schema", "")
        if isinstance(schema_url, str) and "getpostman.com" in schema_url:
            return "postman"
    # Also check top-level 'schema' for Postman
    top_schema = content.get("schema")
    if isinstance(top_schema, str) and "getpostman.com" in top_schema:
        return "postman"

    # HAR 1.2 -- top-level 'log' containing 'entries'
    log = content.get("log")
    if isinstance(log, dict) and "entries" in log:
        return "har"

    raise ValueError(
        "Unable to detect document format. "
        "Supported formats: Swagger 2.0, OpenAPI 3.0, Postman v2.1, HAR 1.2"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_document(content: dict) -> List[ParsedEndpoint]:
    """Parse an API document and return a list of normalised endpoints.

    Parameters
    ----------
    content : dict
        The deserialised JSON content of the document.

    Returns
    -------
    list[ParsedEndpoint]

    Raises
    ------
    ValueError
        If the format is unknown.
    """
    fmt = detect_format(content)

    # Resolve JSON ``$ref`` pointers for Swagger / OpenAPI
    if fmt in ("swagger2", "openapi3"):
        content = _resolve_refs(content)

    parsers = {
        "swagger2": _parse_swagger2,
        "openapi3": _parse_openapi3,
        "postman": _parse_postman,
        "har": _parse_har,
    }

    parser = parsers.get(fmt)
    if parser is None:
        raise ValueError(f"Unsupported format: {fmt}")

    return parser(content)


# ---------------------------------------------------------------------------
# $ref resolution
# ---------------------------------------------------------------------------

def _resolve_refs(content: dict) -> dict:
    """Resolve all ``$ref`` pointers in the document.

    Tries the ``prance`` library first (fast, correct, handles remote refs).
    Falls back to a simple local-only dereference when ``prance`` is not
    available.
    """
    try:
        import prance
        from prance import BaseParser
        parser = BaseParser(spec_string=content)
        return parser.specification
    except ImportError:
        logger.info("prance not installed; using simple $ref resolver")
        return _simple_dereference(content, content)
    except Exception as exc:
        logger.warning("prance resolution failed (%s); falling back", exc)
        return _simple_dereference(content, content)


def _simple_dereference(obj: Any, ref_base: dict) -> Any:
    """Recursively walk *obj* and replace ``{"$ref": "#/..."}`` with the
    resolved value from *ref_base*.

    Supports ``#/definitions/`` (Swagger 2.0) and ``#/components/schemas/``
    (OpenAPI 3.0).
    """
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref_path = obj["$ref"]
            if isinstance(ref_path, str) and ref_path.startswith("#/"):
                parts = ref_path[2:].split("/")
                resolved: Any = ref_base
                for part in parts:
                    if isinstance(resolved, dict):
                        resolved = resolved.get(part)
                    else:
                        resolved = None
                        break
                if resolved is not None:
                    # Deep-copy so mutations don't affect the base spec
                    return copy.deepcopy(resolved)
            # If we can't resolve, return the ref as-is
            return copy.deepcopy(obj)

        return {k: _simple_dereference(v, ref_base) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_simple_dereference(item, ref_base) for item in obj]

    return obj


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _normalize_method(method: str) -> str:
    """Normalise an HTTP method to uppercase."""
    return method.upper().strip()


def _swagger_type_to_python(t: str) -> str:
    """Map Swagger/OpenAPI ``type`` values to our internal type strings."""
    mapping = {
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "array": "array",
        "object": "object",
        "string": "string",
        "file": "file",
    }
    return mapping.get(t, "string")


# ---------------------------------------------------------------------------
# Swagger 2.0 parser
# ---------------------------------------------------------------------------

def _parse_swagger2(content: dict) -> List[ParsedEndpoint]:
    """Parse a Swagger 2.0 document."""
    endpoints: List[ParsedEndpoint] = []
    base_path = content.get("basePath", "")
    paths = content.get("paths", {})
    definitions = content.get("definitions", {})

    if not paths:
        return endpoints

    http_methods = {"get", "post", "put", "delete", "patch", "head", "options"}

    for relative_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        full_path = base_path.rstrip("/") + "/" + relative_path.lstrip("/")

        # Shared (path-level) parameters
        shared_params = path_item.get("parameters", [])

        for method, operation in path_item.items():
            if method.lower() not in http_methods:
                continue

            method_upper = _normalize_method(method)
            tags = operation.get("tags", [])
            summary = operation.get("summary", "") or ""
            description = operation.get("description", "") or ""
            operation_id = operation.get("operationId", "") or ""
            deprecated = operation.get("deprecated", False)

            # Merge path-level + operation-level parameters
            op_params = operation.get("parameters", [])
            merged_params: List[dict] = (
                copy.deepcopy(shared_params) + copy.deepcopy(op_params)
            )

            parameters = _parse_swagger_parameters(merged_params)

            # Request body (Swagger 2.0 uses ``body`` parameter with ``schema``)
            request_body = None
            for p in merged_params:
                if isinstance(p, dict) and p.get("in") == "body":
                    schema = p.get("schema", {})
                    request_body = {
                        "description": p.get("description", ""),
                        "required": p.get("required", False),
                        "content": {
                            "application/json": {
                                "schema": schema,
                            }
                        },
                    }
                    break

            # Responses
            responses = {}
            raw_responses = operation.get("responses", {})
            for status_code, resp_data in raw_responses.items():
                if isinstance(resp_data, dict):
                    responses[status_code] = {
                        "description": resp_data.get("description", ""),
                        "schema": resp_data.get("schema", {}),
                    }

            # Security
            security = operation.get("security", content.get("security", []))

            ep: ParsedEndpoint = {
                "path": full_path,
                "method": method_upper,
                "summary": summary,
                "description": description,
                "tags": tags if tags else [],
                "operation_id": operation_id,
                "parameters": parameters,
                "request_body": request_body,
                "responses": responses,
                "security": security if security else [],
                "deprecated": bool(deprecated),
            }
            endpoints.append(ep)

    return endpoints


def _parse_swagger_parameters(params: List[dict]) -> List[ParsedParameter]:
    """Convert a list of Swagger 2.0 parameter dicts to ``ParsedParameter``."""
    result: List[ParsedParameter] = []
    for p in params:
        if not isinstance(p, dict):
            continue
        param: ParsedParameter = {
            "name": p.get("name", ""),
            "location": p.get("in", "query"),
            "type": _swagger_type_to_python(p.get("type", "string")),
            "description": p.get("description", "") or "",
            "required": bool(p.get("required", False)),
        }
        if "default" in p:
            param["default"] = p["default"]
        if "example" in p:
            param["example"] = p["example"]
        if "enum" in p:
            param["enum"] = list(p["enum"])
        # For array types with items
        if p.get("type") == "array" and "items" in p:
            param["type"] = "array"
        result.append(param)
    return result


# ---------------------------------------------------------------------------
# OpenAPI 3.0 parser
# ---------------------------------------------------------------------------

def _parse_openapi3(content: dict) -> List[ParsedEndpoint]:
    """Parse an OpenAPI 3.x document."""
    endpoints: List[ParsedEndpoint] = []
    paths = content.get("paths", {})

    if not paths:
        return endpoints

    http_methods = {"get", "post", "put", "delete", "patch", "head", "options"}

    for relative_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Shared (path-level) parameters
        shared_params = path_item.get("parameters", [])

        for method, operation in path_item.items():
            if method.lower() not in http_methods:
                continue

            method_upper = _normalize_method(method)
            tags = operation.get("tags", [])
            summary = operation.get("summary", "") or ""
            description = operation.get("description", "") or ""
            operation_id = operation.get("operationId", "") or ""
            deprecated = operation.get("deprecated", False)

            # Merge path-level + operation-level parameters
            op_params = operation.get("parameters", [])
            merged_params: List[dict] = (
                copy.deepcopy(shared_params) + copy.deepcopy(op_params)
            )

            parameters = _parse_openapi_parameters(merged_params)

            # Request body
            request_body = None
            raw_rb = operation.get("requestBody")
            if isinstance(raw_rb, dict):
                rb_required = bool(raw_rb.get("required", False))
                rb_description = raw_rb.get("description", "") or ""
                content_dict = raw_rb.get("content", {})
                normalized_content: Dict[str, Any] = {}
                for media_type, media_obj in content_dict.items():
                    normalized_content[media_type] = {
                        "schema": media_obj.get("schema", {}),
                    }
                request_body = {
                    "description": rb_description,
                    "required": rb_required,
                    "content": normalized_content,
                }

            # Responses
            responses = {}
            raw_responses = operation.get("responses", {})
            for status_code, resp_data in raw_responses.items():
                if isinstance(resp_data, dict):
                    resp_content = resp_data.get("content", {})
                    normalized_resp_content: Dict[str, Any] = {}
                    for mt, mobj in resp_content.items():
                        normalized_resp_content[mt] = {
                            "schema": mobj.get("schema", {}),
                        }
                    responses[status_code] = {
                        "description": resp_data.get("description", ""),
                        "content": normalized_resp_content,
                    }

            # Security
            security = operation.get("security", content.get("security", []))

            ep: ParsedEndpoint = {
                "path": relative_path,
                "method": method_upper,
                "summary": summary,
                "description": description,
                "tags": tags if tags else [],
                "operation_id": operation_id,
                "parameters": parameters,
                "request_body": request_body,
                "responses": responses,
                "security": security if security else [],
                "deprecated": bool(deprecated),
            }
            endpoints.append(ep)

    return endpoints


def _parse_openapi_parameters(params: List[dict]) -> List[ParsedParameter]:
    """Convert a list of OpenAPI 3.x parameter objects to ``ParsedParameter``."""
    result: List[ParsedParameter] = []
    for p in params:
        if not isinstance(p, dict):
            continue
        schema = p.get("schema", {})
        param_type = schema.get("type", p.get("type", "string"))

        param: ParsedParameter = {
            "name": p.get("name", ""),
            "location": p.get("in", "query"),
            "type": _swagger_type_to_python(param_type),
            "description": p.get("description", "") or "",
            "required": bool(p.get("required", False)),
        }
        if "default" in schema:
            param["default"] = schema["default"]
        elif "default" in p:
            param["default"] = p["default"]
        if "example" in schema:
            param["example"] = schema["example"]
        elif "example" in p:
            param["example"] = p["example"]
        if "enum" in schema:
            param["enum"] = list(schema["enum"])
        elif "enum" in p:
            param["enum"] = list(p["enum"])
        result.append(param)
    return result


# ---------------------------------------------------------------------------
# Postman Collection v2.1 parser
# ---------------------------------------------------------------------------

def _parse_postman(content: dict) -> List[ParsedEndpoint]:
    """Parse a Postman Collection v2.1 document.

    Handles nested folder structures recursively.
    """
    endpoints: List[ParsedEndpoint] = []

    # Postman items live under ``item``
    items = content.get("item", [])
    _walk_postman_items(items, endpoints)

    return endpoints


def _walk_postman_items(
    items: List[dict],
    endpoints: List[ParsedEndpoint],
    parent_tags: Optional[List[str]] = None,
) -> None:
    """Recursively walk Postman items, flattening folders into tag groups."""
    if parent_tags is None:
        parent_tags = []

    for item in items:
        if not isinstance(item, dict):
            continue

        # A folder has ``item`` inside; use folder name as a tag
        if "item" in item and "request" not in item:
            folder_name = item.get("name", "")
            folder_tags = parent_tags + ([folder_name] if folder_name else [])
            _walk_postman_items(item.get("item", []), endpoints, folder_tags)
            continue

        # A leaf request
        request_data = item.get("request")
        if not isinstance(request_data, dict):
            continue

        method = _normalize_method(request_data.get("method", "GET"))
        url_obj = request_data.get("url", {})

        # Build the path from Postman's URL structure
        path = _build_postman_path(url_obj)

        # Name / summary
        summary = item.get("name", "") or ""

        # Tags: folder hierarchy + any custom tags
        tags = list(parent_tags) if parent_tags else []

        # Parameters
        parameters: List[ParsedParameter] = []
        parsed_params = _parse_postman_params(url_obj, request_data)
        parameters.extend(parsed_params)

        # Headers
        headers = _parse_postman_headers(request_data)
        parameters.extend(headers)

        # Request body
        request_body = _parse_postman_body(request_data)

        # Responses (Postman v2.1 includes example responses)
        responses: Dict[str, Any] = {}
        raw_responses = item.get("response", [])
        for resp in raw_responses:
            if isinstance(resp, dict):
                code = str(resp.get("code", 200))
                responses[code] = {
                    "description": resp.get("name", "") or resp.get("status", "") or "",
                }

        ep: ParsedEndpoint = {
            "path": path,
            "method": method,
            "summary": summary,
            "description": summary,
            "tags": tags,
            "parameters": parameters,
            "request_body": request_body if request_body else None,
            "responses": responses,
            "security": [],
            "deprecated": False,
        }
        endpoints.append(ep)


def _build_postman_path(url_obj: Any) -> str:
    """Build a path string from a Postman URL object.

    Supports both ``url.path`` (list) and ``url.path`` (string) variants,
    plus path variables.
    """
    if not isinstance(url_obj, dict):
        return ""

    # Extract the raw path
    path_parts = url_obj.get("path", "")
    if isinstance(path_parts, list):
        parts: List[str] = []
        for segment in path_parts:
            if isinstance(segment, dict):
                # Postman can represent path variables as objects
                parts.append(segment.get("value", ""))
            else:
                parts.append(str(segment))

        # Replace any path variables that are in ``variable``
        variables = url_obj.get("variable", [])
        var_map = {}
        for var in variables:
            if isinstance(var, dict):
                var_map[var.get("key", "")] = var.get("value", f"{{{var.get('key', '')}}}")

        resolved = []
        for p in parts:
            if p.startswith(":"):
                # Postman uses :id notation; convert to {id}
                var_name = p[1:]
                resolved.append(f"{{{var_name}}}")
            elif p in var_map:
                resolved.append(var_map[p])
            else:
                resolved.append(p)
        return "/" + "/".join(resolved)

    if isinstance(path_parts, str):
        return "/" + path_parts.lstrip("/")

    return ""


def _parse_postman_params(url_obj: dict, request_data: dict) -> List[ParsedParameter]:
    """Extract query and path parameters from a Postman request."""
    result: List[ParsedParameter] = []

    if not isinstance(url_obj, dict):
        return result

    # Query parameters
    query_params = url_obj.get("query", [])
    if isinstance(query_params, list):
        for qp in query_params:
            if isinstance(qp, dict):
                param: ParsedParameter = {
                    "name": qp.get("key", ""),
                    "location": "query",
                    "type": "string",
                    "description": qp.get("description", "") or "",
                    "required": False,
                }
                if qp.get("value"):
                    param["default"] = qp["value"]
                result.append(param)

    # Path variables defined in URL
    variables = url_obj.get("variable", [])
    if isinstance(variables, list):
        for var in variables:
            if isinstance(var, dict):
                param: ParsedParameter = {
                    "name": var.get("key", ""),
                    "location": "path",
                    "type": "string",
                    "description": var.get("description", "") or "",
                    "required": True,
                }
                if var.get("value"):
                    param["default"] = var["value"]
                result.append(param)

    return result


def _parse_postman_headers(request_data: dict) -> List[ParsedParameter]:
    """Extract headers from a Postman request as parameters."""
    result: List[ParsedParameter] = []
    headers = request_data.get("header", [])
    if not isinstance(headers, list):
        return result

    for h in headers:
        if isinstance(h, dict):
            disabled = h.get("disabled", False)
            if disabled:
                continue
            param: ParsedParameter = {
                "name": h.get("key", ""),
                "location": "header",
                "type": "string",
                "description": h.get("description", "") or "",
                "required": False,
            }
            if h.get("value"):
                param["default"] = h["value"]
            result.append(param)

    return result


def _parse_postman_body(request_data: dict) -> Optional[Dict[str, Any]]:
    """Extract the request body from a Postman request."""
    body = request_data.get("body")
    if not isinstance(body, dict):
        return None

    mode = body.get("mode", "raw")
    body_description = body.get("description", "") or ""

    content: Dict[str, Any] = {}

    if mode == "raw":
        raw_body = body.get("raw", "")
        # Try to determine media type from options
        options = body.get("options", {})
        raw_lang = options.get("raw", {}).get("language", "json")
        media_type_map = {
            "json": "application/json",
            "xml": "application/xml",
            "text": "text/plain",
            "javascript": "application/javascript",
            "html": "text/html",
        }
        media_type = media_type_map.get(raw_lang, "application/json")
        schema: Dict[str, Any] = {}
        if raw_body:
            try:
                parsed = json.loads(raw_body)
                schema = {"example": parsed, "type": "object"}
            except (json.JSONDecodeError, ValueError):
                schema = {"example": raw_body, "type": "string"}
        content[media_type] = {"schema": schema}

    elif mode == "urlencoded":
        form_data = body.get("urlencoded", [])
        properties = {}
        for fd in form_data:
            if isinstance(fd, dict):
                properties[fd.get("key", "")] = {
                    "type": "string",
                    "description": fd.get("description", "") or "",
                }
        content["application/x-www-form-urlencoded"] = {
            "schema": {
                "type": "object",
                "properties": properties,
            }
        }

    elif mode == "formdata":
        form_data = body.get("formdata", [])
        properties = {}
        for fd in form_data:
            if isinstance(fd, dict):
                fd_type = fd.get("type", "text")
                properties[fd.get("key", "")] = {
                    "type": "file" if fd_type == "file" else "string",
                    "description": fd.get("description", "") or "",
                }
        content["multipart/form-data"] = {
            "schema": {
                "type": "object",
                "properties": properties,
            }
        }

    if not content:
        return None

    return {
        "description": body_description,
        "required": False,
        "content": content,
    }


# ---------------------------------------------------------------------------
# HAR 1.2 parser
# ---------------------------------------------------------------------------

def _parse_har(content: dict) -> List[ParsedEndpoint]:
    """Parse a HAR 1.2 document."""
    endpoints: List[ParsedEndpoint] = []
    log = content.get("log", {})
    entries = log.get("entries", [])

    if not entries:
        return endpoints

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        request_data = entry.get("request")
        if not isinstance(request_data, dict):
            continue

        method = _normalize_method(request_data.get("method", "GET"))
        url = request_data.get("url", "")

        # Parse the URL to extract path
        path = _har_url_to_path(url)

        # Summary from the URL (HAR has no explicit summary)
        summary = url

        # Parameters from query string
        parameters: List[ParsedParameter] = _parse_har_query_params(
            request_data.get("queryString", [])
        )

        # Headers
        headers = _parse_har_headers(request_data.get("headers", []))
        parameters.extend(headers)

        # Request body
        request_body = _parse_har_post_data(request_data.get("postData"))

        # Response info
        response_data = entry.get("response", {})
        responses: Dict[str, Any] = {}
        if isinstance(response_data, dict):
            status_code = str(response_data.get("status", 0))
            responses[status_code] = {
                "description": response_data.get("statusText", ""),
                "content": _har_response_content(response_data),
            }

        ep: ParsedEndpoint = {
            "path": path,
            "method": method,
            "summary": summary,
            "description": "",
            "tags": [],
            "parameters": parameters,
            "request_body": request_body,
            "responses": responses,
            "security": [],
            "deprecated": False,
        }
        endpoints.append(ep)

    return endpoints


def _har_url_to_path(url: str) -> str:
    """Extract the path component from a full URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path or "/"
        return path
    except Exception:
        return url


def _har_response_content(response_data: dict) -> Dict[str, Any]:
    """Extract response content info from a HAR response object."""
    content_obj = response_data.get("content", {})
    if not isinstance(content_obj, dict):
        return {}

    mime = content_obj.get("mimeType", "")
    text = content_obj.get("text", "")

    schema: Dict[str, Any] = {}
    if text and "json" in mime:
        try:
            schema["example"] = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            schema["example"] = text
    elif text:
        schema["example"] = text

    return {
        mime: {
            "schema": schema,
        }
    }


def _parse_har_query_params(query_string: Any) -> List[ParsedParameter]:
    """Parse HAR query string parameters."""
    result: List[ParsedParameter] = []
    if not isinstance(query_string, list):
        return result

    for qp in query_string:
        if isinstance(qp, dict):
            param: ParsedParameter = {
                "name": qp.get("name", ""),
                "location": "query",
                "type": "string",
                "description": "",
                "required": False,
            }
            if qp.get("value"):
                param["default"] = qp["value"]
            result.append(param)

    return result


def _parse_har_headers(headers: Any) -> List[ParsedParameter]:
    """Parse HAR headers."""
    result: List[ParsedParameter] = []
    if not isinstance(headers, list):
        return result

    for h in headers:
        if isinstance(h, dict):
            param: ParsedParameter = {
                "name": h.get("name", ""),
                "location": "header",
                "type": "string",
                "description": "",
                "required": False,
            }
            if h.get("value"):
                param["default"] = h["value"]
            result.append(param)

    return result


def _parse_har_post_data(post_data: Any) -> Optional[Dict[str, Any]]:
    """Parse HAR postData into a normalized request body."""
    if not isinstance(post_data, dict):
        return None

    mime_type = post_data.get("mimeType", "application/octet-stream")
    text = post_data.get("text", "")
    params = post_data.get("params", [])

    content: Dict[str, Any] = {}
    schema: Dict[str, Any] = {}

    if params:
        # Form parameters
        properties = {}
        for p in params:
            if isinstance(p, dict):
                properties[p.get("name", "")] = {
                    "type": "string",
                }
        schema = {
            "type": "object",
            "properties": properties,
        }
    elif text:
        if "json" in mime_type:
            try:
                schema["example"] = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                schema["example"] = text
        else:
            schema["example"] = text

    content[mime_type] = {"schema": schema}

    return {
        "description": "",
        "required": False,
        "content": content,
    }
