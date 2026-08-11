# Scratch VM compatibility

## Full-ABI Xenon-131 projects

`build_full_abi_vm.py` upgrades the pinned `Xenon-131-VM-1ec2a237.sb3`
attachment without changing the source archive. It writes two deterministic
projects:

- `examples/scratch/Xenon-131-VM-Full-ABI.sb3` contains the upgraded VM;
- `examples/scratch/Xenon-131-VM-Full-ABI-File-Explorer.sb3` embeds and launches
  the current compiled `apps/file_explorer.xe` program.

Both `Xenon Graphics Engine` and the OS `Graphics Engine` are merged into
`Xenon-131 VM` with remapped block, variable, list, procedure, comment, monitor,
and costume references. The generated projects contain exactly `Stage` and
`Xenon-131 VM`; their 257 engine costumes retain rewritten numeric selectors and
the original VM costume follows them.

The generated dispatcher covers every current `SyscallID`, including 26-29,
54-58, and the complete application range. Project metadata records argument
counts, result kinds, and the backend class for every added operation. Scratch-
local graphics, window, preferences, VFS, currency, string, and input state are
kept inside the project. Clipboard access fails closed. Host compilation and
portable media paths report explicit unsupported backends until a deterministic
project ROM is implemented; they are not silently presented as host-exact services.

Memory is ten physical 200,000-item lists. Every legacy `MEM_DATA` read and write
is lowered to target-local warp helpers using a type-preserving cache, so numeric
words, text cells, and empty cells do not coerce into one another. Logical
addresses map as `bank = floor(address / 200000)` and Scratch slot
`(address mod 200000) + 1`, for a total range of 0 through 1,999,999.

Build, verify, and optionally copy both downloads:

```powershell
python -B -m scratch_vm.build_full_abi_vm
python -B -m scratch_vm.build_full_abi_vm --check
python -B -m scratch_vm.build_full_abi_vm --copy-downloads
```

The structural tests prove exact dispatcher coverage, merge and costume invariants,
physical bank sizes, boundary routing, archive assets, and exact compiled Explorer
program injection. Release QA can additionally pass both archives through OpenCTS
and a Scratch project parser without making either tool a build dependency.

`full-abi-profile.json` is the converter's default compatibility profile. It is
generated beside the projects, pins the VM-only archive hash, declares all ten
memory lists, and enables only the syscall implementations that are portable in
vanilla Scratch. Host compilation and portable image/audio assets stay blocked until
a deterministic project ROM exists. This prevents a profile from claiming a backend merely because a dispatch
branch exists.

Both full projects load and execute through Scratch's **Load from your computer**
flow, but their uncompressed `project.json` files are about 11.5 MB and 11.6 MB.
That exceeds the Scratch website's current roughly 5 MiB project-JSON save/share
limit. They are therefore local-load projects even though they use only standard
Scratch blocks. The two million physical list cells are an explicit VM requirement;
silently removing them to fit the service limit would break the memory contract.

`xenon131-vm.sb3` is a pinned, vanilla-Scratch-compatible Xenon-131 template. It is intentionally described by `profile.json` as `legacy-core`; the profile is a compatibility gate, not a claim that the template implements the current Xe application ABI.

The checked `capability-manifest.json` is generated from all current `apps/*.xe`, the current `SyscallID` enum, the profile, and the template itself:

```powershell
python -m scratch_vm.audit --check
python -m scratch_vm.audit --write
```

The audit derives supported syscall IDs from the template's `sys_dispatch` block, verifies the pinned SHA-256, safe and unique ZIP members, the unique Stage `MEM_PROGRAM`/`MEM_DATA` lists, and the actual `MEM_DATA` initializer. Every application is recorded as either `exact` or `blocked`, with named blockers and syscall IDs. Export must never remove, replace, or approximate an unsupported operation silently.

## Native File Explorer reference project

