# Xenon design system

Xenon's window appearance is data-driven. The canonical source is
`xe_lang/design_tokens.py`, organized in three layers:

1. Primitive tokens contain the six indexed palettes and logical-pixel measures.
2. Semantic tokens assign roles such as window border, title, content, text, and
   controls to palette indices.
3. Component tokens collect the complete window-chrome contract used by the
   renderer, Xe standard library, Settings preview, and Scratch builder.

Changing a palette color, role, or window measure in that file updates every native
consumer without editing renderer geometry. Palette entries remain sixteen-color
indexed values; component colors are indices, not embedded RGB values.

## Xe applications

The compiler exposes the shared component values as `graphics::WINDOW_*`
constants. Use those constants for window-like previews and controls instead of
copying title heights, border widths, colors, control sizes, gaps, or rounded
insets. Only Square and Rounded corner styles are supported.

Applications keep their own layout, timing, and asset policy near their
`APP_TITLE`, `APP_DEFAULT_WIDTH`, and `APP_DEFAULT_HEIGHT` header. Replaceable image
and animation declarations belong to the app's entry in `apps/manifest.json` and
its namespace under `apps/assets`; see `apps/README.md` and
`apps/assets/README.md`.

## Settings preview

Settings stages edits locally. `os::preview_preferences` applies background,
palette, theme, and corner values to the effective renderer without writing the
settings file. Apply commits atomically; Cancel, validation failure, and window
close restore the committed appearance. This makes the nested preview use the same
palette roles and geometry as a real window instead of maintaining a second mock
theme.

Window transparency is removed. Its legacy ABI fields remain as opaque no-ops so
older bytecode and settings files load safely.

## Scratch projects

The full-ABI builder copies the same source tokens into the Stage lists
`XE_DESIGN_TOKEN_NAMES` and `XE_DESIGN_TOKEN_VALUES`. Mutable appearance state is
kept in the aligned `XE_DESIGN_STATE_NAMES` and `XE_DESIGN_STATE_VALUES` lists.
Generated window and graphics procedures resolve their colors and measures through
those tables, so a Scratch-side redesign is a list edit rather than a block-graph
rewrite.

Do not reorder one aligned list without the other. Keep palette rows at sixteen
colors, retain the logical-pixel units, and rebuild both deterministic Scratch
artifacts after changing a token.

## Verification checklist

- Render Square and Rounded windows in all six palettes.
- Compare the Settings nested preview with a real runtime window.
- Test normal, narrow, maximized, and restored geometry.
- Rebuild every app artifact and the Scratch capability manifest.
- Rebuild each Scratch project twice and compare bytes and hashes.
- Run the complete strict regression suite before publishing.
