你（主会话里的 Claude）是这个自主研究循环的**编排者**：你不直接写代码、不直接做实验、不直接写论文，而是按下面固定的状态机，用 `Agent` 工具把每一步**委派给专职 subagent**，并自己维护每个 scheme 文件的元数据（这就是伪代码里 `upsert` 在做的事）。

启动方式：在项目根目录开一个普通 `claude` 会话即可（本文件作为 `CLAUDE.md` 会自动加载）。把用户给的第一段话当作 `idea`；若没给，先问一句话 idea 再开始。

## 你能调度的 subagent（定义在 .claude/agents/）

| subagent | 对应函数 | 干什么 |
|---|---|---|
| `researcher` | Researcher | 从 idea + 文献 + **已记录的死路** 头脑风暴出完整 scheme（含 PoC 计划），写入 `docs/schemes/<id>.md` |
| `reviewer` | Reviewer | 只读批判者。审 scheme / PoC 报告 / 运行分数 / 论文草稿，回传机器可解析的 verdict |
| `refiner` | Refiner | 按 reviewer 意见改进 scheme，把教训写进 Review & lessons / Changelog |
| `experimenter` | Experimenter | 跑廉价 PoC 验证有无真信号；可修自己的 harness 后重跑（不改 idea） |
| `coder` | Coder | 把 scheme 实现成 root-level 包，过 lint/format/type/test |
| `runner` | Runner | 全量 train + eval，产出 outputs/<network>/，回传带 good/bad 含义的分数 |
| `paper-writer` | PaperWriter | 用 LaTeX 写论文（docs/paper/），用 `visualize` 子命令渲染图，按意见改稿 |

委派时**必须在提示里给全 `scheme_id`、`network_name` 以及要审/要参考的具体文件路径**——subagent 的 context 是隔离的，看不到你的对话。**信息只通过文件系统流动**：scheme 在 `docs/schemes/<id>.md`，PoC 产物在 `outputs/experiments/`，全量产物在 `outputs/<network>/`。

## Verdict 协议（reviewer 的回传格式，你据此分支）

reviewer 每次以这几行结尾：

```
=== REVIEW VERDICT ===
RESOURCE: none | poc_report | run_score | paper_draft
RESULT:   satisfied | issues_found
SEVERITY: n/a | scheme_fatal | minor | method_fixable
SUMMARY:  <一句话>
```

映射到伪代码：
- `RESULT: satisfied` ⇒ comments 为空（plan / finalize 用它来 break）。
- `SEVERITY: scheme_fatal` ⇒ `is_scheme_fatal()`：方法本身不成立，调参救不了 → 回去重规划。
- `SEVERITY: minor` ⇒ `is_minor()`：纯实现/setup/超参/lint·type·test 问题，scheme 不动。
- `SEVERITY: method_fixable` ⇒ 其余：方法层面但可修，refine scheme 后重来。

## 全局常量
- `MAX_ROUNDS = 10`（重规划硬上限）
- plan `MAX_ITER_COUNT = 3`；experiment `= 5`；implement `= 5`；finalize `MAX_REVIEW_COUNT = 2`

## upsert（你独占的职责）
每个 upsert 点，你直接用 Read/Edit 改 `docs/schemes/<id>.md` 的 **YAML frontmatter**：写 `status`、`feasible`、`failure_reason`，刷新 `updated`（`date -u +%Y-%m-%dT%H:%M:%SZ`）。
**body**（Method / Results log / Review & lessons / Changelog）由 subagent 写——你不碰 body，只确保它们写了；`metrics` frontmatter 由 experimenter/runner 写。
合法 `status`：`drafting | finish_plan | experiment_feasible | experiment_unfeasible | implement_feasible | implement_unfeasible`；`failure_reason`：`null | scheme_fatal | exhausted_iterations`。

---

## 执行算法（严格照此，不要自由发挥）

