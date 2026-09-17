---
name: stdd-build
description: "STDD Phase 3: BUILD — 切片规划 + TDD 实现 + 质量验证（SLICE/BUILD/VERIFY 合并，V3.0.5）"
stdd_version: "3.0.5"
---
# STDD Phase 3: BUILD — 切片规划 + TDD 实现 + 质量验证

## 阶段目标

本阶段是合并后的 BUILD（原 SLICE + BUILD + VERIFY 三阶段合一）：
1. **A. 切片规划**：将测试方案拆分为可独立实现的垂直切片
2. **B. TDD 实现**：按切片逐一执行 RED → GREEN → REFACTOR
3. **C. 质量验证**：全量质量检查 + 失败模式检查 + 生成 test-report

### Step 0: 版本自检

先读取并执行版本自检步骤：`.fstdd/skills/_shared/version-check.md`

> 检查项目 `.fstdd/version.yaml` 与技能版本是否一致。落后时告警但不阻断执行。

---

## 前置条件

- Phase 2 已完成（design.md + specs + test-plan.md 经用户确认）
- `.fstdd.yaml` 中 `phases.spec.confirmed_at` 已设置

## 执行模式

**自动迭代，行为取决于所选模式：**

- **普通模式**：仅在遇到阻塞或重大设计偏离时暂停与用户交互。
- **长程模式**：所有偏离和阻塞自动处理并记录，全程不中断。仅在触发降级条件时暂停。

进入本阶段时，先读取 `.fstdd.yaml` 中的 `long_range.mode` 确定当前模式。

**长程退出检测**：在每个切片开始前，检查用户最新消息是否包含"切换普通模式"或"退出长程"。如检测到，更新 `.fstdd.yaml` 中 `long_range.mode: normal`，当前切片完成后暂停等待用户确认，后续切片按普通模式交互。

## 长程模式运行协议（仅在 `long_range.mode == "full_auto"` 时适用）

### ⚠️ 长程模式强制约束 / MANDATORY LONG-RANGE CONSTRAINTS

> 长程模式 ≠ 可以跳过流程步骤。
> Long-range mode skips authorization interactions, NOT process steps.

以下规则不可违反。违反任一条 = 流程失败：

| # | 中文 | English |
|---|------|---------|
| 1 | **每个 Step 必须执行** — 长程模式跳过的是授权交互，不是流程步骤 | **EVERY Step MUST be executed** — long-range skips authorization, NOT process steps |
| 2 | **Step B4 切片验证不可跳过** — 每个切片必须通过 TC 覆盖 + 产出物核对 + 测试通过三项检查 | **Step B4 slice verification CANNOT be skipped** |
| 3 | **每个切片必须有新增测试** — 新增测试数必须 > 0 | **EVERY slice MUST have new tests** |
| 4 | **进度标记必须有证据** — slice done 必须关联 tc_coverage / new_tests / verified_at | **Progress markers MUST be evidence-backed** |
| 5 | **降级触发覆盖静默失败** — 0 TC 覆盖 → WARNING；连续 3 切片无新增测试 → DEGRADE | **Degradation covers silent failures** |
| 6 | **失败模式检查全量执行** — 不得用占位符代替未执行的检查 | **ALL failure-mode checks MUST be real** |
| 7 | **Gate 3 报告必须如实** — 不得美化、不得省略缺口 | **Gate 3 report MUST be truthful** |

### 运行协议

1. **无交互原则**：A/B/C 各部分自动执行，不使用 AskUserQuestion（Gate 3 除外）
2. **批量执行**：将同一切片的 RED+GREEN+REFACTOR 合并在一轮内完成
3. **自动降级检测**：连续 3 次修复失败 / 通过率 < 95% / 安全问题 / 0 TC 覆盖 / 3 切片无新增测试
4. **切片验证**：每个切片完成后必须执行 Step B4，通过后才能进入下一切片
5. **进度汇报**：每个切片完成后输出验证结果（TC 覆盖率 + 测试数），但不等待回复
6. **仅降级/仅 Gate 3 时暂停**

---

