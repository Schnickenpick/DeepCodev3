# Changelog

## Unreleased

### Added
- `/server` command — view/change the backend base URL, API key, and models
  endpoint path. Arrow-key GUI (`/server`) or typed form
  (`/server <url> [key]`, `/server models=<path>`, `/server reset`).
- `--base-url` / `--api-key` CLI flags for a session-only backend override.
- `DEEPCODE_BASE_URL` / `DEEPCODE_API_KEY` / `DEEPCODE_MODELS_PATH` env vars,
  read before saved config.
- `/model` and `/models` now fetch the live model list from a configured
  custom backend (normalizes both `{"models":[...]}` and OpenAI/Anthropic
  `{"data":[...]}` shapes) instead of always using the built-in static
  catalog. Falls back to the static catalog if the backend has none/fails.
- `/reasoning` with no argument now opens an arrow-key picker instead of
  erroring; typed form (`/reasoning high`) unchanged.

### Fixed
- `/server`'s API-key prompt no longer says "leave blank for none" — the
  input box can't submit empty text. Now accepts `none`/`-`/`skip`/`n/a`.
- Arrow-key pickers built with a "Cancel" item as the literal last option
  silently broke: `_pick_option`'s last slot is a dedicated free-text slot
  and returns the literal string `"__free__"`, not the option's label.
  `/server` and `/reasoning` pickers now map that back to "Cancel" instead
  of placing Cancel last.
- UltraCode leader prompt biased toward leaving `depends_on` empty even for
  naturally sequential build steps, so independent-looking groups fired
  simultaneously and finished in effectively random order. Prompt now
  defaults to sequential dependencies unless groups are genuinely
  independent. Also fixed a `depend_on`/`depends_on` key mismatch between
  the prompt text and the parser.
- System prompt's quiz/clarification guidance was vague enough that the
  model defaulted to asking a clarifying quiz before most tasks, even when
  the answer was inferable or obvious. Added explicit guidance: infer from
  context/conventions, only quiz for genuinely ambiguous or irreversible
  choices.
