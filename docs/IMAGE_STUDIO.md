# Image Studio and portable animation guide

Image Studio is a host-workbench tool for creating static images, icons, layered
art, and frame animation. Open it with the **Image Studio** workbench tab or launch
the IDE with:

```powershell
python ide.py --tab image-studio
```

The editor keeps full RGBA artwork while you work. Xe runtime images use the fixed
16-colour XVM palette plus transparent index `16`; the indexed preview shows the
exact portable result before export.

## Canvas controls

- Pencil (`P`), Eraser (`E`), Fill (`F`), Pick (`I`), Line (`L`), Rectangle (`R`),
  Ellipse (`O`), and Select/Move (`M`) are defined in
  `xe_lang/host_tools/image_specs.py`. Tool metadata is centralized there; implement
  a new tool's pointer behavior and preview in `ImageCanvas`.
- The outlined cursor on the canvas is the exact current brush footprint.
- Drag any dark area surrounding the canvas with the left button to pan. Middle-drag
  or Space-drag works from any point. The wheel zooms around the pointer.
- Layers are composited bottom-to-top. Visibility, name, and opacity are editable.
- The timeline contains one duration per frame. Changing **ms** updates **FPS**, and
  changing **FPS** updates the frame duration. Onion skin displays the adjacent
  frames without modifying them.
- Undo history is bounded by memory, and the complete project is limited to a
  64 MiB decoded pixel budget. An operation that would exceed it is rejected before
  allocating a new layer or frame.

## Editor and animator workflow

1. Choose **New** for a blank project or **Import** for PNG, JPG/JPEG, GIF, BMP,
   WebP, XIP, or XIMG. XIP is the editable master format and is reopened with
   **Import**. Animated GIF import requires Qt's GIF image plugin.
2. Pick a tool or use its shortcut. `Ctrl+Z` undoes and `Ctrl+Y` redoes. Brush size
   changes the painted footprint and the live canvas outline together.
3. In **Layers**, `+` adds a layer and `-` removes the selected layer. The checkbox
   controls visibility, double-clicking a layer name renames it, and **Opacity**
   changes only that layer. Layers are composited from bottom to top.
4. In the timeline, `+` adds a blank frame, **Copy** duplicates the selected frame,
   and `-` deletes it. **Play** previews the loop and **Onion skin** ghosts adjacent
   frames. A frame may last 20–60,000 ms (about 50–0.017 FPS); editing either field
   updates the other.
5. Choose **Export**, select the target format, and enter a name. Image Studio adds
   the selected suffix when needed and writes atomically, so an encoding failure
   does not replace the previous file. GIF export requires Pillow; if Pillow is not
   installed, export fails safely without writing a partial file.

## Choosing an export

| Format | Use it for | Preserves |
| --- | --- | --- |
| `.xip` | Reopening and editing in Image Studio | Layers, RGBA, frames, durations |
| `.ximg` | Drawing from an Xe program | Flattened 16-colour frames, transparency, durations |
| `.sprite3` | Importing a ready-made animated sprite into Scratch | Flattened RGBA costumes and a playback stack |
| `.png` | One flattened still image | RGBA pixels |
| `.gif` | A broadly viewable animation (export requires Pillow) | Flattened frames and supported GIF timing |
| sprite-sheet `.png` | Another engine or manual frame slicing | Flattened frames in one horizontal row |

Save an `.xip` master before making a runtime export. Runtime exports intentionally
flatten visible layers. Existing destinations are replaced atomically only after the
new file has encoded successfully.

## Use an image in an Xe window

Export `button.ximg` into the Xenon virtual drive, for example
`SystemAssets/MyApp/button.ximg`. The default Windows drive is under
`%LOCALAPPDATA%\XenonOS\VirtualDrive`; a host that supplies another private drive
uses that drive instead. Xe asset paths are portable forward-slash paths relative to
the virtual-drive root, never absolute computer paths.

```xe
var win: graphics::Window
var button: graphics::Image

win.x = 40
win.y = 30
win.width = 180
win.height = 120
win.title = "Image example"
win.ui_scale = 1
win.state = graphics::WINDOW_NORMAL

button = graphics::load_image("SystemAssets/MyApp/button.ximg")

while (win.state != graphics::WINDOW_CLOSED) {
	call graphics::begin_draw(win)
	call graphics::clear(win, graphics::BLACK)
	call graphics::draw_image(win, button, 12, 12, 0, 2)
	call graphics::update(win)
}
```

`draw_image(target, image, x, y, frame, scale)` uses nearest-neighbour integer
scaling. Scale `1` draws one source pixel as one logical pixel. Transparent pixels do
not overwrite the target. A failed load returns an image whose width is zero, so an
app can display a fallback:

