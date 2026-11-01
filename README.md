# 景区游览指南的程序性知识挖掘

## 1. 项目当前状态

**截至 2026-10-29，原始数据采集与候选验收阶段已完成，最终选集为 18 篇文档。** 实验 1 清洗与格式统一已实际执行并通过技术验收；不需要重新开展批量爬取。

| 景区编号 | 景区 | 官方路线 | 游客游记 | 合计 |
|---|---|---:|---:|---:|
| S01 | 白云山 | 1 | 5 | 6 |
| S02 | 长隆 | 1 | 5 | 6 |
| S03 | 黄山 | 1 | 5 | 6 |
| **合计** |  | **3** | **15** | **18** |

18 篇最终选集由 **16 篇通过 HTTP 请求取得正文的文档**和 **2 篇通过浏览器保存 HTML 后离线提取的官方路线**组成。离线文档没有真实 HTTP 请求记录，因此其 `status_code`、`crawled_at`、`final_url` 保留为 `null`；不以文件修改时间冒充抓取时间。

其中两篇离线官方路线为：

- `S01-O-001`《游览线路（A线）》：原始 HTML 位于 `data/raw/html/S01-O-005.html`，对应关系及来源说明见验收报告。
- `S03-O-002`《秋游必备，精选黄山赏秋路线》：原始 HTML 位于 `data/raw/html/S03-O-004.html`，对应关系及来源说明见验收报告。

本地 HTML 文件名与最终文档 ID 不一致，是因为沿用了来源清单中对应真实网页的文档 ID；请勿仅凭 HTML 文件名判断文档身份。

### 当前完成情况

- 已完成：三个景区的数据准备、来源记录、候选质量判断与 18 篇最终选集。
- 已完成：正式爬虫的配置校验、串行请求、正文提取、采集报告与历史备份机制。
- 已完成：实验 1 正式九字段输出 `data/processed/documents.csv`，UTF-8-SIG，共18篇。
- 已完成：清洗前后样例、异常清单、重复候选与质量统计，见 `data/processed/cleaning_report.md`。
- 尚未完成：后续实体识别、路线抽取、共指消解、条件分析、知识图谱和 Agent 等实验模块。

注意：18 篇满足小组约定的目标数量，但“数量达标”不等于每篇内容都完全没有噪声。原始正文中的评论、相关推荐、推广内容、历史票价等，留待实验 1 按规则清洗。`S03-T-005` 有具体的一日游/两日游行程，但没有独立验证其内容是否来自作者亲历；验收时按“包含具体已发布行程信息”纳入，报告中不将其表述为已核实的个人亲历。

## 2. 项目目录与文件职责

```text
tourism-mining/
├── config/
│   ├── scenic_spots.json       # S01、S02、S03 与景区名称的固定映射
│   └── source_urls.csv         # 候选来源清单：URL、类型及启用状态
├── data/
│   ├── raw/
│   │   ├── selected_documents.jsonl  # 最终验收的18篇原始文档
│   │   ├── raw_documents.jsonl      # 爬虫某次运行的采集快照，不等同于最终选集
│   │   ├── crawl_report.json        # 某次爬虫运行的状态与统计
│   │   ├── source_acceptance.csv    # 逐来源采集状态与质量验收记录
│   │   ├── acceptance_report.md     # 最终选集、质量判断和来源说明
│   │   ├── html/                    # 部分来源的原始 HTML 快照/离线保存文件
│   │   ├── history/                 # 覆盖采集输出前生成的历史备份
│   │   └── batches/                 # 独立补采、离线补入等批次证据与备份
│   └── processed/
│       └── documents.csv            # 实验1正式九字段输出，18篇
├── probe_urls.py              # 早期试爬脚本，作为历史材料保留
├── probe_results.json         # 早期试爬结果，不能代替最终选集
├── probe_texts/               # 早期试爬文本，可能包含整页噪声
├── src/
│   ├── crawl_sources.py       # 正式网页采集程序
│   └── exp1_preprocess.py     # 离线清洗、异常/重复检查及报告
├── tests/
│   ├── test_crawl_sources.py  # 爬虫离线单元测试
│   └── test_exp1_preprocess.py # 清洗离线单元测试
├── requirements.txt
└── README.md
```

