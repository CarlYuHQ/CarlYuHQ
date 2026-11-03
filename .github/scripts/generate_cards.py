#!/usr/bin/env python3
"""用 GitHub 官方 REST 数据生成浅色与深色卡片；验证失败时不替换现有图片。

数据只来自两个接口：
  GET /users/{username}
  GET /users/{username}/repos?per_page=100&type=owner&sort=updated
语言分布按仓库主语言统计（仓库数），不伪造字节数。
"""

import argparse
import json
import os
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from html import escape
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


USERNAME = "carlyuhq"
PINNED = (("information-analysis", "Python"), ("SnakeGame_pku", "C++"))
ASSETS = Path(__file__).resolve().parents[2] / "assets"
API = "https://api.github.com"
MIN_REPOS = 10
LANG_COLORS = {
    "Python": "#3572A5", "C++": "#f34b7d", "C": "#555555", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "Java": "#b07219", "HTML": "#e34c26", "CSS": "#563d7c",
    "Shell": "#89e051", "Jupyter Notebook": "#DA5B0B", "Go": "#00ADD8",
    "Rust": "#dea584", "Ruby": "#701516", "Vue": "#41b883", "C#": "#178600",
}
VARIANTS = {
    "light": {"bg": "#fbfaf7", "text": "#242438", "muted": "#6b6478", "accent": "#7761cf",
              "border": "#e8e3f1", "bar": "#eeedf4", "suffix": "", "label": "浅色"},
    "dark": {"bg": "#14141d", "text": "#e7e4f0", "muted": "#9a93ad", "accent": "#a58bf0",
             "border": "#2a2836", "bar": "#241f33", "suffix": "-dark", "label": "深色"},
}
FONT = "system-ui, -apple-system, Segoe UI, sans-serif"


def fetch_json(url, token):
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-profile-cards/1.0",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"GitHub 返回 HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def load_data(source_dir, token):
    if source_dir is not None:
        user = json.loads((source_dir / "user.json").read_text(encoding="utf-8"))
        repos = json.loads((source_dir / "repos.json").read_text(encoding="utf-8"))
        return user, repos
    return (
        fetch_json(f"{API}/users/{USERNAME}", token),
        fetch_json(f"{API}/users/{USERNAME}/repos?per_page=100&type=owner&sort=updated", token),
    )


def validate(user, repos):
    if not isinstance(user, dict) or not isinstance(repos, list):
        raise ValueError("接口返回结构不是预期的对象 / 数组")
    if user.get("login", "").lower() != USERNAME:
        raise ValueError("用户接口返回的 login 与目标不一致")
    for key in ("followers", "public_repos"):
        if not isinstance(user.get(key), int) or user[key] < 0:
            raise ValueError(f"用户字段 {key} 缺失或非法")
    owned = [r for r in repos if isinstance(r, dict) and not r.get("fork") and not r.get("archived")]
    if len(owned) < MIN_REPOS:
        raise ValueError(f"有效公开仓库数量异常：{len(owned)}；要求不少于 {MIN_REPOS}")
    for repo in owned:
        if not isinstance(repo.get("name"), str) or not isinstance(repo.get("stargazers_count"), int):
            raise ValueError("仓库字段缺失或非法")
    missing = [name for name, _ in PINNED if name not in {r["name"] for r in owned}]
    if missing:
        raise ValueError(f"置顶仓库不存在：{missing}")
    return owned


def summarize(owned):
    stars = sum(int(r["stargazers_count"]) for r in owned)
    forks = sum(int(r["forks_count"]) for r in owned)
    counts = {}
    for repo in owned:
        language = repo.get("language")
        if language:
            counts[language] = counts.get(language, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    total = sum(count for _, count in ranked) or 1
    return {
        "repos": len(owned),
        "stars": stars,
        "forks": forks,
        "followers": int(user_cache["followers"]),
        "joined": str(user_cache.get("created_at", ""))[:4],
        "languages": [(name, count, count * 100.0 / total) for name, count in ranked],
    }


def head(theme, title, width, height):
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f'  <title>{escape(title)}</title>',
        f'  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{theme["bg"]}" stroke="{theme["border"]}"/>',
        f'  <g font-family="{FONT}" fill="{theme["text"]}">',
        f'    <text x="20" y="34" font-size="17" font-weight="700">{escape(title)}</text>',
    ]


def stat_card(theme, stats):
    rows = [
        ("Public Repositories", f'{stats["repos"]}'),
        ("Followers", f'{stats["followers"]}'),
        ("Total Stars", f'{stats["stars"]}'),
        ("Total Forks", f'{stats["forks"]}'),
        ("Languages Used", f'{len(stats["languages"])}'),
        ("On GitHub Since", stats["joined"] or "—"),
    ]
    width, height = 470, 205
    lines = head(theme, f"{USERNAME}'s GitHub Stats", width, height)
    for index, (label, value) in enumerate(rows):
        column, row = index % 2, index // 2
        x = 20 + column * 225
        y = 76 + row * 42
        lines.append(f'    <text x="{x}" y="{y}" font-size="12" fill="{theme["muted"]}">{escape(label)}</text>')
        lines.append(f'    <text x="{x}" y="{y + 20}" font-size="17" font-weight="700" fill="{theme["accent"]}">{escape(value)}</text>')
    lines.append('  </g>\n</svg>\n')
    return "\n".join(lines)


