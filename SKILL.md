---
name: eolink
description: 用于对接 Eolink APIkit 平台的自动化 API 测试 skill。当用户希望从 Eolink 拉取接口文档、自动生成测试用例、执行 API 测试并将结果输出为 JSON 文件时，必须使用此 skill。触发关键词包括：Eolink 测试、APIkit 测试、从 Eolink 跑测试、获取 Eolink 接口、Eolink API Key、自动化接口测试、Eolink 接口文档测试、Eolink open API。只要涉及 Eolink 平台的接口读取或测试执行，请始终使用此 skill。
---

# Eolink 自动化 API 测试 Skill

## 概述

本 skill 帮助 Claude 完成以下完整流程：

1. **认证** — 使用 API Key 调用 Eolink Open API 鉴权
2. **拉取接口文档** — 获取 Eolink 项目下的接口列表及详情
3. **生成测试用例** — 根据接口定义（method、path、params、body）自动生成测试用例
4. **执行测试** — 使用 Python `requests` 向真实后端发送请求
5. **输出 JSON 报告** — 将测试结果保存为结构化 JSON 文件

---

## 环境前置检查

在开始前，确认以下信息已由用户提供：

| 信息 | 说明 |
|------|------|
| `EOLINK_API_KEY` | Eolink 平台的 Open API Key（在 Eolink 控制台 → 账户设置 → Open API 中获取） |
| `EOLINK_SPACE_URL` | 空间域名，格式为 `https://apis.eolink.com` |
| `project_id` | 项目的唯一标识 hash（在项目 URL 或设置中查看） |
| `BASE_URL` | 被测后端的根域名，如 `https://apis.eolink.com` |
| `TEST_ENV` | 测试环境名称（可选，默认取 Eolink 项目中已配置的环境） |

如用户未提供以上信息，**先询问清楚再执行后续步骤**。

## 推荐流程

1. 准备好 `www/EolinkProject/EolinkSkills/eolink_config.json`（或等效 JSON），确保 `space_url`/`Eo-Secret-Key`/`space_id`/`project_id`/`base_url` 等字段齐全。
2. 运行 `python scripts/eolink_skill_runner.py`（配合配置文件可省掉重复确认），该脚本会先调用 `create_eolink_studio_coupon_case.py` 与 `create_eolink_studio_coupon_edit_case.py` 生成不同 `adjustment_type` 的用例，再调用 `run_eolink_test.py` 匹配执行。
3. 如果只想单次跑测试，可传 `--skip-coupon-cases` 或 `--skip-edit-cases`；只生成用例不跑请求，可加 `--skip-tests`。更多参数（匹配、头部、导出）参考脚本帮助信息。

## 主要脚本（Skill 内部工具）

- `scripts/eolink_skill_runner.py`：一键串联所有操作，默认会生成创建/编辑优惠码的 `adjustment_type` 用例，然后再调用 `run_eolink_test.py` 发起接口测试；支持 `--match`/`--no-run`/`--coupon-code-rand6` 等 run script 参数，以及 `--skip-*-cases`、`--skip-tests`。
- `scripts/run_eolink_test.py`：链接 Eolink Open API、生成测试用例、执行 HTTP 请求并输出 JSON 报告。该脚本仍可以单独调用用于高级调试。
- `scripts/create_eolink_studio_coupon_case.py`：同步“创建优惠码”接口的 case，针对 `adjustment_type` 可批量生成 `percentage/flat/m_for_n/buy_x_get_y_discount` 版本，并保证 `param_info/param_value` 都能被脚本覆盖。
- `scripts/create_eolink_studio_coupon_edit_case.py`：与上面类似但针对“编辑优惠码”接口，同时补充 restful path 的 `id` 占位符。
- `scripts/create_eolink_studio_notices_case.py`：生成“告警/消息”接口的用例，包括固定头和分页参数。

---

## 步骤一：认证与获取接口列表

### Eolink Open API 通用请求格式

```python
import requests

SPACE_URL = "https://apis.eolink.com"  # 用户提供
API_KEY = "your_api_key"               # 用户提供

headers = {
    "Content-Type": "application/json",
    "Eo-Secret-Key": API_KEY         # Eolink Open API 鉴权头
}
```

> ⚠️ 若用户使用的是私有部署版 Eolink，`SPACE_URL` 为内网地址，`Eo-Secret-Key` 头格式可能略有不同，需用户确认。

### 获取项目下的测试接口列表

```python
def get_api_list(space_url, api_key, api_id, space_id, project_id, api_id=None, page=1, page_size=100):
    url = f"{space_url}/api/v2/api_studio/management/test_case/get_list"
    headers = {"Eo-Secret-Key": api_key, "Content-Type": "application/json"}
    params = {
        "space_id":       space_id,
        "api_id":         api_id,
        "project_id":     project_id,
        "page":           page,
        "pageSize":       page_size
    }
    if api_id is not None:
        params["api_id"] = api_id

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()
```

