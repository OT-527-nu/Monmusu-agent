import hashlib
import json
import tempfile
import unittest
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from monmusu_agent.agentic_harness import AgenticHarness
from monmusu_agent.agentic_model import (
    ModelCallError,
    ModelResponse,
    ScriptedGameMasterModel,
)
from monmusu_agent.agentic_session import (
    AgenticSessionLoadError,
    AgenticSessionPublishError,
    AgenticSessionSourceError,
    AgenticSessionSources,
    AgenticSessionStore,
    NewSessionRequest,
    SessionCatalogIssue,
)
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
            self.assertEqual(session["schema_version"], "agentic-mvp-2")
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
            self.assertEqual(
                {actor["actor_id"] for actor in session["actors"]},
                {
                    "investigator_tracker",
                    "npc_vespera",
                    "npc_saphra",
                    "npc_aranis",
                },
            )
            selected = next(
                actor
                for actor in session["actors"]
                if actor["actor_id"] == "investigator_tracker"
            )
            self.assertEqual(selected["role"], "investigator")
            self.assertEqual(selected["skill_catalog_version"], "coc7e-agentic-mvp-1")
            self.assertEqual(selected["skills"]["spot_hidden"], 70)
            self.assertEqual(selected["skills"]["locksmith"], 1)
            self.assertEqual(selected["hp"], {"current": 10, "max": 10})
            self.assertEqual(
                selected["san"],
                {"current": 65, "max": 65, "session_loss": 0},
            )
            self.assertEqual(selected["luck"], {"current": 55})
            self.assertEqual(selected["armor"], 0)
            self.assertEqual(
                session["actor_display_names"]["investigator_tracker"],
                "林雁",
            )
            self.assertNotIn(
                "investigator_mediator",
                session["actor_display_names"],
            )
            self.assertNotIn(
                "investigator_mender",
                session["actor_display_names"],
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

    def test_create_session_freezes_each_production_investigator_roster(self) -> None:
        """三种选卡都冻结选中的调查员和三名固定同行者。"""

        expected = {
            "investigator_tracker": ("investigator", 70, 10, 65, 55),
            "investigator_mediator": ("investigator", 50, 11, 60, 60),
            "investigator_mender": ("investigator", 55, 13, 55, 50),
        }
        npc_ids = {"npc_vespera", "npc_saphra", "npc_aranis"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            game_ids = iter(("game_tracker", "game_mediator", "game_mender"))
            store = AgenticSessionStore(
                session_root=root,
                game_id_factory=game_ids.__next__,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )

            for investigator_id, (role, spot_hidden, hp, san, luck) in expected.items():
                request = NewSessionRequest(
                    investigator_id=investigator_id,
                    display_name=f"自定义-{investigator_id}",
                )
                session = store.create_session(request).session
                actors = {actor["actor_id"]: actor for actor in session["actors"]}
                self.assertEqual(set(actors), {investigator_id, *npc_ids})
                self.assertEqual(len(actors), 4)
                self.assertEqual(actors[investigator_id]["role"], role)
                self.assertEqual(actors[investigator_id]["skills"]["spot_hidden"], spot_hidden)
                self.assertEqual(actors[investigator_id]["hp"]["max"], hp)
                self.assertEqual(actors[investigator_id]["san"]["max"], san)
                self.assertEqual(actors[investigator_id]["luck"]["current"], luck)
                self.assertEqual(
                    set(session["actor_display_names"]),
                    {investigator_id, *npc_ids},
                )

    def test_production_templates_cover_all_stable_actor_ids_and_specialty_values(self) -> None:
        """生产模板覆盖六个稳定角色及目录中的专长键。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(session_root=Path(directory))
            choices = store.available_investigators()
            self.assertEqual(
                {choice.actor_id for choice in choices},
                {
                    "investigator_tracker",
                    "investigator_mediator",
                    "investigator_mender",
                },
            )
            created = store.create_session(self._request())
            actors = {actor["actor_id"]: actor for actor in created.session["actors"]}
            self.assertEqual(actors["npc_vespera"]["skills"]["flight"], 55)
            self.assertNotIn("flight", actors["investigator_tracker"]["skills"])
            self.assertNotIn("flight", actors["npc_saphra"]["skills"])
            self.assertNotIn("flight", actors["npc_aranis"]["skills"])
            self.assertEqual(
                actors["npc_saphra"]["skills"]["language_other__ancient_serpent"],
                75,
            )
            self.assertEqual(actors["npc_aranis"]["skills"]["art_craft__rigging"], 75)
            self.assertEqual(actors["npc_aranis"]["hp"], {"current": 9, "max": 12})

    def test_create_session_freezes_reference_revisions_and_content_hashes(self) -> None:
        """SessionSetup 的发布标识与会话快照逐字节对应。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._copy_sources(root)
            module_bytes = sources.module_reference.read_bytes()
            character_bytes = sources.character_reference.read_bytes()
            fixture = read_json(sources.setup_fixture)
            store = AgenticSessionStore(
                session_root=root / "sessions",
                sources=sources,
                game_id_factory=lambda: "game_reference_identity",
            )

            created = store.create_session(self._request())

            setup = created.session["setup"]
            self.assertEqual(
                setup["module_reference_revision"],
                fixture["module_reference_revision"],
            )
            self.assertEqual(
                setup["character_reference_revision"],
                fixture["character_reference_revision"],
            )
            module_hash = hashlib.sha256(module_bytes).hexdigest()
            character_hash = hashlib.sha256(character_bytes).hexdigest()
            self.assertEqual(setup["module_reference_sha256"], module_hash)
            self.assertEqual(setup["character_reference_sha256"], character_hash)
            self.assertEqual(
                (
                    created.session_directory
                    / "snapshots"
                    / "module_reference"
                    / f"{module_hash}.md"
                ).read_bytes(),
                module_bytes,
            )
            self.assertEqual(
                (
                    created.session_directory
                    / "snapshots"
                    / "character_reference"
                    / f"{character_hash}.md"
                ).read_bytes(),
                character_bytes,
            )

    def test_profile_customization_does_not_change_frozen_mechanics(self) -> None:
        """同一预生成卡的身份资料变化不改 actor_id 或机械值。"""

        with tempfile.TemporaryDirectory() as directory:
            game_ids = iter(("game_profile_a", "game_profile_b"))
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=game_ids.__next__,
                clock=lambda: datetime(2026, 7, 27, tzinfo=timezone.utc),
            )
            first = store.create_session(self._request()).session
            second = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_tracker",
                    display_name="另一姓名",
                    occupation="记者",
                    appearance="戴圆框眼镜",
                )
            ).session

            first_actor = next(
                actor
                for actor in first["actors"]
                if actor["role"] == "investigator"
            )
            second_actor = next(
                actor
                for actor in second["actors"]
                if actor["role"] == "investigator"
            )
            self.assertEqual(first_actor, second_actor)
            self.assertNotEqual(
                first["investigator_profile"],
                second["investigator_profile"],
            )

    def test_create_session_resolves_catalog_base_and_derived_skills(self) -> None:
        """建局时把固定基础值和派生值写入冻结卡。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._copy_sources(root)
            templates = read_json(sources.actor_templates)
            mediator = next(
                actor
                for actor in templates["actors"]
                if actor["actor_id"] == "investigator_mediator"
            )
            del mediator["skill_overrides"]["dodge"]
            write_json_atomic(sources.actor_templates, templates)
            store = AgenticSessionStore(
                session_root=root / "sessions",
                sources=sources,
                game_id_factory=lambda: "game_catalog_values",
            )

            session = store.create_session(
                NewSessionRequest(
                    investigator_id="investigator_mediator",
                    display_name="纪澄",
                )
            ).session
            actor = next(
                item
                for item in session["actors"]
                if item["actor_id"] == "investigator_mediator"
            )
            catalog = read_json(sources.skill_catalog)
            self.assertEqual(
                set(actor["skills"]),
                set(catalog["skills"]) - {"flight"},
            )
            self.assertEqual(actor["skills"]["locksmith"], 1)
            self.assertEqual(actor["skills"]["dodge"], 27)

    def test_source_failures_publish_no_partial_session(self) -> None:
        """六卡模板的结构、边界或交叉引用错误都在发布前停止。"""

        cases = (
            "missing_actor",
            "duplicate_actor",
            "bad_role",
            "unknown_skill",
            "version_mismatch",
            "unsupported_catalog_version",
            "unknown_catalog_skill",
            "unexpected_template_field",
            "display_name_boundary",
            "setting_skill_wrong_owner",
            "attribute_out_of_bounds",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sources = self._copy_sources(root)
                templates = read_json(sources.actor_templates)
                if case == "missing_actor":
                    templates["actors"].pop()
                elif case == "duplicate_actor":
                    templates["actors"].append(dict(templates["actors"][0]))
                elif case == "bad_role":
                    templates["actors"][3]["role"] = "investigator"
                elif case == "unknown_skill":
                    templates["actors"][3]["skill_overrides"]["invented_skill"] = 40
                elif case == "version_mismatch":
                    templates["skill_catalog_version"] = "coc7e-other"
                elif case == "unsupported_catalog_version":
                    catalog = read_json(sources.skill_catalog)
                    catalog["catalog_version"] = "coc7e-other"
                    templates["skill_catalog_version"] = "coc7e-other"
                    write_json_atomic(sources.skill_catalog, catalog)
                elif case == "unknown_catalog_skill":
                    catalog = read_json(sources.skill_catalog)
                    catalog["skills"]["invented_skill"] = {
                        "display_name": "虚构技能",
                        "base": {"kind": "fixed", "value": 20},
                    }
                    write_json_atomic(sources.skill_catalog, catalog)
                elif case == "unexpected_template_field":
                    templates["actors"][0]["mechanic_notes"] = "not schema"
                elif case == "display_name_boundary":
                    fixture = read_json(sources.setup_fixture)
                    fixture["actor_display_names"]["investigator_mediator"] = "纪澄"
                    write_json_atomic(sources.setup_fixture, fixture)
                elif case == "setting_skill_wrong_owner":
                    templates["actors"][0]["skill_overrides"]["flight"] = 40
                else:
                    templates["actors"][5]["attributes"]["strength"] = 101
                write_json_atomic(sources.actor_templates, templates)
                session_root = root / "sessions"
                store = AgenticSessionStore(
                    session_root=session_root,
                    sources=sources,
                    game_id_factory=lambda: "game_invalid_source",
                )

                with self.assertRaises(AgenticSessionSourceError):
                    store.create_session(self._request())

                self.assertFalse(session_root.exists())

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
            corrupt = Path(directory) / "sessions" / "game_corrupt"
            corrupt.mkdir()
            (corrupt / "session.json").write_text("{}", encoding="utf-8")
            ready_bytes = ready.session_file.read_bytes()
            interrupted_bytes = interrupted.session_file.read_bytes()

            found = store.find_incomplete_session_ids()

            self.assertEqual(found, ("game_interrupted",))
            self.assertEqual(ready.session_file.read_bytes(), ready_bytes)
            self.assertEqual(interrupted.session_file.read_bytes(), interrupted_bytes)

    def test_list_session_catalog_projects_valid_sessions_in_updated_order(
        self,
    ) -> None:
        """目录投影只暴露安全摘要，并按更新时间稳定排序。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            game_ids = iter(("game_ongoing", "game_incomplete", "game_complete"))
            store = AgenticSessionStore(
                session_root=root,
                game_id_factory=game_ids.__next__,
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            empty_catalog = store.list_session_catalog()
            self.assertEqual(empty_catalog.sessions, ())
            self.assertEqual(empty_catalog.issues, ())
            ongoing = store.create_session(self._request())
            incomplete = store.create_session(self._request())
            complete = store.create_session(self._request())

            incomplete_model = ScriptedGameMasterModel(
                [ModelCallError("request_timeout", "private", retryable=True)]
            )
            AgenticHarness(
                store,
                incomplete_model,
                turn_id_factory=lambda: "turn_incomplete",
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            ).start_turn(incomplete.game_id, "我检查门锁。")
            complete_model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "narration": "门开了。",
                                    "establish": [],
                                    "retire": [],
                                    "session_status": "complete",
                                },
                                ensure_ascii=False,
                            ),
                            "reasoning_content": None,
                            "tool_calls": [],
                        },
                        finish_reason="stop",
                        usage=None,
                        latency_ms=1,
                    )
                ]
            )
            AgenticHarness(
                store,
                complete_model,
                turn_id_factory=lambda: "turn_complete",
                clock=lambda: datetime(2026, 8, 7, 0, 2, tzinfo=timezone.utc),
            ).start_turn(complete.game_id, "我推开门。")

            for game_id, updated_at in (
                (ongoing.game_id, "2026-08-07T00:03:00Z"),
                (incomplete.game_id, "2026-08-07T00:02:00Z"),
                (complete.game_id, "2026-08-07T00:02:00Z"),
            ):
                session_file = root / game_id / "session.json"
                session = json.loads(session_file.read_text(encoding="utf-8"))
                session["updated_at"] = updated_at
                write_json_atomic(session_file, session)

            before = {
                game_id: (root / game_id / "session.json").read_bytes()
                for game_id in (
                    ongoing.game_id,
                    incomplete.game_id,
                    complete.game_id,
                )
            }
            model_request_counts = (
                len(incomplete_model.requests),
                len(complete_model.requests),
            )

            catalog = store.list_session_catalog()

            self.assertEqual(
                [entry.game_id for entry in catalog.sessions],
                ["game_ongoing", "game_complete", "game_incomplete"],
            )
            self.assertEqual(
                catalog.sessions[0].investigator_display_name,
                "林雁",
            )
            self.assertEqual(catalog.sessions[0].session_status, "ongoing")
            self.assertEqual(catalog.sessions[0].committed_turn_count, 0)
            self.assertEqual(catalog.sessions[0].updated_at, "2026-08-07T00:03:00Z")
            self.assertFalse(catalog.sessions[0].has_incomplete_turn)
            self.assertEqual(catalog.sessions[1].session_status, "complete")
            self.assertEqual(catalog.sessions[1].committed_turn_count, 1)
            self.assertTrue(catalog.sessions[2].has_incomplete_turn)
            self.assertEqual(catalog.sessions[2].committed_turn_count, 0)
            self.assertEqual(catalog.issues, ())
            self.assertEqual(
                (
                    len(incomplete_model.requests),
                    len(complete_model.requests),
                ),
                model_request_counts,
            )
            self.assertEqual(
                {
                    game_id: (root / game_id / "session.json").read_bytes()
                    for game_id in before
                },
                before,
            )

    def test_list_session_catalog_isolates_corrupt_entries_with_safe_issue(
        self,
    ) -> None:
        """损坏条目只产生固定提示，不泄露路径、异常或 provider 内容。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = AgenticSessionStore(
                session_root=root,
                game_id_factory=lambda: "game_valid",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            valid = store.create_session(self._request())
            corrupt = root / "game_corrupt"
            corrupt.mkdir()
            secret = "Authorization: Bearer provider-secret"
            (corrupt / "session.json").write_text(
                json.dumps({"schema_version": {"provider_detail": secret}}),
                encoding="utf-8",
            )
            valid_before = valid.session_file.read_bytes()
            corrupt_before = (corrupt / "session.json").read_bytes()

            catalog = store.list_session_catalog()

            self.assertEqual([entry.game_id for entry in catalog.sessions], [valid.game_id])
            self.assertEqual(len(catalog.issues), 1)
            self.assertIsInstance(catalog.issues[0], SessionCatalogIssue)
            self.assertFalse(catalog.issues[0].selectable)
            self.assertEqual(
                catalog.issues[0].message,
                "有一个 session 无法读取，已跳过。",
            )
            rendered = repr(catalog)
            self.assertNotIn(secret, rendered)
            self.assertNotIn(str(corrupt), rendered)
            self.assertNotIn("Traceback", rendered)
            self.assertEqual(valid.session_file.read_bytes(), valid_before)
            self.assertEqual(
                (corrupt / "session.json").read_bytes(),
                corrupt_before,
            )

    def test_get_session_review_projects_zero_turn_public_state(self) -> None:
        """零回合回顾使用冻结开场，并过滤隐藏或非 active 事实。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_review",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            session = json.loads(created.session_file.read_text(encoding="utf-8"))
            hidden_text = "不应出现在玩家回顾中的秘密"
            session["facts"][0]["visibility"] = "hidden"
            session["facts"][0]["text"] = hidden_text
            write_json_atomic(created.session_file, session)
            before = created.session_file.read_bytes()

            review = store.get_session_review(created.game_id)

            self.assertEqual(review.game_id, created.game_id)
            self.assertEqual(review.investigator_display_name, "林雁")
            self.assertEqual(review.session_status, "ongoing")
            self.assertEqual(review.committed_turn_count, 0)
            self.assertEqual(review.latest_narration, None)
            self.assertEqual(
                review.opening_narration,
                session["setup"]["opening_narration"],
            )
            self.assertTrue(review.public_facts)
            self.assertNotIn(hidden_text, repr(review))
            self.assertEqual(created.session_file.read_bytes(), before)

    def test_get_session_review_uses_latest_narration_and_filters_hidden_facts(
        self,
    ) -> None:
        """有回合回顾使用最近叙事，只保留当前有效公开事实。"""

        response = ModelResponse(
            assistant_message={
                "role": "assistant",
                "content": json.dumps(
                    {
                        "narration": "你在墙缝里找到一枚新铜片。",
                        "establish": [
                            {"visibility": "public", "text": "墙缝里有新铜片"},
                            {"visibility": "hidden", "text": "守卫已经听见动静"},
                        ],
                        "retire": [],
                        "session_status": "ongoing",
                    },
                    ensure_ascii=False,
                ),
                "reasoning_content": "provider reasoning must stay private",
                "tool_calls": [],
            },
            finish_reason="stop",
            usage=None,
            latency_ms=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_review_turn",
                clock=lambda: datetime(2026, 8, 7, tzinfo=timezone.utc),
            )
            created = store.create_session(self._request())
            model = ScriptedGameMasterModel([response])
            AgenticHarness(
                store,
                model,
                turn_id_factory=lambda: "turn_review",
                fact_id_factory=iter(("fact_public", "fact_hidden")).__next__,
                clock=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
            ).start_turn(created.game_id, "我检查墙缝。")

            review = store.get_session_review(created.game_id)

        self.assertEqual(review.committed_turn_count, 1)
        self.assertEqual(review.latest_narration, "你在墙缝里找到一枚新铜片。")
        public_texts = {fact.text for fact in review.public_facts}
        self.assertIn("墙缝里有新铜片", public_texts)
        self.assertNotIn("守卫已经听见动静", public_texts)
        self.assertNotIn("provider reasoning must stay private", repr(review))

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
            model = ScriptedGameMasterModel(
                [
                    ModelResponse(
                        assistant_message={
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "narration": "快照中的牢门仍在你眼前。",
                                    "establish": [],
                                    "retire": [],
                                    "session_status": "ongoing",
                                },
                                ensure_ascii=False,
                            ),
                            "tool_calls": [],
                        },
                        finish_reason="stop",
                        usage=None,
                        latency_ms=1,
                    )
                ]
            )
            AgenticHarness(store, model).start_turn(
                created.game_id,
                "我继续观察牢门。",
            )
            package = json.loads(model.requests[0].messages[1]["content"])
            self.assertEqual(package["MODULE_REFERENCE"], module_reference)
            self.assertEqual(package["CHARACTER_REFERENCE"], character_reference)
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

    def test_load_session_uses_frozen_actor_sheets_after_sources_are_removed(self) -> None:
        """既有会话装载不回读已删除的模板或技能目录。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = self._copy_sources(root)
            store = AgenticSessionStore(
                session_root=root / "sessions",
                sources=sources,
                game_id_factory=lambda: "game_frozen_actors",
            )
            created = store.create_session(self._request())
            frozen_actors = created.session["actors"]
            sources.actor_templates.unlink()
            sources.skill_catalog.unlink()

            loaded = store.load_session(created.game_id)

            self.assertEqual(loaded.session["actors"], frozen_actors)

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

    def test_load_session_rejects_skill_outside_frozen_catalog(self) -> None:
        """目录外技能不能被注入冻结卡并成为可信检定能力。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_unknown_frozen_skill",
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["actors"][0]["skills"]["invented_skill"] = 99
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ActorSheet 格式无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_accepts_historical_single_actor_snapshot_without_upgrading(self) -> None:
        """历史单卡存档保持原样装载，不从当前模板补入 NPC。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_historical_single_actor",
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            legacy_actor = next(
                actor
                for actor in session["actors"]
                if actor["actor_id"] == "investigator_tracker"
            )
            legacy_actor["skills"]["flight"] = 1
            session["schema_version"] = "agentic-mvp-1"
            session["actors"] = [legacy_actor]
            write_json_atomic(created.session_file, session)

            loaded = store.load_session(created.game_id)

            self.assertEqual(len(loaded.session["actors"]), 1)
            self.assertEqual(
                loaded.session["actors"][0]["actor_id"],
                "investigator_tracker",
            )

    def test_load_session_rejects_current_schema_single_actor_roster(self) -> None:
        """v2 会话不能通过删除三张 NPC 卡伪装成历史单卡。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_current_single_actor",
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["actors"] = [session["actors"][0]]
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ActorSheet 角色集合无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_legacy_schema_four_actor_roster(self) -> None:
        """v1 只代表历史单卡，不接受当前四卡形状。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_legacy_four_actors",
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["schema_version"] = "agentic-mvp-1"
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ActorSheet 角色集合无效",
            ):
                store.load_session(created.game_id)

    def test_load_session_rejects_partial_production_actor_roster(self) -> None:
        """新四卡存档缺少任一固定同行者时不能进入 GM 上下文。"""

        with tempfile.TemporaryDirectory() as directory:
            store = AgenticSessionStore(
                session_root=Path(directory) / "sessions",
                game_id_factory=lambda: "game_partial_roster",
            )
            created = store.create_session(self._request())
            session = read_json(created.session_file)
            session["actors"] = [
                actor
                for actor in session["actors"]
                if actor["actor_id"] != "npc_aranis"
            ]
            write_json_atomic(created.session_file, session)

            with self.assertRaisesRegex(
                AgenticSessionLoadError,
                "ActorSheet 角色集合无效",
            ):
                store.load_session(created.game_id)


if __name__ == "__main__":
    unittest.main()
