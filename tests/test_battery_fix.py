import json
import plistlib
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import antigravity_battery_fix as abf


class TestAntigravityBatteryFix(unittest.TestCase):
    def test_strip_jsonc_handles_comments_and_trailing_commas(self) -> None:
        raw = """
        {
            // Theme comment with https://url/inside/string preserved below
            "workbench.colorTheme": "Solarized Dark", /* inline block */
            "url": "https://example.com//not-a-comment",
            "files.watcherExclude": {
                "**/custom/**": true,
            },
        }
        """
        parsed = json.loads(abf.strip_jsonc(raw))
        self.assertEqual(parsed["workbench.colorTheme"], "Solarized Dark")
        self.assertEqual(parsed["url"], "https://example.com//not-a-comment")
        self.assertTrue(parsed["files.watcherExclude"]["**/custom/**"])

    def test_merge_and_manifest_restore_preserves_user_edits(self) -> None:
        initial = {
            "workbench.colorTheme": "Solarized Dark",
            "workbench.localHistory.enabled": True,
            "files.watcherExclude": {"**/my_custom_dir/**": True},
        }
        updated, manifest = abf.merge_settings(initial, ram_gb=8)
        self.assertFalse(updated["workbench.localHistory.enabled"])
        self.assertEqual(updated["typescript.tsserver.maxTsServerMemory"], 1536)
        self.assertEqual(updated["antigravity.heartbeatIntervalMs"], 60000)
        self.assertTrue(updated["workbench.editor.limit.excludeDirty"])
        self.assertTrue(updated["files.watcherExclude"]["**/my_custom_dir/**"])
        self.assertTrue(updated["files.watcherExclude"]["**/dist/**"])
        self.assertTrue(updated["search.exclude"]["**/package-lock.json"])

        # Simulate the user adding a new setting after --apply
        updated["editor.fontSize"] = 14

        restored = abf.restore_settings_from_manifest(updated, manifest)
        self.assertEqual(restored["workbench.colorTheme"], "Solarized Dark")
        self.assertTrue(restored["workbench.localHistory.enabled"])
        self.assertEqual(restored["editor.fontSize"], 14)
        self.assertEqual(restored["files.watcherExclude"], {"**/my_custom_dir/**": True})
        self.assertNotIn("antigravity.heartbeatIntervalMs", restored)
        self.assertNotIn("search.exclude", restored)

    def test_cli_apply_and_restore_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            settings_path = tmp_dir / "settings.json"
            la_path = tmp_dir / "local.antigravity.uv-threadpool.plist"
            fake_app = tmp_dir / "NonExistent.app"

            settings_path.write_text(
                '{\n    // user comment\n    "workbench.colorTheme": "Monokai",\n}\n',
                encoding="utf-8",
            )

            rc_apply = abf.main(
                [
                    "--apply",
                    "--settings-path",
                    str(settings_path),
                    "--launchagent-path",
                    str(la_path),
                    "--app-bundle",
                    str(fake_app),
                    "--ram-gb",
                    "8",
                    "--allow-non-darwin",
                ]
            )
            self.assertEqual(rc_apply, 0)
            self.assertTrue(la_path.exists())
            plist_data = plistlib.loads(la_path.read_bytes())
            self.assertEqual(plist_data["ProgramArguments"][-1], "8")

            applied_cfg = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertFalse(applied_cfg["workbench.localHistory.enabled"])

            rc_restore = abf.main(
                [
                    "--restore",
                    "--settings-path",
                    str(settings_path),
                    "--launchagent-path",
                    str(la_path),
                ]
            )
            self.assertEqual(rc_restore, 0)
            self.assertFalse(la_path.exists())
            restored_cfg = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(restored_cfg, {"workbench.colorTheme": "Monokai"})


if __name__ == "__main__":
    unittest.main()

