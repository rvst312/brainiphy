# Software audit and remediation — 2026-09-06

This review covers the repository's Python package, generated connector templates, GoHighLevel preset,
interactive flow, installer, packaging, CI and primary operating documentation. It combines source review,
regression tests, temporary-project reproductions, a real terminal pseudo-device, real local mirrors and
an installed-wheel smoke test. It is not a certification that all possible defects have been eliminated.

The original eight findings have implementations and regression coverage. Additional data-loss and
usability defects discovered in the expanded review were also corrected. Changes to copied client scripts
require the migration steps below; updating this repository deliberately does not overwrite client code.

## Original findings and implemented fixes

| ID | Original impact | Implemented behavior | Main evidence |
| --- | --- | --- | --- |
| A01 | Credentials attached to pagination URLs on other origins | Validate scheme, host and port before reading credentials; reject cross-origin redirects, including HTTPS downgrades and custom authentication headers | `HTTPBoundaryTests`; `httpclient.py` |
| A02 | Same-named client brains share Keychain, MCP and schedule names | Persist a fresh namespace in `connectors/identity.json`; retain it across moves; a reused path receives a new identity. New MCP and schedule names are scoped to it. Legacy credential references have an explicit migration command | `NamespaceTests`, `ScheduleTests`; `project.py` |
| A03 | Distinct remote IDs overwrite one Markdown file after slug normalization | Use a readable prefix and a hash of the complete, opaque ID. Migrate a matching legacy file only after writing its replacement | `RecordIdentityTests`, `LegacyCompatibilityTests`; `frontmatter.py` |
| A04 | Connector names and symlinks escape the project | Validate simple connector names and resolved script/output/state paths during creation and sync; reject overlapping mirror source and output | `ProjectBoundaryTests`; `storage.py`, `project.py`, `sync.py` |
| A05 | Deleted records remain in the corpus | New templates opt into complete-snapshot reconciliation. Stage and validate records, track ownership per collector and folder, then remove only absent owned files. Preserve prior data on failed/incomplete fetches | `SnapshotTests`, `ManifestLocationTests`; `collect.py` |
| A06 | Repeating sync after a failed graph build does not retry indexing | Persist pending indexing before running connectors; retry the graph independently of polling intervals. Refuse concurrent sync writers | `BuildRecoveryTests`, `AdditionalBoundaryTests`; `sync.py` |
| A07 | A malformed record stops every following collector | Include validation and publication in per-collector error handling. A bad record preserves that collector's previous snapshot while later collectors still run | `SnapshotTests`; `collect.py` |
| A08 | Ampersands and other path characters produce invalid LaunchAgent XML | Parse the template, substitute structured values and serialize with `plistlib`; verify project ownership before reusing an existing label; reload an already loaded job when requested | `ScheduleTests`, `AdditionalBoundaryTests`; `project.py` |

The fixes do not silently opt older copied connectors into deletion. `Collector.snapshot` defaults to
`False` for compatibility. Newly generated custom/API connectors and the updated preset explicitly set
`snapshot=True`, with strict response validation. An old connector that treats a malformed response as an
empty list must be corrected before enabling snapshot reconciliation.

## Further corrections from the expanded review

- **Document freshness:** normal sync now uses semantic extraction after the first build too.
  `--code-only` opts into AST updates on an existing graph; `--full` also builds when no connector is due.
  Pending semantic work survives a later code-only request. Polling intervals remain independent of `--full`.
- **Stable unchanged records:** unchanged content retains bytes, mtime and capture time, including mirror
  conversions. Polling alone does not make every record appear edited.
- **Malformed mirrored exports:** a valid JSON export followed by invalid JSON originally returned success
  while the converted-file count fell from one to zero. Conversion now stages its output, returns an error
  and preserves the previous converted records. A successful empty export still removes obsolete records.
- **Corrupt global registry:** malformed `brains.yaml` originally read as an empty list and was overwritten
  on registration. Updates now validate the existing registry, refuse corruption and use an atomic write
  under a lock. Read-only listing reports the problem.
- **YAML integrity:** control characters U+0080–U+009F previously failed or changed during YAML round trips.
  They are escaped. Reserved YAML words remain string field names, hostile keys are quoted and extra fields
  cannot replace provenance fields.
- **Interactive recovery:** selecting a missing connector script no longer raises `FileNotFoundError`.
  Fetched URL content settles the source step before a graph exists. Malformed graph JSON shapes are reported
  as unbuilt. `brain <path>` now reaches the app as documented, while remaining subject to the TTY requirement.
