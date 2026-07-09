# Benchmark Sources

This directory contains a small 10-design-per-suite sample for the local
reproduction. The goal is to keep the first run manageable while matching the
benchmark families discussed in the paper.

## Suites

- `epfl/`: EPFL combinational BLIF files already present in the repository.
- `iscas85/`: 10 ISCAS'85 BLIF designs copied from VTR's
  `vtr_flow/benchmarks/blif/2/` collection.
- `iscas89/`: 10 ISCAS'89 BLIF designs copied from VTR's
  `vtr_flow/benchmarks/blif/2/` collection.
- `iscas99/`: first 10 ITC'99 RTL Verilog designs (`b01` to `b10`) from
  `ccsl-uaegean/ITC99-RTL-Verilog`. This folder preserves the earlier paper
  planning label; for strict naming, treat it as an ITC'99/ISCAS99-style slice.
- `itc99/`: next 10 ITC'99 RTL Verilog designs (`b11` to `b21`, skipping
  unavailable `b16`) from `ccsl-uaegean/ITC99-RTL-Verilog`.
- `vtr/`: 10 VTR Verilog designs selected from VTR's FPU hardlogic and Verilog
  benchmark folders.
- `arithmetic/`: 10 arithmetic Verilog designs selected from VTR arithmetic
  multipliers and generated adder-tree circuits.

## Named Evaluation Sets

- `epfl_10`: `epfl/` only, for fast smoke checks.
- `standard_30`: `epfl/`, `iscas85/`, and `iscas89/` BLIF designs.
- `large_70`: all seven local 10-design suites, including the Verilog suites.

## Current Evaluation Frontend

The active S5/F7 implementation comparison uses ABC directly (`abc_native`).
That frontend currently evaluates ABC-native files (`.blif`, `.bench`, `.aig`)
for CEC-backed promotion. Therefore `large_70` is tracked as 70 benchmark
families, but its current promotion/evaluation scope is the 30 BLIF designs in
`standard_30`; the 40 Verilog designs are recorded in
`unsupported_benchmark_scope` until a Verilog-to-BLIF/AIG frontend is wired.

## Temporary Download Sources

The source repositories were cloned only into `/private/tmp` during setup:

- `https://github.com/verilog-to-routing/vtr-verilog-to-routing.git`
- `https://github.com/ccsl-uaegean/ITC99-RTL-Verilog.git`

Only the selected benchmark files were copied into this repository.