# Part A：切片规划

## A1: 读取 Phase 2 产出

**V2.9.2 Canonical-First**：优先读取 YAML 格式。

1. 读取 `proposal.yaml` → 获取 capabilities 和 risk_areas
2. **优先读取 `specs/<capability>/spec.yaml`**（如存在），回退读取 `specs/<capability>/spec.md`
3. 读取 `agent_spec.yaml`（验证规格）
4. 读取 `test-plan.md`
5. **执行 CLI 依赖图构建**：`python bin/stdd dependency-graph --format json`
   - 获取 `nodes`, `edges`, `zero_dependency`, `cycles`
   - 如检测到循环依赖（exit code 1），先分析 cycles 输出再手动审查

**V2.9 轻量模式**：如果 `.fstdd.yaml` 中 `mode: lightweight`，跳过切片规划，使用 1 个隐式切片，直接进入 Part B。

## A2: 五步智能切片分析

### A2a: 依赖图分析
从 `dependency-graph` JSON 输出中提取零依赖节点、依赖链深度、关键路径。

### A2b: 风险评分
对每个 capability 进行风险评分（1-5）：
1. 经验库风险：`python bin/stdd experience list --format json`，有 `severity: high` 匹配经验 → +2
2. 复杂度风险：Scenario > 5 → +1；跨模块交互 → +1
3. 变更类型：MODIFIED 且接口变更 → +1

风险分 ≥ 4：高风险 🟡 | 2-3：中风险 🟢 | 1：低风险

### A2c: 工作量预估
| 粒度 | 估算 |
|------|------|
| S（小）| 1-2 个 TC，单一文件修改 |
| M（中）| 3-5 个 TC，2-3 个文件修改 |
| L（大）| 6+ 个 TC 或 >3 个文件修改 |

### A2d: 智能分组
1. 同一 capability 内紧密相关 Scenario 合并为一个切片
2. 风险高的 capability 独立成切片
3. 工作量大的 capability 拆分为多个切片（S-M 粒度）
4. 跨 capability 的 Scenario 合并为集成切片

### A2e: 并行化建议
1. 零依赖节点标记为"可并行"（parallel_group = 1, 2, 3...）
2. 每个并行组约 2-3 个切片

## A3: 排序

1. 按依赖关系拓扑排序
2. P0 切片优先
3. 无依赖的切片标记为"可并行"

## A4: 生成执行计划

先读取模板：`.fstdd/templates/tasks.md` 和 `.fstdd/templates/slices.md`

生成 `tasks.md`（实现任务清单）和 `slices.md`（切片执行计划，含 Dependency Graph Summary + 五步分析结果 + Rationale）。

## A5: 写入文件

1. 写入 `tasks.md`
2. 如需要，写入 `slices.md`
3. 通知用户切片数量和执行顺序
4. 进入 Part B：TDD 实现

---

# Part B：TDD 实现

## B0: 上下文预算检查（context-budget-check）

1. 估算对话轮次：> 80 轮 → 强烈建议重置
2. 如建议重置：确认 phase-context.md 已更新 + 输出 `stdd state --resume` 结果

> 软建议，不阻断。

## B1: 学习开发规范与项目规则

1. 读取 `.fstdd/config.d/project.yaml` → 获取 `project.language`
2. 读取 `.fstdd/standards/<language>.md`
3. **加载 `.fstdd/rules/`**：读取 `.fstdd/rules/common/*.md` 和 `.fstdd/rules/<language>/*.md`
4. **执行代码结构摘要**：`python bin/stdd structure delta <change>` 记录本 change 的代码结构变化

## B2: 加载匹配经验

从项目经验库加载与当前变更相关的经验，预防已知失败模式：

1. 执行 `python bin/stdd experience list --language <project.language> --format json`
2. 筛选 `lifecycle_state` 为 `verified` 或 `settled` 的经验
3. 按 `project_type` 过滤；选出最相关的经验（默认最多 10 条）
4. 将匹配经验内容（pattern + root_cause + fix_template）注入编码上下文
5. 编码时对照经验库检查：模式匹配 → 参考 fix_template 预防已知错误

