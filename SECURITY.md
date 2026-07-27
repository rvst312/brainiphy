# Security

## Reporting a vulnerability

Report anything security-relevant privately — open a
[security advisory](https://github.com/rvst312/brainiphy/security/advisories/new)
or email aaron@rvconsulting.services. Please don't open a public issue for it.

## What this tool touches

Worth knowing before reviewing a change, because these are the boundaries a
patch can quietly cross:

- **Credentials live in the macOS Keychain, and only there.** Connector scripts
  read them through `keychain.get_secret()`. A secret must never travel as a
  CLI argument, a value in `connectors/registry.yaml`, or any file brainiphy
  writes — shell history, `ps` output and launchd logs all capture those.
  `brain secret get` prints to stdout unstyled and alone, so it can be piped
  without leaking into a log line.
- **Connector scripts are executed.** `brain sync` runs every
  `connectors/<name>/sync.py` in the target project. A brain built from an
  untrusted project directory runs whatever those scripts contain.
- **Mirrored folders are copied into the project.** `--mirror` rsyncs a local
  folder into `raw/`, and `raw/` is gitignored precisely so that mirrored
  client material does not end up committed. Check that before changing the
  generated `.gitignore`.
- **`brain connect-claude --trust-desktop`** adds the project to
  `localAgentModeTrustedFolders` in `claude_desktop_config.json`. It must stay
  additive, and the config is backed up before every edit.
- **Untrusted strings reach the terminal and YAML.** Connector output and
  record titles are written through `frontmatter.write_record()`, which applies
  graphify's own escaping, and printed as Rich `Text` with `highlight=False`.
  Bypassing either turns a hostile document title into markup or malformed
  frontmatter.
