#!/usr/bin/env python3
"""
inject_token.py — 自动登录后端，把 access_token 写入 Eolink 环境变量

用法：
  python scripts/inject_token.py --config eolink_config.json --env-id 669385

流程：
  1. 用 config 里的 admin_test_account 登录后端，拿到 access_token
  2. 调用 Eolink Open API 把 token 写入指定环境的变量 admin_access_token
  3. 后续用例里用 {{admin_access_token}} 引用即可
"""

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
        return os.environ.get(s[len("{{ENV:"):-2].strip(), "")
    if s.startswith("${ENV:") and s.endswith("}") and len(s) > 7:
        return os.environ.get(s[len("${ENV:"):-1].strip(), "")
    return obj


# ─────────────────────────────────────────
# Step 1：登录后端拿 access_token
# ─────────────────────────────────────────

def fetch_access_token(base_url: str, username: str, password: str, login_path: str) -> str:
    url = base_url.rstrip("/") + login_path
    print(f"🔐 正在登录：{url}")
    resp = requests.post(
        url,
        json={"username": username, "password": password},
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    # 兼容多种返回格式：access_token / token / data.token / data.access_token
    token = (
        data.get("access_token")
        or data.get("token")
        or (data.get("data") or {}).get("access_token")
        or (data.get("data") or {}).get("token")
    )
    if not token:
        raise RuntimeError(f"登录成功但未找到 token 字段，返回内容：{list(data.keys())}")

    print(f"   ✅ 登录成功，token 前12位：{token[:12]}...")
    return token


# ─────────────────────────────────────────
# Step 2：把 token 写入 Eolink 环境变量
# ─────────────────────────────────────────

def get_env_detail(space_url: str, secret_key: str, space_id: str, project_id: str, env_id: int) -> dict:
    """拉取当前环境的完整配置（含现有变量列表）"""
    headers = {"Eo-Secret-Key": secret_key, "Content-Type": "application/json"}
    resp = requests.post(
        space_url.rstrip("/") + "/api/v2/api_studio/management/global_source/env/get",
        headers=headers,
        json={"space_id": space_id, "project_id": project_id, "env_id": env_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def upsert_env_variable(
    space_url: str,
    secret_key: str,
    space_id: str,
    project_id: str,
    env_id: int,
    var_name: str,
    var_value: str,
) -> dict:
    """
    把指定变量写入 Eolink 环境。
    先拉取现有环境配置，找到对应变量更新，或新增一条，然后调用 update 接口保存。
    """
    headers = {"Eo-Secret-Key": secret_key, "Content-Type": "application/json"}

    # 拉取现有环境详情
    detail_resp = get_env_detail(space_url, secret_key, space_id, project_id, env_id)
    print(f"   环境详情返回 status={detail_resp.get('status')!r}")

    env_info = (
        detail_resp.get("env_info")
        or (detail_resp.get("data") or {}).get("env_info")
        or {}
    )
    env_name = env_info.get("env_name") or f"env_{env_id}"
    front_uri = env_info.get("front_uri") or ""

    # 现有变量列表
    param_list = list(
        env_info.get("param_list")
        or env_info.get("variables")
        or detail_resp.get("param_list")
        or []
    )

    # 找到同名变量更新，否则追加
    found = False
    for p in param_list:
        if str(p.get("param_key", "")).lower() == var_name.lower():
            p["param_value"] = var_value
            p["checkbox"] = True
            found = True
            break
    if not found:
        param_list.append({
            "checkbox": True,
            "param_key": var_name,
            "param_value": var_value,
            "param_name": var_name,
        })

    action = "更新" if found else "新增"
    print(f"   {action}环境变量 {var_name}（共 {len(param_list)} 个变量）")

    # 调用 update 接口保存
    update_resp = requests.post(
        space_url.rstrip("/") + "/api/v2/api_studio/management/global_source/env/update",
        headers=headers,
        json={
            "space_id": space_id,
            "project_id": project_id,
            "env_id": env_id,
            "env_name": env_name,
            "front_uri": front_uri,
            "param_list": param_list,
        },
        timeout=15,
    )
    update_resp.raise_for_status()
    return update_resp.json()


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="自动登录后端并把 token 写入 Eolink 环境变量")
    parser.add_argument("--config",     default="eolink_config.json", help="配置文件路径")
    parser.add_argument("--env-id",     type=int, required=True,      help="Eolink 测试环境 ID")
    parser.add_argument("--login-path", default="/api/admin/oauth/token",
                        help="后端登录接口路径（默认 /api/admin/oauth/token）")
    parser.add_argument("--var-name",   default="admin_access_token",
                        help="写入 Eolink 环境的变量名（默认 admin_access_token）")
    parser.add_argument("--username",   default=None, help="覆盖 config 里的登录账号")
    parser.add_argument("--password",   default=None, help="覆盖 config 里的登录密码")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"❌ 配置文件不存在：{cfg_path}")
        return 2
    cfg = _resolve_env_placeholders(json.loads(cfg_path.read_text(encoding="utf-8")))

    space_url  = cfg.get("space_url")
    secret_key = cfg.get("Eo-Secret-Key") or cfg.get("eo_secret_key")
    space_id   = cfg.get("space_id")
    project_id = cfg.get("project_id")
    base_url   = cfg.get("base_url")

    admin = cfg.get("admin_test_account") or {}
    username = args.username or admin.get("username") or os.environ.get("EOLINK_ADMIN_USER", "")
    password = args.password or admin.get("password") or os.environ.get("EOLINK_ADMIN_PASS", "")

    missing = [k for k, v in {
        "space_url": space_url, "Eo-Secret-Key": secret_key,
        "space_id": space_id, "project_id": project_id,
        "base_url": base_url, "username": username, "password": password,
    }.items() if not v]
    if missing:
        print("❌ 缺少配置：" + ", ".join(missing))
        return 2

    # Step 1：登录拿 token
    token = fetch_access_token(base_url, username, password, args.login_path)

    # Step 2：写入 Eolink 环境变量
    print(f"\n📝 正在写入 Eolink 环境变量（env_id={args.env_id}，变量名={args.var_name}）...")
    result = upsert_env_variable(
        space_url, secret_key, space_id, project_id,
        args.env_id, args.var_name, token,
    )
    print(f"   update 返回 status={result.get('status')!r}")

    if result.get("status") == "success":
        print(f"\n✅ 完成！Eolink 环境变量 {{{{{{args.var_name}}}}} 已更新为最新 token")
        print(f"   现在可以执行测试用例，用例里的 {{{{{{args.var_name}}}}} 会自动替换为 Bearer token")
    else:
        print(f"\n⚠️  update 返回异常：{result}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
