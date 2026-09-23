#!/usr/bin/env python3
"""Build the personal-tutor library with blank templates; never overwrite files."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

PACKAGE = Path(__file__).resolve().parent.parent
TEMPLATES = PACKAGE / "assets" / "library"
METHOD = "个人导师-AI学习skill.md"
RESERVED = {"README.md", "AGENTS.md", METHOD, "课题模板", "跨课题串联日志",
            "课题A", "课题B", "课题名", "用户选定的课题A", "用户选定的课题B"}


def is_link(path):
    if path.is_symlink():
        return True
    if path.exists():
        return bool(getattr(path.lstat(), "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return False


def check_path(path, is_file):
    for parent in [path, *path.parents]:
        if is_link(parent):
            raise ValueError("不沿符号链接或联接点写入：" + str(parent))
        if parent != path and parent.exists() and not parent.is_dir():
            raise ValueError("父路径不是目录：" + str(parent))
    if path.exists() and (path.is_file() != is_file):
        raise ValueError("路径类型冲突，未覆盖：" + str(path))


def validate_topic(topic):
    if not topic:
        return
    if topic != topic.strip() or topic.endswith("."):
        raise ValueError("课题名不能带首尾空白或末尾句点")
    if topic in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', topic):
        raise ValueError("请用一个不含路径分隔符或系统禁用字符的课题名")
    if topic.casefold() in {name.casefold() for name in RESERVED}:
        raise ValueError("请提供真实课题名，不能使用模板名、入口名或示例占位名")
    if re.fullmatch(r"(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", topic, re.I):
        raise ValueError("课题名是 Windows 保留名称")


def initialize(destination, topic=None):
    validate_topic(topic)
    root = Path(os.path.abspath(os.path.expanduser(str(destination))))
    if root == PACKAGE or PACKAGE in root.parents or root in PACKAGE.parents:
        raise ValueError("请选择发布包之外的独立学习目录")
    check_path(root, is_file=False)
    if not (PACKAGE / "SKILL.md").is_file() or not TEMPLATES.is_dir():
        raise ValueError("请保留完整 personal-tutor 发布包，包括 SKILL.md 与 assets/library")
    plan = {}
    for source in sorted(TEMPLATES.rglob("*")):
        if source.is_file():
            plan[root / source.relative_to(TEMPLATES)] = source.read_text(encoding="utf-8")
    plan[root / METHOD] = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    nav_path = root / "README.md"
    plan[nav_path] = plan[nav_path].replace(
        "待确认（建库后填写本学习库的实际位置）", str(root))
    topic_had_content = bool(topic and (root / topic).is_dir()
                             and any((root / topic).iterdir()))
    if topic:
        for source in sorted((TEMPLATES / "课题模板").rglob("*")):
            if source.is_file():
                target = root / topic / source.relative_to(TEMPLATES / "课题模板")
                content = source.read_text(encoding="utf-8")
                if source.name == "课题进度.md":
                    content = content.replace("# 课题进度", "# " + topic + " · 课题进度", 1)
                    content = content.replace(
                        "模板使用说明：课题模板目录里的字段留空，不计入学习进度。建立真实课题时，填入已知信息；未知项标“待确认”，尚未学习标“未开始”。",
                        "已知目标由 AI 根据用户原话填写；未知项保持待确认，勿虚构学习记录。")
                    content = content.replace("- 课题：", "- 课题：" + topic)
                    for field in ("为什么学", "想达到什么程度", "当前基础", "暂定计划"):
                        content = content.replace("- " + field + "：", "- " + field + "：待确认")
                    status = "待核对已有记录" if topic_had_content else "未开始"
                    content = content.replace("- 状态：", "- 状态：" + status)
                    content = content.replace("- 下次从哪里继续：",
                                              "- 下次从哪里继续：核对目标、基础和本次可用时间")
                plan[target] = content
        # Only a newly created README receives this navigation. Existing files stay intact.
        from urllib.parse import quote
        plan[nav_path] = plan[nav_path].replace("当前课题：待选", "当前课题：" + topic)
        if topic_had_content:
            plan[nav_path] = plan[nav_path].replace("尚未开始学习", "待核对已有记录")
        plan[nav_path] = plan[nav_path].replace(
            "首次无课题时保持为空。选定课题后登记真实目录链接、最近进度与下次入口。",
            "- [" + topic + "](" + quote(topic) + "/" + quote("课题进度.md") + ")")

    # Preflight every destination before creating anything.
    for target in plan:
        check_path(target, is_file=True)
    created, kept = [], []
    for target, content in plan.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        check_path(target, is_file=True)
        try:
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            if target.read_text(encoding="utf-8") != content:
                raise OSError("写入后核对失败：" + str(target))
            created.append(str(target.relative_to(root)))
        except FileExistsError:
            kept.append(str(target.relative_to(root)))
        # Read back existing files too; content completion is the agent's responsibility.
        target.read_text(encoding="utf-8")
    return {
        "root": str(root), "topic": topic, "created": created, "preserved": kept,
        "verified_files": sorted(str(path.relative_to(root)) for path in plan),
        "next": "基础文件已检查。按 SKILL.md 读回内容与链接，填写已知目标并补全导航；保留原历史。只建库时到此结束。"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", help="用户指定的独立学习库位置")
    parser.add_argument("--topic", help="用户已选的真实课题；未选时省略")
    args = parser.parse_args()
    try:
        result = initialize(args.destination, args.topic)
    except (OSError, ValueError) as exc:
        print("初始化未完成：" + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

