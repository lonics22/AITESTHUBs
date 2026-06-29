# -*- coding: utf-8 -*-
"""
AI-Powered API Test Case Import Service

Three-phase pipeline:
  Phase 1 — Hybrid (heuristic + LLM) parameter classification
  Phase 2 — Structured question generation for manual params
  Phase 3 — Request dict generation from user answers

Usage::

    from apps.api_testing.doc_parser import parse_document
    from apps.api_testing.ai_import_service import (
        analyze_endpoints,
        generate_questions,
        generate_requests,
        AIQuestion,
    )

    with open("swagger.json") as f:
        raw = json.load(f)
    endpoints = parse_document(raw)
    classification = analyze_endpoints(endpoints)
    questions = generate_questions(classification, endpoints)
    requests = generate_requests(endpoints, classification, answers, env_vars)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from typing_extensions import TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TypedDicts – shared vocabulary for the entire import pipeline
# ---------------------------------------------------------------------------

class AIQuestion(TypedDict):
    """A question presented to the user during the import workflow."""
    id: str                    # q_1, q_2, ...
    category: str              # env_var / auth / param_value / url_domain
    title: str
    description: str
    field_type: str            # string / select / multi_param / env_var_mapping
    options: List[Dict[str, Any]]   # dropdown options or multi-param list
    related_endpoints: List[str]
    related_params: List[str]


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

# Parameter names matching these patterns are classified as "auto"
_AUTO_PARAM_PATTERNS: List[re.Pattern] = [
    # Pagination
    re.compile(r'^page$', re.I),
    re.compile(r'^page_num$', re.I),
    re.compile(r'^page_index$', re.I),
    re.compile(r'^offset$', re.I),
    re.compile(r'^page_size$', re.I),
    re.compile(r'^limit$', re.I),
    re.compile(r'^per_page$', re.I),
    re.compile(r'^perpage$', re.I),
    # Timestamp
    re.compile(r'^timestamp$', re.I),
    re.compile(r'^_t$'),
    re.compile(r'^_timestamp$', re.I),
    # Format / locale
    re.compile(r'^format$', re.I),
    re.compile(r'^locale$', re.I),
    re.compile(r'^callback$', re.I),
    re.compile(r'^_dc$'),   # cache buster
]

# Parameter names matching these patterns are classified as "context_ref"
_CONTEXT_REF_PATTERNS: List[re.Pattern] = [
    # Identifiers
    re.compile(r'^id$', re.I),
    re.compile(r'^.*_id$', re.I),      # user_id, product_id, order_id, …
    # Auth tokens
    re.compile(r'^token$', re.I),
    re.compile(r'^access_token$', re.I),
    re.compile(r'^api_key$', re.I),
    re.compile(r'^apikey$', re.I),
    # User identifiers
    re.compile(r'^name$', re.I),
    re.compile(r'^username$', re.I),
]


# ---------------------------------------------------------------------------
# Phase 1 — Parameter classification
# ---------------------------------------------------------------------------

def _apply_heuristic(param_name: str) -> Optional[str]:
    """Return ``"auto"``, ``"context_ref"``, or ``None`` (uncertain)."""
    for pattern in _AUTO_PARAM_PATTERNS:
        if pattern.search(param_name):
            return "auto"
    for pattern in _CONTEXT_REF_PATTERNS:
        if pattern.search(param_name):
            return "context_ref"
    return None


def _extract_body_params(request_body: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract body properties from ``request_body`` schema as param-like dicts."""
    if not request_body:
        return []

    params: List[Dict[str, Any]] = []
    content = request_body.get("content", {})

    for media_type, media_obj in content.items():
        if not isinstance(media_obj, dict):
            continue
        schema = media_obj.get("schema", {})
        if not isinstance(schema, dict):
            continue

        # Object with properties
        if schema.get("type") == "object" or "properties" in schema:
            properties = schema.get("properties", {})
            required_fields = schema.get("required", [])
            for prop_name, prop_schema in properties.items():
                if not isinstance(prop_schema, dict):
                    prop_schema = {}
                param: Dict[str, Any] = {
                    "name": prop_name,
                    "location": "body",
                    "type": prop_schema.get("type", "string"),
                    "description": prop_schema.get("description", "") or "",
                    "required": prop_name in required_fields,
                }
                if "default" in prop_schema:
                    param["default"] = prop_schema["default"]
                if "example" in prop_schema:
                    param["example"] = prop_schema["example"]
                if "enum" in prop_schema:
                    param["enum"] = list(prop_schema["enum"])
                params.append(param)

        # Example-based schema (e.g. Postman raw bodies)
        example = schema.get("example")
        if isinstance(example, dict):
            for key, value in example.items():
                params.append({
                    "name": key,
                    "location": "body",
                    "type": _python_type_to_string(type(value)),
                    "description": "",
                    "required": False,
                })

    return params


