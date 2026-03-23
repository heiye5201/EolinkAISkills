#!/usr/bin/env python3
import argparse
import json
import os
import sys
import getpass
import re
from typing import Optional, Any, Dict
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"


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


def _request_headers(content_type: str, user_agent: Optional[str]):
    return {
        "Content-Type": content_type,
        "Accept": "application/json, text/plain, */*",
        "User-Agent": user_agent or DEFAULT_UA,
    }


def _post_json(url: str, payload: dict, timeout_s: int, user_agent: Optional[str]):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers=_request_headers("application/json", user_agent),
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout_s)


def _post_form(url: str, payload: dict, timeout_s: int, user_agent: Optional[str]):
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        headers=_request_headers("application/x-www-form-urlencoded", user_agent),
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout_s)


def _read_body(resp, limit=2000):
    raw = resp.read(limit) if limit is not None else resp.read()
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return repr(raw)


def _safe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _redact_tokens(obj):
    if not isinstance(obj, dict):
        return obj
    redacted = dict(obj)
    for k in ("access_token", "refresh_token"):
        v = redacted.get(k)
        if isinstance(v, str) and v:
            redacted[k] = v[:12] + "...(redacted,len=" + str(len(v)) + ")"
    return redacted


def _redact_tokens_in_text(text: str) -> str:
    text = re.sub(
        r'("access_token"\s*:\s*")([^"]+)(")',
        lambda m: f'{m.group(1)}{m.group(2)[:12]}...(redacted,len={len(m.group(2))}){m.group(3)}',
        text,
    )
    text = re.sub(
        r'("refresh_token"\s*:\s*")([^"]+)(")',
        lambda m: f'{m.group(1)}{m.group(2)[:12]}...(redacted,len={len(m.group(2))}){m.group(3)}',
        text,
    )
    return text


def _get(url: str, timeout_s: int, user_agent: Optional[str], extra_headers: Optional[dict] = None):
    headers = _request_headers("application/json", user_agent)
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url=url, headers=headers, method="GET")
    return urllib.request.urlopen(req, timeout=timeout_s)


def _assert(cond: bool, msg: str) -> Optional[str]:
    return None if cond else msg


def _assert_notices_schema(obj: Any) -> list:
    if not isinstance(obj, dict):
        return ["response is not a JSON object"]
    errors = []
    errors.append(_assert("data" in obj, "missing key: data"))
    errors.append(_assert("links" in obj, "missing key: links"))
    errors.append(_assert("meta" in obj, "missing key: meta"))

    data = obj.get("data")
    if data is not None:
        errors.append(_assert(isinstance(data, list), "data is not an array"))

    meta = obj.get("meta")
    if isinstance(meta, dict):
        unread = meta.get("unread_count")
        if unread is not None:
            errors.append(_assert(isinstance(unread, (int, float)), "meta.unread_count is not a number"))

    return [e for e in errors if e]


def _print_key_headers(headers):
    if not headers:
        return
    keys = [
        "server",
        "cf-ray",
        "cf-cache-status",
        "set-cookie",
        "location",
        "x-request-id",
        "x-correlation-id",
        "x-powered-by",
    ]
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    picked = {k: lowered.get(k) for k in keys if lowered.get(k)}
    if picked:
        print("headers:", json.dumps(picked, ensure_ascii=False))


def _try_one(base_url: str, json_path: str, form_path: str, account: dict, timeout_s: int, user_agent: Optional[str]) -> int:
    base_url = base_url.rstrip("/")
    json_url = f"{base_url}{json_path}"
    form_url = f"{base_url}{form_path}"

    payload = {
        "user_name": account.get("user_name", ""),
        "user_password": account.get("user_password", ""),
        "user_type": account.get("user_type", "normal"),
    }

    print(f"\n== BASE_URL: {base_url} ==")
    rc = 0

    for name, url, fn in (
        ("login_json", json_url, _post_json),
        ("login_form", form_url, _post_form),
    ):
        try:
            resp = fn(url, payload, timeout_s, user_agent)
            status = getattr(resp, "status", None) or resp.getcode()
            body = _read_body(resp)
            print(f"[{name}] {status} {url}")
            print(body)
            if int(status) >= 400:
                rc = 2
        except urllib.error.HTTPError as e:
            body = e.read(2000).decode("utf-8", errors="replace")
            print(f"[{name}] {e.code} {url}")
            _print_key_headers(getattr(e, "headers", None))
            print(body)
            rc = 2
        except Exception as e:
            print(f"[{name}] ERR {url} ({e})")
            rc = 2

    return rc


