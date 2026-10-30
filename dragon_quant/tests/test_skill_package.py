import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class TestPackageMetadata(unittest.TestCase):

    def test_package_json_contract(self):
        data = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "dragon-quant-skills")
        self.assertEqual(data["type"], "module")
        self.assertEqual(data["license"], "MIT")
        self.assertEqual(data["engines"]["node"], ">=22.20.0")
        self.assertEqual(data["bin"], {"dragon-quant-skills": "bin/dragon-quant-skills.mjs"})
        self.assertEqual(data["files"], ["bin/", "lib/", "skills/", "README.md", "LICENSE"])
        self.assertNotIn("dependencies", data)
        self.assertNotIn("devDependencies", data)
        for key in ("preinstall", "postinstall", "prepare"):
            self.assertNotIn(key, data.get("scripts", {}))

    def test_manifest_in_excludes_node_artifacts(self):
        text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for token in ("exclude package.json", "prune bin", "prune lib",
                      "prune skills", "prune test", "prune dragon_quant/tests",
                      "prune node_modules"):
            self.assertIn(token, text)
