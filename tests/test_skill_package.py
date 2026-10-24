import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _npm_version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def _py_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "version not found in pyproject.toml"
    return match.group(1)


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

    def test_npm_minor_matches_python_minor(self):
        npm = _npm_version().split(".")
        py = _py_version().split(".")
        self.assertEqual(npm[:2], py[:2])

    def test_manifest_in_excludes_node_artifacts(self):
        text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for token in ("exclude package.json", "prune bin", "prune lib",
                      "prune skills", "prune test", "prune node_modules"):
            self.assertIn(token, text)
