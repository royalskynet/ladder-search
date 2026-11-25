"""Codex packaging intent: one canonical skill under skills/, a portable root plugin.json,
and root symlinks only for backwards-compatible direct skill clones."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "ladder-search"


def test_portable_manifest_matches_claude_manifest_and_real_skill():
    portable = json.loads((ROOT / "plugin.json").read_text())
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert portable["name"] == claude["name"] == "ladder-search"
    assert portable["version"] == claude["version"]
    assert portable["extensions"]["com.openai"]["hooks"] == "./hooks/hooks.json"
    assert (SKILL / "SKILL.md").is_file()
    assert (SKILL / "scripts" / "ladder.py").is_file()


def test_claude_manifest_scans_canonical_skill_and_hook_path_exists():
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    command = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert claude["skills"] == ["./skills/ladder-search/"]
    assert "skills/ladder-search/scripts/hint.py" in command
    assert (SKILL / "scripts" / "hint.py").is_file()


def test_root_compatibility_links_resolve_for_direct_skill_clones():
    assert (ROOT / "SKILL.md").is_symlink()
    assert (ROOT / "scripts").is_symlink()
    assert (ROOT / "SKILL.md").resolve() == SKILL / "SKILL.md"
    assert (ROOT / "scripts" / "ladder.py").resolve() == SKILL / "scripts" / "ladder.py"


def test_skill_frontmatter_is_open_agent_shape_and_host_neutral():
    text = (SKILL / "SKILL.md").read_text()
    frontmatter = text.split("---", 2)[1]
    values = dict(line.split(":", 1) for line in frontmatter.splitlines() if ":" in line and not line.startswith(" "))
    version = json.loads((ROOT / "plugin.json").read_text())["version"]
    assert values["name"].strip() == "ladder-search"
    assert 0 < len(values["description"].strip()) <= 500
    assert f"version: {version}" in frontmatter
    assert "${CLAUDE_SKILL_DIR}" not in text
