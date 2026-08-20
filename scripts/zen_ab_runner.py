"""v3 散文章程 vs v3-zen 格言章程的 A/B 试跑器。

实验设计（对应 docs/agentic_mvp/gm_prompt.md「章程压缩实验版」）：
- prose：运行时权威章程（PROMPT_REVISION=gm-capability-charter-agentic-mvp-3）。
- zen：格言内核 + 正式章程的协议段落（PROMPT_REVISION=-3-zen）。
- 同一（场景, 轮次）使用相同随机种子，使两种变体拿到完全相同的骰子序列，
  指标差异只归因于 Prompt 主体。

夹具合法性（evaluation.md「统一运行方式」）：场景初始事实通过一个标注为
「场景设定」的 setup 回合建立——它是带 origin.kind=gm_turn 的可追溯前置
CommittedTurn，不进入行为指标。

用法:
    PYTHONPATH=src python3 scripts/zen_ab_runner.py [--dry-run]
        [--scenarios S1,S2,S7] [--runs 2] [--seed-base 1000]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

import monmusu_agent.agentic_harness as harness_module
import monmusu_agent.agentic_model as model_module
from monmusu_agent.agentic_cli import ProviderConfigError, provider_config_from_env
from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    DEFAULT_DEEPSEEK_MODEL_ID,
    DeepSeekGameMasterModel,
    ModelResponse,
    deepseek_model_profile,
)
from monmusu_agent.agentic_session import AgenticSessionStore, NewSessionRequest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPT_DOC = PROJECT_ROOT / "docs" / "agentic_mvp" / "gm_prompt.md"
RUNS_ROOT = PROJECT_ROOT / "var" / "zen_ab_runs"

PROSE_REVISION = "gm-capability-charter-agentic-mvp-3"
ZEN_REVISION = "gm-capability-charter-agentic-mvp-3-zen"

SETUP_PREFIX = "【场景设定"

# 场景夹具：fixture 回合用确定性模型建立初始事实并退役冲突事实
# （对应 evaluation.md「统一运行方式」与 agentic_contract 的既有模式），
# inputs 是固定玩家输入序列。
SCENARIOS: dict[str, dict[str, object]] = {
    "S1": {
        "name": "模组未预写的破局（潮缝）",
        "fixture": {
            "narration": "你留意到靠海那面墙脚有一条窄缝：涨潮时海水会从缝里短暂涌入，退潮时又慢慢渗回。",
            "establish": [
                "牢房靠海那面墙脚有一条窄缝，涨潮时海水会短暂涌入。"
            ],
            "retire": [],
        },
        "inputs": [
            "我趴下观察海水进出的节奏，再把衣袖撕成细条探进缝里，"
            "想判断墙后是不是有能容人通过的排水道。",
            "我不去找钥匙，也不改走别的路线。我叫阿兰妮丝和我一起沿潮水缝拆下最松的石块，"
            "想把它扩大到能爬过；即使墙后只是填土，我们也要确认它到底通向哪里。",
        ],
    },
    "S2": {
        "name": "免检 vs 风险行动（仓库钥匙与断桥）",
        "fixture": {
            "narration": "你们已经离开石牢，来到码头边一间无人看守的旧仓库。"
            "桌上放着一把触手可及的铜钥匙；仓库外，一段断桥正被海浪间歇冲刷，"
            "从桥上跌落会受伤并引来注意。",
            "establish": [
                "调查员与同行者已离开石牢，来到码头边一间无人看守的旧仓库内。",
                "一把已经看见且能够直接够到的铜钥匙放在仓库的桌上。",
                "仓库外是一段被海浪间歇冲刷的断桥。",
                "从断桥跌落会受伤并引来注意。",
            ],
            "retire": ["石牢的牢门仍然锁着。"],
        },
        "inputs": [
            "我拿起桌上的铜钥匙收好。",
            "趁下一股浪还没打来，我助跑跳过断桥，去对面的门楼。",
        ],
    },
    "S7": {
        "name": "暗渠逃脱（对应 game_9917 下水道困境）",
        "fixture": {
            "narration": "牢房地面靠墙处嵌着一块松动的铁格排水口，撬开它就能进入下面"
            "一条黑暗的排水暗渠；你手里还留着一截可以当撬棍的短铁钎。",
            "establish": [
                "牢房地面靠墙处有一块松动的铁格排水口，可以撬开；下面是条黑暗的排水暗渠。",
                "调查员手里有一段可以当撬棍的短铁钎。",
            ],
            "retire": [],
        },
        "inputs": [
            "我撬开排水口，看看下面通向哪里。",
            "我先下去探路，尝试走到出口。",
            "我沿着水流继续往前走，寻找出口。",
            "我继续往前走，直到找到出口或看见岔路。",
        ],
    },
}

# S7 出口判定关键词：命中即视为玩家在本场景内脱困。
EXIT_KEYWORDS = (
    "码头",
    "栈道",
    "出口",
    "浅湾",
    "上岸",
    "豁然开朗",
    "离开暗渠",
    "走出暗渠",
    "水渠尽头",
    "仓库",
    "货栈",
    "地窖",
    "街道",
)


def _load_prompts() -> dict[str, str]:
    """从权威文档切出两种 Prompt 主体；zen = 格言块 + 协议段落。"""

    doc = PROMPT_DOC.read_text(encoding="utf-8")
    main_section = doc.split("## 主持能力章程", 1)[1]
    prose = main_section.split("```text", 1)[1].split("```", 1)[0].strip()
    zen_section = doc.split("## 章程压缩实验版", 1)[1]
    zen_block = zen_section.split("```text", 1)[1].split("```", 1)[0].strip()

    preamble = prose.split("每轮裁定遵循同一个循环", 1)[0].rstrip()
    law = prose.split("COC 工具是你把真实不确定性", 1)[1]
    law_head, law_tail = law.split("节奏与威胁：", 1)
    law_tail = "把需要跨回合记住" + law_tail.split("把需要跨回合记住", 1)[1]
    zen = "\n\n".join(
        (preamble, zen_block, law_head.rstrip(), law_tail.strip())
    )
    return {"prose": prose, "zen": zen}


def _apply_variant(variant: str, prompts: dict[str, str]) -> None:
    """切换本次运行的运行时章程与记录 revision。"""

    if variant == "zen":
        harness_module._GM_CAPABILITY_CHARTER = prompts["zen"]
        model_module.PROMPT_REVISION = ZEN_REVISION
    else:
        harness_module._GM_CAPABILITY_CHARTER = prompts["prose"]
        model_module.PROMPT_REVISION = PROSE_REVISION


class FixtureModel:
    """用公开 Harness seam 建立可追溯的场景前置事实（与 agentic_contract 同模式）。"""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self._used = False

    def complete(self, request: object) -> ModelResponse:
        del request
        if self._used:
            raise AssertionError("fixture model 没有剩余响应")
        self._used = True
        return self._response


def _fixture_response(
    fixture: Mapping[str, object],
    session: Mapping[str, object],
) -> ModelResponse:
    """组装夹具最终答复；retire 按开场事实原文定位 fact_id。"""

    facts = session.get("facts", [])
    retirements: list[Mapping[str, str]] = []
    for text in fixture.get("retire", []):
        matches = [
            fact["fact_id"]
            for fact in facts
            if isinstance(fact, Mapping)
            and fact.get("status") == "active"
            and fact.get("text") == text
        ]
        if len(matches) != 1:
            raise ValueError(f"fixture retire fact missing or ambiguous: {text!r}")
        retirements.append(
            {"fact_id": matches[0], "reason": "场景夹具确立的新处境不再需要该事实。"}
        )
    return ModelResponse(
        assistant_message={
            "role": "assistant",
            "content": json.dumps(
                {
                    "narration": str(fixture["narration"]),
                    "establish": [
                        {"visibility": "public", "text": str(text)}
                        for text in fixture.get("establish", [])
                    ],
                    "retire": retirements,
                    "session_status": "ongoing",
                },
                ensure_ascii=False,
            ),
            "reasoning_content": None,
            "tool_calls": [],
        },
        finish_reason="stop",
        usage=None,
        latency_ms=None,
    )


def _char_bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _jaccard(first: str, second: str) -> float:
    left, right = _char_bigrams(first), _char_bigrams(second)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _run_turns(
    harness: AgenticHarness,
    game_id: str,
    inputs: list[str],
) -> tuple[list[str], str | None]:
    """依次执行输入；返回每回合状态与首个中断错误码。"""

    statuses: list[str] = []
    error_code: str | None = None
    for player_input in inputs:
        result = harness.start_turn(game_id, player_input)
        statuses.append(result.status)
        if result.status != "committed":
            error_code = result.error_code
            break
    return statuses, error_code


def _metrics_for(session: dict[str, object], scenario_key: str) -> dict[str, object]:
    """从 session.json 计算行为指标；setup 回合不进入统计。"""

    turns = [
        turn
        for turn in session["turns"]
        if not turn["player_input"].startswith(SETUP_PREFIX)
    ]
    if not turns:
        return {
            "turns": 0,
            "check_rate": None,
            "retry_pairs": [],
            "luck_spends": 0,
            "pushes": 0,
            "exit_reached": None,
            "hard_gate_s2_turn1_no_check": None,
            "hard_gate_s1_turn1_established": None,
        }
    checks_per_turn = [
        [m for m in turn["mechanics"] if m.get("kind") == "check"]
        for turn in turns
    ]
    turns_with_check = sum(1 for checks in checks_per_turn if checks)
    check_rate = turns_with_check / len(turns)

    retry_pairs: list[tuple[int, str, str]] = []
    for index in range(1, len(turns)):
        if not checks_per_turn[index - 1] or not checks_per_turn[index]:
            continue
        first = checks_per_turn[index - 1][0]
        second = checks_per_turn[index][0]
        same_target = (
            first.get("actor_id") == second.get("actor_id")
            and first.get("ability") == second.get("ability")
            and _jaccard(first.get("action", ""), second.get("action", "")) >= 0.25
        )
        if same_target:
            retry_pairs.append((index + 1, first.get("ability", ""), second.get("ability", "")))

    luck_spends = sum(
        1
        for turn in turns
        for mechanic in turn["mechanics"]
        if mechanic.get("kind") == "luck_spend"
    )
    pushes = sum(
        1
        for turn in turns
        for mechanic in turn["mechanics"]
        if mechanic.get("kind") == "push_check"
    )

    if scenario_key == "S7":
        last_text = " ".join(
            [turns[-1].get("narration", "")]
            + [
                fact["text"]
                for fact in session["facts"]
                if fact["fact_id"] in turns[-1].get("established_fact_ids", [])
            ]
        )
        exit_reached = any(keyword in last_text for keyword in EXIT_KEYWORDS)
    else:
        exit_reached = None

    return {
        "turns": len(turns),
        "check_rate": round(check_rate, 3),
        "turns_with_check": turns_with_check,
        "retry_pairs": retry_pairs,
        "luck_spends": luck_spends,
        "pushes": pushes,
        "exit_reached": exit_reached,
        # 硬门槛（可自动计算的两项，来自 evaluation.md 场景一/二）
        "hard_gate_s2_turn1_no_check": (
            not checks_per_turn[0] if scenario_key == "S2" else None
        ),
        "hard_gate_s1_turn1_established": (
            bool(turns[0].get("established_fact_ids"))
            if scenario_key == "S1"
            else None
        ),
    }


def run_experiment(args: argparse.Namespace) -> list[dict[str, object]]:
    load_dotenv()
    prompts = _load_prompts()
    print(
        f"[prompts] prose={len(prompts['prose'])} chars, "
        f"zen={len(prompts['zen'])} chars"
    )
    if args.dry_run:
        print("[dry-run] 场景矩阵：")
        for key, spec in SCENARIOS.items():
            if key not in args.scenarios:
                continue
            print(
                f"  {key} {spec['name']}: setup + {len(spec['inputs'])} 输入 × "
                f"{len(('prose', 'zen'))} 变体 × {args.runs} 轮"
            )
        return []

    try:
        config = provider_config_from_env(os.environ)
    except ProviderConfigError as error:
        raise SystemExit(f"[provider] {error}") from error
    if config is None:
        raise SystemExit("[provider] 未配置：请先设置 MONMUSU_PROVIDER 与对应 key")

    if args.thinking == "env":
        thinking_text = (
            os.environ.get("MONMUSU_DEEPSEEK_THINKING", "false")
            .strip()
            .lower()
        )
        if thinking_text not in {"false", "true"}:
            raise SystemExit("MONMUSU_DEEPSEEK_THINKING 必须为 false 或 true。")
        thinking = thinking_text == "true"
    else:
        thinking = args.thinking == "true"
    max_tokens = (
        args.max_tokens
        if args.max_tokens is not None
        else (16384 if thinking else 4096)
    )
    print(f"[thinking] {thinking} max_tokens={max_tokens}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = RUNS_ROOT / timestamp
    root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    scenario_keys = [key for key in args.scenarios if key in SCENARIOS]
    for variant in ("prose", "zen"):
        _apply_variant(variant, prompts)
        for scenario_key in scenario_keys:
            spec = SCENARIOS[scenario_key]
            for run_index in range(args.runs):
                seed = args.seed_base + (
                    (scenario_keys.index(scenario_key) + 1) * 100
                ) + run_index
                session_root = root / f"{scenario_key}_{variant}_r{run_index}"
                store = AgenticSessionStore(session_root)
                choice = store.available_investigators()[0]
                created = store.create_session(
                    NewSessionRequest(
                        investigator_id=choice.actor_id,
                        display_name="崔克",
                    )
                )
                profile = deepseek_model_profile(
                    model_id=DEFAULT_DEEPSEEK_MODEL_ID,
                    thinking=thinking,
                    provider=config.provider,
                    base_url=config.base_url,
                )
                profile = {
                    **profile,
                    "max_tokens": max_tokens,
                }
                model = DeepSeekGameMasterModel(
                    config.api_key,
                    base_url=config.base_url,
                )
                harness = AgenticHarness(
                    store,
                    model,
                    model_profile=profile,
                    random_source=random.Random(seed),
                )
                fixture_harness = AgenticHarness(
                    store,
                    FixtureModel(
                        _fixture_response(spec["fixture"], created.session)
                    ),
                    model_profile=profile,
                    random_source=random.Random(seed),
                )
                setup_result = fixture_harness.start_turn(
                    created.game_id,
                    f"【场景设定：{spec['name']}】",
                )
                setup_note = (
                    "ok" if setup_result.status == "committed"
                    else f"interrupted:{setup_result.error_code}"
                )
                if setup_result.status != "committed":
                    rows.append(
                        {
                            "scenario": scenario_key,
                            "variant": variant,
                            "run": run_index,
                            "seed": seed,
                            "game_id": created.game_id,
                            "setup": setup_note,
                            "turn_statuses": [],
                            "interrupt": setup_result.error_code,
                            "turns": 0,
                            "check_rate": None,
                            "turns_with_check": 0,
                            "retry_pairs": [],
                            "luck_spends": 0,
                            "pushes": 0,
                            "exit_reached": None,
                            "hard_gate_s2_turn1_no_check": None,
                            "hard_gate_s1_turn1_established": None,
                        }
                    )
                    print(
                        f"[run] {scenario_key}/{variant}/r{run_index} seed={seed} "
                        f"setup={setup_note} (skipped)",
                        flush=True,
                    )
                    continue
                statuses, error_code = _run_turns(
                    harness, created.game_id, [str(x) for x in spec["inputs"]]
                )
                session = store.load_session(created.game_id).session
                metrics = _metrics_for(session, scenario_key)
                row = {
                    "scenario": scenario_key,
                    "variant": variant,
                    "run": run_index,
                    "seed": seed,
                    "game_id": created.game_id,
                    "thinking": thinking,
                    "max_tokens": max_tokens,
                    "setup": setup_note,
                    "turn_statuses": statuses,
                    "interrupt": error_code,
                    **metrics,
                }
                rows.append(row)
                print(
                    f"[run] {scenario_key}/{variant}/r{run_index} seed={seed} "
                    f"turns={metrics['turns']} check_rate={metrics['check_rate']} "
                    f"retries={len(metrics['retry_pairs'])} "
                    f"exit={metrics['exit_reached']} setup={setup_note}",
                    flush=True,
                )
    return rows


def _render_report(rows: list[dict[str, object]]) -> str:
    lines: list[str] = []
    lines.append("# 章程压缩版 A/B 试跑记录（pilot）")
    lines.append("")
    lines.append("实验分支：`feat/gm-charter-zen-ab`；试跑器：`scripts/zen_ab_runner.py`。")
    lines.append("")
    lines.append("两种 Prompt 主体：")
    lines.append("- prose：`gm-capability-charter-agentic-mvp-3`（运行时权威，散文版）。")
    lines.append("- zen：`gm-capability-charter-agentic-mvp-3-zen`（格言内核 + 协议段落，实验版）。")
    lines.append("")
    if rows:
        lines.append(
            f"GM 思考模式：`thinking={rows[0]['thinking']}`，"
            f"`max_tokens={rows[0]['max_tokens']}`。"
        )
        lines.append("")
    lines.append("同一（场景, 轮次）固定随机种子，两变体骰子序列相同。setup 回合按")
    lines.append("evaluation.md 的夹具来源规定建立场景初始事实，不进入指标。")
    lines.append("")
    lines.append("| 场景 | 变体 | 轮 | 回合 | 检定率 | 重掷对 | 幸运 | 推骰 | S7脱困 | 中断 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        lines.append(
            "| {scenario} | {variant} | {run} | {turns} | {check_rate} | "
            "{retries} | {luck} | {pushes} | {exit} | {interrupt} |".format(
                scenario=row["scenario"],
                variant=row["variant"],
                run=row["run"],
                turns=row["turns"],
                check_rate=row["check_rate"],
                retries=len(row["retry_pairs"]),
                luck=row["luck_spends"],
                pushes=row["pushes"],
                exit="-" if row["exit_reached"] is None else ("是" if row["exit_reached"] else "否"),
                interrupt=row["interrupt"] or "-",
            )
        )
    lines.append("")
    lines.append("## 指标定义")
    lines.append("")
    lines.append("- 检定率：含 `make_check` 机械的回合数 / 行动回合数（setup 回合除外）。")
    lines.append("- 重掷对：相邻两个回合都掷了同角色同技能、且 action 文本二元组 Jaccard ≥ 0.25。")
    lines.append("- S7 脱困：最后一回合叙事或新事实命中出口关键词（码头/栈道/出口/浅湾/上岸/仓库/街道等）。")
    lines.append("- 中断：GM 执行超时/协议失败等非提交状态。")
    lines.append("")
    lines.append("## 原始会话")
    lines.append("")
    lines.append("各局完整记录在 `var/zen_ab_runs/<timestamp>/<scenario>_<variant>_r<n>/session.json`。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--thinking",
        choices=["env", "true", "false"],
        default="env",
        help="GM 思考模式；env 表示读 MONMUSU_DEEPSEEK_THINKING（默认 false）",
    )
    parser.add_argument(
        "--scenarios", default="S1,S2,S7", help="逗号分隔的场景键"
    )
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--seed-base", type=int, default=1000)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="输出预算；默认 thinking=false 时 4096、true 时 16384",
    )
    args = parser.parse_args()
    args.scenarios = args.scenarios.split(",")
    rows = run_experiment(args)
    if rows:
        report = _render_report(rows)
        evidence_dir = PROJECT_ROOT / "docs" / "agentic_mvp" / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        report_path = evidence_dir / (
            f"zen-ab-pilot-{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')}.md"
        )
        report_path.write_text(report, encoding="utf-8")
        print(f"[report] {report_path}")
        print(report)


if __name__ == "__main__":
    main()
