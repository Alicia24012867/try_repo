#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# ----------------------------------------------------------------------------
# 模型配置 — 从 .env 加载
# ----------------------------------------------------------------------------
set -a
source .env
set +a

# ----------------------------------------------------------------------------
# 启动多轮自主迭代闭环
# ----------------------------------------------------------------------------
# 注意：完整 ABC 编译、CEC、QoR 对比应在远程 Linux/ABC host 上运行。
# 本地 macOS 仅用于轻量 Python 校验和代码编辑。
# cycle_001 assignment 由 init_cycle.py 一次性创建后 git 追踪，
# 无需每次启动都重新初始化。后续循环由 next_cycle.py 自动生成。
python3 -B -m scripts.agents.self_evolved_abc.flow.cycle_loop \
  --auto-resume \
  --build-candidate-binary \
  --build-jobs 8 \
  --max-cycles 5
#
# 参数说明：
#   --auto-resume              自动从最后一个已完成 cycle 的下一个未跑
#                              cycle 开始（不覆盖已有数据）。
#                              首次启动时等价于从 cycle_001 开始。
#                              如需显式指定起始点，改用 --assignment。
#                              （去掉 --auto-resume 并加回 --assignment）
#
#   --build-candidate-binary   在 S4 阶段编译 candidate ABC 二进制。
#                              去掉此标志则跳过编译，仅做 Python smoke 检查。
#                              （需要在远程 Linux/ABC 环境下启用）
#
#   --build-jobs 8             make 并行编译的 job 数量
#
#   --max-cycles 5             最大自动迭代轮数（含起始轮）
#                              每轮 = 模型调用 → patch apply → 编译 →
#                                     CEC 验证 → QoR 对比 → review →
#                                     下一轮 assignment 生成
#
#   终止条件：
#     - 达到 --max-cycles 上限
#     - 连续 3 轮 review decision 完全相同（stuck 检测）
#     - 模型连续返回 NEEDS_HUMAN_REVIEW（validation 持续失败）
#     - 下一轮 assignment 不存在
#
#   其他可用参数（传给 cycle_loop）：
#     --timeout-seconds 300    每个 benchmark 的 ABC 运行超时（秒）
#     --build-timeout-seconds 900  candidate ABC 编译超时（秒）
#     --repo-root .            项目根目录（默认当前目录）
