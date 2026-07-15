# LSOracle profile

LSOracle is most useful as an orchestration reference: it composes mockturtle
rewriting, refactoring, resubstitution, balancing, and learned/adaptive choices.

## High-value code index

- `aig_script.hpp` through `aig_script5.hpp`: concrete pass sequences and
  parameter variations.
- `parser.hpp`: mapping textual optimization operations to typed pass calls.
- `mab.hpp`: adaptive/multi-armed-bandit selection precedent.
- `core/commands/optimization/`: command exposure and experiment-facing options.

## Transferable patterns

Use a small vocabulary of known-safe passes; measure after each pass or bounded
sequence; compare area and depth rather than collapsing QoR into one opaque
score; and keep adaptive selection outside correctness checks. A useful Logic
candidate may conditionally choose between two already-safe local sequences if
the condition is based on circuit structure and has a deterministic fallback.

## Caveats

Many scripts are research experiments and can be expensive or overfit. The
implementation depends on mockturtle/C++ and is not source-compatible with ABC.
Do not import ML/runtime dependencies or reproduce long fixed schedules in one
candidate. Test one orchestration hypothesis and retain compile→CEC→QoR order.
License: MIT.