def main():
    parser = argparse.ArgumentParser(description="直接测试登录接口（不依赖 Eolink OpenAPI 拉取接口列表）")
    parser.add_argument("--config", default="eolink_config.json", help="配置文件路径（默认 eolink_config.json）")
    parser.add_argument("--base-url", default=None, help="覆盖配置中的 base_url")
    parser.add_argument("--json-path", default="/api/admin/oauth/token", help="登录接口 path（默认后台 OAuth 登录：/api/admin/oauth/token）")
    parser.add_argument("--me-path", default="/api/v2/admin/me", help="用户详情 path（OAuth 登录成功后会请求）")
    parser.add_argument("--notices-path", default="/api/v2/admin/me/notices", help="用户消息 path（OAuth 登录成功后可请求）")
    parser.add_argument("--notices", action="store_true", help="登录成功后额外请求用户消息接口")
    parser.add_argument("--notices-page", default="1", help="用户消息页码（默认 1）")
    parser.add_argument("--notices-page-size", default="20", help="用户消息分页大小（默认 20）")
    parser.add_argument("--notices-type", default="all", help="用户消息类型（默认 all）")
    parser.add_argument("--timeout", type=int, default=15, help="超时秒数")
    parser.add_argument("--also-try-api-prefix", action="store_true", help="额外尝试 base_url + /api")
    parser.add_argument("--use-sample-account", action="store_true", help="使用示例账号 eolink/123456（不读取 config）")
    parser.add_argument("--user-agent", default=DEFAULT_UA, help="请求 UA（用于绕过部分 WAF 误杀）")
    parser.add_argument("--admin-username", default=None, help="后台登录账号（优先于环境变量 EOLINK_ADMIN_USER）")
    parser.add_argument("--admin-password", default=None, help="后台登录密码（优先于环境变量 EOLINK_ADMIN_PASS；不建议明文传参）")
    parser.add_argument("--no-prompt", action="store_true", help="缺少账号/密码时不交互提示（直接用空值请求）")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    try:
        cfg = _resolve_env_placeholders(json.loads(cfg_path.read_text(encoding="utf-8")))
    except Exception as e:
        print(f"❌ 读取配置失败：{cfg_path}：{e}")
        return 2

    base_url = args.base_url or cfg.get("base_url") or cfg.get("baseUrl")
    if not base_url:
        print("❌ 缺少 base_url（可在 config 里配置 base_url 或使用 --base-url 覆盖）")
        return 2

    if args.use_sample_account:
        account = {"user_name": "eolink", "user_password": "123456", "user_type": "normal"}
    else:
        account = cfg.get("test_account") or cfg.get("testAccount") or {}

    admin_account = cfg.get("admin_test_account") or cfg.get("adminTestAccount") or {}
    admin_username = args.admin_username or admin_account.get("username") or os.environ.get("EOLINK_ADMIN_USER", "")
    admin_password = args.admin_password or admin_account.get("password") or os.environ.get("EOLINK_ADMIN_PASS", "")

    if "/oauth/token" not in (args.json_path or ""):
        if not account.get("user_name") or not account.get("user_password"):
            print("⚠️  未检测到 test_account.user_name / user_password（当前会用空值发起请求）。")
            print("   建议用环境变量提供：EOLINK_TEST_USER / EOLINK_TEST_PASS（config 里已默认使用占位符）。")

    # oauth/token 通常使用 username/password 字段
    if "/oauth/token" in (args.json_path or ""):
        if (not admin_username or not admin_password) and not args.no_prompt:
            if not admin_username:
                admin_username = input("Admin username: ").strip()
            if not admin_password:
                admin_password = getpass.getpass("Admin password: ").strip()

        if not admin_username or not admin_password:
            print("⚠️  未检测到 admin_test_account.username / password（oauth/token 可能会失败）。")
            print("   建议用环境变量提供：EOLINK_ADMIN_USER / EOLINK_ADMIN_PASS，或使用 --admin-username/--admin-password。")
        rc = 0
        base_url = base_url.rstrip("/")
        url = f"{base_url}{args.json_path}"
        payload = {
            "username": admin_username or "",
            "password": admin_password or "",
        }
        print(f"\n== BASE_URL: {base_url} ==")
        try:
            resp = _post_json(url, payload, args.timeout, args.user_agent)
            status = getattr(resp, "status", None) or resp.getcode()
            body = _read_body(resp, limit=2_000_000)
            print(f"[admin_oauth_token] {status} {url}")
            parsed = _safe_json(body)
            if parsed is not None:
                print(json.dumps(_redact_tokens(parsed), ensure_ascii=False))
            else:
                safe = _redact_tokens_in_text(body)
                print(safe[:2000] + ("...(truncated)" if len(safe) > 2000 else ""))
            if int(status) >= 400:
                rc = 2
            else:
                access_token = ""
                if isinstance(parsed, dict):
                    access_token = str(parsed.get("access_token", "")).strip()
                if access_token:
                    me_url = f"{base_url}{args.me_path}"
                    print(f"[admin_me] GET {me_url}")
                    try:
                        me_resp = _get(me_url, args.timeout, args.user_agent, {"Authorization": f"Bearer {access_token}"})
                        me_status = getattr(me_resp, "status", None) or me_resp.getcode()
                        me_body = _read_body(me_resp, limit=2000)
                        print(f"[admin_me] {me_status} {me_url}")
                        print(me_body)
                        if int(me_status) >= 400:
                            rc = 2
                    except urllib.error.HTTPError as e:
                        me_body = e.read(2000).decode("utf-8", errors="replace")
                        print(f"[admin_me] {e.code} {me_url}")
                        _print_key_headers(getattr(e, "headers", None))
                        print(me_body)
                        rc = 2
                    except Exception as e:
                        print(f"[admin_me] ERR {me_url} ({e})")
                        rc = 2

                    if args.notices:
                        qs = urllib.parse.urlencode(
                            {
                                "page": args.notices_page,
                                "page_size": args.notices_page_size,
                                "type": args.notices_type,
                            }
                        )
                        notices_url = f"{base_url}{args.notices_path}?{qs}"
                        headers = {
                            "Authorization": f"Bearer {access_token}",
                            # 文档里把这些写成 header 参数；这里同时作为 header 发送，兼容后端实现
                            "page": str(args.notices_page),
                            "page_size": str(args.notices_page_size),
                            "type": str(args.notices_type),
                        }
                        print(f"[admin_notices] GET {notices_url}")
                        try:
                            n_resp = _get(notices_url, args.timeout, args.user_agent, headers)
                            n_status = getattr(n_resp, "status", None) or n_resp.getcode()
                            n_body = _read_body(n_resp, limit=2_000_000)
                            print(f"[admin_notices] {n_status} {notices_url}")
                            print(n_body[:2000] + ("...(truncated)" if len(n_body) > 2000 else ""))
                            if int(n_status) >= 400:
                                rc = 2
                            else:
                                n_json = _safe_json(n_body)
                                schema_errors = _assert_notices_schema(n_json)
                                if schema_errors:
                                    print("[admin_notices] schema_errors:", "; ".join(schema_errors))
                                    rc = 2
                        except urllib.error.HTTPError as e:
                            n_body = e.read(2000).decode("utf-8", errors="replace")
                            print(f"[admin_notices] {e.code} {notices_url}")
                            _print_key_headers(getattr(e, "headers", None))
                            print(n_body)
                            rc = 2
                        except Exception as e:
                            print(f"[admin_notices] ERR {notices_url} ({e})")
                            rc = 2
        except urllib.error.HTTPError as e:
            body = e.read(2000).decode("utf-8", errors="replace")
            print(f"[admin_oauth_token] {e.code} {url}")
            _print_key_headers(getattr(e, "headers", None))
            print(body)
            rc = 2
        except Exception as e:
            print(f"[admin_oauth_token] ERR {url} ({e})")
            rc = 2
        if args.also_try_api_prefix:
            url2 = f"{base_url}/api{args.json_path}"
            try:
                resp = _post_json(url2, payload, args.timeout, args.user_agent)
                status = getattr(resp, "status", None) or resp.getcode()
                body = _read_body(resp)
                print(f"[admin_oauth_token(api)] {status} {url2}")
                print(body)
                if int(status) >= 400:
                    rc = 2
            except urllib.error.HTTPError as e:
                body = e.read(2000).decode("utf-8", errors="replace")
                print(f"[admin_oauth_token(api)] {e.code} {url2}")
                _print_key_headers(getattr(e, "headers", None))
                print(body)
                rc = 2
            except Exception as e:
                print(f"[admin_oauth_token(api)] ERR {url2} ({e})")
                rc = 2
        return rc

    rc = _try_one(base_url, args.json_path, args.form_path, account, args.timeout, args.user_agent)
    if args.also_try_api_prefix:
        rc2 = _try_one(base_url.rstrip("/") + "/api", args.json_path, args.form_path, account, args.timeout, args.user_agent)
        rc = max(rc, rc2)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
