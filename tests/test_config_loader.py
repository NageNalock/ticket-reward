from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from src.utils.config_loader import ConfigError, load_config, validate_config


class ConfigLoaderTests(unittest.TestCase):
    def test_repository_config_is_valid(self) -> None:
        config = load_config()
        self.assertEqual(config["search"]["pc_count"], 30)
        self.assertEqual(config["search"]["mobile_count"], 20)
        self.assertEqual(config["schedule"]["retry_count"], 3)
        self.assertEqual(config["schedule"]["retry_interval_min"], 30)

    def test_absolute_config_path_is_supported(self) -> None:
        source = load_config()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(source), encoding="utf-8")
            loaded = load_config(path)
        self.assertEqual(loaded["browser"]["viewport"]["width"], 1280)

    def test_existing_config_receives_retry_defaults(self) -> None:
        source = load_config()
        source["schedule"].pop("retry_count")
        source["schedule"].pop("retry_interval_min")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(yaml.safe_dump(source), encoding="utf-8")
            loaded = load_config(path)
        self.assertEqual(loaded["schedule"]["retry_count"], 3)
        self.assertEqual(loaded["schedule"]["retry_interval_min"], 30)

    def test_invalid_search_interval_is_rejected(self) -> None:
        config = load_config()
        config["search"]["min_interval_sec"] = 9
        config["search"]["max_interval_sec"] = 3
        with self.assertRaises(ConfigError):
            validate_config(config)

    def test_invalid_retry_policy_is_rejected(self) -> None:
        config = load_config()
        config["schedule"]["retry_interval_min"] = 0
        with self.assertRaises(ConfigError):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
