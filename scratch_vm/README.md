# Scratch VM compatibility

`xenon131-vm.sb3` is a pinned, vanilla-Scratch-compatible Xenon-131 template. It is intentionally described by `profile.json` as `legacy-core`; the profile is a compatibility gate, not a claim that the template implements the current Xe application ABI.

The checked `capability-manifest.json` is generated from all current `apps/*.xe`, the current `SyscallID` enum, the profile, and the template itself:

```powershell
python -m scratch_vm.audit --check
python -m scratch_vm.audit --write
```

The audit derives supported syscall IDs from the template's `sys_dispatch` block, verifies the pinned SHA-256, safe and unique ZIP members, the unique Stage `MEM_PROGRAM`/`MEM_DATA` lists, and the actual `MEM_DATA` initializer. Every application is recorded as either `exact` or `blocked`, with named blockers and syscall IDs. Export must never remove, replace, or approximate an unsupported operation silently.

## Native File Explorer reference project

[`examples/scratch/xenon_file_explorer_native.sb3`](../examples/scratch/xenon_file_explorer_native.sb3) is a directly implemented, vanilla-Scratch reference port of the File Explorer's essential atom UX. It genuinely runs in Scratch 3 and includes orbiting file/folder clones, hover labels, double-click folder navigation, Back, New item, Trash, and a project-local list-backed VFS.

This artifact is deliberately identified as `native-scratch-reference` in `project.json`. It is not a renamed XBN and does not claim to execute `apps/file_explorer.xe`. The exact Xe exporter continues to block that application because the bundled VM does not implement its 49 application ABI syscalls.

The project also freezes the eventual Scratch memory layout:

- ten `MEM_DATA_0` through `MEM_DATA_9` lists;
- exactly 200,000 zero-initialized entries per list and exactly one logical 32-bit word per list item;
- `bank = floor(address / 200000)` and Scratch `slot = (address mod 200000) + 1`;
- banks 0-4 form the 1,000,000-word working tier;
- banks 5-9 form the 1,000,000-word standby tier and become active when allocation crosses the working tier.

The deterministic builder materializes all 2,000,000 physical list items before packaging, so high-offset access never runs a padding loop. `memory write` floors the input and stores it modulo 2^32, including negative values. `memory allocate` rejects zero, negative, non-finite, and fractional sizes without moving its cursor. The custom `memory map`, `memory read`, `memory write`, and `memory allocate` blocks enforce the mapping. The native explorer uses only a few low words; the bundled Xe bytecode VM still has its separately documented legacy memory boundary.

The expanded `project.json` is about 4.1 MB and the repeated zeroes compress the checked `.sb3` to about 19 KB. Scratch must still create two million runtime list entries when loading it, so startup and memory use are intentionally much higher than the earlier sparse reference project. This cost is required by the one-list-item-per-word contract.

Build or verify the downloadable artifact deterministically:

```powershell
python -B -m scratch_vm.build_file_explorer_port --overwrite
python -B -m scratch_vm.build_file_explorer_port --check
```

The editable ScratchASM source is `file_explorer_native.sasm`; the generated vanilla project snapshot and hashed SVG costumes live in `file_explorer_native_project/`. See the example README for usage and known boundaries.

## Current boundary

- The template dispatches 48 legacy-core syscalls. Its dispatch table matches `profile.json` exactly.
- The template initializes `MEM_DATA` to 65,536 words. The current Xe/XVM address contract is 200,000 words.
- Current apps require application graphics, windowing, OS preferences, filesystem, compiler, currency, image, and/or audio calls which this template does not implement.
- Portable image and music files require a deterministic asset ROM or equivalent Scratch-list representation. Dynamic asset paths cannot be exported exactly.

Consequently the bundled profile blocks exact SB3 export for current Xe builds. Only
when the user explicitly enables fallback does the converter write an XBN plus a
compatibility report; that pair is not a Scratch project. Editing only `profile.json`
must never be used to bypass this boundary.

## Path to full vanilla Scratch parity

1. Expand `MEM_DATA`, allocation bounds, indirect reads/writes, stack interaction, and all address validation to the same 200,000-address contract as XVM.
2. Implement every required `SyscallID` in Scratch with the same argument order, return values, signed/float conversions, errors, and observable side effects as the Python VM. This includes application graphics/input/window APIs, VFS, compiler, currency, XIMG2, and XMusic calls.
3. Embed literal portable assets deterministically and implement XIMG2/XMusic decoding in Scratch. Reject dynamic paths unless a deterministic workspace asset table resolves them before export.
4. Implement the in-VM compiler APIs used by the virtual IDE; a host-only compiler shortcut is not full Scratch compatibility.
5. Add differential fixtures that run each syscall and representative apps in both XVM and the Scratch template, then compare memory, output, framebuffer, filesystem, audio timeline, and errors.
6. Keep long operations cooperative and delta-time aware so vanilla Scratch remains responsive, without TurboWarp-only blocks or extensions.
7. Only after those tests pass, replace the template, update its pinned hash/profile, regenerate the manifest, and enable exact `.sb3` export for newly compatible apps.

Deterministic packaging remains: canonical `project.json`, sorted ZIP members, fixed timestamps, pinned input template, and atomic output replacement.