[`examples/scratch/xenon_file_explorer_native.sb3`](../examples/scratch/xenon_file_explorer_native.sb3) is a directly implemented, vanilla-Scratch reference port of the File Explorer's essential atom UX. It genuinely runs in Scratch 3 and includes orbiting file/folder clones, hover labels, double-click folder navigation, Back, New item, Trash, and a project-local list-backed VFS.

This artifact is deliberately identified as `native-scratch-reference` in
`project.json`. It remains a small independently authored comparison project. The
separate `Xenon-131-VM-Full-ABI-File-Explorer.sb3` artifact executes the compiled Xe
application through the full VM and portable syscall backends.

The project also freezes the eventual Scratch memory layout:

- ten `MEM_DATA_0` through `MEM_DATA_9` lists;
- exactly 200,000 zero-initialized entries per list and exactly one logical 32-bit word per list item;
- `bank = floor(address / 200000)` and Scratch `slot = (address mod 200000) + 1`;
- banks 0-4 form the 1,000,000-word working tier;
- banks 5-9 form the 1,000,000-word standby tier and become active when allocation crosses the working tier.

The deterministic builder materializes all 2,000,000 physical list items before packaging, so high-offset access never runs a padding loop. `memory write` floors the input and stores it modulo 2^32, including negative values. `memory allocate` rejects zero, negative, non-finite, and fractional sizes without moving its cursor. The custom `memory map`, `memory read`, `memory write`, and `memory allocate` blocks enforce the mapping. The full Xe bytecode VM uses the same ten-bank mapping; the separate legacy audit template retains its historical boundary.

The expanded `project.json` is about 4.1 MB and the repeated zeroes compress the checked `.sb3` to about 21 KB. Scratch must still create two million runtime list entries when loading it, so startup and memory use are intentionally much higher than the earlier sparse reference project. This cost is required by the one-list-item-per-word contract.

Build or verify the downloadable artifact deterministically:

```powershell
python -B -m scratch_vm.build_file_explorer_port --overwrite
python -B -m scratch_vm.build_file_explorer_port --check
```

The editable ScratchASM source is `file_explorer_native.sasm`; the generated vanilla project snapshot and hashed SVG costumes live in `file_explorer_native_project/`. See the example README for usage and known boundaries.

## Compatibility boundaries

- The full template dispatches every current `SyscallID`; the generated converter
  profile enables the portable subset rather than treating dispatch coverage as
  proof of semantic support.
- Its logical data space is exactly 2,000,000 words in ten physical lists.
- Project-local graphics, input, windows, settings, VFS, strings, and the drawing
  services exercised by File Explorer are available to compatible programs. Command
  stream `276` and native right-click `248` remain unavailable in vanilla Scratch;
  only the pinned File Explorer artifact receives a hash-bound compatibility allowance
  because its tested Xe code uses primitive drawing and a 500 ms left-hold fallback.
- Clipboard access fails closed. Host compilation and portable image or music assets
  are rejected by compatibility analysis until a deterministic project ROM exists.
- The legacy template still dispatches only its historical core set and initializes
  one 65,536-word list. `profile.json`, `xenon131-vm.sb3`, and
  `capability-manifest.json` preserve that boundary for regression auditing.

The converter never uses a profile edit to bypass these checks. A blocked export can
write an explicitly requested XBN plus compatibility report, but that pair is not a
Scratch project.

## Path to full vanilla Scratch parity

1. Embed literal portable assets deterministically and add complete XIMG2/XMusic
   project-ROM decoding; continue rejecting unresolved dynamic paths.
2. Port the in-VM compiler services used by the virtual IDE, without delegating to a
   host compiler.
3. Expand differential fixtures across every portable syscall and representative app,
   comparing memory, output, framebuffer, VFS, audio timeline, and errors.
4. Keep long operations cooperative and delta-time aware in vanilla Scratch, without
   TurboWarp-only blocks or extensions.
5. Add a capability to the generated converter profile only after its Scratch backend
   passes those semantic fixtures.

Deterministic packaging remains: canonical `project.json`, sorted ZIP members, fixed timestamps, pinned input template, and atomic output replacement.
