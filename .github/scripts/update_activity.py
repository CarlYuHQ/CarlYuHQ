#!/usr/bin/env python3
"""从 GitHub 官方日历生成浅色贡献图；验证失败时不替换现有图片。"""

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


USERNAME = "carlyuhq"
SOURCE_URL = f"https://github.com/users/{USERNAME}/contributions"
OUTPUT_STEM = Path(__file__).resolve().parents[2] / "assets" / "activity"
MAX_BYTES = 4 * 1024 * 1024
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
COLORS = ("#eeedf4", "#d9d1f2", "#b7a8e5", "#9782dc", "#7761cf")
VARIANTS = {
    "light": {
        "background": "#fbfaf7",
        "text": "#242438",
        "accent": "#7761cf",
        "grid": "#eeedf4",
        "colors": COLORS,
        "suffix": "",
        "label": "浅色",
    },
    "dark": {
        "background": "#14141d",
        "text": "#e7e4f0",
        "accent": "#a091ea",
        "grid": "#1f1e2b",
        "colors": ("#1f1e2b", "#31407a", "#4a559a", "#7d6bad", "#a58bf0"),
        "suffix": "-dark",
        "label": "深色",
    },
}
NUMBER = r"(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)"
TOOLTIP = re.compile(
    rf"(?P<count>No|{NUMBER}) contribution(?:s)? on "
    r"(?P<month>[A-Za-z]+) (?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,? (?P<year>\d{4}))?\.?"
)
TOTAL = re.compile(rf"({NUMBER}) contributions? (?:in the last year|in \d{{4}})\.?")


@dataclass(frozen=True)
class Day:
    date: date
    level: int
    count: int


class ContributionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells = {}
        self.dates = set()
        self.tooltips = {}
        self.headings = []
        self.ranges = []
        self.capture = None
        self.target = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and "data-graph-url" in attrs:
            if attrs["data-graph-url"].lower() != f"/users/{USERNAME}/contributions":
                raise ValueError("日历用户名与固定目标不一致")
            self.ranges.append((attrs.get("data-from"), attrs.get("data-to")))
        if tag == "td" and "data-date" in attrs:
            iso = attrs["data-date"]
            day = date.fromisoformat(iso)
            if iso != day.isoformat():
                raise ValueError("单元格日期不是 ISO 日期")
            identifier = attrs.get("id")
            level = attrs.get("data-level")
            if not identifier or identifier in self.cells or day in self.dates:
                raise ValueError("单元格 ID 或日期缺失/重复")
            if level not in {"0", "1", "2", "3", "4"}:
                raise ValueError(f"{iso} 的贡献等级无效")
            self.cells[identifier] = (day, int(level))
            self.dates.add(day)
        if tag == "h2" and attrs.get("id") == "js-contribution-activity-description":
            self.begin_capture("h2", None)
        if tag == "tool-tip" and attrs.get("for"):
            self.begin_capture("tool-tip", attrs["for"])

    def begin_capture(self, tag, target):
        if self.capture is not None:
            raise ValueError("日历文本结构异常")
        self.capture, self.target, self.parts = tag, target, []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != self.capture:
            return
        text = " ".join("".join(self.parts).split())
        if tag == "h2":
            self.headings.append(text)
        else:
            if self.target in self.tooltips:
                raise ValueError("同一单元格出现重复 tooltip")
            self.tooltips[self.target] = text
        self.capture, self.target, self.parts = None, None, []