## B3: 按切片顺序执行

对 `slices.md` 中的每个切片（或 `tasks.md` 中的每个任务）：

### B3.1: RED — 编写测试

**V2.9 模式缩放**：先检查 `.fstdd.yaml` 中的 `mode` 字段。

*lightweight 模式*：写 1-2 个聚焦测试，直接针对 bug 条件或优化预期行为，确认失败（RED）。

*standard/thorough 模式*：
1. 从 `test-plan.md` 中找到本切片对应的 TC 案例，转化为 pytest 测试函数
2. 测试命名：`test_<被测方法>_<场景>_<预期结果>`，注释中标注 TC-ID
3. 运行测试 → **确认失败（RED）**
4. 如果测试直接通过 → 检查是否已有等价测试，有则跳过 RED 阶段
5. **经验检查点**：测试是否反映了 B2 加载的经验模式？

### B3.2: GREEN — 最小实现

*lightweight 模式*：最小实现代码，仅满足聚焦测试。
*standard/thorough 模式*：
1. 写刚好够通过测试的代码
2. **不写超过测试覆盖范围的代码**
3. 遵循开发规范
4. 运行测试 → **确认通过（GREEN）**，同时运行已有测试 → 确认无回归

### B3.3: REFACTOR — 重构

1. 消除重复代码、改善命名、提取公共逻辑
2. 应用 deep modules 原则 + deletion test
3. 运行测试 → **保持 GREEN**

### B3.4: 切片验证（每切片强制，不可跳过）

1. **TC 覆盖检查**：读取 `test-plan.md`，确认本切片每个 TC-ID 都有对应测试函数；覆盖率 < 100% → 回到 B3.1
2. **产出物核对**：对照 `slices.md` 实现目标，逐项检查文件/模块是否存在
3. **测试运行**：`pytest tests/ -k "<slice_test_pattern>" -v`，本切片新增测试必须 > 0 且全部通过；同时跑全量回归
4. **更新状态**（仅在全部通过后，写入 `.fstdd.yaml`）：
   ```yaml
   phases:
     build:
       slices_completed:
         "<N>":
           status: "done"
           tc_coverage: "<M>/<K>"
           new_tests: <M>
           verified_at: "<timestamp>"
   ```
5. **不通过处理**：修复 → 重新验证 → 最多 3 次；3 次仍不通过 → 降级为普通模式暂停

### B3.5: 并行切片合并验证（条件触发）

当 slices.md 存在 `parallel_group` 标记且本组所有切片已完成 B3.4：
1. `git diff --check` 冲突检查
2. 全量测试确认无意外交互
3. 接口签名兼容验证；产出物合并

### B3.6: 更新 phase-context.md

每个切片完成后在 phase-context.md 的 Phase 3 章节追加切片记录（编号、TC 覆盖、新文件）。触发经验库条目时注明 EXP-ID。

## B4: 处理设计偏离

如果在实现过程中发现 spec/design 需要调整：

**小的偏离**（不改变接口和行为语义）：
- 记录到 `pending-adjustments.yaml`（Canonical YAML 格式）
- 检查是否命中经验库已知模式 → 如命中，引用 EXP-ID
- 继续执行

**大的偏离**（改变接口或行为语义）：

*普通模式*：
- 记录到 `pending-adjustments.yaml`
- **暂停自动迭代，向用户报告**设计偏离详情

*长程模式*：
- 自动记录到 `pending-adjustments.yaml`（含原始设计引用、实际调整、原因、影响范围）
- 继续执行，不暂停
- 调整将在质量验证部分汇总为 `design-adjustments.yaml`

**技术阻塞**：
- *普通模式*：暂停，向用户报告
- *长程模式*：按预授权 A2 策略（workaround / skip_slice），无法处理时降级暂停

## B5: 切片完成

每个切片完成后标记 tasks.md 中对应任务为 `[x]`，进入下一个切片。

**所有切片完成后** → 进入 Part C：质量验证。

---

# Part C：质量验证

