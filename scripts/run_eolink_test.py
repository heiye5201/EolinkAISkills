#!/usr/bin/env python3
"""
run_eolink_test.py — Eolink APIkit 自动化测试脚本
用法：
    python run_eolink_test.py \
        --api-key YOUR_EOLINK_API_KEY \
        --space-url https://xxx.w.eolink.com \
        --project-hash abcdef1234 \
        --base-url https://api.your-backend.com \
        --output eolink_test_report.json

或使用配置文件（避免在命令行暴露 API Key）：
    python run_eolink_test.py --config eolink_config.json --match login --list-only
"""

import argparse
import json
import time
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
import os
import re
import secrets
import string

try:
    import requests
except ImportError:
    print("缺少依赖：请先运行 pip install requests --break-system-packages")
    sys.exit(1)


# ─────────────────────────────────────────────
# 1. Eolink Open API 客户端
# ─────────────────────────────────────────────

class EolinkClient:
    def __init__(self, space_url: str, api_key: str):
        self.space_url = space_url.rstrip("/")
        self.headers = {
            "Eo-Authorization": api_key,
            "Content-Type": "application/json"
        }

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.space_url}{path}"
        resp = requests.post(url, json=payload, headers=self.headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Eolink 通常用 statusCode=000000 表示成功
        code = str(data.get("statusCode", data.get("code", "000000")))
        if code not in ("000000", "0", "200"):
            raise RuntimeError(f"Eolink API 错误 [{code}]: {data.get('statusInfo', data)}")
        return data

    def list_apis(self, project_hash_key: str, page: int = 1, page_size: int = 100) -> dict:
        return self._post("/api/apikit/open/project/api/list", {
            "projectHashKey": project_hash_key,
            "page": page,
            "pageSize": page_size
        })

    def get_api_detail(self, project_hash_key: str, api_hash_key: str) -> dict:
        return self._post("/api/apikit/open/project/api/detail", {
            "projectHashKey": project_hash_key,
            "apiHashKey": api_hash_key
        })

    def fetch_all_apis(self, project_hash_key: str) -> list:
        """分页拉取项目下全部接口"""
        all_apis = []
        page = 1
        while True:
            data = self.list_apis(project_hash_key, page=page)
            apis = data.get("data", {}).get("apiList", data.get("data", []))
            if not apis:
                break
            all_apis.extend(apis)
            total = data.get("data", {}).get("total", len(all_apis))
            if len(all_apis) >= total:
                break
            page += 1
        return all_apis


# ─────────────────────────────────────────────
# 2. 测试用例生成
# ─────────────────────────────────────────────

METHOD_MAP = {0: "GET", 1: "POST", 2: "PUT", 3: "DELETE", 4: "PATCH"}


_RAND_TOKEN_RE = re.compile(r"\{\{\s*RAND(?:_(?P<kind>ALNUM|DIGITS))?\s*:\s*(?P<len>\d+)\s*\}\}")


def _rand_string(length: int, *, kind: str = "ALNUM") -> str:
    if length <= 0:
        return ""
    kind = (kind or "ALNUM").upper()
    if kind == "DIGITS":
        alphabet = string.digits
    else:
        alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _materialize_dynamic_values(obj):
    """
    将测试用例中的动态占位符替换为真实值：
      - {{RAND:6}} / {{RAND_ALNUM:6}}  → 6位大写字母+数字
      - {{RAND_DIGITS:6}}            → 6位数字
    """
    if isinstance(obj, dict):
        return {k: _materialize_dynamic_values(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_materialize_dynamic_values(v) for v in obj]
    if not isinstance(obj, str):
        return obj

    def _repl(m: re.Match) -> str:
        kind = (m.group("kind") or "ALNUM").upper()
        length = int(m.group("len"))
        return _rand_string(length, kind=kind)

    return _RAND_TOKEN_RE.sub(_repl, obj)


def generate_test_case(
    api_detail: dict,
    base_url: str,
    test_account: Optional[dict] = None,
    *,
    coupon_code_rand6: bool = False,
) -> dict:
    """将 Eolink 接口详情转换为可执行的测试用例"""
    api = api_detail.get("data", api_detail)  # 兼容直接传 data 层
    method = METHOD_MAP.get(api.get("apiRequestType", 1), "POST")
    uri = api.get("apiURI", api.get("apiUri", "/"))

    params = {}
    body = {}

    for p in api.get("apiRequestParam", []):
        name = p.get("paramName", "")
        if not name:
            continue
        value: object = (
            p.get("paramDefaultValue")
            or p.get("paramValue")
            or p.get("paramExample")
            or f"<{name}>"
        )
        if coupon_code_rand6:
            lower_name = str(name).strip().lower()
            if (
                lower_name in ("code", "couponcode", "coupon_code", "promocode", "promo_code")
                or ("coupon" in lower_name and "code" in lower_name)
                or ("promo" in lower_name and "code" in lower_name)
            ):
                value = "{{RAND:6}}"
        if method == "GET":
            params[name] = value
        else:
            body[name] = value

    return {
        "test_name": api.get("apiName", uri),
        "api_hash_key": api.get("apiHashKey", ""),
        "method": method,
        "url": base_url.rstrip("/") + uri,
        "params": params,
        "body": body if method != "GET" else {},
        "expected_status": 200
    }


# ─────────────────────────────────────────────
# 3. 测试执行
# ─────────────────────────────────────────────

def _resolve_env_placeholders(obj):
    """
    支持在 config 中用占位符引用环境变量，避免把密码写入文件：
      - {{ENV:VAR_NAME}}
      - ${ENV:VAR_NAME}
    """
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


def _apply_login_account_override(test_case: dict, test_account: Optional[dict]) -> dict:
    if not test_account:
        return test_case
    url = str(test_case.get("url", "")).lower()
    name = str(test_case.get("test_name", "")).lower()
    if "login" not in url and "登录" not in name and "login" not in name:
        return test_case
    user_name = test_account.get("user_name")
    user_password = test_account.get("user_password")
    user_type = test_account.get("user_type")

    if test_case.get("method") == "GET":
        params = dict(test_case.get("params") or {})
        if user_name:
            params["user_name"] = user_name
        if user_password:
            params["user_password"] = user_password
        if user_type:
            params["user_type"] = user_type
        test_case["params"] = params
        return test_case

    body = dict(test_case.get("body") or {})
    if user_name:
        body["user_name"] = user_name
    if user_password:
        body["user_password"] = user_password
    if user_type:
        body["user_type"] = user_type
    test_case["body"] = body
    return test_case


def run_test(test_case: dict, extra_headers: dict = None) -> dict:
    """执行单个测试，返回结构化结果"""
    materialized = dict(test_case)
    materialized["params"] = _materialize_dynamic_values(materialized.get("params") or {})
    materialized["body"] = _materialize_dynamic_values(materialized.get("body") or {})

    start = time.time()
    try:
        resp = requests.request(
            method=materialized["method"],
            url=materialized["url"],
            params=materialized.get("params") or None,
            json=materialized.get("body") or None,
            headers=extra_headers or {},
            timeout=15
        )
        elapsed = round(time.time() - start, 3)
        return {
            "test_name": test_case["test_name"],
            "method": materialized["method"],
            "url": materialized["url"],
            "status_code": resp.status_code,
            "passed": resp.status_code == test_case.get("expected_status", 200),
            "response_time_s": elapsed,
            "response_body": _safe_json(resp),
            "error": None
        }
    except Exception as e:
        return {
            "test_name": test_case["test_name"],
            "method": test_case["method"],
            "url": test_case["url"],
            "status_code": None,
            "passed": False,
            "response_time_s": round(time.time() - start, 3),
            "response_body": None,
            "error": str(e)
        }


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return resp.text[:500]


# ─────────────────────────────────────────────
# 4. 报告输出
# ─────────────────────────────────────────────

def save_report(results: list, output_path: str) -> dict:
    report = {
        "generated_at": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 报告已保存：{output_path}")
    print(f"   总计: {report['total']}  通过: {report['passed']}  失败: {report['failed']}")
    return report


# ─────────────────────────────────────────────
# 5. 主流程
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Eolink APIkit 自动化测试工具")
    parser.add_argument("--config",        default=None, help="从 JSON 配置文件读取参数（如 eolink_config.json）")
    parser.add_argument("--api-key",       default=None, help="Eolink Open API Key（可选：也可从 --config 读取）")
    parser.add_argument("--space-url",     default=None, help="Eolink 空间地址，如 https://xxx.w.eolink.com（可选：也可从 --config 读取）")
    parser.add_argument("--project-hash",  default=None, help="项目 Hash Key（可选：也可从 --config 读取；兼容 config 中的 project_id）")
    parser.add_argument("--base-url",      default=None, help="被测后端根地址，如 https://api.example.com（可选：也可从 --config 读取）")
    parser.add_argument("--output",        default="eolink_test_report.json", help="输出 JSON 报告路径")
    parser.add_argument("--auth-header",   default=None, help="后端鉴权 Header，格式: 'HeaderName:HeaderValue'")
    parser.add_argument("--match",         action="append", default=None, help="仅测试匹配的接口（对接口名/URI 进行包含匹配，可传多次）")
    parser.add_argument("--list-only",     action="store_true", help="只列出匹配到的接口，不执行请求")
    parser.add_argument("--no-run",        action="store_true", help="只生成测试用例，不执行请求")
    parser.add_argument("--export-cases",  default=None, help="导出生成的测试用例 JSON（不会包含响应结果）")
    parser.add_argument("--coupon-code-rand6", action="store_true", help="对疑似优惠码字段自动填充 {{RAND:6}}（6位随机字符串）")
    args = parser.parse_args()

    cfg = {}
    if args.config:
        cfg_path = Path(args.config)
        try:
            cfg = _resolve_env_placeholders(json.loads(cfg_path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"❌ 读取配置失败：{cfg_path}：{e}")
            sys.exit(2)

    api_key = args.api_key or cfg.get("api_key") or cfg.get("apiKey")
    space_url = args.space_url or cfg.get("space_url") or cfg.get("spaceUrl")
    project_hash = (
        args.project_hash
        or cfg.get("project_hash")
        or cfg.get("projectHash")
        or cfg.get("project_id")   # 历史字段名
        or cfg.get("projectId")
    )
    base_url = args.base_url or cfg.get("base_url") or cfg.get("baseUrl")
    auth_header = args.auth_header if args.auth_header is not None else cfg.get("auth_header") or cfg.get("authHeader")
    test_account = cfg.get("test_account") or cfg.get("testAccount")
    coupon_code_rand6 = bool(args.coupon_code_rand6 or cfg.get("coupon_code_rand6") or cfg.get("couponCodeRand6"))

    missing = [k for k, v in {
        "--api-key": api_key,
        "--space-url": space_url,
        "--project-hash": project_hash,
        "--base-url": base_url,
    }.items() if not v]
    if missing:
        print("❌ 缺少必要参数：" + ", ".join(missing))
        print("   你可以：")
        print("   1) 直接传参运行；或")
        print("   2) 使用 --config eolink_config.json（包含 api_key/space_url/project_id/base_url）。")
        sys.exit(2)

    # 构造后端鉴权 Header
    extra_headers = {}
    if auth_header and ":" in auth_header:
        k, v = auth_header.split(":", 1)
        extra_headers[k.strip()] = v.strip()

    client = EolinkClient(space_url, api_key)

    # 步骤1：拉取全部接口
    print(f"📡 正在从 Eolink 拉取接口列表（项目：{project_hash}）...")
    try:
        api_list = client.fetch_all_apis(project_hash)
    except Exception as e:
        print(f"❌ 拉取接口列表失败：{e}")
        sys.exit(1)
    print(f"   共找到 {len(api_list)} 个接口")

    match_terms = [m for m in (args.match or []) if m and str(m).strip()]
    if match_terms:
        lowered_terms = [t.lower() for t in match_terms]

        def _matched(api_summary: dict) -> bool:
            name = str(api_summary.get("apiName", "")).lower()
            uri = str(api_summary.get("apiURI", api_summary.get("apiUri", ""))).lower()
            return any(t in name or t in uri for t in lowered_terms)

        api_list = [a for a in api_list if _matched(a)]
        print(f"🔎 匹配条件：{', '.join(match_terms)}  → 命中 {len(api_list)} 个接口")

    if args.list_only:
        for i, api_summary in enumerate(api_list, 1):
            name = api_summary.get("apiName", "")
            uri = api_summary.get("apiURI", api_summary.get("apiUri", ""))
            hash_key = api_summary.get("apiHashKey", api_summary.get("hashKey", ""))
            print(f"{i}. {name}  {uri}  ({hash_key})")
        return

    # 步骤2：逐个获取详情并生成测试用例
    test_cases = []
    for api_summary in api_list:
        hash_key = api_summary.get("apiHashKey", api_summary.get("hashKey", ""))
        if not hash_key:
            continue
        try:
            detail = client.get_api_detail(project_hash, hash_key)
            tc = generate_test_case(detail, base_url, test_account=test_account, coupon_code_rand6=coupon_code_rand6)
            tc = _apply_login_account_override(tc, test_account)
            test_cases.append(tc)
        except Exception as e:
            print(f"   ⚠️  跳过接口 {hash_key}：{e}")

    print(f"📝 已生成 {len(test_cases)} 条测试用例")

    if args.export_cases:
        out_path = Path(args.export_cases)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(test_cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📦 已导出测试用例：{out_path}")

    if args.no_run:
        return

    # 步骤3：执行测试
    results = []
    for i, tc in enumerate(test_cases, 1):
        print(f"   [{i}/{len(test_cases)}] {tc['method']} {tc['url']} ...", end=" ")
        result = run_test(tc, extra_headers)
        status = "✅" if result["passed"] else "❌"
        print(f"{status} {result.get('status_code', 'ERR')} ({result['response_time_s']}s)")
        results.append(result)

    # 步骤4：保存报告
    save_report(results, args.output)


if __name__ == "__main__":
    main()
