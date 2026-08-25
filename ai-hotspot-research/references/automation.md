# 结构化账本与报告脚本

只有在候选较多、需要交付 Markdown 报告、需要复查时间边界，或用户希望保留可继续编辑的研究账本时，才使用本自动化流程。普通的三四条简报可以直接按 `SKILL.md` 完成。

脚本只做离线校验和排版，不搜索新闻、不判断网页是否真实，也不替 Agent 完成一手信源核查。

## 文件

- `scripts/hotspot_research.py`：初始化账本、校验字段、按自然日过滤并渲染报告。
- `assets/hotspot-ledger-template.json`：空白研究账本，由 `init` 子命令复制。
- `assets/hotspot-item-template.json`：可复制到 `items` 数组中的单条候选骨架。
- `assets/hotspot-ledger-example.json`：包含窗口内、旧闻再传播和待核实候选的演示数据。
- `assets/hotspot-report-template.md`：Markdown 报告模板。复制后可以修改版式，不要在原资产上填具体项目。

## 命令

以下命令都从 Skill 根目录执行，脚本仅依赖 Python 3 标准库。

创建新账本：

```text
python scripts/hotspot_research.py init research-ledger.json --title "AI 热点调研" --audience "中文 AI 内容读者"
```

校验已经填写的账本：

```text
python scripts/hotspot_research.py validate research-ledger.json
```

生成 Markdown 报告：

```text
python scripts/hotspot_research.py render research-ledger.json --output hotspot-report.md
```

测试内置示例：

```text
python scripts/hotspot_research.py validate assets/hotspot-ledger-example.json
python scripts/hotspot_research.py render assets/hotspot-ledger-example.json
```

输出文件已存在时脚本默认拒绝覆盖。只有调用方已经确认覆盖目标，才使用 `--overwrite`。

## 账本字段

### report

| 字段 | 含义 |
|---|---|
| `title` | 报告标题 |
| `audience` | 目标受众 |
| `conclusion` | 人工总结；留空时脚本只生成数量结论 |
| `as_of` | 调研截止时间；ISO 8601，留空表示运行时当前时间 |
| `days` | 自然日数量，默认 3 |
| `utc_offset` | 时区偏移，默认 `+08:00` |

### items

| 字段 | 含义 |
|---|---|
| `title` | 候选热点标题 |
| `source` / `source_url` | 来源名称与可核验链接；旧账本中的 `primary_url` 仍兼容 |
| `source_kind` | `official`、`paper`、`filing`、`transcript`、`repository`、`media`、`aggregator` 或 `social` |
| `first_hand` | 是否为一手来源 |
| `published_at` | 首次发布时间；ISO 8601 或纯日期。`pending` 候选可为 null |
| `updated_at` | 更新时间，仅记录，不用于把旧闻纳入新窗口 |
| `status` | `new`、`resurfaced` 或 `pending` |
| `evidence` | `A` 到 `E`，定义见 `evidence-standard.md` |
| `facts` | 一手材料能直接确认的事实数组 |
| `source_claims` | 厂商、作者或媒体自己声称的内容数组 |
| `inferences` | Agent 的分析推测数组 |
| `corroboration` | 独立佐证数组；每项包含 `source`、`url` 和可选 `note` |
| `recommended` | 是否进入优先选题 |
| `angle` | 推荐内容角度；推荐项必填 |
| `audience` | 该选题的具体受众 |
| `selection_reason` | 为什么值得做 |
| `risk` | 最容易写错或夸大的地方 |

## 时间规则

脚本按 `utc_offset` 换算后使用自然日窗口。例如截止时间为 8 月 25 日、`days=3`，窗口就是 8 月 23 日至 8 月 25 日。`updated_at` 不参与过滤；`resurfaced` 和 `pending` 会进入“排除与待核实”，不会进入严格热点表。
