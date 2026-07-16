# Planning / Flow / Logic 双 Agent 循环

当前主工作流是一个严格的 fan-out / fan-in 循环：Planning Agent 在同一份基线
快照上同时派发 Flow Agent 与 Logic Minimization Agent；两条候选流水线彼此隔离，
全部结束后由 Planning 侧集中评审，最多选择一个 champion，然后才生成下一轮两份任务。

每次 Planning 模型调用还携带固定的 cycle-0 先验：十个精确 commit 的仓库画像和
查询相关源码窗口。Planning 使用全部十个仓库；Logic 使用九个外部参考；Flow 使用
六个与调度、命令、指标及验证相关的参考。仓库文本只读且不可信，不能修改派发包络、
角色源码边界或 compile→CEC→QoR 门禁。

```text
                         ┌─ Flow Agent  ─ build ─ CEC ─ QoR ─ review ─┐
Planning frozen dispatch ┤                                            ├─ fan-in ─ next Planning round
                         └─ Logic Agent ─ build ─ CEC ─ QoR ─ review ─┘
```

## 三轮重构结果

1. 角色边界：`roles/registry.py` 统一保存 agent 名称、论文角色、实现类和 assignment
   normalizer；未知角色及非 coding role 均 fail closed。
2. 执行边界：通用候选执行移动到 `workflow/`。新 assignment 使用
   `candidate_scoped_v2`，实现产物位于
   `experiments/<cycle>/candidates/<candidate_id>/impl_compare/`，不再共享 workspace、
   build manifest、CEC/QoR CSV 或 review。
3. 编排边界：`planning/portfolio.py` 一次创建 Flow/Logic 两份任务；
   `workflow/dual_agent_loop.py` 并发运行且采用 all-settled 语义；
   `workflow/portfolio_review.py` 统一选取 champion 并驱动下一轮。

Planning 模型不直接生成 assignment。代码先锁定 baseline、benchmark、评测 flow、
候选 ID 与写域；这些字段只作为只读推理上下文，不属于模型响应。模型只返回两个
分支的 hypothesis/task、验收/回滚条件与风险说明。有效建议写入
`planning/planner_advice.json`，其 hash 同时绑定到 portfolio plan、两个 assignment
和 branch run manifest。`--planner-mode auto` 在配置模型时，常规派发阶段每个新 cycle
调用一次模型，
无模型配置时使用字节稳定的 deterministic fallback；执行 planner batch 的 cycle 会在
测量完成后额外调用一次 Planning refresh，使 Flow/Logic 同时消费新证据。已提交的合法
plan 或 refresh 在 resume 时都不会重复调用模型。

## 唯一公开入口

公开的 campaign 接口只有 `bash run.sh` 及其直接调用的
`python3 -B -m scripts.agents.self_evolved_abc.workflow.dual_agent_loop`。
`workflow/candidate_pipeline.py` 和 `cycle_driver.py` 是 coordinator 调用的单分支内部
构件；`scripts/init_cycle.py`、`flow/cycle_loop.py`、`flow/iteration_loop.py`、
`flow/next_cycle.py` 属于 internal/legacy 工具，只保留给聚焦测试或诊断，不能用来创建
或推进双 Agent campaign。

论文没有规定“最多五轮”或全局收敛停止条件。第 3.3 节要求 cycle ≥ 1 的 Planning
读取上一轮反馈并规划下一步，第 3.4 节也要求 CEC 拒绝成为下一 Planning cycle 的纠正
反馈；第 4.2 节则报告每个完整 evaluation cycle 约需 2–3 小时和 60–80 美元。
因此公开成本接口是 `--new-cycle-budget N`：它限制本次调用最多推进 N 个尚未完成的
evaluation cycle，而不是从 `cycle_001` 重新计数历史。启动时 coordinator 只沿
assignment/review/manifest hash 均有效且达到 quorum 的 lineage 快进；历史 cycle 不消耗
预算。纯预算耗尽时，最后一轮 fan-in 后仍会创建下一轮 frozen Planning dispatch，使刚
产生的反馈被 Planning 消费，但不执行该预备轮。另一个独立参数
`--target-cycle T` 是绝对终点：第 T 轮 fan-in 持久化后立即停止，不生成无用的 T+1
dispatch。`run.sh` 的预算与绝对终点均默认为 10，所以已有 cycle 1–5 的有效 lineage
会只执行 cycle 6–10。分别通过 `EDA_AGENT_NEW_CYCLE_BUDGET` 与
`EDA_AGENT_TARGET_CYCLE` 调整。