### 获取测试的单个接口详情

```python
def get_api_detail(space_url, api_key, space_id, project_id, case_id):
    url = f"{space_url}/api/v2/api_studio/management/test_case/get_info"
    headers = {"Eo-Secret-Key": api_key, "Content-Type": "application/json"}
    params = {
        "space_id": space_id,
        "project_id": project_id,
        "case_id": case_id
    }
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()
```

> 📌 若以上接口路径不匹配，说明 Eolink 版本或私有部署路径不同。请参考 `references/eolink-api-paths.md` 中的常见路径列表，或让用户提供其 Eolink 的 Open API 文档链接。

## 路径参考

所有与 Skill 交互的 Open API 路径都整理在 `references/eolink-api-paths.md`，目前覆盖：

- `/api/v2/api_studio/management/test_case/get_list`
- `/api/v2/api_studio/management/test_case/get_info`
- `/api/v2/api_studio/management/test_case/add`
- `/api/v2/api_studio/management/test_case/edit`
- `/api/v2/api_studio/management/test_case/delete`
- `/api/v2/api_studio/management/api/get_api`

如果用户的部署使用不同前缀（如 `/api/v1/`）或改了授权头，请以该文件为参考调整路径。

---

## 步骤二：生成测试用例

接口详情返回的典型结构（根据 Eolink 版本有差异）：

```json
{
  "apiName": "获取用户信息",
  "apiURI": "/user/info",
  "apiRequestType": 0,       // 0=GET, 1=POST, 2=PUT, 3=DELETE
  "apiRequestParamType": 1,  // 1=JSON, 0=form
  "apiRequestParam": [...],  // query/body 参数列表
  "apiResultParam": [...]    // 返回参数列表
}
```

### 测试用例生成逻辑

```python
def generate_test_case(api_detail: dict, base_url: str) -> dict:
    """从接口详情生成一条基础测试用例"""
    method_map = {0: "GET", 1: "POST", 2: "PUT", 3: "DELETE", 4: "PATCH"}
    method = method_map.get(api_detail.get("apiRequestType", 1), "GET")
    
    # 生成示例参数（使用参数默认值或占位符）
    params = {}
    body = {}
    for p in api_detail.get("apiRequestParam", []):
        name = p.get("paramName", "")
        default = p.get("paramDefaultValue") or p.get("paramValue") or f"<{name}>"
        if method == "GET":
            params[name] = default
        else:
            body[name] = default
    
    return {
        "test_name": api_detail.get("apiName", "unnamed"),
        "method": method,
        "url": base_url.rstrip("/") + api_detail.get("apiURI", "/"),
        "params": params,
        "body": body if method != "GET" else {},
        "expected_status": 200
    }
```

---

## 步骤三：执行测试

```python
import time

def run_test(test_case: dict, headers: dict = None) -> dict:
    """执行单个测试用例，返回结构化结果"""
    start = time.time()
    try:
        resp = requests.request(
            method=test_case["method"],
            url=test_case["url"],
            params=test_case.get("params"),
            json=test_case.get("body") or None,
            headers=headers or {},
            timeout=15
        )
        elapsed = round(time.time() - start, 3)
        return {
            "test_name": test_case["test_name"],
            "method": test_case["method"],
            "url": test_case["url"],
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
```

---

## 步骤四：输出 JSON 报告

```python
import json
from datetime import datetime

def save_report(results: list, output_path: str = "eolink_test_report.json"):
    report = {
        "generated_at": datetime.now().isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告已保存：{output_path}  ({report['passed']}/{report['total']} 通过)")
    return report
```

---

## 完整执行脚本

参考 `scripts/run_eolink_test.py` 中的完整可运行脚本。

---

## 常见问题处理

| 问题 | 处理方式 |
|------|----------|
| `401 Unauthorized` | 检查 `EEo-Secret-Key` 是否传入正确的 API Key |
| 接口列表为空 | 检查 `projectHashKey` 是否正确；确认该 API Key 有对应项目权限 |
| 接口路径 404 | 可能是 Eolink 版本差异，参考 `references/eolink-api-paths.md` |
| 测试后端连不上 | 检查 `BASE_URL` 是否正确，以及网络/VPN 配置 |
| 参数占位符未替换 | 需要用户补充真实测试数据，或配置 Eolink 的环境变量 |

---

## 扩展功能（可按需实现）

- **认证 header 注入**：用户后端若需要 Token 认证，在 `run_test` 的 `headers` 参数中传入
- **断言增强**：对 `response_body` 内特定字段做值校验
- **分页拉取**：接口数量 > 100 时循环拉取所有页
- **环境变量替换**：支持 Eolink 的 `{{variable}}` 语法在 params/body 中替换真实值
