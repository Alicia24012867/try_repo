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
和 branch run manifest。`--planner-mode auto` 在配置模型时每个新 cycle 调用一次模型，
无模型配置时使用字节稳定的 deterministic fallback；resume 已存在的合法 plan 不重复调用。

## 唯一公开入口

公开的 campaign 接口只有 `bash run.sh` 及其直接调用的
`python3 -B -m scripts.agents.self_evolved_abc.workflow.dual_agent_loop`。
`workflow/candidate_pipeline.py` 和 `cycle_driver.py` 是 coordinator 调用的单分支内部
构件；`scripts/init_cycle.py`、`flow/cycle_loop.py`、`flow/iteration_loop.py`、
`flow/next_cycle.py` 属于 internal/legacy 工具，只保留给聚焦测试或诊断，不能用来创建
或推进双 Agent campaign。

主循环只接受 `candidate_scoped_v2` assignment。旧的 cycle 级共享
`experiments/<cycle>/impl_compare/` 布局不是兼容输入；应重新冻结 Planning dispatch，
而不是把旧 assignment 自动迁移或回填到新循环。

## 关键不变量

- 每轮恰好一份 `flow_candidate_001` 和一份 `logic_candidate_001`。
- 两个 assignment 共享完全相同的 `baseline_ref` 和
  `evaluation_contract_hash`，且在任一分支启动前已经写入磁盘并冻结。
- Flow 默认只编辑 `third_party/FlowTune/src/src/opt`；Logic 默认只编辑
  `third_party/FlowTune/src/src/base/abci`。
- 两个角色使用同一 promotion flow、benchmark scope、阈值与 timeout；角色专属命令
  只保存在 `diagnostic_flow_commands`。
- 任一分支异常只会把该分支标为 `failed`，不会取消另一个 future。汇总必须等待全部
  future settled。
- Coding Agent 未产出合法候选（包括模型响应连续校验失败）时，流水线会写入
  `REPAIR_VALIDATION` 负评审；它不具备晋级资格，但属于已结算结果，因此仍可与另一
  分支 fan-in 并把失败证据交给下一轮 Planning。
- 晋级要求两个分支都产出有效 review（严格 quorum）。pipeline 非零、缺失 benchmark、
  CEC/正确性行数不等于冻结 scope，或旧 review lineage 不匹配时，该候选不得晋级。
- `REPAIR_QOR`、`REPAIR_VALIDATION` 等有效负评审的命令返回码可以非零；非零码本身
  不等于分支缺失。只有没有有效且身份匹配的 review 时，round 才是 `incomplete`。
- 单分支 `champion_update` 仅表示“具备晋升资格”。真正 champion 只由
  `planning/portfolio_review.json` 决定，且与完成顺序无关。
- 不会隐式合并 Flow/Logic patch。组合方案必须作为第三个候选重新执行 build、CEC 和
  QoR。
- 下一轮 assignment 只有在集中评审文件持久化后才会生成；两个角色均指向集中选出的
  同一个 champion baseline。

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
  candidates/
    flow_candidate_001/impl_compare/...
    logic_candidate_001/impl_compare/...
```

## 运行

完整 Linux/ABC 运行：

```bash
python3 -B scripts/bootstrap_agent_context.py
bash run.sh
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
PYTHONPATH=. python3 -B scripts/test_python38_compat.py
```

`--max-workers 2` 是默认值，表示两个 coding agent 同时运行；`--max-workers 1` 仅用于
串行诊断。只有 review 与 coordinator 生成的 branch manifest、assignment hash、合同 hash
及 baseline lineage 全部一致时才能 resume；裸 review 或被修改的 review 会重跑对应分支。
终端会逐分支打印 `review_valid`、decision、reason、review 与 log 路径。若 round 仍为
`incomplete`，先查看 `planning/portfolio_review.md` 和对应的
`planning/branch_logs/<candidate>.log`；修复后直接重跑 `bash run.sh`，合法的另一分支会
从 manifest 恢复，仅缺失分支重新执行。