- **Diagnostics:** ordinary filesystem/configuration failures and cancellation receive CLI exit codes;
  invalid registry errors also propagate from dry runs. Non-string type metadata does not crash rendering.
- **Executable resolution:** tools installed beside the running interpreter are found before unrelated PATH
  installations. This includes `graphify-mcp`, which is not one of the installer's two public symlinks.
- **Scheduling:** lookup verifies the actual project arguments and recognizes custom labels. Rescheduling a
  moved project reuses its persisted namespace and updates the existing job's arguments. Configuration
  backups include microseconds. Status explicitly says it does not verify live activation.
- **Installer preservation:** removing a PATH block refuses unclosed/nested markers instead of truncating
  the rest of the shell profile. Uninstallation preserves skill symlinks pointing to a different checkout.
- **HTTP completeness:** unfinished pagination and missing required collection/object fields are explicit
  failures. The preset uses strict helpers for non-paginated resources too. Truncated HTTP reads are retryable.

## Verification

The final test suite contains **239 tests**, up from 185 at the start of the first audit. Both Python 3.11
and the dedicated Python 3.13 environment pass the suite on this Mac. These include the following checks:

| Surface | Validation | Boundary of the evidence |
| --- | --- | --- |
| Shared CLI/package | Full `unittest` suite under Python 3.11 and 3.13 | macOS execution; Linux execution remains CI's responsibility |
| Generated connectors | Real smoke test, generated scripts, real `rsync`, CSV conversion and a second sync | No live vendor API |
| Interactive app | Scripted keys add a real folder and run its actual mirror through the app | Graphify build replaced to avoid model calls |
| Terminal input | Real PTY reads queued arrow sequences and checks terminal restoration | Does not prove all terminal protocols work; see R05 |
| HTTP | Simulated pages, retry failures, custom headers, foreign origins and redirects | No genuine token or external request |
| Credential migration | In-memory Keychain replacements test copy, preservation, failure and repeat execution | Login Keychain was not changed |
| Scheduling/Desktop | Temporary plists and Desktop JSON, ownership checks and mocked launchctl calls | No actual job or Desktop configuration was installed |
| Packaging | Wheel build, offline installation in a temporary venv and installed-wheel smoke test | Reuses available dependencies; not a fresh network installation |
| Installer | Shell syntax, dry run, actual extracted profile-filter function against temporary profiles | No live uninstall or shell-profile edit |
| Version floor | Parse source/tests with Python 3.9 grammar; byte compilation | Python 3.9 runtime was not available locally; CI must run it |
| Dependency environment | Dedicated Python 3.13 venv passes `pip check` | Older shared Python 3.11 installation has unrelated dependency conflicts |
| Graphify interface | Installed Graphify 0.9.53 global help exposes the extraction/update/clustering flags used | Not a live semantic extraction, nor proof of compatibility with future releases |

No live CRM requests, model extraction, credential changes, job activation or deployment were performed.
No commit or publication is included. Pre-existing user edits to the connector registry and `AGENTS.md` were
preserved. This report does not represent a vulnerability-database scan of third-party dependencies.

## Remaining findings and recommendations

### R01 — P2: duplicate CSV headers can discard column values

**Reproduced.** Input `Name,Name` with row `Alice,Bob` yields `{"name": "Bob"}`. Headers are normalized
independently and then assigned into a dictionary, so both an exact duplicate and distinct labels that
normalize identically can overwrite a value.

Location: [`records_from_table()`](../../src/brainiphy_cli/mirror_template.py).

Recommended correction: generate unique field names after normalization, preserving all columns and the
original labels. Test exact duplicates, punctuation-only differences and localized header variants. Table
record IDs also currently use row positions; reordering an export changes which business entity owns that
ID. Consider an explicit source ID-column option rather than guessing one.

### R02 — P2: every resource denied can still mean exit zero

**Reproduced.** Two collectors both raising `NoScope("HTTP 401: expired token")` produce exit zero. A sync
can therefore look successful even when an expired credential read nothing. Existing records are preserved,
which prevents deletion, but the operational health signal remains weak.

Locations: [`collect.run()`](../../src/brainiphy_cli/collect.py),
[`HttpClient.request()`](../../src/brainiphy_cli/httpclient.py).

