#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

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
        var = s[len("{{ENV:") : -2].strip()
        return os.environ.get(var, "")
    if s.startswith("${ENV:") and s.endswith("}") and len(s) > 7:
        var = s[len("${ENV:") : -1].strip()
        return os.environ.get(var, "")
    return obj


class StudioClient:
    def __init__(self, space_url: str, secret_key: str):
        self.base = space_url.rstrip("/")
        self.headers = {"Eo-Secret-Key": secret_key, "Content-Type": "application/json"}

    def get(self, path: str, params: dict) -> dict:
        r = requests.get(self.base + path, headers=self.headers, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = requests.post(self.base + path, headers=self.headers, json=payload, timeout=20)
        r.raise_for_status()
        return r.json()


def _search_api(client: StudioClient, space_id: str, project_id: str, keyword: str) -> dict:
    data = client.post(
        "/api/v2/api_studio/management/api/search",
        {"space_id": space_id, "project_id": project_id, "page": 1, "pageSize": 50, "keyword": keyword},
    )
    if data.get("status") != "success":
        raise RuntimeError(f"api/search failed: {data}")
    results = data.get("result") or []
    if not results:
        raise RuntimeError(f"api/search no result for keyword={keyword!r}")
    return results[0]


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


def _upsert_form_param(case_data: dict, key: str, value: str) -> None:
    params = list(case_data.get("params") or [])
    for p in params:
        if str(p.get("param_key", "")).lower() == key.lower():
            p["checkbox"] = True
            # Eolink Studio 的用例数据里，实际用于生成请求体的字段通常是 `param_info`；
            # `param_value` 在部分场景下只是“输入框值/覆盖值”。两者同时写入更稳妥。
            p["param_info"] = value
            p["param_value"] = value
            case_data["params"] = params
            return
    params.append(
        {
            "checkbox": True,
            "param_key": key,
            "param_info": value,
            "param_value": value,
            "param_name": "",
        }
    )
    case_data["params"] = params


def _get_adjustment_type_options(client: StudioClient, *, space_id: str, project_id: str, api_id: int) -> list:
    # 这个接口在当前 Eolink 版本上需要 GET + query params（POST 会报 api_id 为空）
    data = client.get(
        "/api/v2/api_studio/management/api/get_api",
        {"space_id": space_id, "project_id": project_id, "api_id": api_id},
    )
    if data.get("status") != "success":
        return []
    result = data.get("result") or {}
    values = []

    def walk(o):
        if isinstance(o, dict):
            if str(o.get("param_key", "")).lower() == "adjustment_type":
                for item in (o.get("param_value_list") or []):
                    values.append(
                        {
                            "value": item.get("value"),
                            "desc": item.get("value_description"),
                        }
                    )
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(result)

    seen = set()
    uniq = []
    for v in values:
        if not v.get("value") or v["value"] in seen:
            continue
        seen.add(v["value"])
        uniq.append(v)
    return uniq


def _make_before_script(var_name: str, length: int) -> str:
    length = int(length)
    if length <= 0:
        length = 6
    return f"""function __rand(len) {{
  var chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  var s = '';
  for (var i = 0; i < len; i++) {{
    s += chars.charAt(Math.floor(Math.random() * chars.length));
  }}
  return s;
}}
var v = __rand({length});
if (typeof pm !== 'undefined' && pm.environment && pm.environment.set) {{ pm.environment.set('{var_name}', v); }}
if (typeof pm !== 'undefined' && pm.variables && pm.variables.set) {{ pm.variables.set('{var_name}', v); }}
"""


def _update_case_for_coupon(case_info: dict, *, code_value: str, var_name: str, length: int) -> dict:
    case_data = case_info.get("case_data") or {}
    _upsert_form_param(case_data, "code", code_value)

    # Also set script holders if present
    script = case_data.get("script") or {}
    if isinstance(script, dict):
        script["before"] = _make_before_script(var_name, length)
        case_data["script"] = script

    # Update the first custom before-script step if it exists
    before_list = list(case_info.get("before_script_list") or [])
    if before_list and isinstance(before_list[0], dict):
        before_list[0]["script"] = _make_before_script(var_name, length)
        case_info["before_script_list"] = before_list
        case_info["before_script_mode"] = case_info.get("before_script_mode") or 2

    case_info["case_data"] = case_data
    return case_info


def main():
    parser = argparse.ArgumentParser(description="在 Eolink API Studio 中创建/更新“创建优惠码”测试用例（随机6位 code）")
    parser.add_argument("--config", default="eolink_config.json", help="配置文件路径")
    parser.add_argument("--keyword", default="创建优惠码", help="用于搜索接口的关键字")
    parser.add_argument("--api-id", type=int, default=None, help="直接指定 api_id（跳过搜索）")
    parser.add_argument("--case-name", default="创建优惠码-自动生成", help="用例名称（单条用例）")
    parser.add_argument(
        "--adjustment-types",
        default="percentage,flat,m_for_n,buy_x_get_y_discount",
        help="为 adjustment_type 批量创建用例，逗号分隔（默认4种）",
    )
    parser.add_argument(
        "--create-adjustment-type-cases",
        action="store_true",
        help="为每个 adjustment_type 创建一条用例（用例名为 <case-name>-<type>）",
    )
    parser.add_argument("--code-len", type=int, default=6, help="优惠码随机长度（默认 6）")
    parser.add_argument("--code-var", default="{{coupon_code}}", help="写入请求 code 的变量占位符")
    parser.add_argument("--var-name", default="coupon_code", help="脚本里写入的变量名")
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

    if args.api_id:
        api_id = int(args.api_id)
        api_path = None
        api_name = None
    else:
        api = _search_api(client, space_id, project_id, args.keyword)
        api_id = int(api["api_id"])
        api_path = api.get("api_path")
        api_name = api.get("api_name")

    def upsert_one(case_name: str, *, adjustment_type: Optional[str] = None) -> dict:
        case_id = _ensure_case(client, space_id, project_id, api_id, case_name)
        info = client.get(
            "/api/v2/api_studio/management/test_case/get_info",
            {"space_id": space_id, "project_id": project_id, "case_id": case_id},
        )
        if info.get("status") != "success":
            raise RuntimeError(f"get_info failed: {info}")

        case_info = info["case_info"]
        case_info = _update_case_for_coupon(
            case_info,
            code_value=args.code_var,
            var_name=args.var_name,
            length=args.code_len,
        )
        if adjustment_type:
            case_data = case_info.get("case_data") or {}
            _upsert_form_param(case_data, "adjustment_type", adjustment_type)
            case_info["case_data"] = case_data

        payload = {
            "space_id": space_id,
            "project_id": project_id,
            "case_id": case_id,
            "case_name": case_info.get("case_name"),
            "priority": case_info.get("priority", "P0"),
            "case_data": case_info.get("case_data"),
            "before_script_mode": case_info.get("before_script_mode"),
            "before_script_list": case_info.get("before_script_list"),
            "after_script_mode": case_info.get("after_script_mode"),
            "after_script_list": case_info.get("after_script_list"),
            "status_code_verification": case_info.get("status_code_verification"),
            "response_time_verification": case_info.get("response_time_verification"),
            "response_result_verification": case_info.get("response_result_verification"),
        }
        edited = client.post("/api/v2/api_studio/management/test_case/edit", payload)
        if edited.get("status") not in (None, "success"):
            raise RuntimeError(f"edit failed: {edited}")
        return {"case_id": case_id, "case_name": case_name, "adjustment_type": adjustment_type}

    results = []
    if args.create_adjustment_type_cases:
        types = [t.strip() for t in str(args.adjustment_types).split(",") if t and t.strip()]
        if not types:
            raise RuntimeError("adjustment-types is empty")
        # best-effort: print options from API detail for debugging
        options = _get_adjustment_type_options(client, space_id=space_id, project_id=project_id, api_id=api_id)
        if options:
            print("ℹ️ adjustment_type options:", json.dumps(options, ensure_ascii=False))
        for t in types:
            results.append(upsert_one(f"{args.case_name}-{t}", adjustment_type=t))
    else:
        results.append(upsert_one(args.case_name))

    out = {
        "status": "success",
        "api_id": api_id,
        "api_name": api_name,
        "api_path": api_path,
        "cases": results,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
