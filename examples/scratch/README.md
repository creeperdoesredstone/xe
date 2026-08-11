# Xenon File Explorer native Scratch test

Download [`xenon_file_explorer_native.sb3`](xenon_file_explorer_native.sb3), choose **File → Load from your computer** at [scratch.mit.edu](https://scratch.mit.edu/projects/editor/), then click the green flag.

The reference project uses only vanilla Scratch 3 blocks and SVG costumes. No browser extension, TurboWarp-only block, remote service, or host filesystem is required.

## Controls

- Hover over an orbiting file or folder to slow it and show its name.
- Click an item to select it.
- Double-click a folder to open it.
- Click **Back** to return to its parent.
- Click **New item**, enter a name, then enter `F` or `D` to create a file or folder.
- Click **Trash** and type `yes` to delete the selected file or empty folder.
- Click the nucleus to show the current virtual path.

The VFS is stored entirely in the project lists `VFS_IDS`, `VFS_NAMES`, `VFS_TYPES`, `VFS_PARENTS`, and `VFS_PATHS`. Saving the Scratch project after an edit preserves those list values in that project. It never reads, writes, renames, or deletes files on the computer.

## Boundaries

This is a direct Scratch reference port for testing the atom explorer interaction. It is not an exact Xe-bytecode conversion and currently omits multi-select, drag-and-drop shell assignment, file contents, recursive folder deletion, host clipboard access, real window management, and the optimized orbit command-stream syscall. For the compiled Xe application, use [`Xenon-131-VM-Full-ABI-File-Explorer.sb3`](Xenon-131-VM-Full-ABI-File-Explorer.sb3); the normal Xe-to-SB3 converter also exports `apps/file_explorer.xe` against that same pinned full-ABI profile. The compiled VM variant loads and runs locally but its two-million-cell `project.json` exceeds the Scratch website's save/share limit.

The project includes the frozen `MEM_DATA_0` through `MEM_DATA_9` banks for later VM work. Each bank physically contains exactly 200,000 zero-initialized list items, for 2,000,000 addressable words total, although the native explorer itself uses only a few low addresses. Scratch therefore needs noticeably more memory and startup time than the roughly 19 KB compressed download size suggests.
