import tempfile
import unittest
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from monmusu_agent.agentic_session import (
    AgenticSessionLoadError,
    AgenticSessionPublishError,
    AgenticSessionSources,
    AgenticSessionStore,
    NewSessionRequest,
)
from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import ModelCallError, ScriptedGameMasterModel
from monmusu_agent.storage import read_json, write_json_atomic


class AgenticSessionStoreTest(unittest.TestCase):
    @staticmethod
    def _copy_sources(directory: Path) -> AgenticSessionSources:
        defaults = AgenticSessionSources()
        source_directory = directory / "sources"
        source_directory.mkdir()
        copied: dict[str, Path] = {}
        for source_field in fields(AgenticSessionSources):
            source = getattr(defaults, source_field.name)
            destination = source_directory / source.name
            destination.write_bytes(source.read_bytes())
            copied[source_field.name] = destination
        return AgenticSessionSources(**copied)

    @staticmethod
    def _request() -> NewSessionRequest:
        return NewSessionRequest(
            investigator_id="investigator_tracker",
            display_name="林雁",
            honorific="林女士",
            pronouns="她",
            occupation="档案员",
            appearance="短发，穿旧防水外套",
            background_hook="来梦中寻找失踪的弟弟",
            keepsake="一枚裂了边的铜怀表",
        )

    def test_create_session_persists_minimum_agentic_aggregate(self) -> None:
        """新会话一次写入冻结角色资料与空回合集合。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory),
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            created = store.create_session(self._request())

            session = created.session
            self.assertEqual(
                set(session),
                {
                    "schema_version",
                    "game_id",
                    "module_id",
                    "skill_catalog_version",
                    "setup",
                    "session_status",
                    "selected_investigator_id",
                    "actor_display_names",
                    "investigator_profile",
                    "actors",
                    "facts",
                    "turns",
                    "incomplete_turn",
                    "created_at",
                    "updated_at",
                },
            )
            self.assertEqual(session["schema_version"], "agentic-mvp-1")
            self.assertEqual(session["game_id"], "game_test_0001")
            self.assertEqual(session["module_id"], "escape_thalarion")
            self.assertEqual(
                session["skill_catalog_version"],
                "coc7e-agentic-mvp-1",
            )
            self.assertEqual(
                session["selected_investigator_id"],
                "investigator_tracker",
            )
            self.assertEqual(
                session["investigator_profile"],
                {
                    "actor_id": "investigator_tracker",
                    "display_name": "林雁",
                    "honorific": "林女士",
                    "pronouns": "她",
                    "occupation": "档案员",
                    "appearance": "短发，穿旧防水外套",
                    "background_hook": "来梦中寻找失踪的弟弟",
                    "keepsake": "一枚裂了边的铜怀表",
                },
            )
            self.assertEqual(len(session["actors"]), 1)
            self.assertEqual(
                session["actors"][0]["actor_id"],
                "investigator_tracker",
            )
            self.assertEqual(session["actors"][0]["role"], "investigator")
            self.assertEqual(
                session["actors"][0]["skill_catalog_version"],
                "coc7e-agentic-mvp-1",
            )
            self.assertEqual(session["actors"][0]["skills"]["spot_hidden"], 70)
            self.assertEqual(session["actors"][0]["skills"]["locksmith"], 1)
            self.assertEqual(session["actors"][0]["hp"], {"current": 10, "max": 10})
            self.assertEqual(
                session["actors"][0]["san"],
                {"current": 65, "max": 65, "session_loss": 0},
            )
            self.assertEqual(session["actors"][0]["luck"], {"current": 55})
            self.assertEqual(session["actors"][0]["armor"], 0)
            self.assertEqual(
                session["actor_display_names"]["investigator_tracker"],
                "林雁",
            )
            opening_fact_ids = session["setup"]["opening_fact_ids"]
            self.assertEqual(
                opening_fact_ids,
                [fact["fact_id"] for fact in session["facts"]],
            )
            self.assertGreater(len(opening_fact_ids), 5)
            self.assertEqual(
                {fact["origin"]["kind"] for fact in session["facts"]},
                {"opening_canon"},
            )
            self.assertEqual(
                {fact["origin"]["source_ref"] for fact in session["facts"]},
                {"escape_thalarion-agentic-mvp-1#opening_minimum_canon"},
            )
            self.assertTrue(
                all(fact["established_turn_id"] is None for fact in session["facts"])
            )
            self.assertTrue(
                all(fact["status"] == "active" for fact in session["facts"])
            )
            self.assertTrue(
                all(fact["visibility"] == "public" for fact in session["facts"])
            )
            self.assertEqual(session["turns"], [])
            self.assertIsNone(session["incomplete_turn"])
            self.assertEqual(session["session_status"], "ongoing")
            self.assertEqual(
                session["created_at"],
                "2026-07-27T00:00:00Z",
            )
            self.assertEqual(session["updated_at"], session["created_at"])
            self.assertTrue(created.session_file.is_file())

    def test_create_session_assigns_a_unique_setup_id_per_game(self) -> None:
        """开场 ID 由 Harness 为每局单独创建，而不是复用 fixture 常量。"""

        with tempfile.TemporaryDirectory() as directory:
            game_ids = iter(("game_test_0001", "game_test_0002"))
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: next(game_ids),
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            first = store.create_session(self._request())
            second = store.create_session(self._request())

            self.assertNotEqual(
                first.session["setup"]["setup_id"],
                second.session["setup"]["setup_id"],
            )

    def test_find_incomplete_session_ids_returns_only_valid_blockers(self) -> None:
        """发现只返回经过完整装载校验的未完成 Agentic 会话。"""

        with tempfile.TemporaryDirectory() as directory:
            game_ids = iter(("game_ready", "game_interrupted"))
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=game_ids.__next__,
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            ready = store.create_session(self._request())
            interrupted = store.create_session(self._request())
            model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "private", retryable=True)]
            )
            AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_interrupted",
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            ).start_turn(interrupted.game_id, "我检查门锁。")
            ready_bytes = ready.session_file.read_bytes()
            interrupted_bytes = interrupted.session_file.read_bytes()

            found = store.find_incomplete_session_ids()

            self.assertEqual(found, ("game_interrupted",))
            self.assertEqual(ready.session_file.read_bytes(), ready_bytes)
            self.assertEqual(interrupted.session_file.read_bytes(), interrupted_bytes)

    def test_load_session_uses_read_only_snapshots_after_sources_change(self) -> None:
        """工作树材料变化后，会话仍只装载建局时冻结的全文。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._copy_sources(root)
            module_reference = sources.module_reference.read_text(encoding="utf-8")
            character_reference = sources.character_reference.read_text(
                encoding="utf-8"
            )
            store = AgenticSessionStore(
                session_root=root / "sessions",
                sources=sources,
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())

            sources.module_reference.write_text("已经变化的模组", encoding="utf-8")
            sources.character_reference.write_text("已经变化的人物", encoding="utf-8")
            loaded = store.load_session(created.game_id)

            self.assertEqual(loaded.module_reference, module_reference)
            self.assertEqual(loaded.character_reference, character_reference)
            module_hash = created.session["setup"]["module_reference_sha256"]
            character_hash = created.session["setup"]["character_reference_sha256"]
            module_snapshot = (
                created.session_directory
                / "snapshots"
                / "module_reference"
                / f"{module_hash}.md"
            )
            character_snapshot = (
                created.session_directory
                / "snapshots"
                / "character_reference"
                / f"{character_hash}.md"
            )
            self.assertEqual(module_snapshot.stat().st_mode & 0o222, 0)
            self.assertEqual(character_snapshot.stat().st_mode & 0o222, 0)

    def test_load_session_rejects_missing_snapshot_without_fallback(self) -> None:
        """冻结快照缺失时稳定停止，即使原始参考书仍然可读。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._copy_sources(root)
            store = AgenticSessionStore(
                session_root=root / "sessions",
                sources=sources,
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            module_hash = created.session["setup"]["module_reference_sha256"]
            snapshot = (
                created.session_directory
                / "snapshots"
                / "module_reference"
                / f"{module_hash}.md"
            )
            snapshot.unlink()

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "模组参考书快照无法读取",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_unreadable_snapshot_without_fallback(self) -> None:
        """快照路径不可读时，装载器不能改读工作树参考书。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            module_hash = created.session["setup"]["module_reference_sha256"]
            snapshot = (
                created.session_directory
                / "snapshots"
                / "module_reference"
                / f"{module_hash}.md"
            )
            snapshot.unlink()
            snapshot.mkdir()

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "模组参考书快照无法读取",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_snapshot_with_wrong_hash(self) -> None:
        """快照正文被改写后不能进入后续 GM 上下文。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AgenticSessionStore(
                session_root=root / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            module_hash = created.session["setup"]["module_reference_sha256"]
            snapshot = (
                created.session_directory
                / "snapshots"
                / "module_reference"
                / f"{module_hash}.md"
            )
            snapshot.chmod(0o644)
            snapshot.write_text("被篡改的模组快照", encoding="utf-8")

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "模组参考书快照哈希不匹配",
            ):
                store.load_session(created.game_id)

    def test_publish_failure_leaves_no_partial_session(self) -> None:
        """目录发布失败时，正式路径和临时聚合都不可继续使用。"""

        with tempfile.TemporaryDirectory() as directory:
            session_root = Path(directory) / "sessions"

            def fail_publish(staging: Path, destination: Path) -> None:
                self.assertTrue((staging / "session.json").is_file())
                self.assertFalse(destination.exists())
                raise OSError("injected publish failure")

            store = AgenticSessionStore(
                session_root=session_root,
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
                directory_publisher=fail_publish,
            )

            with self.assertRaisesRegex(
                AgenticSessionPublishError,
                "会话无法原子发布",
            ):
                store.create_session(self._request())

            self.assertFalse((session_root / "game_test_0001").exists())
            self.assertEqual(list(session_root.iterdir()), [])

    def test_load_session_rejects_inconsistent_opening_fact_references(self) -> None:
        """开场事实引用损坏时，装载不会形成可供 GM 使用的上下文。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["setup"]["opening_fact_ids"] = ["fact_missing"]
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "opening_fact_ids 与开场事实不一致",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_unknown_fields_in_fact_record(self) -> None:
        """开场事实也必须完整匹配 FactRecord 契约。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["facts"][0]["untrusted_extra"] = "must be rejected"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "FactRecord 格式无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_gm_fact_without_committed_turn(self) -> None:
        """GM 事实必须引用真实存在且反向声明它的已提交回合。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["facts"].append(
                {
                    "fact_id": "fact_9999",
                    "text": "走廊另一端的脚步声正在远去。",
                    "visibility": "public",
                    "status": "active",
                    "established_turn_id": "turn_missing",
                    "origin": {"kind": "gm_turn", "source_ref": None},
                    "retired_turn_id": None,
                    "retire_reason": None,
                }
            )
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "GM 事实引用未知回合",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_retired_fact_without_turn_history(self) -> None:
        """事实不能伪造一条不存在于回合记录中的结束历史。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["facts"][0]["status"] = "retired"
            session["facts"][0]["retired_turn_id"] = "turn_missing"
            session["facts"][0]["retire_reason"] = "篡改过的开场。"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "FactRecord 结束历史不一致",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_malformed_committed_turn(self) -> None:
        """非空回合必须完整符合 CommittedTurn 契约。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["turns"] = [{"turn_id": "turn_0001"}]
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "CommittedTurn 格式无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_complete_bootstrap_state(self) -> None:
        """初始化尚未经过 GM 回合，因此不能已经结束。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["session_status"] = "complete"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "会话状态与回合历史不一致",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_malformed_incomplete_turn(self) -> None:
        """未完成外壳缺失字段时不能成为可恢复状态。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["incomplete_turn"] = {}
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "IncompleteTurn 格式无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_inconsistent_investigator_references(self) -> None:
        """调查员选择、叙事资料和机械卡必须指向同一冻结角色。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["investigator_profile"]["actor_id"] = "investigator_other"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "调查员引用不一致",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_invalid_initial_session_state(self) -> None:
        """新版装载器不会把未知会话状态当成合法 Agentic 聚合。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["session_status"] = "legacy_running"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "session.json 基本结构无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_invalid_frozen_actor_sheet(self) -> None:
        """冻结角色卡越出 COC 数值边界时不能进入后续机械。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_test_0001",
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["actors"][0]["attributes"]["strength"] = 101
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ActorSheet 格式无效",
            ):
                store.load_session(created.game_id)


if __name__ == "__main__":
    unittest.main()
