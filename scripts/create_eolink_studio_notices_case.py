#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

import requests


def _resolve_env_placeholders(obj):
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_placeholders(v) for v in obj]
    if not isinstance(obj, str):
        return obj
    s = obj.strip()
    if s.startswith("{{ENV:") and s.endswith("}}") and len(s) > 8:
        var = s[len("{{ENV:"):-2].strip()
        return os.environ.get(var, "")
    if s.startswith("${ENV:") and s.endswith("}") and len(s) > 7:
        var = s[len("${ENV:"):-1].strip()
        return os.environ.get(var, "")
    return obj


class StudioClient:
    def __init__(self, space_url: str, secret_key: str):
        self.base = space_url.rstrip("/")
        self.headers = {"Eo-Secret-Key": secret_key, "Content-Type": "application/json"}

    def get(self, path: str, params: dict) -> dict:
        r = requests.get(self.base + path, headers=self.headers, params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = requests.post(self.base + path, headers=self.headers, json=payload, timeout=15)
        r.raise_for_status()
        return r.json()


def _ensure_case(client: StudioClient, space_id: str, project_id: str, api_id: int, case_name: str) -> int:
    lst = client.get(
        "/api/v2/api_studio/management/test_case/get_list",
        {"space_id": space_id, "project_id": project_id, "apiId": api_id, "page": 1, "pageSize": 50},
    )
    for c in lst.get("case_list") or []:
        if c.get("case_name") == case_name:
            return int(c["case_id"])

    created = client.post(
        "/api/v2/api_studio/management/test_case/add",
        {"space_id": space_id, "project_id": project_id, "api_id": api_id, "case_name": case_name},
    )
    if created.get("status") != "success":
        raise RuntimeError(f"create failed: {created}")
    return int(created["case_id"])


def _update_case_for_notices(case_info: dict, token_placeholder: str) -> dict:
    case_data = case_info.get("case_data") or {}

    # Move pagination fields from headers to query params if present.
    existing_headers = list(case_data.get("headers") or [])
    kept_headers = []
    for h in existing_headers:
        hn = str(h.get("header_name", "")).lower()
        if hn in ("page", "page_size", "type"):
            continue
        kept_headers.append(h)

    # Ensure Authorization header exists
    kept_headers.append(
        {
            "checkbox": True,
            "header_name": "Authorization",
            "header_value": f"Bearer {token_placeholder}",
            "param_name": "鉴权",
        }
    )
    case_data["headers"] = kept_headers

    # Query params
    params = list(case_data.get("params") or [])
    def upsert_param(key: str, value: str, name: str):
        for p in params:
            if str(p.get("param_key", "")).lower() == key.lower():
                p["checkbox"] = True
                p["param_value"] = value
                p["param_name"] = name
                return
        params.append({"checkbox": True, "param_key": key, "param_value": value, "param_name": name})

    upsert_param("page", "1", "第一页")
    upsert_param("page_size", "20", "页数")
    upsert_param("type", "all", "类型")
    case_data["params"] = params

    case_info["case_data"] = case_data
    return case_info


def main():
    parser = argparse.ArgumentParser(description="在 Eolink API Studio 中创建/更新“用户消息”测试用例")
    parser.add_argument("--config", default="eolink_config.json", help="配置文件路径")
    parser.add_argument("--api-id", type=int, default=56426751, help="用户消息接口 apiId（默认 56426751）")
    parser.add_argument("--case-name", default="用户消息-自动生成", help="用例名称")
    parser.add_argument("--token-var", default="{{admin_access_token}}", help="Authorization 占位符变量（Eolink 环境变量）")
    args = parser.parse_args()

    cfg = _resolve_env_placeholders(json.loads(Path(args.config).read_text(encoding="utf-8")))
    space_url = cfg.get("space_url")
    secret_key = cfg.get("Eo-Secret-Key") or cfg.get("eo_secret_key")
    space_id = cfg.get("space_id")
    project_id = cfg.get("project_id")

    missing = [k for k, v in {"space_url": space_url, "Eo-Secret-Key": secret_key, "space_id": space_id, "project_id": project_id}.items() if not v]
    if missing:
        print("❌ 缺少配置：" + ", ".join(missing))
        return 2

    client = StudioClient(space_url, secret_key)
    case_id = _ensure_case(client, space_id, project_id, args.api_id, args.case_name)

    info = client.get(
        "/api/v2/api_studio/management/test_case/get_info",
        {"space_id": space_id, "project_id": project_id, "case_id": case_id},
    )
    if info.get("status") != "success":
        raise RuntimeError(f"get_info failed: {info}")

    case_info = info["case_info"]
    case_info = _update_case_for_notices(case_info, args.token_var)

    # Persist changes
    payload = {
        "space_id": space_id,
        "project_id": project_id,
        "case_id": case_id,
        "case_name": case_info.get("case_name"),
        "priority": case_info.get("priority", "P0"),
        "case_data": case_info.get("case_data"),
        "status_code_verification": case_info.get("status_code_verification"),
        "response_time_verification": case_info.get("response_time_verification"),
        "response_result_verification": case_info.get("response_result_verification"),
    }
    edited = client.post("/api/v2/api_studio/management/test_case/edit", payload)
    if edited.get("status") not in (None, "success"):
        raise RuntimeError(f"edit failed: {edited}")

    print(json.dumps({"status": "success", "case_id": case_id, "case_name": args.case_name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