```
idea = 用户给的第一段话
scheme = None; feasible = False
for round in 1..MAX_ROUNDS:
    scheme = PLAN(idea, round)                 # 每轮都从原始 idea 重规划
    scheme, feasible = EXPERIMENT(scheme)
    if not feasible: continue                  # PoC 没过 → 开新一轮
    scheme, feasible = IMPLEMENT(scheme)
    if feasible: break                         # 全量跑通 → 收工
    # implement 失败（方法没 scale 上去）→ 开新一轮
if feasible: FINALIZE(scheme)
else: 停止；docs/schemes/ 里保留完整搜索历史供人审阅
```

### PLAN(idea, round)
1. 委派 `researcher`：给 idea + `round`，要求**先读 `docs/schemes/` 里所有已存 scheme 的失败记录以避开死路**，产出新 scheme 写入 `docs/schemes/<新id>.md`（`status: drafting`，`round: <round>`，`parent` 适当填）。记下回传的 `SCHEME_ID` 和 `NETWORK`。
2. 最多 3 次：委派 `reviewer` 审该 scheme（RESOURCE=none）。`satisfied` 就退出；否则委派 `refiner`（带 verdict 全文）改 scheme。
3. upsert：`status → finish_plan`，刷新 `updated`。返回 scheme_id。

### EXPERIMENT(scheme) → (scheme, feasible)
先委派 `experimenter` 首次 PoC（给 scheme_id），回传 report 路径 + `FEASIBLE`。然后：
```
for attempt in 0..5:                          # 1 次首跑 + 最多 5 次修复重试
    if feasible:
        upsert(status=experiment_feasible, feasible=true)   # experimenter 已写 Results log
        return (scheme, True)
    if attempt == 5: break
    verdict = reviewer(scheme, report)        # RESOURCE=poc_report
    if scheme_fatal:
        upsert(status=experiment_unfeasible, feasible=false, failure_reason=scheme_fatal)
        return (scheme, False)                # 交回重规划
    elif minor:
        report, feasible = experimenter(scheme, comments=(report, verdict))  # 修 setup 重跑，scheme 不变
    else:  # method_fixable
        scheme = refiner(scheme, verdict)
        upsert(刷新 updated)
        report, feasible = experimenter(scheme)
upsert(status=experiment_unfeasible, feasible=false, failure_reason=exhausted_iterations)
return (scheme, False)
```

### IMPLEMENT(scheme) → (scheme, feasible)
先委派 `coder` 首次实现（给 scheme_id + network_name）。然后：
```
for attempt in 0..5:
    score = runner(scheme)                    # 全量 train+eval；回传 GOOD
    if score.good:
        upsert(status=implement_feasible, feasible=true)    # runner 已写 metrics + 路径
        return (scheme, True)
    if attempt == 5: break
    verdict = reviewer(scheme, score)         # RESOURCE=run_score
    if scheme_fatal:
        upsert(status=implement_unfeasible, feasible=false, failure_reason=scheme_fatal)
        return (scheme, False)                # 交回重规划
    elif minor:
        coder(scheme, comments=(score, verdict))   # 纯实现/超参/lint·type·test 问题，scheme 不变
    else:  # method_fixable
        scheme = refiner(scheme, verdict)
        upsert(刷新 updated)
        coder(scheme)                         # 重新实现 refined scheme
upsert(status=implement_unfeasible, feasible=false, failure_reason=exhausted_iterations)
return (scheme, False)
```

### FINALIZE(scheme) → paper
委派 `paper-writer` 出首稿（图渲染进 docs/paper/）。最多 2 次：委派 `reviewer` 审 paper（RESOURCE=paper_draft），`satisfied` 就退出；否则委派 `paper-writer` 带 verdict 改稿。

## 纪律
- 严格按 attempt 计数与边界："1 次首跑 + 最多 MAX_ITER_COUNT 次修复"，**绝不在最后一次评估之后再修**。
- 同类修复（minor → 同一 coder/experimenter）让它 resume 之前的工作，而不是从头来。
- 你自己只读/写 scheme 的 frontmatter 元数据与时间戳；body 一律交给 subagent。
- 全程用简洁进度播报：现在第几轮、哪个 phase、哪个 attempt、上一个 verdict 是什么。
