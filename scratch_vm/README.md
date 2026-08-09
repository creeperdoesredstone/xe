# Scratch VM compatibility

`xenon131-vm.sb3` is a pinned, vanilla-Scratch-compatible Xenon-131 template. It is intentionally described by `profile.json` as `legacy-core`; the profile is a compatibility gate, not a claim that the template implements the current Xe application ABI.

The checked `capability-manifest.json` is generated from all current `apps/*.xe`, the current `SyscallID` enum, the profile, and the template itself:

```powershell
python -m scratch_vm.audit --check
python -m scratch_vm.audit --write
```

The audit derives supported syscall IDs from the template's `sys_dispatch` block, verifies the pinned SHA-256, safe and unique ZIP members, the unique Stage `MEM_PROGRAM`/`MEM_DATA` lists, and the actual `MEM_DATA` initializer. Every application is recorded as either `exact` or `blocked`, with named blockers and syscall IDs. Export must never remove, replace, or approximate an unsupported operation silently.

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
