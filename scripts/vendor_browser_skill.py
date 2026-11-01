"""Refresh the vendored BrowserSkill extension and skill from upstream.

Development tool only. Run it when moving to a new `cli-v*` release; the
runtime never calls it.

    python -m scripts.vendor_browser_skill --tag cli-v0.4.0
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "yoreni" / "sandbox" / "assets" / "browser-skill"
SKILL = ROOT / "yoreni" / "skills" / "browser-skill"
REPO = "https://github.com/Tencent/BrowserSkill.git"


YORENI_HEADING = "## Yoreni environment"
UPSTREAM_HEADING = "## Upstream workflow"


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def _yoreni_preamble(skill_md: str) -> str:
    """Extract Yoreni's sandbox guidance, which upstream does not have.

    It sits between the skill's title and the first upstream section, so it is
    recovered from the vendored copy before the tree is replaced.
    """
    title = re.search(r"^# .*$", skill_md, re.MULTILINE)
    end = skill_md.find(UPSTREAM_HEADING)
    if title is None or end < title.end() or YORENI_HEADING not in skill_md[title.end():end]:
        raise SystemExit(
            f"{SKILL / 'SKILL.md'} lost its title, {YORENI_HEADING!r}, or "
            f"{UPSTREAM_HEADING!r}; re-vendoring would drop the sandbox guidance"
        )
    return skill_md[title.end():end].strip("\n") + "\n\n"


def _compose_skill(upstream: str, preamble: str) -> str:
    """Keep the upstream title and body, and slot the Yoreni guidance between them."""
    front, separator, body = upstream.partition("\n---\n")
    if not separator:
        raise SystemExit("the upstream SKILL.md has no frontmatter")
    title = re.search(r"^# .*$", body, re.MULTILINE)
    if title is None:
        raise SystemExit("the upstream SKILL.md has no title")
    head = f"{front}{separator}{body[:title.end()]}\n\n"
    return f"{head}{preamble}{UPSTREAM_HEADING}\n\n{body[title.end():].strip(chr(10))}\n"


def vendor(tag: str) -> None:
    preamble = _yoreni_preamble((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="browserskill-") as temporary:
        source = Path(temporary) / "src"
        _run("git", "clone", "--depth", "1", "--branch", tag, REPO, str(source), cwd=Path(temporary))
        _run("pnpm", "install", "--frozen-lockfile", cwd=source)
        _run("pnpm", "ext:build", cwd=source)

        built = source / "apps" / "extension" / "dist" / "chrome-mv3"
        if not (built / "manifest.json").is_file():
            raise SystemExit(f"{built} has no manifest.json; upstream changed its output layout")
        shutil.rmtree(ASSETS / "extension", ignore_errors=True)
        shutil.copytree(built, ASSETS / "extension")
        shutil.copy2(source / "LICENSE", ASSETS / "LICENSE")
        (ASSETS / "VERSION").write_text(tag + "\n", encoding="utf-8")

        shutil.rmtree(SKILL, ignore_errors=True)
        shutil.copytree(source / "crates" / "bsk-cli" / "skill", SKILL)
        vendored = SKILL / "SKILL.md"
        vendored.write_text(
            _compose_skill(vendored.read_text(encoding="utf-8"), preamble),
            encoding="utf-8",
        )
    _sync_release_metadata(tag)


def _sync_release_metadata(tag: str) -> None:
    """Print the CLI archive checksums so `browser_assets.py` can be pinned."""
    url = f"https://github.com/Tencent/BrowserSkill/releases/download/{tag}/version.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        print(response.read().decode("utf-8"))
    print("\nUpdate BSK_VERSION and _ARCHIVES in yoreni/sandbox/browser_assets.py.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="cli-v0.4.0", help="Upstream release tag to vendor")
    args = parser.parse_args()
    vendor(args.tag)


if __name__ == "__main__":
    main()
