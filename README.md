Documentation for this programming language can be found [here](https://creeperdoesredstone.github.io/xe-docs/)

Graphics, window, and OS standard-library APIs are documented in
[STDLIB.md](STDLIB.md).

# Xenon IDE

Launch the Xenon IDE with `python ide.py`. Pass an Xe file path as the first
argument to open it directly, for example `python ide.py apps/calculator.xe`.
Add `--run` to execute it immediately.

## Included graphical applications

- `apps/calculator.xe` - seven-mode calculator with Standard, Scientific,
  Programmer, Graphing, offline-snapshot Currency, Unit Conversion, and History views.
- `apps/settings.xe` - staged system preferences with an animated compact menu.
- `apps/xenon_terminal.xe` - tabbed deterministic terminal with history,
  autocomplete, saved commands, split view, themes, and a resource monitor.

For example: `python ide.py apps/xenon_terminal.xe --run`.

## Features

* Create a new file by clicking `New` or pressing `Ctrl + N`.
* Load a file by clicking `Open` or pressing `Ctrl + O`.
* Save the contents of the loaded file by clicking `Save` or pressing `Ctrl + S`.
* Save the editor contents into a file by clicking `Save As` or pressing `Ctrl + Shift + S`.

* Run the code in the editor by clicking `New` or pressing `Ctrl + Return`.
* Find all instances of a text by clicking `Find` or pressing `Ctrl + F`.
* Find and replace all instances of a text by clicking `Replace` button or pressing `Ctrl + H`.
* Rename the selected symbol by clicking `Rename Symbol` or pressing `F2`.
* Toggle whether a line is commented by clicking `Rename Symbol` or pressing `Ctrl + /`.