This preserves the repository's explicit rule that missing scopes are normal. A follow-up should retain
per-resource `NoScope` handling while adding an aggregate outcome such as “no authorized resource read,”
with separate behavior for discovery probes and unattended synchronization.

### R03 — P2: long subprocesses have neither an overall deadline nor progressive output

**Confirmed by source review.** Connector and graph commands use `subprocess.run(capture_output=True)`
without a timeout. Output appears after a process exits. A stuck custom connector or model subprocess can
hold the sync lock indefinitely and block later scheduled syncs; large output is also buffered in memory.

Location: [`sync.py`](../../src/brainiphy_cli/sync.py).

Recommended correction: stream output and add separately configurable connector/build deadlines, with
termination of the process tree and retention of pending indexing. Choose defaults appropriate to large
model builds rather than reusing the HTTP request timeout.

### R04 — P2: concurrent connector registration can lose a registry update

**Confirmed by source review; not stress-tested.** Creating a connector still reads the connector registry,
creates its script and writes the modified list in separate steps. Two independent registrations can read
the same list and publish conflicting replacements. Atomic file replacement prevents truncation but does
not make that whole read–modify–write operation atomic. The global brains list and sync execution are locked;
connector registration and simultaneous Desktop configuration edits are not fully serialized.

Location: [`create_connector()`](../../src/brainiphy_cli/project.py).

Recommended correction: hold a project mutation lock across connector creation and registry publication,
and a configuration lock across Desktop read/backup/write. Test two writers against a barrier-controlled
schedule, verifying both entries and scripts survive.

### R05 — P3: incomplete terminal sequences can wait indefinitely

**Reproduced in an isolated PTY process with a timeout.** Sending `ESC [` without the final arrow byte
blocks the next byte read. UTF-8 continuation reads also have no bounded wait.

Location: [`keys.read_key()`](../../src/brainiphy_cli/keys.py).

Recommended correction: bound continuation reads, return a recoverable unknown/escape result and preserve
unconsumed input. Extend the PTY tests with truncated CSI and UTF-8 sequences plus cancellation.

### R06 — P3: the typed folder-picker fallback ignores `allow_new=False`

**Reproduced.** With raw-key support disabled, choosing `n` creates a source folder even though the public
picker was called with `allow_new=False`. The flag is passed to the navigable implementation but not the
typed fallback. A typed missing path can also reach the create-folder confirmation.

Location: [`picker.py`](../../src/brainiphy_cli/picker.py).

Recommended correction: propagate the constraint through both implementations and path resolution, hiding
and rejecting folder creation when the caller requires an existing source.

## Deployment and migration considerations

1. **New brains:** use the current generator. New credentials/resources use the persisted unique namespace;
   new API/custom templates and the preset opt into validated snapshots.
2. **Existing credentials:** run `brain migrate-identity <project>` locally. It copies conventional legacy
   items, preserves old/custom items and updates template `SECRET_ITEM` assignments only after a successful
   copy. Probe afterwards: no tool can infer whether a previously shared token was already overwritten with
   another tenant's credential. Hand-written credential lookups require manual adaptation.
3. **Existing connector copies:** updating library code does not rewrite scripts. Audit complete-fetch
   semantics, replace permissive response fallbacks with strict helpers, then enable `snapshot=True`.
   Adapt old custom write loops to `collect.run()` and update old mirrors to the staged-conversion template.
   Old manifests without folder binding or a changed collector subfolder require explicit state migration;
   the code refuses incompatible manifests rather than pruning files in an uncertain location.
4. **Existing data:** matching legacy record filenames migrate on write. Files whose old IDs already collided
   may need to be fetched again. Unmatched legacy files in a shared collector folder require review; they are
   not guessed to belong to a collector and silently removed.
5. **Schedules:** the presence of a plist proves configuration, not live activation. Actual launchd/Desktop
   and Keychain behavior should be checked on the target machine before treating an unattended brain as
   operational. A model key exported only in an interactive shell is not injected into a generated plist.
6. **Extraction behavior:** normal sync now performs semantic extraction. Changed documents may invoke the
   configured model. `--code-only` is the explicit local AST option; `--full` does not force connector polling.
7. **Dependencies:** the installer still requests an unpinned Graphify release. Add a compatibility job using
   the supported Graphify version range before broad distribution; the mocked unit tests do not replace it.

Recommended next work: R01 and R04 for data integrity, R02/R03 for unattended-operation health, then the two
terminal/picker edge cases. Preserve the existing lightweight `unittest` approach; no new test framework is
needed for these changes.