不同批次可能还包含独立的诊断记录、来源证据、离线提取来源信息及备份清单。具体以 `data/raw/` 中实际文件为准。

### 关键文件怎么用

| 文件 | 用途 | 是否作为下游正式输入 |
|---|---|---|
| `data/raw/selected_documents.jsonl` | 18 篇已验收文档，包含原始正文与来源字段 | **是：实验1读取它进行清洗** |
| `data/raw/raw_documents.jsonl` | 爬虫某一次运行的结果快照，可能只有当次请求的部分结果 | 否，不应直接替代最终选集 |
| `data/raw/crawl_report.json` | 记录某次运行的 HTTP 状态、正文长度、失败原因等 | 否，用于追溯采集过程 |
| `data/raw/source_acceptance.csv` | 逐条候选来源的采集状态、质量结论和入选情况 | 否，用于验收与审计 |
| `data/raw/acceptance_report.md` | 最终 18 篇清单、来源取得方式、质量判断和限制说明 | 否，用于说明数据准备过程 |
| `data/raw/html/` | 保存的网页 HTML；部分文件通过浏览器保存，非本次爬虫 HTTP 响应 | 不是直接的下游文本输入，主要用于追溯或重新离线提取 |
| `probe_results.json`、`probe_texts/` | 早期试爬材料 | 否，不应作为最终数据集 |

## 3. 环境与运行方式

项目开发与当前验证环境为 Windows、Python **3.12.7**。爬虫代码按 Python 3.11 兼容语法编写，但目前没有在 Python 3.11 解释器中完成实际运行验证。

在项目根目录 `D:\tourism-mining` 下执行：

```powershell
# 安装依赖
python -m pip install -r requirements.txt

# 只检查配置，不请求网站，也不覆盖采集输出
python src/crawl_sources.py --dry-run

# 运行离线测试，不请求真实网站
python -m unittest discover -s tests -v
```

当前全部离线测试34项通过（爬虫11项、清洗23项）。最终18篇选集合并与实验1清洗没有重新请求网站。

### 是否需要再次运行爬虫？

**目前不需要。** 最终选集已经准备好。直接运行：

```powershell
python src/crawl_sources.py
```

会重新请求来源清单中启用的 URL，并更新爬虫自己的采集快照和报告；这不是重建最终 18 篇选集的必要步骤。尤其是通过浏览器保存 HTML 后离线提取的两篇官方路线，不能假设重新请求时一定能获得相同结果。

如果后续确实需要复现采集，请先备份当前文件，确认来源访问规则及 `config/source_urls.csv`，再按单条试爬、人工检查、最后批量采集的顺序执行。不要为了验证最终选集而重复发起无必要的网络请求。

## 4. 来源清单与爬虫行为

### 4.1 来源清单

`config/source_urls.csv` 是正式爬虫使用的来源清单，采用 UTF-8-SIG 编码，字段顺序固定：

```text
doc_id,scenic_id,source_type,source_name,source_url,enabled
```

- `doc_id`：全局唯一文档 ID，格式示例为 `S01-O-001`、`S01-T-001`。
- `scenic_id`：景区 ID，只使用 `S01`、`S02`、`S03`。
- `source_type`：`official`（官方路线）或 `tourist`（游客游记）。
- `source_name`：来源网站或平台名称。
- `source_url`：文章原始 URL。
- `enabled`：`true` 表示允许参与爬虫执行，`false` 表示停用。

最终验收记录显示来源清单保留 **30 条候选来源，其中 18 条启用、12 条停用**。启用状态只控制爬虫是否尝试请求，不代表来源一定可访问，也不代表提取内容自动通过质量验收。

新增来源应使用新的、唯一的文档 ID；不要把某个旧 ID 改作另一篇文章的 ID。更新 URL 前，应核对来源网站、文章标题、景区归属和网站访问规则。

### 4.2 正式爬虫

`src/crawl_sources.py` 负责：

1. 校验景区配置和来源 CSV 字段；
2. 按启用状态逐条串行请求来源；
3. 解析 HTML 并尝试定位文章正文；
4. 将当次采集结果写入 JSONL；
5. 生成包含 HTTP 状态、正文长度、解析状态和错误原因的采集报告；
6. 在覆盖采集输出前备份旧输出。

爬虫只负责**网页获取和原始正文提取**，不负责正式文本清洗、实体识别、路线抽取或生成 `documents.csv`。

