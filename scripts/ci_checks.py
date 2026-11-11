"""Repository rules CI enforces (from the assignment):
  1. no secrets in the repo           - scans tracked files for key patterns; .env must not be tracked
  2. nothing under gigsim/ changed    - gigsim/ files must match gigsim/MANIFEST.sha256 (frozen simulator)
  3. every prompt lives in agent/prompts/
  4. every number in README.md traces to a file in evals/results/ (numbers in the RESULTS block are regenerated
     from those files and must match exactly)

    python -m scripts.ci_checks            (exit 1 on any violation)
    python -m scripts.ci_checks --freeze-gigsim   (write the manifest; only when GigSim is intentionally released)
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(sk-or-v1-[0-9a-f]{20,}|gsk_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|KGAT_[A-Za-z0-9]{10,}|"
                    r"ghp_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16})")


def tracked() -> list[str]:
    return subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()


def manifest() -> str:
    lines = []
    for f in sorted(p for p in (ROOT / "gigsim").rglob("*") if p.is_file() and "state" not in p.parts
                    and "__pycache__" not in p.parts and p.name != "MANIFEST.sha256"):
        lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(ROOT)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if "--freeze-gigsim" in sys.argv:
        (ROOT / "gigsim/MANIFEST.sha256").write_text(manifest())
        print("gigsim manifest written")
        return 0
    errors = []
    files = tracked()
    if ".env" in files:
        errors.append(".env is tracked")
    for f in files:
        p = ROOT / f
        if p.suffix in (".png", ".pdf", ".db", ".zip") or not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        try:
            if SECRET.search(p.read_text(errors="ignore")):
                errors.append(f"possible secret in {f}")
        except OSError:
            pass
    m = ROOT / "gigsim/MANIFEST.sha256"
    if not m.exists() or m.read_text() != manifest():
        errors.append("gigsim/ differs from gigsim/MANIFEST.sha256 (the simulator must not be modified)")
    for f in files:
        if f.startswith("agent/") and f.endswith(".py") and "prompts" not in f:
            src = (ROOT / f).read_text()
            if re.search(r"(?im)^\s*[\"']{1,3}you are (the|a) [a-z ]{3,40}(in gigbid|for (one|a) freelancer)", src):
                errors.append(f"inline prompt in {f}")
    readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
    if "<!-- RESULTS:START" in readme:
        before = readme[readme.index("<!-- RESULTS:START"):readme.index("<!-- RESULTS:END -->")]
        out = subprocess.run([sys.executable, "-m", "evals.runners.results_table"], cwd=ROOT, capture_output=True, text=True)
        after = (ROOT / "README.md").read_text()
        regenerated = after[after.index("<!-- RESULTS:START"):after.index("<!-- RESULTS:END -->")]
        (ROOT / "README.md").write_text(readme)
        if out.returncode != 0 or regenerated.strip() != before.strip():
            errors.append("README results table does not match evals/results/*.json (run evals.runners.results_table)")
    for e in errors:
        print("FAIL:", e)
    print("ci checks:", "OK" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