def lang_card(theme, stats):
    # 与总览卡使用完全相同的内建尺寸，保证并排缩放后高度一致。
    width, height = 470, 205
    bar_x, bar_width = 210, 190
    lines = head(theme, "Most Used Languages", width, height)
    lines.append(f'    <text x="20" y="54" font-size="11" fill="{theme["muted"]}">by repository count · {stats["repos"]} public repos</text>')
    for index, (name, count, percent) in enumerate(stats["languages"]):
        y = 86 + index * 20
        color = LANG_COLORS.get(name, theme["accent"])
        lines.append(f'    <circle cx="26" cy="{y - 4}" r="4.5" fill="{color}"/>')
        lines.append(f'    <text x="38" y="{y}" font-size="12">{escape(name)}</text>')
        lines.append(f'    <rect x="{bar_x}" y="{y - 9}" width="{bar_width}" height="8" rx="4" fill="{theme["bar"]}"/>')
        lines.append(f'    <rect x="{bar_x}" y="{y - 9}" width="{max(4, round(bar_width * percent / 100))}" height="8" rx="4" fill="{color}"/>')
        lines.append(f'    <text x="450" y="{y}" font-size="11" text-anchor="end" fill="{theme["muted"]}">{count} · {percent:.0f}%</text>')
    lines.append('  </g>\n</svg>\n')
    return "\n".join(lines)


def repo_card(theme, repo):
    width, height = 470, 132
    title = f'{USERNAME}/{repo["name"]}'
    description = (repo.get("description") or "No description provided.").strip()
    if len(description) > 92:
        description = description[:89].rstrip() + "..."
    language = repo.get("language") or "—"
    updated = str(repo.get("pushed_at") or "")[:10]
    lines = head(theme, title, width, height)
    lines.append(f'    <text x="20" y="66" font-size="12.5" fill="{theme["muted"]}">{escape(description)}</text>')
    lines.append(f'    <circle cx="26" cy="102" r="4.5" fill="{LANG_COLORS.get(language, theme["accent"])}"/>')
    lines.append(f'    <text x="38" y="106" font-size="12">{escape(language)}</text>')
    lines.append(f'    <text x="130" y="106" font-size="12">★ {int(repo["stargazers_count"])}</text>')
    lines.append(f'    <text x="190" y="106" font-size="12">⑂ {int(repo["forks_count"])}</text>')
    lines.append(f'    <text x="450" y="106" font-size="11.5" text-anchor="end" fill="{theme["muted"]}">Updated {escape(updated)}</text>')
    lines.append('  </g>\n</svg>\n')
    return "\n".join(lines)


def validate_svg(svg, width, height):
    root = ET.fromstring(svg)
    if root.get("width") != str(width) or root.get("height") != str(height):
        raise ValueError("生成的卡片尺寸验证失败")
    if not root.find("{http://www.w3.org/2000/svg}title").text:
        raise ValueError("生成的卡片缺少 title")
    return True


def replace_atomically(svg, output):
    data = svg.encode("utf-8")
    if output.exists() and output.read_bytes() == data:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".card-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="用 GitHub 官方数据生成浅色与深色个人卡片")
    parser.add_argument("--source-dir", type=Path, help="使用本地缓存 JSON；省略时联网抓取")
    args = parser.parse_args(argv)

    global user_cache
    try:
        user_cache, repos = load_data(args.source_dir, os.environ.get("GITHUB_TOKEN"))
        owned = validate(user_cache, repos)
        stats = summarize(owned)
        by_name = {repo["name"]: repo for repo in owned}
        jobs = {}
        for variant, theme in VARIANTS.items():
            suffix = theme["suffix"]
            jobs[f"stats{suffix}.svg"] = (stat_card(theme, stats), 470, 205)
            jobs[f"langs{suffix}.svg"] = (lang_card(theme, stats), 470, 205)
            for name, _ in PINNED:
                jobs[f"{name}{suffix}.svg"] = (repo_card(theme, by_name[name]), 470, 132)
        validated = {}
        for filename, (svg, width, height) in jobs.items():
            validate_svg(svg, width, height)
            validated[filename] = svg
        ASSETS.mkdir(parents=True, exist_ok=True)
        changed = {name: replace_atomically(svg, ASSETS / name) for name, svg in validated.items()}
    except (OSError, ValueError, HTTPError, ET.ParseError, json.JSONDecodeError) as error:
        print(f"生成失败，现有图片保持不变：{error}", file=sys.stderr)
        return 1

    print(f"验证通过：{stats['repos']} 个公开仓库 · stars {stats['stars']} · forks {stats['forks']} "
          f"· followers {stats['followers']} · 语言 {[n for n, _, _ in stats['languages']]}")
    for name in sorted(changed):
        print(f"  {'已更新' if changed[name] else '内容未变'} {ASSETS / name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
