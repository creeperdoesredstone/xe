# App asset extension points

`apps/manifest.json` is the single catalog for app identity, default window geometry,
build outputs, asset namespaces, and declared assets. Add an asset record there before
referencing it from an app.

- Use `.ximg` for compact indexed images and animations loaded by
  `graphics::load_image`. A single file may contain multiple frames and per-frame
  durations.
- Use `.xip` as the editable Image Studio project. Export an `.ximg` for Xe runtime
  use and a `.sprite3` when a standalone Scratch sprite is useful.
- Use `.xmusic` for deterministic sequenced audio loaded by `audio::load`.
- Keep paths portable, relative, and inside the app's `asset_namespace`. The runtime
  resolves them in the private Xenon virtual drive; source code must never embed a
  host workspace path.

App-specific drawing procedures are the replaceable presentation boundary. Assets
can replace those procedures without changing state, input, layout, or filesystem
logic. The app catalog validator rejects duplicate IDs, unsafe paths, undeclared
formats, missing app sources, and artifact collisions.