正文提取优先使用文章容器，并排除导航、页脚、脚本、样式以及明确的评论、推荐或弹窗区域。正文定位失败时记录失败，不把整页 `body` 直接当成文章正文。即便 HTTP 返回 200，也不意味着正文完整或内容适合实验。

程序对网络请求采用串行方式，默认请求间隔至少 2 秒；对部分连接错误、超时和指定服务器错误进行有限重试。对 403、412、429 等拒绝或限制访问的响应不进行绕过，不登录、不尝试规避验证码。遇到拒绝访问时，应停用来源或人工寻找合法、可访问的替代来源。

### 4.3 采集结果状态的解释

- `success`：请求成功取得页面，不等于内容已通过人工验收。
- `failed`：请求、HTTP 或响应内容获取失败。
- `blocked`：遇到验证码或明确拒绝访问等情况。
- `needs_review`：提取到了候选正文，但仍需人工检查。
- `parse_failed`：未能提取到可用正文。
- `disabled`：来源当前停用，未请求。

采集状态、质量验收状态和最终选集是不同概念，应以 `source_acceptance.csv` 和 `acceptance_report.md` 共同追溯。最终 18 篇选集中的两篇离线官方路线应按离线来源记录理解，不能将其描述为本次 HTTP 请求成功。

## 5. 数据格式与实验1接口

### 5.1 原始数据

`data/raw/selected_documents.jsonl` 是最终选集，每行一个 JSON 对象。当前包含 18 条记录，必要信息包括：

- `doc_id`
- `scenic_id`
- `scenic_name`
- `source_type`
- `source_name`
- `source_url`
- `title`
- `raw_text`

此外还包含采集时间、最终 URL、HTTP 状态、解析器名称及复核标记等采集追溯字段。对于离线提取、未发生 HTTP 请求的文档，相关 HTTP 字段按真实情况保留为 `null`。

`raw_text` 是原始正文，不应在清洗过程中直接覆盖。

### 5.2 实验1正式输出

根据《数据挖掘》小组分工与接口约定，实验 1 的唯一正式输出是：

```text
data/processed/documents.csv
```

字段顺序必须固定为以下 9 列：

```text
doc_id,scenic_id,scenic_name,source_type,source_name,source_url,title,raw_text,clean_text
```

其中：

- `raw_text`：保留原始正文，不修改、不覆盖；
- `clean_text`：存放清洗后的文本，供实体识别、路线抽取等后续实验使用；
- CSV 使用 UTF-8-SIG 编码，方便使用 Excel 查看；
- JSON 使用 UTF-8 编码；
- 字段名使用小写英文下划线；
- 空值应按约定保留为空，不要用“暂无”“不详”等文字替代真正缺失值。

清洗时需尽量去除页面导航、评论、相关推荐、无关推广等噪声，同时保留景点名称、路线顺序、编号、箭头、时间、交通方式和游览用时。清洗结果需要通过抽样人工检查，避免把有效的路线信息误删。

实验1已输出清洗前后样例、空值、重复候选和景区分布统计。下游应读取 `data/processed/documents.csv` 的 `clean_text`，原始JSONL仅作为可追溯的清洗输入。

## 6. 数据质量边界与可追溯性

- 18 篇选集的数量与景区分布已达到小组目标。
- 文章正文仍可能包含评论、相关推荐、推广、历史票价或与其他景区有关的内容；这些属于后续清洗范围。
- 旅游网页中的路线、时间、价格可能随时间变化。本数据集用于课程中的程序性知识挖掘，不应将旧文章中的价格或开放信息直接当作当前出行建议。
- 游记平台上的文章不必然等于已经独立核实的亲历记录；对存在疑问的样本应如实说明，不夸大数据真实性。
- 原始候选、最终选集、验收记录和备份的职责不同。清洗与后续处理应生成新文件，保留原始正文与历史证据。
- 两篇离线官方路线的来源映射、HTML 哈希、提取方式及发布机构等细节，以 `data/raw/acceptance_report.md` 和相应的离线来源证据文件为准。

## 7. 实验1：离线清洗与验收

```powershell
python -m unittest discover -s tests -v
python src/exp1_preprocess.py
```

