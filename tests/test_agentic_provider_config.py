from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

from monmusu_agent.agentic_cli import (
    ProviderConfig,
    ProviderConfigError,
    configure_provider,
    main,
    provider_config_from_env,
    save_provider_config,
    validated_base_url,
)
from monmusu_agent.agentic_model import (
    DeepSeekGameMasterModel,
    deepseek_model_profile,
    validated_model_profile,
)

DEEPSEEK_BASE = "https://api.deepseek.com"
OPENCODE_GO_BASE = "https://opencode.ai/zen/go/v1"


def _read_line(answers: list[str]):
    def read_line(prompt: str) -> str:
        assert answers, f"unexpected prompt: {prompt}"
        return answers.pop(0)

    return read_line


class ProviderConfigFromEnvTest(unittest.TestCase):
    def test_missing_provider_marker_means_unconfigured_even_with_key(self) -> None:
        self.assertIsNone(
            provider_config_from_env(
                {"DEEPSEEK_API_KEY": "sk-existing-secret"}
            )
        )

    def test_deepseek_marker_and_key_resolve_official_default(self) -> None:
        config = provider_config_from_env(
            {
                "MONMUSU_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "sk-ds-secret",
            }
        )
        assert config is not None
        self.assertEqual(config.provider, "deepseek")
        self.assertEqual(config.api_key, "sk-ds-secret")
        self.assertEqual(config.base_url, DEEPSEEK_BASE)

    def test_opencode_go_marker_resolves_openai_compatible_default(self) -> None:
        config = provider_config_from_env(
            {
                "MONMUSU_PROVIDER": "opencode-go",
                "OPENCODE_GO_API_KEY": "sk-go-secret",
            }
        )
        assert config is not None
        self.assertEqual(config.provider, "opencode-go")
        self.assertEqual(config.base_url, OPENCODE_GO_BASE)

    def test_custom_requires_key_and_base_url(self) -> None:
        config = provider_config_from_env(
            {
                "MONMUSU_PROVIDER": "custom",
                "MONMUSU_CUSTOM_API_KEY": "sk-custom-secret",
                "MONMUSU_CUSTOM_BASE_URL": "https://gateway.example.com/v1",
            }
        )
        assert config is not None
        self.assertEqual(config.provider, "custom")
        self.assertEqual(config.base_url, "https://gateway.example.com/v1")

    def test_unknown_provider_is_rejected_without_returning_key(self) -> None:
        with self.assertRaises(ProviderConfigError):
            provider_config_from_env(
                {
                    "MONMUSU_PROVIDER": "not-a-provider",
                    "DEEPSEEK_API_KEY": "sk-secret",
                }
            )

    def test_provider_with_empty_key_remains_unconfigured(self) -> None:
        self.assertIsNone(
            provider_config_from_env(
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "   ",
                }
            )
        )

    def test_custom_without_base_url_is_rejected(self) -> None:
        with self.assertRaises(ProviderConfigError):
            provider_config_from_env(
                {
                    "MONMUSU_PROVIDER": "custom",
                    "MONMUSU_CUSTOM_API_KEY": "sk-custom-secret",
                }
            )

    def test_explicit_base_url_env_overrides_known_default(self) -> None:
        config = provider_config_from_env(
            {
                "MONMUSU_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "sk-ds-secret",
                "DEEPSEEK_BASE_URL": "https://proxy.example.com/v1",
            }
        )
        assert config is not None
        self.assertEqual(config.base_url, "https://proxy.example.com/v1")


class BaseUrlValidationTest(unittest.TestCase):
    def test_accepts_http_and_https_and_trims(self) -> None:
        self.assertEqual(
            validated_base_url("  https://gateway.example.com/v1  "),
            "https://gateway.example.com/v1",
        )
        self.assertEqual(
            validated_base_url("http://localhost:8000/v1"),
            "http://localhost:8000/v1",
        )

    def test_rejects_empty_missing_scheme_and_ftp(self) -> None:
        for value in ("", "   ", "gateway.example.com/v1", "ftp://host/v1"):
            with self.subTest(value=value):
                with self.assertRaises(ProviderConfigError):
                    validated_base_url(value)


