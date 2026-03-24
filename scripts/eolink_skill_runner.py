#!/usr/bin/env python3
"""
顶层入口：为 eolink-test skill 提供一条命令做四件事：
 1. 确保优惠码/编辑优惠码的 adjustment_type 用例存在（生成并提交到 Eolink）
 2. 可选地再生成通知用例（可扩展）
 3. 调用 `run_eolink_test.py` 全量跑一遍匹配的接口（直接向后端发 HTTP）
 4. 【新增】调用 `run_eolink_case_execute.py` 让 Eolink 平台侧执行用例并出报告
"""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List


def _run_command(cmd: List[str]) -> None:
    print(f"\n> {' '.join(shlex.quote(p) for p in cmd)}")
    subprocess.run(cmd, check=True)


def _ensure_config(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"配置文件不存在：{resolved}")
    return resolved


def main():
    scripts_dir = Path(__file__).resolve().parent
    default_config = Path("www/EolinkProject/EolinkSkills/eolink_config.json")

    parser = argparse.ArgumentParser(description="eolink-test skill 的一键执行命令：先生成用例再跑测试")
    parser.add_argument("--config", default=default_config, help="指向 eolink_config.json 或等效配置")

    # ── 用例生成开关 ──
    parser.add_argument("--skip-coupon-cases", action="store_true", help="跳过创建优惠码用例的生成")
    parser.add_argument("--skip-edit-cases",   action="store_true", help="跳过编辑优惠码用例的生成")
    parser.add_argument("--coupon-adjustment-types", default="percentage,flat,m_for_n,buy_x_get_y_discount",
                        help="传给创建优惠码脚本的 adjustment_type 列表")
    parser.add_argument("--edit-adjustment-types",   default="percentage,flat,m_for_n,buy_x_get_y_discount",
                        help="传给编辑优惠码脚本的 adjustment_type 列表")
    parser.add_argument("--edit-api-id",    type=int, default=None, help="直接给编辑接口的 apiId（默认关键词搜索）")
    parser.add_argument("--restful-id-var", default="{{edit_coupon_id}}", help="写入编辑接口 restful path 的变量")

    # ── run_eolink_test.py（直接向后端发 HTTP）开关 ──
    parser.add_argument("--skip-tests",       action="store_true", help="跳过 run_eolink_test.py")
    parser.add_argument("--match",            action="append", default=None, help="只匹配这些关键字再跑测试，可重复")
    parser.add_argument("--list-only",        action="store_true", help="只列出 Case（传给 run_eolink_test）")
    parser.add_argument("--no-run",           action="store_true", help="生成用例但不发起请求")
    parser.add_argument("--coupon-code-rand6",action="store_true", help="对疑似优惠码字段自动写 {{RAND:6}}")
    parser.add_argument("--auth-header",      default=None, help="运行测试时附加的鉴权头，格式 HeaderName:HeaderValue")
    parser.add_argument("--output",           default="eolink_test_report.json", help="run_eolink_test 的输出路径")
    parser.add_argument("--export-cases",     default=None, help="run_eolink_test --export-cases")

    # ── 【新增】run_eolink_case_execute.py（平台侧执行）开关 ──
    parser.add_argument("--run-cases",         action="store_true",
                        help="生成用例后，调用 Eolink execute API 在平台侧执行并输出报告")
    parser.add_argument("--execute-keyword",   action="append", default=None,
                        help="execute 时搜索的接口关键字（默认同时搜'创建优惠码'和'编辑优惠码'）")                    
    parser.add_argument("--execute-api-id",    action="append", type=int, default=None,
                        help="直接指定 execute 的 api_id（可传多次）")
    parser.add_argument("--execute-match-case",default=None,
                        help="只执行用例名包含该关键字的用例（如 --execute-match-case shipping）")
    parser.add_argument("--execute-env-id",    type=int, default=None,
                        help="Eolink 测试环境 ID（不传则使用接口默认环境）")
    parser.add_argument("--execute-output",    default="eolink_execute_report.json",
                        help="平台侧执行报告的输出路径（默认 eolink_execute_report.json）")
    parser.add_argument("--execute-list-only", action="store_true",
                        help="只列出用例，不真正执行（传给 run_eolink_case_execute）")

    args = parser.parse_args()

    cfg_path = _ensure_config(Path(args.config))

    # ── Step 1：生成创建优惠码用例 ──
    if not args.skip_coupon_cases:
        coupon_cmd = [
            sys.executable,
            str(scripts_dir / "create_eolink_studio_coupon_case.py"),
            "--config", str(cfg_path),
            "--create-adjustment-type-cases",
            "--adjustment-types", args.coupon_adjustment_types,
        ]
        _run_command(coupon_cmd)

    # ── Step 2：生成编辑优惠码用例 ──
    if not args.skip_edit_cases:
        edit_cmd = [
            sys.executable,
            str(scripts_dir / "create_eolink_studio_coupon_edit_case.py"),
            "--config", str(cfg_path),
            "--create-adjustment-type-cases",
            "--adjustment-types", args.edit_adjustment_types,
            "--restful-id-var", args.restful_id_var,
        ]
        if args.edit_api_id:
            edit_cmd.extend(["--api-id", str(args.edit_api_id)])
        _run_command(edit_cmd)

    # ── Step 3：run_eolink_test.py（直接向后端发 HTTP 请求）──
    if not args.skip_tests:
        test_cmd = [
            sys.executable,
            str(scripts_dir / "run_eolink_test.py"),
            "--config", str(cfg_path),
            "--output", args.output,
        ]
        if args.match:
            for term in args.match:
                test_cmd.extend(["--match", term])
        if args.list_only:
            test_cmd.append("--list-only")
        if args.no_run:
            test_cmd.append("--no-run")
        if args.coupon_code_rand6:
            test_cmd.append("--coupon-code-rand6")
        if args.auth_header:
            test_cmd.extend(["--auth-header", args.auth_header])
        if args.export_cases:
            test_cmd.extend(["--export-cases", args.export_cases])
        _run_command(test_cmd)
    else:
        print("⚙️  --skip-tests 已设置，跳过 run_eolink_test.py")

    # ── Step 4：run_eolink_case_execute.py（平台侧执行）──
    if args.run_cases:
        execute_cmd = [
            sys.executable,
            str(scripts_dir / "run_eolink_case_execute.py"),
            "--config", str(cfg_path),
            "--output", args.execute_output,
        ]

        # 默认关键字：如果没有显式指定，按照本次生成了哪些用例来决定
        keywords = list(args.execute_keyword or [])
        if not keywords and not args.execute_api_id:
            if not args.skip_coupon_cases:
                keywords.append("创建优惠码")
            if not args.skip_edit_cases:
                keywords.append("编辑优惠码")
            if not keywords:
                # 两个都跳过了，还是给个默认值
                keywords = ["创建优惠码", "编辑优惠码"]

        for kw in keywords:
            execute_cmd.extend(["--keyword", kw])

        for aid in (args.execute_api_id or []):
            execute_cmd.extend(["--api-id", str(aid)])

        if args.execute_match_case:
            execute_cmd.extend(["--match-case", args.execute_match_case])
        if args.execute_env_id is not None:
            execute_cmd.extend(["--env-id", str(args.execute_env_id)])
        if args.execute_list_only:
            execute_cmd.append("--list-only")

        _run_command(execute_cmd)
    else:
        print("\n💡 提示：加上 --run-cases 可让 Eolink 平台侧执行刚生成的测试用例并输出报告。")


if __name__ == "__main__":
    main()