```xe
if (graphics::image_width(button) == 0) {
	call graphics::draw_text(win, 12, 12, "Missing image", graphics::COLOR_12)
}
```

Declare shipped assets under the app's namespace in `apps/manifest.json`. Keeping
the `.xip` source beside the project is optional; the runtime declaration points to
the exported `.ximg`. `apps/assets/README.md` describes the catalog rules.

## Play an XIMG animation in Xe

XIMG stores each frame's duration. Advance with elapsed time rather than assuming a
fixed frame rate:

```xe
var animation: graphics::Image
var frame: int
var elapsed: int
var previous: int
var now: int
var duration: int

animation = graphics::load_image("SystemAssets/MyApp/animation.ximg")
frame = 0
elapsed = 0
previous = os::ticks()

while (win.state != graphics::WINDOW_CLOSED) {
	now = os::ticks()
	elapsed = elapsed + now - previous
	previous = now
	duration = graphics::image_frame_duration(animation, frame)
	while (duration > 0 && elapsed >= duration) {
		elapsed = elapsed - duration
		frame = (frame + 1) % graphics::image_frame_count(animation)
		duration = graphics::image_frame_duration(animation, frame)
	}

	call graphics::begin_draw(win)
	call graphics::clear(win, graphics::BLACK)
	call graphics::draw_image(win, animation, 20, 20, frame, 1)
	call graphics::update(win)
}
```

This state model ports cleanly to Scratch: timing is based on elapsed milliseconds,
frames are integer-indexed, and rendering uses no host-only interpolation.

## Make a desktop or wallpaper

Create a `480x360` project, export it as XIMG, and draw it into a frameless
`graphics::Screen`. A Screen owns the complete stage and has no title bar or border:

```xe
var desktop: graphics::Screen
var wallpaper: graphics::Image

wallpaper = graphics::load_image("SystemAssets/Desktop/wallpaper.ximg")
call graphics::begin_draw(desktop)
call graphics::clear(desktop, graphics::BLACK)
call graphics::draw_image(desktop, wallpaper, 0, 0, 0, 1)
call graphics::update(desktop)
```

For an animated desktop, use the elapsed-time loop above and replace `win` with
`desktop`. Keep important content inside the `480x360` stage and preview the indexed
result: a full-screen RGBA gradient may quantize heavily into the 16-colour palette.

## Make an icon

Start with a transparent `16x16`, `24x24`, or `32x32` project. Work at integer zoom,
avoid partially transparent edge pixels if exact palette output matters, and inspect
the XVM preview. Export to XIMG and draw it at an explicit integer scale:

```xe
var icon: graphics::Image
icon = graphics::load_image("SystemAssets/MyApp/icon.ximg")
call graphics::draw_image(win, icon, 8, 8, 0, os::icon_size + 1)
```

`os::icon_size` is a preference (`0`, `1`, or `2`); it does not resize an image by
itself. For tiny icons that do not need an external asset, `draw_icon_scaled` accepts
a compact row-major palette string and an explicit scale.

## Use the animation directly in Scratch

1. Select **Export…** and choose **Scratch sprite (`.sprite3`)**.
2. Image Studio flattens each visible frame into a PNG costume and creates a
   green-flag playback stack in deterministic costume order.
3. In Scratch, open **Choose a Sprite** below the stage, select **Upload Sprite**
   (the upward-arrow action), and choose the `.sprite3` file.
4. Check the imported costume center and sprite size, then run the green flag.

The requested waits are stored in the Scratch blocks. Scratch schedules scripts in
live ticks, so very short frames can have small timing jitter. Layer data is not part
of `.sprite3`; keep the `.xip` master for later edits.

For an entire Xe program, use the **Xe → SB3** tab. Choose Active file, Workspace,
or **Choose .xe file**, run compatibility analysis, and export only when the result
is **Exact**. A compatible VM profile must implement every used graphics syscall and
asset-ROM feature and must package each declared XIMG. The bundled legacy profile
currently lacks those capabilities, so it correctly blocks graphical Xe exports
instead of producing a misleading Scratch project. The `.sprite3` route remains
available for artwork and animation independently of the Xe VM profile.

## Compression and portability

XIMG is a deterministic 32-bit word stream with dimensions, frame table, duration,
loop metadata, offsets, and CRC. Each frame chooses the smallest deterministic form
from packed raw pixels, run-length encoding, or previous-frame delta RLE. Six 5-bit
palette values fit in one word. The decoder validates all offsets and sizes before
drawing and enforces the 200,000-word XIMG format ceiling.

For smaller files:

- keep unchanged areas identical between neighboring frames so delta encoding wins;
- use transparency for empty regions;
- crop unused margins before export;
- avoid noise and excessive per-pixel variation;
- reuse the same 16-colour palette entries seen in the indexed preview.
