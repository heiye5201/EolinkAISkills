# Eolink 自动化 API 测试 Skill

> 用于对接 Eolink APIkit 平台，实现接口文档拉取、测试用例生成、自动化执行与报告输出的完整闭环。

---

## 功能概览

| 步骤 | 说明 |
|------|------|
| 🔐 认证 | 使用 API Key 调用 Eolink Open API 鉴权 |
| 📄 拉取接口 | 获取项目下的接口列表及详情 |
| 🧪 生成用例 | 根据接口定义自动生成测试用例 |
| 🚀 执行测试 | 使用 Python `requests` 向真实后端发送请求 |
| 📊 输出报告 | 将测试结果保存为结构化 JSON 文件 |

---

## 目录结构

```
EolinkSkills/
├── SKILL.md                              # Skill 定义文件
├── README.md                             # 本文件
├── eolink_config.json                    # 配置文件（密钥、项目 ID 等）
├── scripts/
│   ├── eolink_skill_runner.py            # 一键串联所有操作的主入口
│   ├── run_eolink_test.py                # 执行接口测试并输出报告
│   ├── create_eolink_studio_coupon_case.py       # 生成"创建优惠码"测试用例
│   ├── create_eolink_studio_coupon_edit_case.py  # 生成"编辑优惠码"测试用例
│   └── create_eolink_studio_notices_case.py      # 生成"告警/消息"测试用例
└── references/
    └── eolink-api-paths.md               # Open API 路径参考文档
```

---

## 快速开始

### 1. 准备配置文件

在项目根目录创建 `eolink_config.json`：

```json
{
  "space_url": "https://apis.eolink.com",
  "Eo-Secret-Key": "your_api_key",
  "space_id": "your_space_id",
  "project_id": "your_project_id",
  "base_url": "https://your-backend.com"
}
```

### 2. 一键执行（推荐）

```bash
python scripts/eolink_skill_runner.py
```

默认会依次：生成创建/编辑优惠码的测试用例 → 调用 `run_eolink_test.py` 发起接口测试 → 输出 JSON 报告。

### 3. 常用参数

```bash
# 跳过生成创建优惠码用例
python scripts/eolink_skill_runner.py --skip-coupon-cases

# 跳过生成编辑优惠码用例
python scripts/eolink_skill_runner.py --skip-edit-cases

# 只生成用例，不发起请求
python scripts/eolink_skill_runner.py --skip-tests

# 单独执行测试（高级调试）
python scripts/run_eolink_test.py
```
---

## 在 Codex 中使用

### 挂载 Skill

```bash
ln -s /path/to/EolinkAISkills ~/.codex/skills/eolink
```

### 调用 Skill

```bash
# 进入 Codex
codex

# 显式调用
$eolink 我要测试一下
```

---

## 环境要求

- Python 3.8+
- `requests` 库（`pip install requests`）
- Eolink 账号及 Open API Key（控制台 → 账户设置 → Open API 获取）

---

## 常见问题

| 问题 | 解决方式 |
|------|----------|
| `401 Unauthorized` | 检查 `Eo-Secret-Key` 是否填写正确 |
| 接口列表为空 | 确认 `project_id` 正确，且 API Key 有对应项目权限 |
| 接口路径 404 | 参考 `references/eolink-api-paths.md`，确认路径前缀版本 |
| 后端连不上 | 检查 `base_url` 及网络/VPN 配置 |
| 参数占位符未替换 | 补充真实测试数据，或配置 Eolink 环境变量 |

---

## 相关文档

- [Eolink Open API 文档](https://apis.eolink.com)
- `references/eolink-api-paths.md` — 本 Skill 使用的所有 API 路径汇总