class SaveProviderConfigTest(unittest.TestCase):
    def test_save_deepseek_updates_owned_keys_and_preserves_other_lines(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "UNRELATED_SETTING=keep-me\n"
                "DEEPSEEK_API_KEY=old-secret\n",
                encoding="utf-8",
            )
            save_provider_config(
                env_path,
                ProviderConfig(
                    provider="deepseek",
                    api_key="sk-new-secret",
                    base_url=DEEPSEEK_BASE,
                ),
            )
            values = dotenv_values(env_path)
            self.assertEqual(values["UNRELATED_SETTING"], "keep-me")
            self.assertEqual(values["MONMUSU_PROVIDER"], "deepseek")
            self.assertEqual(values["DEEPSEEK_API_KEY"], "sk-new-secret")
            self.assertEqual(values["DEEPSEEK_BASE_URL"], DEEPSEEK_BASE)
            self.assertNotIn("old-secret", env_path.read_text(encoding="utf-8"))

    def test_save_custom_writes_custom_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            save_provider_config(
                env_path,
                ProviderConfig(
                    provider="custom",
                    api_key="sk-custom-secret",
                    base_url="https://gateway.example.com/v1",
                ),
            )
            values = dotenv_values(env_path)
            self.assertEqual(values["MONMUSU_PROVIDER"], "custom")
            self.assertEqual(
                values["MONMUSU_CUSTOM_API_KEY"],
                "sk-custom-secret",
            )
            self.assertEqual(
                values["MONMUSU_CUSTOM_BASE_URL"],
                "https://gateway.example.com/v1",
            )


class ConfigureProviderWizardTest(unittest.TestCase):
    def test_first_run_menu_supports_opencode_go_without_base_url_prompt(
        self,
    ) -> None:
        output: list[str] = []
        config = configure_provider(
            {},
            read_line=_read_line(["2", "sk-go-secret"]),
            write_line=output.append,
        )
        self.assertEqual(
            config,
            ProviderConfig(
                provider="opencode-go",
                api_key="sk-go-secret",
                base_url=OPENCODE_GO_BASE,
            ),
        )
        self.assertIn("1. DeepSeek 官方", "\n".join(output))
        self.assertIn("2. OpenCode Go", "\n".join(output))
        self.assertNotIn("Base URL", "\n".join(output))

    def test_custom_provider_warns_and_requires_base_url(self) -> None:
        output: list[str] = []
        config = configure_provider(
            {},
            read_line=_read_line(
                ["3", "https://gateway.example.com/v1", "sk-custom-secret"]
            ),
            write_line=output.append,
        )
        self.assertEqual(config.provider, "custom")
        self.assertEqual(config.base_url, "https://gateway.example.com/v1")
        self.assertIn(
            "当前版本仍按 DeepSeek 系列模型调用",
            "\n".join(output),
        )

    def test_custom_provider_reprompts_invalid_base_url(self) -> None:
        output: list[str] = []
        config = configure_provider(
            {},
            read_line=_read_line(
                [
                    "3",
                    "ftp://invalid.example/v1",
                    "https://gateway.example.com/v1",
                    "sk-custom-secret",
                ]
            ),
            write_line=output.append,
        )
        self.assertEqual(config.base_url, "https://gateway.example.com/v1")
        self.assertIn("http:// 或 https://", "\n".join(output))

    def test_existing_key_can_be_kept_with_blank_answer(self) -> None:
        output: list[str] = []
        prompts: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            return prompts.__len__() == 2 and "" or (
                "1" if len(prompts) == 1 else ""
            )

        config = configure_provider(
            {"DEEPSEEK_API_KEY": "sk-existing-secret"},
            read_line=read_line,
            write_line=output.append,
        )
        self.assertEqual(config.api_key, "sk-existing-secret")
        self.assertIn("直接回车保留", prompts[-1])


class ConfigureProviderCurrentFlowTest(unittest.TestCase):
    def test_blank_selection_and_blank_key_keep_current_deepseek(self) -> None:
        output: list[str] = []
        config = configure_provider(
            {
                "MONMUSU_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "sk-current-secret",
            },
            read_line=_read_line(["", ""]),
            write_line=output.append,
        )
        self.assertEqual(
            config,
            ProviderConfig(
                provider="deepseek",
                api_key="sk-current-secret",
                base_url=DEEPSEEK_BASE,
            ),
        )
        rendered = "\n".join(output)
        self.assertIn("当前模型提供商：DeepSeek 官方", rendered)
        self.assertIn("API Key：已保存（****）", rendered)

    def test_switching_provider_requires_a_new_key(self) -> None:
        output: list[str] = []
        prompts: list[str] = []

        def read_line(prompt: str) -> str:
            prompts.append(prompt)
            if "模型提供商编号" in prompt:
                return "2"
            return "sk-new-go-secret"

        config = configure_provider(
            {
                "MONMUSU_PROVIDER": "deepseek",
                "DEEPSEEK_API_KEY": "sk-old-deepseek-secret",
            },
            read_line=read_line,
            write_line=output.append,
        )
        self.assertEqual(config.provider, "opencode-go")
        self.assertEqual(config.api_key, "sk-new-go-secret")
        self.assertNotIn("sk-old-deepseek-secret", "\n".join(prompts))