def _python_type_to_string(tp: type) -> str:
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        type(None): "null",
    }
    return mapping.get(tp, "string")


def _llm_classify_uncertain_params(
    endpoint: Dict[str, Any],
    params: List[Dict[str, Any]],
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Use LLM to classify uncertain parameters.

    Falls back to ``None`` on any error — callers default uncertain params
    to ``"manual"`` in that case.
    """
    # Lazy imports to avoid loading Django models at module level.
    # Catch *all* exceptions (not just ImportError) because Django model
    # loading can raise ImproperlyConfigured when settings are not configured.
    try:
        from apps.requirement_analysis.models import AIModelConfig
        from apps.requirement_analysis.models import AIModelService
    except Exception:
        logger.warning("AIModelConfig / AIModelService not available; skipping LLM")
        return None

    try:
        # Look for an AIModelConfig with role='api_import', or fall back
        # to any active config.
        config = AIModelConfig.objects.filter(
            role="api_import", is_active=True,
        ).first()
        if config is None:
            config = AIModelConfig.objects.filter(is_active=True).first()
        if config is None:
            logger.info("No active AIModelConfig found; skipping LLM classification")
            return None

        param_names = [p.get("name", "") for p in params]
        param_details = "\n".join(
            f"  - {p.get('name', '?')} (in: {p.get('location', 'query')}, "
            f"type: {p.get('type', 'string')})"
            for p in params
        )

        system_prompt = (
            "You are an API parameter classifier. Given an endpoint and a list of "
            "parameter names, classify each parameter into exactly one of:\n"
            '- "auto" — values that can be auto-generated (pagination, timestamps, '
            "format, locale, cache busters, …)\n"
            '- "manual" — values that require user input (business data, custom '
            "search terms, filters, …)\n"
            '- "context_ref" — values that reference existing context (identifiers, '
            "tokens, names, relationships, …)\n\n"
            "Respond with ONLY a JSON object (no markdown, no explanation):\n"
            '{"auto": ["name1", "name2"], "manual": ["name3"], '
            '"context_ref": ["name4"]}'
        )

        user_prompt = (
            f"Endpoint: {endpoint.get('method', '?')} {endpoint.get('path', '?')}\n"
            f"Summary: {endpoint.get('summary', '')}\n"
            f"Parameters to classify:\n{param_details}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # call_openai_compatible_api is async; bridge with asyncio.run()
        response = asyncio.run(
            AIModelService.call_openai_compatible_api(config, messages)
        )
        raw_content = response["choices"][0]["message"]["content"]

        # Strip markdown code fences if present
        json_match = re.search(
            r"```(?:json)?\s*([\s\S]*?)\s*```", raw_content,
        )
        if json_match:
            raw_content = json_match.group(1)

        parsed = json.loads(raw_content.strip())

        # Build result dict; unknown names default to "manual"
        name_to_param = {p.get("name", ""): p for p in params}
        result: Dict[str, List[Dict[str, Any]]] = {
            "auto": [],
            "manual": [],
            "context_ref": [],
        }

        for category in ("auto", "manual", "context_ref"):
            names = parsed.get(category, [])
            if isinstance(names, list):
                for n in names:
                    if n in name_to_param:
                        result[category].append(name_to_param.pop(n))

        # Leftovers (not mentioned by LLM) → manual
        for remaining in name_to_param.values():
            result["manual"].append(remaining)

        logger.info(
            "LLM classified %d uncertain params for %s %s",
            len(params),
            endpoint.get("method", "?"),
            endpoint.get("path", "?"),
        )
        return result

    except Exception as exc:
        logger.warning(
            "LLM classification failed for %s %s: %s",
            endpoint.get("method", "?"),
            endpoint.get("path", "?"),
            exc,
        )
        return None


def _hybrid_classify_params(
    endpoint: Dict[str, Any],
    all_params: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Classify parameters using heuristic rules first, then LLM for uncertain ones.

    Returns ``{"auto": [...], "manual": [...], "context_ref": [...]}``.
    """
    result: Dict[str, List[Dict[str, Any]]] = {
        "auto": [],
        "manual": [],
        "context_ref": [],
    }
    uncertain: List[Dict[str, Any]] = []

    for param in all_params:
        name = param.get("name", "")
        if not name:
            continue
        heuristic = _apply_heuristic(name)
        if heuristic is not None:
            result[heuristic].append(param)
        else:
            uncertain.append(param)

    if not uncertain:
        return result

    # If 3+ uncertain params exist, try LLM
    if len(uncertain) >= 3:
        llm_result = _llm_classify_uncertain_params(endpoint, uncertain)
        if llm_result is not None:
            result["auto"].extend(llm_result.get("auto", []))
            result["manual"].extend(llm_result.get("manual", []))
            result["context_ref"].extend(llm_result.get("context_ref", []))
            return result

    # < 3 uncertain, or LLM failed → default to manual
    result["manual"].extend(uncertain)
    return result


def analyze_endpoints(
    parsed_endpoints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Phase 1: Classify every parameter across all parsed endpoints.

    Returns a summary dict::

        {
            "endpoint_count": int,
            "total_params": int,
            "auto_params": int,
            "manual_params": int,
            "context_ref_params": int,
            "classification": {
                "GET /users": {"auto": [...], "manual": [...], "context_ref": [...]},
                ...
            }
        }
    """
    classification: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    total_auto = 0
    total_manual = 0
    total_context_ref = 0
    total_params = 0

    for ep in parsed_endpoints:
        key = f"{ep['method'].upper()} {ep['path']}"

        # Gather all parameters (path/query/header) + body properties
        all_params = list(ep.get("parameters", []))
        body_params = _extract_body_params(ep.get("request_body"))
        all_params.extend(body_params)

        classified = _hybrid_classify_params(ep, all_params)
        classification[key] = classified

        total_params += len(all_params)
        total_auto += len(classified["auto"])
        total_manual += len(classified["manual"])
        total_context_ref += len(classified["context_ref"])

    return {
        "endpoint_count": len(parsed_endpoints),
        "total_params": total_params,
        "auto_params": total_auto,
        "manual_params": total_manual,
        "context_ref_params": total_context_ref,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# Phase 2 — Question generation
# ---------------------------------------------------------------------------

def _collect_manual_params(
    classification: Dict[str, Any],
    parsed_endpoints: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Collect all manual params with endpoint context attached."""
    manual_params: List[Dict[str, Any]] = []
    for ep in parsed_endpoints:
        key = f"{ep['method'].upper()} {ep['path']}"
        classified = classification.get("classification", classification).get(key, {})
        for p in classified.get("manual", []):
            param_with_ctx = dict(p)  # shallow copy
            param_with_ctx["_method"] = ep["method"]
            param_with_ctx["_path"] = ep["path"]
            manual_params.append(param_with_ctx)
    return manual_params


def _extract_domains(parsed_endpoints: List[Dict[str, Any]]) -> List[str]:
    """Extract unique hostnames from parsed endpoint paths."""
    domains: set = set()
    for ep in parsed_endpoints:
        path = ep.get("path", "")
        if path.startswith(("http://", "https://")):
            try:
                parsed = urlparse(path)
                if parsed.hostname:
                    domains.add(parsed.hostname)
            except Exception:
                pass
    return sorted(domains)


def generate_questions(
    classification: Dict[str, Any],
    parsed_endpoints: List[Dict[str, Any]],
) -> List[AIQuestion]:
    """Phase 2: Generate structured questions from classification results.

    Returns a list of :class:`AIQuestion` TypedDicts covering four categories:
    ``url_domain``, ``auth``, ``param_value``, ``env_var``.
    """
    questions: List[AIQuestion] = []
    q_id = 0

    if not parsed_endpoints:
        return questions

    # -- Collect manual params with endpoint context -------------------------
    manual_params = _collect_manual_params(classification, parsed_endpoints)
    all_endpoint_keys = [f"{ep['method'].upper()} {ep['path']}" for ep in parsed_endpoints]

    # 1. url_domain ----------------------------------------------------------
    domains = _extract_domains(parsed_endpoints)
    if len(domains) <= 1:
        # Always ask for base URL since paths may be relative
        q_id += 1
        questions.append({
            "id": f"q_{q_id}",
            "category": "url_domain",
            "title": "API Base URL",
            "description": "Enter the base URL / domain for these API endpoints.",
            "field_type": "string",
            "options": [{"domain": d} for d in domains] if domains else [],
            "related_endpoints": list(all_endpoint_keys),
            "related_params": [],
        })
    else:
        # Multiple domains → ask which one to use
        q_id += 1
        questions.append({
            "id": f"q_{q_id}",
            "category": "url_domain",
            "title": "API Base URL",
            "description": "Multiple domains detected. Enter the base URL to use.",
            "field_type": "string",
            "options": [{"domain_choices": domains}],
            "related_endpoints": list(all_endpoint_keys),
            "related_params": [],
        })

    # 2. auth ----------------------------------------------------------------
    auth_param_names = {"authorization", "x-api-key", "api-key", "api_key"}
    auth_headers = [
        p for p in manual_params
        if p.get("location") == "header" and p["name"].lower() in auth_param_names
    ]
    if auth_headers:
        auth_related_endpoints = sorted(set(
            f"{ep['method'].upper()} {ep['path']}"
            for ep in parsed_endpoints
            for p in ep.get("parameters", [])
            if p.get("name", "").lower() in auth_param_names
        ))
        q_id += 1
        questions.append({
            "id": f"q_{q_id}",
            "category": "auth",
            "title": "Authentication Method",
            "description": "Select the authentication method used by these endpoints.",
            "field_type": "select",
            "options": [
                {"label": "Bearer Token", "value": "bearer"},
                {"label": "Basic Auth", "value": "basic"},
                {"label": "API Key", "value": "apikey"},
                {"label": "No Auth", "value": "none"},
            ],
            "related_endpoints": auth_related_endpoints or list(all_endpoint_keys),
            "related_params": list(set(p["name"] for p in auth_headers)),
        })

    # 3. param_value ---------------------------------------------------------
    biz_params = [
        p for p in manual_params
        if p["name"].lower() not in auth_param_names
    ]
    if biz_params:
        q_id += 1
        options: List[Dict[str, Any]] = []
        for p in biz_params:
            options.append({
                "param_name": p["name"],
                "location": p.get("location", "query"),
                "endpoint": f"{p.get('_method', '')} {p.get('_path', '')}".strip(),
                "method": p.get("_method", ""),
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
            })
        questions.append({
            "id": f"q_{q_id}",
            "category": "param_value",
            "title": "Parameter Values",
            "description": "Provide values for the following parameters.",
            "field_type": "multi_param",
            "options": options,
            "related_endpoints": sorted(set(o["endpoint"] for o in options)),
            "related_params": sorted(set(o["param_name"] for o in options)),
        })

    # 4. env_var -------------------------------------------------------------
    q_id += 1
    questions.append({
        "id": f"q_{q_id}",
        "category": "env_var",
        "title": "Environment Variable Mapping",
        "description": "Map raw values to environment variable keys (e.g. api.example.com -> {{BASE_URL}}).",
        "field_type": "env_var_mapping",
        "options": [],
        "related_endpoints": list(all_endpoint_keys),
        "related_params": [],
    })

    return questions


# ---------------------------------------------------------------------------
# Phase 3 — Request generation
# ---------------------------------------------------------------------------

def _generate_auto_value(param: Dict[str, Any]) -> str:
    """Generate a mock value for an auto-classified parameter."""
    param_name = param.get("name", "").lower()
    param_type = param.get("type", "string")

    # Name-based smart defaults
    if any(p == param_name for p in ("page", "page_num", "page_index", "offset")):
        return "1"
    if any(p in param_name for p in ("page_size", "per_page", "perpage", "limit")):
        return "10"
    if "format" in param_name:
        return "json"
    if "locale" in param_name:
        return "zh-CN"
    if "timestamp" in param_name or param_name == "_t":
        return "1700000000"
    if param_name == "callback":
        return "jQueryCallback"
    if param_name == "_dc":
        return "1234567890"

    # Type-based defaults
    type_defaults = {
        "string": "test",
        "integer": "1",
        "number": "1.0",
        "boolean": "true",
        "array": "[]",
        "object": "{}",
        "file": "",
    }
    return type_defaults.get(param_type, "test")


def _replace_env_vars(
    value: str,
    environment_vars: Dict[str, str],
) -> str:
    """If *value* matches an env var original value, replace with ``{{key}}``."""
    for orig, var_key in environment_vars.items():
        if value == orig:
            return f"{{{{{var_key}}}}}"
    return value


def _apply_env_vars(value: str, env_vars: Dict[str, str]) -> str:
    """Apply environment variable mapping to a value.

    Replaces any occurrence of an env-var original value with the
    ``{{var_key}}`` template syntax.
    """
    if not env_vars or not value:
        return value
    for original, var_key in env_vars.items():
        value = value.replace(original, f'{{{{{var_key}}}}}')
    return value


def _lookup_user_value(
    param_name: str,
    endpoint_key: str,
    user_answers: Dict[str, Any],
    environment_vars: Dict[str, str],
) -> str:
    """Look up a user-provided value for a given parameter.

    Checks ``user_answers`` (flat or nested under question IDs), then
    applies env-var replacement.
    """
    # Case 1: direct param_name match at top level
    if param_name in user_answers:
        raw = user_answers[param_name]
        return _apply_env_vars(str(raw), environment_vars) if not isinstance(raw, (list, dict)) else None

    # Case 2: endpoint_key|param_name format
    key = f"{endpoint_key}|{param_name}"
    if key in user_answers:
        return str(user_answers[key])

    # Case 3: scan all values for lists of param objects (multi_param answers)
    for qid, value in user_answers.items():
        if isinstance(value, list) and len(value) > 0:
            if isinstance(value[0], dict) and 'param_name' in value[0]:
                for item in value:
                    if item.get('param_name') == param_name:
                        raw = item.get('value', '')
                        return _apply_env_vars(str(raw), environment_vars) if raw else ""

    # Case 4: nested dict under recognized section keys (backward compat)
    for section_key in ("param_value", "q_param_value"):
        section = user_answers.get(section_key)
        if isinstance(section, dict):
            raw = section.get(param_name)
            if raw is not None and isinstance(raw, str):
                return _apply_env_vars(raw, environment_vars) if raw else ""

    return ""


def _build_auth(
    headers: Dict[str, str],
    user_answers: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract auth configuration from headers and user answers."""
    # Check if user specified auth type
    auth_answer = None
    for key, val in user_answers.items():
        if isinstance(val, str) and val in ("bearer", "basic", "apikey", "none"):
            auth_answer = val
            break
        if isinstance(val, dict) and val.get("type") in ("bearer", "basic", "apikey", "none"):
            auth_answer = val["type"]
            break

    if auth_answer and auth_answer != "none":
        return {"type": auth_answer}

    # Auto-detect from Authorization header
    auth_header = headers.get("Authorization", "")
    if auth_header:
        if auth_header.startswith("Bearer "):
            return {"type": "bearer", "token": auth_header[7:]}
        if auth_header.startswith("Basic "):
            return {"type": "basic", "credentials": auth_header[6:]}

    # Check for API key header
    for hdr_name in ("X-Api-Key", "api-key", "api_key"):
        if hdr_name in headers:
            return {"type": "apikey", "key": headers[hdr_name]}

    return {"type": "none"}


def generate_requests(
    parsed_endpoints: List[Dict[str, Any]],
    classification: Dict[str, Any],
    user_answers: Dict[str, Any],
    environment_vars: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Phase 3: Generate complete API request dicts from classification and user input.

    Each returned dict is ready for ``ApiRequest`` creation with keys::

        name, description, method, url, headers, params, body, auth

    Path template variables use ``{{var}}`` syntax for the test runner.
    """
    # Support both direct classification and nested format
    class_map = classification.get("classification", classification)
    requests: List[Dict[str, Any]] = []

    for ep in parsed_endpoints:
        endpoint_key = f"{ep['method'].upper()} {ep['path']}"
        classified = class_map.get(endpoint_key, {
            "auto": [], "manual": [], "context_ref": [],
        })

        # -- Build display name ------------------------------------------------
        name = (ep.get("summary") or "").strip()
        if not name:
            name = f"{ep['method'].upper()} {ep['path']}"

        description = ep.get("description", "") or ""

        # -- Replace {path_param} with {{path_param}} template syntax ----------
        url = re.sub(r"\{(\w+)\}", r"{{\1}}", ep.get("path", ""))

        # -- Build headers, params, body from classified params ----------------
        headers: Dict[str, str] = {}
        params: Dict[str, str] = {}
        body: Dict[str, Any] = {}

        for param in classified.get("auto", []):
            value = _replace_env_vars(
                _generate_auto_value(param), environment_vars,
            )
            _assign_param(param, headers, params, body, value)

        for param in classified.get("manual", []):
            value = _lookup_user_value(
                param["name"], endpoint_key, user_answers, environment_vars,
            )
            _assign_param(param, headers, params, body, value)

        for param in classified.get("context_ref", []):
            ctx_value = _lookup_user_value(
                param["name"], endpoint_key, user_answers, environment_vars,
            )
            _assign_param(param, headers, params, body, ctx_value)

        # -- Resolve auth ------------------------------------------------------
        auth = _build_auth(headers, user_answers)

        # If auth is bearer and there's no Authorization header, add one
        if auth.get("type") == "bearer" and "Authorization" not in headers:
            headers["Authorization"] = "Bearer {{API_TOKEN}}"

        requests.append({
            "name": name,
            "description": description,
            "method": ep["method"].upper(),
            "url": url,
            "headers": headers,
            "params": params,
            "body": body if body else {},
            "auth": auth,
        })

    return requests


def _assign_param(
    param: Dict[str, Any],
    headers: Dict[str, str],
    params_dict: Dict[str, str],
    body_dict: Dict[str, Any],
    value: str,
) -> None:
    """Place a parameter value into the appropriate request section."""
    location = param.get("location", "query")
    name = param["name"]

    if location == "header":
        headers[name] = value
    elif location == "path":
        # Path parameters are embedded in the URL template already
        pass
    elif location == "query":
        params_dict[name] = value
    elif location in ("body", "formdata"):
        body_dict[name] = value
