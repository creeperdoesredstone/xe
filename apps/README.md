# Xe application architecture

Each app remains one standalone `.xe` entry so it can run directly in the host IDE
and eventually be packaged for Scratch without a host-only source loader. Modularity
is expressed through stable boundaries inside each entry rather than hidden path
includes:

1. `APP_TITLE`, `APP_DEFAULT_WIDTH`, and `APP_DEFAULT_HEIGHT` are the source-level
   metadata header.
2. Theme, spacing, timing, and capacity constants define presentation policy.
3. Model state and pure domain procedures do not draw or poll input.
4. Layout procedures derive bounded rectangles and viewport sizes.
5. Input procedures mutate model/view state and respect modal input ownership.
6. Paint and asset-hook procedures render the already-computed state.
7. The driver initializes the app and runs its delta-time-aware frame loop.

`manifest.json` is the repository-level source of truth for app identity, category,
default geometry, artifact paths, asset namespace, and declared assets. Its metadata
is checked against the source header. `python tools/build_apps.py` deterministically
rebuilds every declared `.xas` and `.xbn`; add `--check` in CI or before a commit to
detect missing or stale outputs without writing.

## Adding an app

1. Add one `apps/<id>.xe` entry with the standard metadata header and boundaries
   above.
2. Add exactly one matching record to `manifest.json` with unique assembly and
   bytecode paths.
3. Put image, animation, and music declarations under the app's unique
   `asset_namespace`; follow [assets/README.md](assets/README.md).
4. Add domain, interaction, resize, and bounded-frame smoke tests.
5. Run the artifact builder and the complete regression suite.

The manifest validator accounts for every top-level `.xe` app, confines portable
paths, rejects collisions, and verifies the supported asset extension for every
declaration. This keeps metadata and generated programs editable without coupling
app logic to Python host paths.