论文第 3.2 节的“连续超过十次编译或等价检查失败后触发人工介入”属于 coding
self-debugging 的安全阈值，不是上述 campaign evaluation-cycle 成本预算，两者不能合并
解释。

主循环只接受 `candidate_scoped_v2` assignment。旧的 cycle 级共享
`experiments/<cycle>/impl_compare/` 布局不是兼容输入；应重新冻结 Planning dispatch，
而不是把旧 assignment 自动迁移或回填到新循环。

## 关键不变量

- 每轮恰好一份 `flow_candidate_001` 和一份 `logic_candidate_001`。
- 两个 assignment 共享完全相同的 `baseline_ref` 和
  `evaluation_contract_hash`。若 Flow 的 `should_skip_llm` 为真，coordinator 会先执行
  model-free batch，再用 measured evidence 刷新两条 Planning advice；早期 batch 按
  planner command 过滤，连续四轮 correctness-backed QoR miss 后的 structural phase
  每轮运行至多 12 个、覆盖全部命令族的 rotating `flow_wide` stage，并在 cycle 6–10
  轮转覆盖完整 opt-only probe space；
  刷新仍不得改变共享 baseline/contract，完成后才冻结并启动任一 Coding 分支。
  自动 batch 使用带 lineage hash 后缀的 generation 目录；lineage 同时绑定父 plan/review、
  baseline、contract、planner advice 以及实际 source/patch variant space。缺少匹配
  manifest/probe/patch 的裸 `winner.json` 一律拒绝。刷新 advice、Flow assignment、Logic
  assignment 和 portfolio plan 时使用可 roll-forward 的四文件 journal；进程在任一
  replace 点中断后，下一次 load 会先完成同一 generation，且不会再次调用 Planning provider。
- Flow 默认只编辑 `third_party/FlowTune/src/src/opt`；Logic 默认只编辑
  `third_party/FlowTune/src/src/base/abci`。
- 两个角色使用同一 promotion flow、benchmark scope、阈值与 timeout；角色专属命令
  只保存在 `diagnostic_flow_commands`。
- 任一分支异常只会把该分支标为 `failed`，不会取消另一个 future。汇总必须等待全部
  future settled。
- Coding Agent 只有在结构化 decision 为 `PROPOSE_CANDIDATE` 时才进入 patch、build、
  CEC、QoR。合法 `DEFER` / `NEEDS_PLANNER_APPROVAL` 的 review decision 分别为
  `DEFERRED_BY_AGENT` / `NEEDS_PLANNER_APPROVAL`，build status 分别为
  `agent_deferred` / `agent_needs_planner_approval`，不会被误送入 source patch runner。
  本地字段、路径或 diff 连续校验失败写成 `agent_response_validation_failed`，属于可交给
  下一轮 Planning 的已结算负证据。每次失败另存 `attempt_XX.feedback.md` 和 SHA-256；
  终态 exact issue 还会写进 hash-bound review，并以 Flow/Logic 隔离的专用 prompt 区块
  进入下一轮 Planning 与同角色 Coding，而不是依赖会被截断的通用 evidence 文本。
- 严格 patch apply-check 或 C/C++ 编译失败不会立即浪费整个分支：coordinator 在同一
  candidate 的隔离 workspace 中预检/编译，把完整 build log 快照写入
  `agents/attempts/<candidate>/attempt_XX.compile.log`，并把严格 apply diagnostics 或有界
  compiler tail 交给下一次模型尝试修复。
  仅当尝试耗尽后才结算为 `REPAIR_PATCH` 或 `REPAIR_COMPILE`。