def parse_contributions(html):
    parser = ContributionParser()
    parser.feed(html)
    parser.close()
    if parser.capture is not None:
        raise ValueError("日历 HTML 不完整")
    if not 350 <= len(parser.cells) <= 371:
        raise ValueError(f"唯一日期数量异常：{len(parser.cells)}；要求 350 至 371")
    if len(parser.headings) != 1 or len(parser.ranges) != 1:
        raise ValueError("官方年度总量标题或日期区间缺失/重复")
    heading = TOTAL.fullmatch(parser.headings[0])
    if not heading:
        raise ValueError("无法可靠解析官方年度贡献总量")
    reported_total = int(heading[1].replace(",", ""))
    days = []
    for identifier, (day, level) in parser.cells.items():
        match = TOOLTIP.fullmatch(parser.tooltips.get(identifier, ""))
        if not match:
            raise ValueError(f"{day} 缺少有效的每日次数 tooltip")
        count_text = match["count"]
        count = 0 if count_text == "No" else int(count_text.replace(",", ""))
        if match["month"] != MONTHS[day.month - 1] or int(match["day"]) != day.day:
            raise ValueError(f"{day} 的 tooltip 月日与单元格不匹配")
        if match["year"] and int(match["year"]) != day.year:
            raise ValueError(f"{day} 的 tooltip 年份不匹配")
        if (count == 0) != (level == 0):
            raise ValueError(f"{day} 的每日次数与贡献等级矛盾")
        days.append(Day(day, level, count))
    days.sort(key=lambda item: item.date)
    start, end = days[0].date, days[-1].date
    if (end - start).days + 1 != len(days):
        raise ValueError("年度日历日期不连续")
    raw_start, raw_end = parser.ranges[0]
    if raw_start != f"{start} 00:00:00 UTC" or raw_end != f"{end} 23:59:59 UTC":
        raise ValueError("单元格日期区间与官方日历元数据不一致")
    sunday = start - timedelta(days=(start.weekday() + 1) % 7)
    weeks = (end - sunday).days // 7 + 1
    if weeks not in (53, 54):
        raise ValueError(f"年度日历周数异常：{weeks}；要求 53 或 54")
    actual_total = sum(item.count for item in days)
    if actual_total != reported_total:
        raise ValueError(f"每日次数合计 {actual_total} 不等于官方年度总量 {reported_total}")
    return days, reported_total, sunday, weeks


def render_svg(days, total, sunday, weeks, variant="light"):
    theme = VARIANTS[variant]
    start, end = days[0].date, days[-1].date
    by_date = {item.date: item for item in days}
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 230" width="900" height="230" role="img" aria-labelledby="activity-title activity-desc">',
        f'  <title id="activity-title">{USERNAME}: {total:,} contributions, {start} to {end}</title>',
        f'  <desc id="activity-desc">GitHub official contribution calendar for {USERNAME}. {len(days)} verified dates across {weeks} Sunday-first weeks; daily tooltip counts sum to {total:,}. Outlined boundary cells are outside the reported range, not zero-contribution days.</desc>',
        f'  <rect width="900" height="230" rx="24" fill="{theme["background"]}"/>',
        f'  <g fill="{theme["text"]}" font-family="system-ui, -apple-system, Segoe UI, sans-serif" font-size="14">',
        '    <text x="32" y="38" font-size="24" font-weight="700">Small commits. Steady progress.</text>',
        f'    <text x="32" y="64">{USERNAME} · {start} — {end}</text>',
        f'    <text x="868" y="38" text-anchor="end" font-size="30" font-weight="700" fill="{theme["accent"]}">{total:,}</text>',
        '    <text x="868" y="62" text-anchor="end">contributions</text>',
    ]
    grid_x, grid_y, step, cell = 84, 100, 14.4, 11.5
    for row, label in enumerate(("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")):
        lines.append(f'    <text x="66" y="{grid_y + row * step + 10.5:.1f}" text-anchor="end">{label}</text>')
    month_marks = [(start, MONTHS[start.month - 1][:3])]
    month_marks.extend(
        (item.date, MONTHS[item.date.month - 1][:3])
        for item in days if item.date.day == 1 and item.date != start
    )
    previous_x = -100
    for mark, label in month_marks:
        column = (mark - sunday).days // 7
        x = grid_x + column * step
        # 短首月改用右对齐，保留跨年首尾月份且不重叠下一月份。
        if mark == start and len(month_marks) > 1:
            next_x = grid_x + (month_marks[1][0] - sunday).days // 7 * step
            if next_x - x < 40:
                lines.append(f'    <text x="{grid_x + cell:.1f}" y="88" text-anchor="end">{label}</text>')
                continue
        if x - previous_x < 40:
            raise ValueError("月份标签间距不足")
        x = min(x, 854)
        lines.append(f'    <text x="{x:.1f}" y="88">{label}</text>')
        previous_x = x
    for column in range(weeks):
        for row in range(7):
            day = sunday + timedelta(days=column * 7 + row)
            x, y = grid_x + column * step, grid_y + row * step
            if x + cell > 868 or y + cell > 200:
                raise ValueError("贡献网格超出绘图区")
            item = by_date.get(day)
            geometry = f'x="{x:.1f}" y="{y:.1f}" width="{cell}" height="{cell}" rx="2.5"'
            if item is None:
                lines.append(f'    <rect {geometry} fill="none" stroke="{theme["grid"]}" data-padding="true"><title>Outside reported date range; no contribution data.</title></rect>')
            else:
                noun = "contribution" if item.count == 1 else "contributions"
                title = escape(f"{day}: {item.count:,} {noun} (GitHub level {item.level})")
                lines.append(f'    <rect {geometry} fill="{theme["colors"][item.level]}" data-date="{day}" data-level="{item.level}" data-count="{item.count}"><title>{title}</title></rect>')
    lines.append('    <text x="84" y="221">GitHub contribution activity</text>')
    lines.append('    <text x="704" y="221" text-anchor="end">Low</text>')
    for level, color in enumerate(theme["colors"]):
        x = 717 + level * 19
        lines.append(f'    <rect x="{x}" y="209" width="12" height="12" rx="2.5" fill="{color}"><title>Contribution intensity level {level}</title></rect>')
    lines.extend([
        '    <text x="868" y="221" text-anchor="end">High</text>',
        '  </g>',
        '</svg>',
    ])
    svg = "\n".join(lines) + "\n"
    validate_svg(svg, days, total, weeks)
    return svg