class MainProviderFlowTest(unittest.TestCase):
    def test_existing_config_loads_directly_and_prints_provider_only(self) -> None:
        output = io.StringIO()
        store = object()
        harness = object()
        with (
            patch.dict(
                os.environ,
                {
                    "MONMUSU_PROVIDER": "deepseek",
                    "DEEPSEEK_API_KEY": "sk-cli-secret",
                    "MONMUSU_DEEPSEEK_MODEL_ID": "deepseek-v4-flash",
                    "MONMUSU_DEEPSEEK_THINKING": "false",
                },
                clear=True,
            ),
            patch("monmusu_agent.agentic_cli.load_dotenv") as load_dotenv,
            patch(
                "monmusu_agent.agentic_cli.AgenticSessionStore",
                return_value=store,
            ),
            patch(
                "monmusu_agent.agentic_cli.compose_deepseek_harness",
                return_value=harness,
            ) as compose,
            patch("monmusu_agent.agentic_cli.run_agentic_cli") as run_cli,
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 0)

        load_dotenv.assert_called_once()
        self.assertEqual(load_dotenv.call_args.kwargs.get("override"), False)
        compose.assert_called_once_with(
            store,
            api_key="sk-cli-secret",
            model_id="deepseek-v4-flash",
            thinking=False,
            provider="deepseek",
            base_url=DEEPSEEK_BASE,
        )
        run_cli.assert_called_once_with(harness, store)
        rendered = output.getvalue()
        self.assertIn("模型提供商：DeepSeek 官方", rendered)
        self.assertNotIn("sk-cli-secret", rendered)
        self.assertNotIn("https://", rendered)

    def test_configure_flag_writes_config_without_creating_session_or_model(
        self,
    ) -> None:
        output = io.StringIO()
        configured = ProviderConfig(
            provider="opencode-go",
            api_key="sk-go-secret",
            base_url=OPENCODE_GO_BASE,
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch(
                "monmusu_agent.agentic_cli.configure_provider",
                return_value=configured,
            ) as configure,
            patch(
                "monmusu_agent.agentic_cli.save_provider_config"
            ) as save_config,
            patch("monmusu_agent.agentic_cli.AgenticSessionStore") as store,
            patch("monmusu_agent.agentic_cli.compose_deepseek_harness") as compose,
            patch("monmusu_agent.agentic_cli.run_agentic_cli") as run_cli,
            redirect_stdout(output),
        ):
            self.assertEqual(main(["--configure"]), 0)

        configure.assert_called_once()
        save_config.assert_called_once()
        store.assert_not_called()
        compose.assert_not_called()
        run_cli.assert_not_called()

    def test_noninteractive_without_config_prints_hint_and_exits_2(self) -> None:
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("monmusu_agent.agentic_cli.load_dotenv"),
            patch(
                "monmusu_agent.agentic_cli._stdin_is_interactive",
                return_value=False,
            ),
            patch(
                "monmusu_agent.agentic_cli.AgenticSessionStore"
            ) as store,
            patch(
                "monmusu_agent.agentic_cli.compose_deepseek_harness"
            ) as compose,
            redirect_stdout(output),
        ):
            self.assertEqual(main([]), 2)

        store.assert_not_called()
        compose.assert_not_called()
        rendered = output.getvalue()
        self.assertIn("--configure", rendered)
        self.assertIn("MONMUSU_PROVIDER", rendered)


class ModelProfileBaseUrlTest(unittest.TestCase):
    def test_deepseek_profile_contains_official_base_url(self) -> None:
        profile = deepseek_model_profile()
        self.assertEqual(profile["base_url"], DEEPSEEK_BASE)

    def test_legacy_profile_without_base_url_gets_provider_default(self) -> None:
        profile = deepseek_model_profile()
        legacy = {key: value for key, value in profile.items() if key != "base_url"}
        rebuilt = validated_model_profile(
            legacy,
            enabled_tools=profile["enabled_tools"],
        )
        self.assertEqual(rebuilt["base_url"], DEEPSEEK_BASE)

    def test_unknown_provider_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            deepseek_model_profile(provider="unsupported")

    def test_adapter_uses_injected_base_url(self) -> None:
        client = object()
        with patch("openai.OpenAI", return_value=client) as constructor:
            DeepSeekGameMasterModel(
                "sk-secret",
                base_url="https://gateway.example.com/v1",
            )
        constructor.assert_called_once_with(
            api_key="sk-secret",
            base_url="https://gateway.example.com/v1",
        )


if __name__ == "__main__":
    unittest.main()