- Provider 临时、永久或配置故障，空/截断/非法 JSON，refusal/filter，以及本地 agent
  preparation 故障均有独立 `build_status`。这类结果不是论文实验负样本：两条 future
  仍会 all-settled 并写 `CODING_INFRASTRUCTURE_FAILURE` review，但 round 标为
  `infrastructure_failed`，且在下一轮 Planning 前停止。
- 晋级要求两个分支都产出有效实验 review（严格 quorum）。pipeline 非零、缺失 benchmark、
  CEC/正确性行数不等于冻结 scope，或旧 review lineage 不匹配时，该候选不得晋级。
- 晋级奖励遵循论文的“标量奖励 + 详细 QoR 向量”：标量 AND 减少是一条通道；节点/深度
  乘积的结构 Pareto 改进在有界单设计退化 guardrail 下也是一条通道。CEC、完整 benchmark
  coverage 与真实 candidate build 始终是硬门槛。只有部分互补价值的 size/depth trade-off
  标记为 `RETAIN_FOR_SYNERGY` 并进入 frontier，不会更新 baseline；组合方案必须重新 build、
  全量 CEC、QoR 后才能晋级。
- recovery 使用 Flow correctness-backed QoR streak 与 portfolio-level 连续无 winner
  streak 的最大值；因此即使另一分支完成了有效评审，单分支 repair 也不会把整个组合的
  停滞历史清零。有效 streak 达到三个后启用增量积累阈值，达到四个后进入 structural
  recovery。Flow 运行有界且跨命令族轮转的 batch；Logic 的
  coordinator-owned target 独立轮转 rewrite/resub/refactor/orchestrate，并避开同轮 Flow
  family。该变化复现论文从保守局部编辑逐步转向结构探索的方向，而不是简单增加循环次数。
- `REPAIR_QOR`、`REPAIR_VALIDATION` 等有效负评审的命令返回码可以非零；非零码本身
  不等于分支缺失。Provider/model/runtime build status 会使 round 成为
  `infrastructure_failed`；缺少有效且身份匹配的 review 才是 `incomplete`。
- 单分支 `champion_update` 仅表示“具备晋升资格”。真正 champion 只由
  `planning/portfolio_review.json` 决定，且与完成顺序无关。
- 已通过 batch build/全量 CEC/QoR 的 probe 会按原 SHA-256 补丁确定性重放为当轮
  Flow candidate，再进入 Flow/Logic fan-in；不会只停留在 Planning evidence。
  绝对目标轮次不创建空的下一轮，而在 `planning/final_champion.json` 固化最终
  baseline；若始终没有 champion，主进程返回非零。
- 不会隐式合并 Flow/Logic patch。组合方案必须作为第三个候选重新执行 build、CEC 和
  QoR。
- 下一轮 assignment 只有在集中评审文件持久化后才会生成；两个角色均指向集中选出的
  同一个 champion baseline。

## 与论文结果的边界

当前工作流解决的是“先产生一个真实、CEC-backed 的 AIG 结构 winner”这一复现基础，
不能等同论文最终表格：论文有 Flow、Mapper、Logic 三个 coding role，并在每个设计上使用
八条 flow，以 ASAP7 timing/area 为主要奖励，最后组合三个子系统。当前只运行 Flow/Logic、
使用一条冻结 AIG recipe，并以 nodes/depth 为代理；Mapper、八 flow 聚合与物理 timing/area
仍是后续阶段。此外，当前 Flow 写域 `src/opt` 覆盖命令 kernel；FlowTune fork 的 MAB
调度器在 `src/base/abc/abcBayestune.cpp`，冻结 recipe 没有调用 `ftune`。因此任何当前 winner
只能声明为本阶段 champion，不能声明为完整论文复现结果。

## 目录