可从任意当前目录用脚本实际路径启动；默认配置、输入和输出路径由脚本位置定位。代码使用Python3.11兼容语法，已在Python3.12.7真实执行，尚未在3.11解释器实测。

本模块只读取原始选集和配置，从JSONL原八个字段同名复制，独立生成clean_text。输入附加的HTTP、解析器等字段不进入九字段正式接口。不会修改raw_text、原始JSONL、爬虫、来源配置或景区映射，不调用网络。

冻结规则落实为：识别HTML结构及实体，保留有效正文和段落；统一换行和特殊空白；局部清除完整固定公告、明确发布日期/阅读量、已确认广告和带完整边界的评论/推荐卡片。出游日期、行程属性、价格、交通、路线编号/箭头及其他景区正文保留；不凭酒店、门票、推荐等关键词过滤。无法判断的日期、乱码或来源质量事项记录复核，不擅自改文。完整模板与平台边界见代码和测试。

严重必需字段错误、空正文、景区映射错误、无法确认的重复doc_id排除并报告；短文和疑似乱码只标记。相同URL、原文/清洗后完全相同及近似重复均只列候选，不自动删除。近似方法为去空白后字符5-gram集合Jaccard≥0.80、最短80字符，不能据此排除局部复用。

每次从原始JSONL重新生成，覆盖前只备份模块负责的以下六个输出至 `data/processed/history/<UTC时间>/`，不移动或删除其他实验文件：

- `documents.csv`：唯一正式九字段接口，UTF-8-SIG。
- `anomalies.csv`：异常文档ID、输入行、类型、原因和处理结论，UTF-8-SIG。
- `duplicate_candidates.csv`：完全及近似重复候选，UTF-8-SIG；零候选时仍有表头。
- `cleaning_report.md`：真实统计、逐篇字符变化、三个景区对比、路线样例及复核事项。
- `cleaning_audit.jsonl`：逐篇清洗规则命中及各阶段字符变化，UTF-8。
- `cleaning_summary.json`：可机器读取的验收统计与输入SHA-256，UTF-8。

本次真实运行：18篇输入、18篇输出、排除0篇；各景区1官方+5游记；原文字符64962、清洗后61786，净减少3176（4.889%）；字段缺失、空正文和重复候选均0。34项离线测试通过。重复运行CSV字节一致；回读逐记录核对前八字段与原文完全一致，110个原始/配置/爬虫文件的哈希未变，校验基线在 `data/exp1_input_integrity.json`。

保留4篇复核：S01-T-001（平台整理和路线事实）、S02-O-002（原标题泛化）、S02-T-003（目标景区段落较短）、S03-T-005（气候综述及亲历日期不确定）。这些不是清洗导致的严重结构异常，均按冻结规则保留。技术验收通过不等于来源事实及历史价格已经独立核实。

项目中未找到独立任务书、评分细则或小组docx，本次依据用户提供的冻结规则和本文九字段约定实现；不能声称已逐项核对不存在于工作区的评分原件。实验1交付完成后停止，不实现实体识别或后续实验。

## 实验二第一步：三类实体识别（已完成）

采用离线 Jieba 自定义词典、原文精确匹配与时间正则。POI仅匹配当前 scenic_id 的内部景点词典并映射标准名称和ID；TRANSPORT匹配交通词典并过滤站名后缀和玩具等语境；TIME包括季节、固定时段及数字日期、时刻、范围、持续时间，暂不归一化。重叠优先保留最长表达，每次出现分别记录，位置使用原始clean_text的左闭右开字符索引。

配置：`config/poi_aliases.csv`、`config/transport_terms.csv`、`config/time_terms.txt`。输入：`data/processed/documents.csv`（UTF-8-SIG，18篇）。输出：`results/exp2/entities.json`（UTF-8数组），校验成功后原子替换。脚本通过自身路径定位项目，可从其他工作目录执行。

