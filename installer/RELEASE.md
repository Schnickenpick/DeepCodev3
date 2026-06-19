# DeepCode v3

A terminal + desktop AI coding agent. Chat with 34 models, and (when you turn
it on) let it read/write files and run commands on your machine.

---

## ⚠️ DISCLAIMER — READ FIRST

**Use at your own risk. The author is NOT responsible for anything that happens
on your computer or account as a result of using this software.**

DeepCode can be given permission to run shell commands and modify files. An AI
can make mistakes or do things you didn't intend. By downloading and running
DeepCode you accept full responsibility for:

- anything the agent does on your system (deleted/changed files, run commands),
- anything **you** ask it to do,
- any consequences to your data, accounts, or hardware.

If you don't accept that, don't run it. No warranty of any kind is provided.

---

## 🛡️ "Windows says it's a virus / unknown publisher" — it's a false positive

These exes are **not code-signed** (signing certs cost money). So:

- **Windows SmartScreen** will show *"Windows protected your PC / unknown
  publisher."* → click **More info → Run anyway**.
- **Antivirus / Windows Defender** may flag the `.exe`. This is a well-known
  **false positive** for apps built with PyInstaller/Electron — the packer
  signature looks suspicious to AV heuristics, but there is no malware. If your
  AV quarantines it, you may need to allow/restore the file.

This is normal for indie unsigned software. If you don't trust it, don't run it
— the source is open, read it yourself.

---

## Install

1. Download the files (both exes + `install.bat`).
2. Put them in one folder, run **`install.bat`** (no admin needed).
   - copies them to `%LOCALAPPDATA%\DeepCode`
   - adds `deepcode` to your PATH
   - makes a desktop shortcut to the GUI
3. Open a **new** terminal → type `deepcode`. Or launch the desktop shortcut.

Don't want to install? Just run the exes directly:
- `DeepCodeCLI.exe` — the terminal app
- `DeepCodeGUI.exe` — the desktop app

`uninstall.bat` removes everything (keeps your chat history).

## Using it

- **GUI** starts in **read-only** mode — it can chat and read files but won't
  write or run commands. Flip the **Agent** toggle to give it tool access.
- **Terminal**: `/agent` to toggle tools, `/help` for all commands, `/model` to
  switch model, shift+tab to change permission mode.
- Some genuinely destructive commands (e.g. `rm -rf /`) are always blocked.

That's it. Have fun.