```text
scripts/agents/self_evolved_abc/
  roles/
    registry.py                 角色注册、lazy class dispatch、scope normalizer
  planning/
    assignment_factory.py      无 argparse 耦合的初始任务工厂
    portfolio.py               双任务冻结、评测合同、下一轮任务
    engine.py                  Flow 的证据驱动策略引擎
  workflow/
    artifacts.py               candidate-safe 路径与 ID 校验
    failure_status.py          实验负样本与 coding 基础设施故障分类
    evaluation_recipe.py       候选级 ABC recipe
    candidate_pipeline.py      内部单分支 agent→build→CEC→QoR→review
    portfolio_review.py        all-settled 汇总与确定性 champion 选择
    dual_agent_loop.py         多轮并发主入口
  coding_agents/               Flow / Logic / Mapper 的模型实现
  flow/                        ABC/FlowTune 的领域门禁与评测实现
  logic/                       Logic 角色的 scope、target 与上下文合同
```

每轮产物：

```text
experiments/cycle_NNN/
  planning/
    portfolio_plan.json        Planning 冻结的双派发
    planner_advice.json        模型/确定性语义建议及其绑定 hash
    portfolio_review.json      唯一 round/champion 决策
    portfolio_review.md        人可读汇总
    branch_runs/*.json         assignment/review/contract hash 恢复凭据
    branch_logs/*.log          每个候选独立的 stdout/stderr（本地忽略，不提交）
  agents/
    assignments/flow_candidate_001.json
    assignments/logic_candidate_001.json
    plans/...
    feedback/...
    attempts/<candidate>/
      attempt_XX.assignment.json  原 hypothesis + 当前 repair hint
      attempt_XX.status.json      typed failure/decision/retryable sidecar
      attempt_XX.feedback.md      本轮完整 validation feedback（status 记录 SHA-256）
  candidates/
    flow_candidate_001/impl_compare/...
    logic_candidate_001/impl_compare/...
```

## 运行

完整 Linux/ABC 运行：

```bash
python3 -B scripts/bootstrap_agent_context.py
bash run.sh

# 本次只推进两个新的/未完成的 evaluation cycle；已完成历史不计费：
EDA_AGENT_NEW_CYCLE_BUDGET=2 bash run.sh
```

只生成并检查 Planning 双派发，不调用模型或 ABC：

```bash
python3 -B -m scripts.agents.self_evolved_abc.workflow.dual_agent_loop \
  --cycle-id cycle_001 \
  --previous-cycle cycle_000 \
  --benchmark-suite large_70 \
  --planner-mode deterministic \
  --prepare-only
```

本地回归：

```bash
PYTHONPATH=. python3 -B scripts/test_dual_agent_loop.py
PYTHONPATH=. python3 -B scripts/test_logic_minimization_agent.py
PYTHONPATH=. python3 -B scripts/test_planning_agent.py
PYTHONPATH=. python3 -B scripts/test_coding_agent_retry.py
PYTHONPATH=. python3 -B scripts/test_frozen_baseline_source_context.py
PYTHONPATH=. python3 -B scripts/test_planning_portfolio_evidence.py
PYTHONPATH=. python3 -B scripts/test_python38_compat.py
```

`--max-workers 2` 是默认值，表示两个 coding agent 同时运行；`--max-workers 1` 仅用于
串行诊断。只有 review 与 coordinator 生成的 branch manifest、assignment hash、合同 hash
及 baseline lineage 全部一致时才能 resume；裸 review 或被修改的 review 会重跑对应分支。
旧版模糊的 `build_status=missing` 及新的 provider/model/runtime failure 不会从 manifest
恢复；修复后直接重跑会重新执行该 lane。若新 review 使下游 plan 的 parent review hash
失效，coordinator 只会自动重建尚未启动的下游 Planning dispatch，而不会继续使用陈旧
hypothesis；若下游任一 branch 已经启动，则拒绝覆盖其冻结工作并报告 parent-lineage drift。
终端会逐分支打印 `review_valid`、decision、reason、review 与 log 路径。若 round 仍为
`incomplete`，先查看 `planning/portfolio_review.md` 和对应的
`planning/branch_logs/<candidate>.log`；修复后直接重跑 `bash run.sh`，合法的另一分支会
从 manifest 恢复，仅缺失分支重新执行。