在项目根目录运行：

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python src/exp2_ner.py
```

已完成词典识别、时间正则、稳定提及ID、批量导出和离线测试。待完成词频统计、路线抽取、共指消解、条件分析等后续阶段。当前规则依赖冻结词典覆盖范围；交通歧义采用局部后缀规则，复杂否定、引用和跨句语境仍需后续人工评估；不推测词典之外的景点，不进行时间归一化。

## 实验二第二步：prepare（历史阶段说明）

安装依赖后运行 `python src/exp2_analysis.py prepare`。统计只读取实体JSON及clean_text，不重新识别或清洗数据。首次冻结entities_before.json并记录SHA256、源文件修改时间和冻结时间；后续运行校验哈希，保留已填写的人工表。

输出位于results/exp2：frequency_before内6份UTF-8-SIG CSV；charts_before内7张300 DPI中文横向柱状图；manual_review.csv内18条分层实体样例；manual_review_snippets.csv内6个独立原文片段；manual_missed_entities.csv为空漏识别模板。填写方法见manual_review_guide.md，阶段结果见optimization_report.md。

POI按景区和poi_id汇总，缺失ID依次回退标准名和原文；TRANSPORT按标准名汇总；TIME按原文汇总。CSV按景区分组，跨景区交通与时间图合计次数。每千字频次的分母是该景区全部文档clean_text字符数。TIME分类优先级：范围、具体时刻、日期、相对日期、持续时间、时间段、季节及旅游时期、其他。混合表达按最高优先级归类。

config/visualization_stopwords.txt仅影响图表，当前无过滤词；config/entity_exclusions.csv为空的人工证据规则模板，本次不参与识别。固定抽样种子20261101；优先不同文档及官方/游客来源。prepare不会生成优化后文件。请完成两份人工表后再继续优化；当前入口仅支持prepare。


## 实验二第二步：人工抽查与迭代
实体抽查18/18正确，不代表全量准确率；六段原文问题单独记录。鸣春谷别名统一，保留景区大门和具体交通线路；补充月份、星期范围，修复小数尾部时间误匹配。
冻结结果results/exp2/entities_before.json；第二轮entities_after.json和正式entities.json；统计frequency_before/、frequency_after/，图表charts_before/、charts_after/。详情见results/exp2/optimization_report.md。
POI按景区与ID，交通按canonical_name，TIME按原文统计，同时输出提及数、文档频率及每千字频率（景区全部clean_text总字符数作分母）。展示停用词与实体排除规则分开管理。
运行：`python -m src.exp2_ner`；将经校验的正式结果保存为entities_after.json后运行`python -m src.exp2_analysis after`。不要重跑prepare覆盖第一轮统计。
测试：`python -m unittest discover -s tests -v`。Windows临时目录权限受限时，将TEMP和TMP设置为项目内.test_tmp。


## 实验二第三步：正式规范与成果索引

实验二目标为识别旅游文本中的POI、TRANSPORT、TIME，采用离线Jieba、自定义词典和正则规则。上述prepare段落记录第一轮历史状态；人工抽查与第二轮迭代现已完成，当前分析入口支持prepare和after。词频统计已完成，路线抽取等不属于本次交付。

| 成果 | 路径 |
|---|---|
| 正式定义规范 | `results/exp2/entity_definition_standards.md`、`results/exp2/entity_definition_standards.csv` |
| 词典配置 | `config/poi_aliases.csv`、`config/transport_terms.csv`、`config/time_terms.txt` |
| 识别与统计程序 | `src/exp2_ner.py`、`src/exp2_analysis.py` |
| 正式JSON | `results/exp2/entities.json`（与entities_after.json一致，1551条） |
| 两轮JSON | `results/exp2/entities_before.json`、`results/exp2/entities_after.json` |
| 两轮词频 | `results/exp2/frequency_before/`、`results/exp2/frequency_after/`（各6份CSV） |
| 两轮图表 | `results/exp2/charts_before/`、`results/exp2/charts_after/`（各7张） |
| 人工抽查 | `results/exp2/manual_review.csv`、`results/exp2/manual_review_snippets.csv`、`results/exp2/manual_missed_entities.csv` |
| 人工原始备份 | `results/exp2/manual_review_original/` |
| 优化报告 | `results/exp2/optimization_report.md` |

安装：`python -m pip install -r requirements.txt`。既有识别入口：`python src/exp2_ner.py`；第二轮统计入口：`python src/exp2_analysis.py after`（读取已有entities_after.json）。本次仅整理规范和读取验收，没有执行这两个生成命令；历史成果应保护，不为验收重跑生成流程。

测试：`python -m unittest discover -s tests -v`。如Windows系统临时目录权限受限，可先在项目内创建.test_tmp并将TEMP和TMP指向其绝对路径。本阶段完成后停止，不进入实验三。
