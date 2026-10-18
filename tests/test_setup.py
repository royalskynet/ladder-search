"""setup.sh intent: on a bare machine, check mode runs no installer; --apply runs each installer,
never sudo, never `agent-reach install --system`."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = ("npm", "uv", "claude", "git", "node")


def _bare_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in STUBS:
        body = f'#!/bin/sh\necho "{name} $*" >> "{log}"\n'
        if name == "node":
            body = "#!/bin/sh\necho 22\n"
        if name == "claude":  # read-only `claude plugin list` reports nothing installed and is not an install
            body = '#!/bin/sh\n[ "$1 $2" = "plugin list" ] && exit 0\n' + body.split("\n", 1)[1]
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    for tool in ("bash", "dirname", "grep", "head", "uname"):  # shell basics only; no mcporter/agent-reach/uvx
        os.symlink(subprocess.check_output(["/usr/bin/which", tool], text=True).strip(), bin_dir / tool)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home")}
    return env, log


def _run(env, *args):
    return subprocess.run([str(ROOT / "scripts" / "setup.sh"), *args], env=env, capture_output=True, text=True)


def test_check_mode_installs_nothing(tmp_path):
    env, log = _bare_env(tmp_path)
    p = _run(env)
    assert p.returncode == 0, p.stderr
    assert "would run: npm i -g mcporter" in p.stdout
    assert "would run: uv tool install" in p.stdout
    assert "would run: claude plugin install web-access@web-access" in p.stdout
    assert "--apply" in p.stdout
    assert not log.exists()


def test_apply_runs_installers_but_not_system(tmp_path):
    env, log = _bare_env(tmp_path)
    p = _run(env, "--apply")
    calls = log.read_text()
    assert "npm i -g mcporter" in calls
    assert "uv tool install https://github.com/Panniantong/agent-reach/archive/main.zip" in calls
    assert "claude plugin install web-access@web-access --scope user" in calls
    assert "--system" not in calls and "sudo" not in calls
    assert "agent-reach install --env=auto --system" in p.stdout  # printed as a next step only
    assert "+ mcporter config add exa --url https://mcp.exa.ai/mcp --scope home" in p.stdout  # default is ./config


def test_detects_exa_when_list_output_is_long(tmp_path):
    # `list | grep -q` under pipefail failed once grep exited early and SIGPIPEd mcporter
    env, log = _bare_env(tmp_path)
    mcp = Path(env["PATH"]) / "mcporter"
    mcp.write_text('#!/bin/sh\necho exa\ni=0; while [ $i -lt 20000 ]; do echo "  filler line $i"; i=$((i+1)); done\n')
    mcp.chmod(0o755)
    p = _run(env)
    assert "ok   exa server" in p.stdout, p.stdout
