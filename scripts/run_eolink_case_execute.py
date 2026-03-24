#!/usr/bin/env python3
"""
run_eolink_case_execute.py — 通过 Eolink Open API 在平台侧执行测试用例并输出报告

用途：
  已在 Eolink 平台上生成/提交好测试用例后，调用本脚本让 Eolink 后端直接执行，
  拉回执行结果并保存为 JSON 报告。

用法示例：
  # 按接口名搜索，执行该接口下所有用例
  python scripts/run_eolink_case_execute.py --config eolink_config.json --keyword 创建优惠码

  # 直接指定 api_id（跳过搜索）
  python scripts/run_eolink_case_execute.py --config eolink_config.json --api-id 12345678

  # 只执行 adjustment_type 包含 shipping 的用例
  python scripts/run_eolink_case_execute.py --config eolink_config.json --keyword 创建优惠码 --match-case shipping

  # 同时执行多个关键字对应的接口
  python scripts/run_eolink_case_execute.py --config eolink_config.json --keyword 创建优惠码 --keyword 编辑优惠码
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def _resolve_env_placeholders(obj):
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_placeholders(v) for v in obj]
    if not isinstance(obj, str):
        return obj
    s = obj.strip()
    if s.startswith("{{ENV:") and s.endswith("}}") and len(s) > 8:
        return os.environ.get(s[len("{{ENV:"):-2].strip(), "")
    if s.startswith("${ENV:") and s.endswith("}") and len(s) > 7:
        return os.environ.get(s[len("${ENV:"):-1].strip(), "")
    return obj


# ─────────────────────────────────────────
# Eolink Studio 客户端（Eo-Secret-Key 鉴权）
# ─────────────────────────────────────────

class StudioClient:
    def __init__(self, space_url: str, secret_key: str):
        self.base = space_url.rstrip("/")
        self.headers = {
            "Eo-Secret-Key": secret_key,
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict) -> dict:
        r = requests.get(self.base + path, headers=self.headers, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, payload: dict) -> dict:
        r = requests.post(self.base + path, headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()


# ─────────────────────────────────────────
# Step 1：搜索接口，拿到 api_id
# ─────────────────────────────────────────

def search_api(client: StudioClient, space_id: str, project_id: str, keyword: str) -> dict:
    data = client.post(
        "/api/v2/api_studio/management/api/search",
        {"space_id": space_id, "project_id": project_id, "page": 1, "pageSize": 50, "keyword": keyword},
    )
    if data.get("status") != "success":
        raise RuntimeError(f"api/search failed: {data}")
    results = data.get("result") or []
    if not results:
        raise RuntimeError(f"api/search 未找到关键字为 {keyword!r} 的接口")
    return results[0]


# ─────────────────────────────────────────
# Step 2：获取用例列表
# ─────────────────────────────────────────

def list_cases(client: StudioClient, space_id: str, project_id: str, api_id: int) -> list:
    data = client.get(
        "/api/v2/api_studio/management/test_case/get_list",
        {"space_id": space_id, "project_id": project_id, "apiId": api_id, "page": 1, "pageSize": 100},
    )
    return data.get("case_list") or []


# ─────────────────────────────────────────
# Step 3：执行测试用例（平台侧执行）
# ─────────────────────────────────────────

def execute_cases(
    client: StudioClient,
    space_id: str,
    project_id: str,
    api_id: int,
    case_ids: Optional[list] = None,
    env_id: Optional[int] = None,
) -> dict:
    """
    调用 Eolink execute 接口，让平台后端执行用例并返回结果。
    参考路径：POST /api/v2/api_studio/management/api_test_case/execute
    """
    payload: dict = {
        "space_id": space_id,
        "project_id": project_id,
        "api_id": api_id,
    }
    if case_ids:
        payload["case_ids"] = case_ids
    if env_id is not None:
        payload["env_id"] = env_id

    data = client.post(
        "/api/v2/api_studio/management/api_test_case/execute",
        payload,
    )
    return data


# ─────────────────────────────────────────
# Step 4：解析执行结果，格式化为统一报告
# ─────────────────────────────────────────

def _parse_execute_result(raw: dict, api_id: int, api_name: str) -> list:
    """
    将 Eolink execute 返回的原始数据解析为统一的结果列表，
    兼容 result_list / results / data 等不同字段名。
    """
    result_rows = (
        raw.get("result_list")
        or raw.get("results")
        or (raw.get("data") or {}).get("result_list")
        or (raw.get("data") or {}).get("results")
        or []
    )

    parsed = []
    for row in result_rows:
        case_name = (
            row.get("case_name")
            or row.get("caseName")
            or row.get("name")
            or f"case_{row.get('case_id', '?')}"
        )
        status_code = (
            row.get("response_code")
            or row.get("responseCode")
            or row.get("status_code")
            or row.get("statusCode")
        )
        passed_flag = row.get("is_pass") or row.get("isPass") or row.get("passed")
        if passed_flag is None and status_code is not None:
            passed_flag = int(status_code) < 400

        resp_body = (
            row.get("response_body")
            or row.get("responseBody")
            or row.get("response")
        )
        # 尝试 JSON 解析 response_body
        if isinstance(resp_body, str):
            try:
                resp_body = json.loads(resp_body)
            except Exception:
                pass

        parsed.append({
            "case_id": row.get("case_id") or row.get("caseId"),
            "case_name": case_name,
            "api_id": api_id,
            "api_name": api_name,
            "status_code": status_code,
            "passed": bool(passed_flag),
            "response_time_ms": row.get("response_time") or row.get("responseTime"),
            "response_body": resp_body,
            "error": row.get("error") or row.get("error_message"),
            "raw": row,  # 保留原始数据，便于调试
        })
    return parsed


# ─────────────────────────────────────────
# Step 5：保存报告
# ─────────────────────────────────────────

def save_report(results: list, output_path: str) -> dict:
    report = {
        "generated_at": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
    }
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 报告已保存：{output_path}")
    print(f"   总计: {report['total']}  通过: {report['passed']}  失败: {report['failed']}")
    return report


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────

def run_for_api(
    client: StudioClient,
    space_id: str,
    project_id: str,
    api_id: int,
    api_name: str,
    match_case: Optional[str],
    env_id: Optional[int],
    list_only: bool,
) -> list:
    """对单个 api_id 执行完整流程，返回结果列表"""
    print(f"\n📋 接口：{api_name}（api_id={api_id}）")

    # 拉取用例列表
    cases = list_cases(client, space_id, project_id, api_id)
    print(f"   共找到 {len(cases)} 条测试用例")

    if match_case:
        cases = [c for c in cases if match_case.lower() in str(c.get("case_name", "")).lower()]
        print(f"   匹配 '{match_case}' 后剩余 {len(cases)} 条")

    if not cases:
        print("   ⚠️  无可执行用例，跳过")
        return []

    for c in cases:
        print(f"     - [{c.get('case_id')}] {c.get('case_name')}")

    if list_only:
        return []

    # 执行
    case_ids = [int(c["case_id"]) for c in cases if c.get("case_id")]
    print(f"\n🚀 正在调用 Eolink execute 执行 {len(case_ids)} 条用例...")
    raw = execute_cases(client, space_id, project_id, api_id, case_ids=case_ids, env_id=env_id)
    print(f"   execute 返回 status={raw.get('status')!r}")

    results = _parse_execute_result(raw, api_id, api_name)

    if not results:
        # Eolink 的 execute 可能是异步的，把原始响应也记录进去
        print("   ⚠️  未解析到用例结果（可能是异步执行），原始返回已写入报告")
        results = [{
            "case_id": None,
            "case_name": "execute_raw_response",
            "api_id": api_id,
            "api_name": api_name,
            "status_code": None,
            "passed": raw.get("status") == "success",
            "response_time_ms": None,
            "response_body": raw,
            "error": None,
            "raw": raw,
        }]

    for r in results:
        icon = "✅" if r["passed"] else "❌"
        print(f"   {icon} [{r['case_id']}] {r['case_name']}  status={r['status_code']}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="通过 Eolink Open API 在平台侧执行测试用例并输出报告"
    )
    parser.add_argument("--config", default="eolink_config.json", help="配置文件路径")
    parser.add_argument("--keyword", action="append", default=None,
                        help="搜索接口关键字（可传多次，如 --keyword 创建优惠码 --keyword 编辑优惠码）")
    parser.add_argument("--api-id", action="append", type=int, default=None,
                        help="直接指定 api_id，跳过搜索（可传多次）")
    parser.add_argument("--match-case", default=None,
                        help="只执行用例名包含该关键字的用例（如 --match-case shipping）")
    parser.add_argument("--env-id", type=int, default=None,
                        help="Eolink 测试环境 ID（不传则使用接口默认环境）")
    parser.add_argument("--list-only", action="store_true",
                        help="只列出用例，不执行")
    parser.add_argument("--output", default="eolink_execute_report.json",
                        help="输出报告路径（默认 eolink_execute_report.json）")
    args = parser.parse_args()

    # 读取配置
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"❌ 配置文件不存在：{cfg_path}")
        return 2
    cfg = _resolve_env_placeholders(json.loads(cfg_path.read_text(encoding="utf-8")))

    space_url  = cfg.get("space_url")
    secret_key = cfg.get("Eo-Secret-Key") or cfg.get("eo_secret_key")
    space_id   = cfg.get("space_id")
    project_id = cfg.get("project_id")

    missing = [k for k, v in {
        "space_url": space_url, "Eo-Secret-Key": secret_key,
        "space_id": space_id, "project_id": project_id,
    }.items() if not v]
    if missing:
        print("❌ 缺少配置项：" + ", ".join(missing))
        return 2

    client = StudioClient(space_url, secret_key)

    # 收集所有要执行的接口
    api_targets: list[tuple[int, str]] = []  # (api_id, api_name)

    for kw in (args.keyword or []):
        api = search_api(client, space_id, project_id, kw)
        api_targets.append((int(api["api_id"]), api.get("api_name") or kw))

    for aid in (args.api_id or []):
        api_targets.append((aid, f"api_{aid}"))

    if not api_targets:
        print("❌ 请通过 --keyword 或 --api-id 指定要执行的接口")
        return 2

    all_results = []
    for api_id, api_name in api_targets:
        results = run_for_api(
            client, space_id, project_id,
            api_id, api_name,
            match_case=args.match_case,
            env_id=args.env_id,
            list_only=args.list_only,
        )
        all_results.extend(results)

    if not args.list_only:
        save_report(all_results, args.output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