## ⚠️ 强制步骤清单

以下 Step 全部强制，**不可跳过任何一步**。进入 Gate 3 前必须确认所有步骤已完成：

| Step | 名称 | 完成标志 |
|------|------|---------|
| C1 | 多路并行技术评审（3 代理） | 3 个代理均返回审查结果 |
| C2 | 全量质量检查 | pytest + coverage + lint 全部执行 |
| C3 | Diff 审查 | 逐文件检查所有变更 |
| C4 | 失败模式检查 | 全部检查完成 |
| C5 | 经验库自动记录/更新 | 失败模式已记录到 .fstdd/experiences/ |
| C6 | 汇总设计调整 | design-adjustments.md 已生成（或确认无需调整） |
| C7 | 生成测试报告 | test-report.md 已写入 |

**Gate 3 前置条件**：上述 7 步全部完成后，才能进入 Gate 3 用户确认。

## C1: 多路并行技术评审

在运行自动化质量检查之前，**必须先执行多路并行技术评审**。读取 `.fstdd/config.d/quality.yaml` 中的 `review` 配置，启动 3 个并行评审代理（代码、测试、文档），收集审查发现并逐条处置（修复 / 记录为已知问题）。

## C2: 全量质量检查

1. 运行全量测试：`pytest` → 确认全部通过
2. 覆盖率检查：`pytest --cov` → 对照阈值
3. **CI 检查**：执行 `python bin/stdd ci check-failures <change>` → 逐项处置
4. lint / 类型检查（如项目配置）

## C3: Diff 审查

逐文件检查所有变更：无调试残留、无死代码、无注释掉的旧逻辑、变更与 spec 一致。

## C4: 失败模式检查

对照经验库中的已知失败模式逐项检查（含锚定缺失、Agent CP 失败、跨系统不一致等）。**全量执行，不得用占位符代替。** 未实际执行的检查必须标注 SKIPPED。

## C5: 经验库自动记录/更新

将失败模式检查中发现的新模式自动记录到 `.fstdd/experiences/`，更新既有经验的命中次数。

## C6: 汇总设计调整

1. 汇总 Part B 记录的所有 `pending-adjustments.yaml`
2. 生成 `design-adjustments.yaml`（修订需求），作为后续迭代的输入

## C7: 生成测试报告

写入 `test-report.md`，包含：
- TC 覆盖率（planned vs actual）
- 每切片验证状态
- 失败模式检查结果
- 已知问题 + 未完成项（逐项说明名称、原因、影响、补完计划）

---

## Gate 3 确认

所有 C1-C7 完成后，进入 **Gate 3 用户确认**：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  STDD Phase 3: BUILD — Gate 3 质量验收
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  TC 覆盖率:   <X/Y>
  切片完成度:  <N/M> 全部通过
  失败模式:    <K> 项检查完成
  测试报告:    test-report.md 已生成

  需要你确认（stdd gate approve --gate 3 --confirmed-by dialog --evidence "用户确认原文"）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

- 长程模式：Gate 3 仍为强制确认门，不自动跳过。
- **V3.0.5 硬防线：AI 不得静默自跑 approve；必须先展示本确认框、等用户明确确认后，再带 `--confirmed-by dialog --evidence <用户确认原文>` 执行；不得伪造 evidence。**

## 产出物

- `tasks.md` — 实现任务清单
- `slices.md` — 切片执行计划
- 实现的源代码 + 测试文件
- `pending-adjustments.yaml` / `design-adjustments.yaml`（如有偏离）
- `test-report.md` — 测试报告

## 质量检查

- [ ] 切片规划覆盖所有 spec Requirements，无循环依赖
- [ ] RED：测试先失败
- [ ] GREEN：最小实现通过测试
- [ ] REFACTOR：重构后测试保持绿色
- [ ] 已有测试无回归
- [ ] 每个切片都有 per-slice 验证证据（tc_coverage/new_tests/verified_at）
- [ ] test-report.md 已生成

## 下一阶段

Phase 3 完成（Gate 3 确认）→ 进入 Phase 4: DELIVER（交付）
