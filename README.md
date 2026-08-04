Documentation for this programming language can be found [here](https://creeperdoesredstone.github.io/xe-docs/)

Graphics, window, and OS standard-library APIs are documented in
[STDLIB.md](STDLIB.md).

Launch the Xenon IDE with `python ide.py`. Pass an Xe file path as the first
argument to open it directly, for example `python ide.py apps/calculator.xe`.
Add `--run` to execute it immediately.

Included graphical applications:

- `apps/calculator.xe` - seven-mode calculator with Standard, Scientific,
  Programmer, Graphing, offline-snapshot Currency, Unit Conversion, and History views.
- `apps/settings.xe` - staged system preferences with an animated compact menu.
- `apps/xenon_terminal.xe` - tabbed deterministic terminal with history,
  autocomplete, saved commands, split view, themes, and a resource monitor.

For example: `python ide.py apps/xenon_terminal.xe --run`.
