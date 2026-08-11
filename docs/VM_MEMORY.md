# XVM banked memory

## Logical contract

The default XVM exposes `2,000,000` unsigned 32-bit data registers at logical
addresses `0..1,999,999`. The first `1,000,000` addresses are the working set and
the remaining `1,000,000` addresses are the standby tier. Text and static data keep
their existing 16-bit `65,536`-word limits; this change expands runtime data and the
heap, not XBN instruction or static-resource encodings.

The complete default layout is fixed:

| Bank | Logical addresses | Words | Tier | Future Scratch list |
| ---: | --- | ---: | --- | --- |
| 0 | `0..199,999` | 200,000 | working | `MEM_DATA_0` |
| 1 | `200,000..399,999` | 200,000 | working | `MEM_DATA_1` |
| 2 | `400,000..599,999` | 200,000 | working | `MEM_DATA_2` |
| 3 | `600,000..799,999` | 200,000 | working | `MEM_DATA_3` |
| 4 | `800,000..999,999` | 200,000 | working | `MEM_DATA_4` |
| 5 | `1,000,000..1,199,999` | 200,000 | standby | `MEM_DATA_5` |
| 6 | `1,200,000..1,399,999` | 200,000 | standby | `MEM_DATA_6` |
| 7 | `1,400,000..1,599,999` | 200,000 | standby | `MEM_DATA_7` |
| 8 | `1,600,000..1,799,999` | 200,000 | standby | `MEM_DATA_8` |
| 9 | `1,800,000..1,999,999` | 200,000 | standby | `MEM_DATA_9` |

For a logical address `a`:

```text
bank = floor(a / 200000)
offset = a mod 200000
Scratch list item = offset + 1
```

Scratch lists are one-based, hence the final `+ 1`. Every logical XVM word maps to
exactly one Scratch list item; no address consumes a metadata item or a pair of list
items. The Scratch port must create all ten independent lists and route reads,
writes, copies, fills, and garbage-collector scans through this mapping. It must not
flatten them into a list larger than Scratch's 200,000-item limit.

## Working and standby allocation

At the beginning of an execution, the heap allocator exposes only
`[heap_start, 1,000,000)`. It uses the existing deterministic first-fit free list.
If no working-set block can satisfy a request, it performs garbage collection once
and retries. Only when that retry still cannot fit the request does it activate
`[1,000,000, 2,000,000)` and retry against the combined free list. Adjacent free
space across the tier boundary is merged, so a single contiguous request can span
working and standby memory.

Once activated, standby remains available for the rest of that execution. Starting
a new execution resets the allocator to working-only. Direct pointer reads and
writes may address the complete logical range at all times; standby activation is
an allocator policy, not an address-permission boundary.

Python embedders can still request `65,536..2,000,000` logical words for compatibility
tests. A shorter host memory uses the minimum number of banks, with a shorter final
bank if necessary. The full Scratch VM uses the ten-bank default.

## Host storage and performance

`BankedMemory` preserves the integer indexing, negative indexing, iteration, and
fixed-length slice read/write behavior used by the VM and its devices. A logical
read or write is constant time; a slice is linear in its word count and works across
bank boundaries.

The host stores a materialized bank in an unsigned 32-bit array, exactly 800,000
payload bytes for a full 200,000-word bank and 8,000,000 payload bytes if all ten
banks are touched. Untouched zero banks have no word payload allocated. Zero-only
writes do not materialize a bank. Garbage collection scans only nonzero values and
skips untouched banks, so reserving the full logical range does not create a
two-million-object Python list or make idle collections walk two million zeros.

Direct `LOAD` and `STORE` operands remain 16-bit for XAssembly compatibility. Heap
addresses above `65,535` are carried as 32-bit values and accessed with indirect
loads/stores, memory-copy operations, and syscalls.