def validate_svg(svg, days, total, weeks):
    root = ET.fromstring(svg)
    ns = {"s": "http://www.w3.org/2000/svg"}
    rects = root.findall(".//s:rect", ns)
    cells = [rect for rect in rects if "data-date" in rect.attrib]
    padding = [rect for rect in rects if rect.get("data-padding") == "true"]
    if root.get("viewBox") != "0 0 900 230" or len(cells) != len(days):
        raise ValueError("生成的 SVG 尺寸或日期数量验证失败")
    if len(cells) + len(padding) != weeks * 7:
        raise ValueError("生成的 SVG 七行完整网格验证失败")
    if sum(int(rect.get("data-count")) for rect in cells) != total:
        raise ValueError("生成的 SVG 统计总量验证失败")
    expected = {str(item.date): (item.level, item.count) for item in days}
    actual = {rect.get("data-date"): (int(rect.get("data-level")), int(rect.get("data-count"))) for rect in cells}
    if actual != expected:
        raise ValueError("生成的 SVG 与每日原始数据不一致")
    for rect in cells + padding:
        if rect.find("s:title", ns) is None:
            raise ValueError("生成的 SVG 缺少单元格 title")
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in {"script", "foreignObject", "image", "style"}:
            raise ValueError("SVG 含有非预期的外部内容或活动内容")


def load_source(source):
    if source is not None:
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("本地 HTML 超过 4 MiB 限制")
        return source.read_text(encoding="utf-8")
    request = Request(SOURCE_URL, headers={
        "User-Agent": "carlyuhq-profile-activity/1.0",
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9",
    })
    deadline = time.monotonic() + 60
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError(f"GitHub 返回 HTTP {response.status}")
        if response.headers.get_content_type() != "text/html":
            raise ValueError("GitHub 返回的内容不是 HTML")
        content = bytearray()
        while True:
            chunk = response.read(65536)
            if time.monotonic() > deadline:
                raise TimeoutError("抓取超过 60 秒总时限")
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise ValueError("GitHub 响应超过 4 MiB 限制")
        return content.decode("utf-8")


def replace_atomically(svg, output):
    data = svg.encode("utf-8")
    if output.exists() and output.read_bytes() == data:
        return False
    # 同目录临时文件确保替换原子性；所有解析与 SVG 验证均已完成。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".activity-", suffix=".tmp", delete=False) as handle:
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
    arguments = argparse.ArgumentParser(description="验证 GitHub 官方每日贡献后更新浅色与深色 SVG")
    arguments.add_argument("--source", type=Path, help="使用本地 GitHub 日历 HTML；省略时联网刷新")
    arguments.add_argument("--variant", choices=sorted(VARIANTS) + ["all"], default="all",
                           help="选择生成浅色、深色或两者；默认两者")
    args = arguments.parse_args(argv)
    wanted = sorted(VARIANTS) if args.variant == "all" else [args.variant]
    try:
        days, total, sunday, weeks = parse_contributions(load_source(args.source))
        rendered = {name: render_svg(days, total, sunday, weeks, name) for name in wanted}
        changed = {}
        for name in wanted:
            output = OUTPUT_STEM.with_name(f"activity{VARIANTS[name]['suffix']}.svg")
            changed[name] = replace_atomically(rendered[name], output)
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"更新失败，现有图片保持不变：{error}", file=sys.stderr)
        return 1
    levels = dict(sorted(Counter(item.level for item in days).items()))
    print(f"验证通过：{len(days)} 个唯一日期，{days[0].date} 至 {days[-1].date}；{weeks} 周 × 7 行；每日次数合计/官方总量 = {total}/{total}；等级分布 = {levels}；XML 有效。")
    for name in wanted:
        output = OUTPUT_STEM.with_name(f"activity{VARIANTS[name]['suffix']}.svg")
        print(f"  {VARIANTS[name]['label']}版：{'已更新' if changed[name] else '内容未变'} {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
