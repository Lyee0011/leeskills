#!/usr/bin/env python3
"""Validate an AI-hotspot research ledger and render an evidence-aware report.

This script is intentionally offline. Research agents must find and verify the
sources; the script only enforces deterministic time-window and evidence fields.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = SKILL_ROOT / "assets" / "hotspot-ledger-template.json"
DEFAULT_REPORT_TEMPLATE = SKILL_ROOT / "assets" / "hotspot-report-template.md"

EVIDENCE_LEVELS = {"A", "B", "C", "D", "E"}
STATUSES = {"new", "resurfaced", "pending"}
SOURCE_KINDS = {
    "official",
    "paper",
    "filing",
    "transcript",
    "repository",
    "media",
    "aggregator",
    "social",
}
SECONDARY_SOURCE_KINDS = {"media", "aggregator", "social"}
FIRST_HAND_SOURCE_KINDS = {"official", "paper", "filing", "transcript", "repository"}
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OFFSET_RE = re.compile(r"^([+-])(\d{2}):(\d{2})$")


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    utc_offset_text: str
    tz: timezone
    days: int
    as_of: datetime
    start_date: date

    @property
    def window_text(self) -> str:
        end = self.as_of.date().isoformat()
        return f"{self.start_date.isoformat()} 至 {end}（UTC{self.utc_offset_text}，自然日）"


def parse_utc_offset(value: str) -> timezone:
    match = OFFSET_RE.fullmatch(value.strip())
    if not match:
        raise LedgerError("utc_offset 必须使用 +08:00 这样的格式")
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
        raise LedgerError(f"无效的 utc_offset：{value}")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_timestamp(value: Any, field: str, default_tz: timezone) -> tuple[datetime | None, bool]:
    if value is None or value == "":
        return None, False
    if not isinstance(value, str):
        raise LedgerError(f"{field} 必须是 ISO 8601 字符串或 null")
    text = value.strip()
    if DATE_ONLY_RE.fullmatch(text):
        return datetime.combine(date.fromisoformat(text), time.min, tzinfo=default_tz), True
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LedgerError(f"{field} 不是有效的 ISO 8601 时间：{value}") from exc
    if parsed.tzinfo is None:
        raise LedgerError(f"{field} 必须包含时区；只有纯日期可以省略时区")
    return parsed, False


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LedgerError(f"找不到文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise LedgerError(f"JSON 格式错误：{exc}") from exc
    if not isinstance(data, dict):
        raise LedgerError("账本根节点必须是 JSON 对象")
    return data


def build_context(
    data: dict[str, Any],
    as_of_override: str | None,
    days_override: int | None,
    offset_override: str | None,
) -> Context:
    report = data.get("report")
    if not isinstance(report, dict):
        raise LedgerError("report 必须是 JSON 对象")

    offset_text = offset_override or report.get("utc_offset") or "+08:00"
    if not isinstance(offset_text, str):
        raise LedgerError("report.utc_offset 必须是字符串")
    tz = parse_utc_offset(offset_text)

    days_value = days_override if days_override is not None else report.get("days", 3)
    if not isinstance(days_value, int) or isinstance(days_value, bool) or days_value < 1 or days_value > 31:
        raise LedgerError("days 必须是 1 到 31 之间的整数")

    as_of_value = as_of_override or report.get("as_of")
    if as_of_value:
        as_of, date_only = parse_timestamp(as_of_value, "report.as_of", tz)
        assert as_of is not None
        if date_only:
            as_of = datetime.combine(as_of.date(), time.max, tzinfo=tz)
        else:
            as_of = as_of.astimezone(tz)
    else:
        as_of = datetime.now(tz)

    start_date = as_of.date() - timedelta(days=days_value - 1)
    return Context(offset_text, tz, days_value, as_of, start_date)


def valid_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def clean_string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{field} 必须是字符串数组")
        return []
    return [item.strip() for item in value if item.strip()]


def clean_corroboration(value: Any, field: str, errors: list[str]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"{field} 必须是对象数组")
        return []
    cleaned: list[dict[str, str]] = []
    for index, raw in enumerate(value):
        prefix = f"{field}[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} 必须是 JSON 对象")
            continue
        source = raw.get("source")
        url = raw.get("url")
        note = raw.get("note", "")
        if not isinstance(source, str) or not source.strip():
            errors.append(f"{prefix}.source 不能为空")
            continue
        if not valid_url(url):
            errors.append(f"{prefix}.url 必须是 http(s) URL")
            continue
        if not isinstance(note, str):
            errors.append(f"{prefix}.note 必须是字符串")
            note = ""
        cleaned.append({"source": source.strip(), "url": url, "note": note.strip()})
    return cleaned


def validate_ledger(
    data: dict[str, Any], context: Context
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    items = data.get("items")
    if not isinstance(items, list):
        raise LedgerError("items 必须是 JSON 数组")
    if not items:
        warnings.append("账本中没有候选热点。")

    normalized: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()

    for index, raw in enumerate(items, start=1):
        prefix = f"items[{index - 1}]"
        if not isinstance(raw, dict):
            errors.append(f"{prefix} 必须是 JSON 对象")
            continue

        item = dict(raw)
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{prefix}.title 不能为空")
            title = f"未命名候选 {index}"
        else:
            title = title.strip()
        item["title"] = title

        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            errors.append(f"{prefix}.source 不能为空")
            source = "未注明来源"
        item["source"] = source.strip()

        source_url = item.get("source_url")
        legacy_url = item.get("primary_url")
        if source_url and legacy_url and source_url != legacy_url:
            errors.append(f"{prefix} 同时提供了不同的 source_url 与 primary_url")
        url = source_url or legacy_url
        if not valid_url(url):
            errors.append(f"{prefix}.source_url 必须是 http(s) URL")
            url = ""
        item["source_url"] = url

        evidence = str(item.get("evidence", "")).upper()
        if evidence not in EVIDENCE_LEVELS:
            errors.append(f"{prefix}.evidence 必须是 A、B、C、D 或 E")
        item["evidence"] = evidence

        status = item.get("status", "new")
        if status not in STATUSES:
            errors.append(f"{prefix}.status 必须是 new、resurfaced 或 pending")
        item["status"] = status

        source_kind = item.get("source_kind")
        if source_kind not in SOURCE_KINDS:
            errors.append(f"{prefix}.source_kind 不在允许列表中")
        item["source_kind"] = source_kind

        first_hand = item.get("first_hand")
        if not isinstance(first_hand, bool):
            errors.append(f"{prefix}.first_hand 必须是 true 或 false")
            first_hand = False
        item["first_hand"] = first_hand

        recommended = item.get("recommended", False)
        if not isinstance(recommended, bool):
            errors.append(f"{prefix}.recommended 必须是 true 或 false")
            recommended = False
        item["recommended"] = recommended

        item["facts"] = clean_string_list(item.get("facts"), f"{prefix}.facts", errors)
        item["source_claims"] = clean_string_list(
            item.get("source_claims"), f"{prefix}.source_claims", errors
        )
        item["inferences"] = clean_string_list(
            item.get("inferences"), f"{prefix}.inferences", errors
        )
        item["corroboration"] = clean_corroboration(
            item.get("corroboration"), f"{prefix}.corroboration", errors
        )

        if recommended and not str(item.get("angle", "")).strip():
            errors.append(f"{prefix}.angle：推荐选题必须提供内容角度")
        if recommended and evidence in {"D", "E"}:
            warnings.append(f"《{title}》证据等级为 {evidence}，不宜作为首选。")
        if recommended and status != "new":
            warnings.append(f"《{title}》状态为 {status}，不应进入严格近期推荐。")
        if recommended and not first_hand:
            warnings.append(f"《{title}》作为推荐项但尚无一手来源，请在报告中明确。")
        if recommended and item["source_claims"] and not item["corroboration"]:
            warnings.append(f"《{title}》包含来源方声明，但尚未记录独立佐证。")
        if first_hand and source_kind in SECONDARY_SOURCE_KINDS:
            warnings.append(f"《{title}》把 {source_kind} 标成一手来源，请复核。")
        if not first_hand and source_kind in FIRST_HAND_SOURCE_KINDS:
            warnings.append(f"《{title}》的来源类型为 {source_kind}，但 first_hand=false，请复核。")
        if not first_hand and evidence == "A":
            warnings.append(f"《{title}》不是一手来源却标为 A，请逐条确认事实依据。")
        if status == "new" and not (
            item["facts"] or item["source_claims"] or item["inferences"]
        ):
            warnings.append(f"《{title}》尚未记录任何事实、声明或推测。")

        try:
            published, date_only = parse_timestamp(
                item.get("published_at"), f"{prefix}.published_at", context.tz
            )
        except LedgerError as exc:
            errors.append(str(exc))
            published, date_only = None, False
        if published is None and status != "pending":
            errors.append(f"{prefix}.published_at：非 pending 候选必须提供首次发布时间")

        try:
            updated, _ = parse_timestamp(item.get("updated_at"), f"{prefix}.updated_at", context.tz)
        except LedgerError as exc:
            errors.append(str(exc))
            updated = None

        if published and updated and updated < published:
            warnings.append(f"《{title}》的 updated_at 早于 published_at，请复核时间。")

        local_published = published.astimezone(context.tz) if published else None
        inside_window = False
        exclusion_reason = ""
        if status == "pending":
            exclusion_reason = "发布时间或关键证据仍待核实"
        elif status == "resurfaced":
            exclusion_reason = "近期再传播，不算时间窗内的新发布"
        elif local_published is None:
            exclusion_reason = "首次发布时间未确认"
        elif local_published > context.as_of:
            exclusion_reason = "发布时间晚于调研截止时间"
        elif local_published.date() < context.start_date:
            exclusion_reason = "首次发布时间早于调研窗口"
        else:
            inside_window = True

        if title.casefold() in seen_titles:
            warnings.append(f"发现重复标题：《{title}》。")
        seen_titles.add(title.casefold())
        if url:
            if url in seen_urls:
                warnings.append(f"发现重复来源链接：{url}")
            seen_urls.add(url)

        item["_published"] = local_published
        item["_published_date_only"] = date_only
        item["_updated"] = updated.astimezone(context.tz) if updated else None
        item["_inside_window"] = inside_window
        item["_exclusion_reason"] = exclusion_reason
        normalized.append(item)

    return normalized, errors, warnings


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def join_claims(values: list[str]) -> str:
    return "<br>".join(escape_cell(value) for value in values) if values else "—"


def format_published(item: dict[str, Any]) -> str:
    published = item.get("_published")
    if not isinstance(published, datetime):
        return "未确认"
    if item.get("_published_date_only"):
        return published.strftime("%Y-%m-%d")
    return published.strftime("%Y-%m-%d %H:%M")


def render_table(items: list[dict[str, Any]]) -> str:
    if not items:
        return "严格时间窗内暂无通过校验的热点。"
    lines = [
        "| 热点 | 北京时间 | 来源与等级 | 已确认事实 | 来源方声明 | 分析推测 |",
        "|---|---|---|---|---|---|",
    ]
    for item in items:
        source_label = "一手" if item["first_hand"] else "二手"
        source = f"[{escape_cell(item['source'])}]({item['source_url']}) · {item['evidence']} · {source_label}"
        if item["corroboration"]:
            links = "、".join(
                f"[{escape_cell(entry['source'])}]({entry['url']})"
                for entry in item["corroboration"]
            )
            source += f"<br>独立佐证：{links}"
        lines.append(
            "| {title} | {published} | {source} | {facts} | {claims} | {inferences} |".format(
                title=escape_cell(item["title"]),
                published=format_published(item),
                source=source,
                facts=join_claims(item["facts"]),
                claims=join_claims(item["source_claims"]),
                inferences=join_claims(item["inferences"]),
            )
        )
    return "\n".join(lines)


def render_recommendations(items: list[dict[str, Any]]) -> str:
    selected = [item for item in items if item.get("recommended")]
    if not selected:
        return "暂无达到推荐标准的选题。"
    blocks: list[str] = []
    for index, item in enumerate(selected, start=1):
        blocks.append(f"### {index}. {item['title']}")
        blocks.append("")
        blocks.append(f"- 推荐角度：{item.get('angle') or '未填写'}")
        blocks.append(f"- 适合受众：{item.get('audience') or '未填写'}")
        blocks.append(f"- 推荐理由：{item.get('selection_reason') or '未填写'}")
        blocks.append(f"- 主要风险：{item.get('risk') or '未填写'}")
        blocks.append(f"- 来源：[{item['source']}]({item['source_url']})")
        if item["corroboration"]:
            links = "、".join(
                f"[{entry['source']}]({entry['url']})" for entry in item["corroboration"]
            )
            blocks.append(f"- 独立佐证：{links}")
        blocks.append("")
    return "\n".join(blocks).rstrip()


def render_excluded(items: list[dict[str, Any]]) -> str:
    excluded = [item for item in items if not item.get("_inside_window")]
    if not excluded:
        return "无。"
    lines = ["| 候选 | 原因 | 来源 |", "|---|---|---|"]
    for item in excluded:
        source = f"[{escape_cell(item['source'])}]({item['source_url']})" if item["source_url"] else "—"
        lines.append(
            f"| {escape_cell(item['title'])} | {escape_cell(item['_exclusion_reason'])} | {source} |"
        )
    return "\n".join(lines)


def render_report(
    data: dict[str, Any],
    items: list[dict[str, Any]],
    warnings: list[str],
    context: Context,
    template_path: Path,
) -> str:
    template = template_path.read_text(encoding="utf-8")
    included = [item for item in items if item.get("_inside_window")]
    included.sort(
        key=lambda item: item.get("_published") or datetime.min.replace(tzinfo=context.tz),
        reverse=True,
    )
    report = data["report"]
    conclusion = str(report.get("conclusion") or "").strip()
    if not conclusion:
        recommended_count = sum(1 for item in included if item.get("recommended"))
        conclusion = f"严格时间窗内保留 {len(included)} 条热点，其中 {recommended_count} 条进入优先选题。"

    replacements = {
        "{{REPORT_TITLE}}": str(report.get("title") or "AI 热点调研报告"),
        "{{WINDOW}}": context.window_text,
        "{{GENERATED_AT}}": context.as_of.strftime("%Y-%m-%d %H:%M UTC") + context.utc_offset_text,
        "{{AUDIENCE}}": str(report.get("audience") or "未指定"),
        "{{CONCLUSION}}": conclusion,
        "{{ITEMS_TABLE}}": render_table(included),
        "{{RECOMMENDATIONS}}": render_recommendations(included),
        "{{EXCLUDED}}": render_excluded(items),
        "{{WARNINGS}}": "\n".join(f"- {warning}" for warning in warnings) if warnings else "无。",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    remaining = re.findall(r"\{\{[A-Z_]+\}\}", template)
    if remaining:
        raise LedgerError(f"报告模板仍有未替换标记：{', '.join(sorted(set(remaining)))}")
    return template.rstrip() + "\n"


def write_text(path: Path, content: str, overwrite: bool = False) -> None:
    if path.exists() and not overwrite:
        raise LedgerError(f"目标文件已存在，未覆盖：{path}")
    if not path.parent.exists():
        raise LedgerError(f"目标目录不存在：{path.parent}")
    path.write_text(content, encoding="utf-8", newline="\n")


def command_init(args: argparse.Namespace) -> int:
    data = load_json(DEFAULT_LEDGER)
    report = data["report"]
    report["title"] = args.title
    report["audience"] = args.audience
    report["days"] = args.days
    report["utc_offset"] = args.utc_offset
    report["as_of"] = args.as_of or ""
    build_context(data, None, None, None)
    write_text(args.output, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(f"已创建研究账本：{args.output}")
    return 0


def load_and_validate(args: argparse.Namespace) -> tuple[dict[str, Any], Context, list[dict[str, Any]], list[str]]:
    data = load_json(args.input)
    context = build_context(data, args.as_of, args.days, args.utc_offset)
    items, errors, warnings = validate_ledger(data, context)
    if errors:
        raise LedgerError("\n".join(f"- {error}" for error in errors))
    return data, context, items, warnings


def command_validate(args: argparse.Namespace) -> int:
    _, context, items, warnings = load_and_validate(args)
    included = sum(1 for item in items if item.get("_inside_window"))
    print(f"账本有效：{len(items)} 条候选，{included} 条位于 {context.window_text}")
    for warning in warnings:
        print(f"警告：{warning}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    data, context, items, warnings = load_and_validate(args)
    template = args.template or DEFAULT_REPORT_TEMPLATE
    content = render_report(data, items, warnings, context, template)
    if args.output:
        write_text(args.output, content, overwrite=args.overwrite)
        print(f"已生成报告：{args.output}")
    else:
        sys.stdout.write(content)
    return 0


def add_context_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--as-of", help="调研截止时间，ISO 8601；覆盖账本设置")
    parser.add_argument("--days", type=int, help="自然日数量；覆盖账本设置")
    parser.add_argument("--utc-offset", help="时区偏移，例如 +08:00；覆盖账本设置")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="创建、校验并渲染 AI 热点调研账本（离线，不负责抓取新闻）。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="从资产模板创建新的研究账本")
    init_parser.add_argument("output", type=Path)
    init_parser.add_argument("--title", default="AI 热点调研报告")
    init_parser.add_argument("--audience", default="中文 AI 内容读者")
    init_parser.add_argument("--days", type=int, default=3)
    init_parser.add_argument("--utc-offset", default="+08:00")
    init_parser.add_argument("--as-of", help="固定调研截止时间；留空则渲染时取当前时间")
    init_parser.set_defaults(func=command_init)

    validate_parser = subparsers.add_parser("validate", help="校验账本字段、时间与证据标签")
    validate_parser.add_argument("input", type=Path)
    add_context_options(validate_parser)
    validate_parser.set_defaults(func=command_validate)

    render_parser = subparsers.add_parser("render", help="校验并生成 Markdown 热点报告")
    render_parser.add_argument("input", type=Path)
    render_parser.add_argument("--output", type=Path, help="输出文件；省略时写到标准输出")
    render_parser.add_argument("--template", type=Path, help="自定义 Markdown 模板")
    render_parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖已有输出文件")
    add_context_options(render_parser)
    render_parser.set_defaults(func=command_render)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (LedgerError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
