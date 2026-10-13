# Provenance and licences

This file records where the code, task content and dependencies in this
release come from, and under what terms. The project licence is Apache-2.0
(`LICENSE`); `NOTICE` carries the attributions and disclaimers that must travel
with redistributions.

## Code and task content

- **Authorship.** All code, task files, fixtures and documentation were written
  for this project in September and October 2026 by Tyler Holland, with AI
  coding assistants (Claude and Codex) drafting much of the code under review.
  No other human contributors appear in the source history.
- **No vendored code.** A search of every published file found no copyright
  headers, SPDX tags, licence texts or "adapted from" markers. The coding-task
  repositories under `ptu/tasks/extreme/*/repo` are small bespoke packages
  written for the tasks. `ruff.py` in XTR-202/XTR-204 is an original 23-line
  stand-in, not Ruff.
- **Synthetic people and companies.** All people, companies and records are
  fictional; email and web domains are reserved example names. Some names
  coincide with real people or businesses (for example the persona "Dev Patel"
  and the company name "Tidewater"); no real person or organisation is meant.
- **LLM-drafted fixture prose.** Short company blurbs, handbook sentences and
  issue text were drafted with Claude Haiku 4.5 and Claude Sonnet 4.6 through
  Amazon Bedrock, screened for real names and edited
  (`ptu/workspaces/tidewater/content_extreme/BRIEF.md`). The drafting scripts
  are not published.
- **Vendor documentation excerpts.** `ptu/apps/*/fidelity.yaml` quote 163 short
  excerpts (about 2,300 words, at most 30 words each) from GitHub, Stripe,
  HubSpot and Slack documentation, each with source URL and access date. They
  specify the behaviour the mock applications reproduce and are excluded from
  the Apache licence; see `NOTICE`. GitHub Docs text is CC BY 4.0.
- **Credentials.** No real credential exists in the tree or its source history.
  Credential-shaped values are derived at run time from task seeds with fake
  prefixes (`ptu/common/secrets.py`); fixture files contain placeholders only.

## Runtime dependencies

Installed with `pip install -r requirements-ptu.lock` (Python 3.14). Nothing is
bundled. Licences below are read from each package's installed metadata.

| Package | Version | Licence (from installed metadata) |
|---|---|---|
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| boto3 | 1.43.105 | Apache-2.0 |
| botocore | 1.43.105 | Apache-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.0 | MIT |
| Jinja2 | 3.1.6 | BSD-3-Clause (classifier "BSD License") |
| jmespath | 1.1.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| mcp | 2.2.0 | MIT |
| mcp-types | 2.2.0 | MIT |
| opentelemetry-api | 1.45.0 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| python-dateutil | 2.9.0.post0 | Apache-2.0 and BSD-3-Clause (dual) |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| rpds-py | 2026.6.3 | MIT |
| s3transfer | 0.19.2 | Apache-2.0 |
| six | 1.17.0 | MIT |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

Notes:

- None is GPL, AGPL or of unknown licence.
- `httpx2` and `httpcore2` are legitimate packages pulled in by `mcp` 2.2.0
  (project URLs point to the pydantic organisation); they are not typosquats.
- `boto3`/`botocore`/`s3transfer`/`jmespath` serve only the historical Amazon
  Bedrock arm, which is suspended. They are imported lazily, so no offline
  check loads them. `botocore` bundles a CA file under MPL-2.0; this matters
  only if dependencies are ever redistributed (for example in a container
  image).
- The lock file pins versions but not hashes.

## External programs

- **Git** is resolved from fixed system locations (`/usr/bin`, `/bin`, and
  `/opt/homebrew` on macOS) and run as a separate process. It is not
  distributed.
- **Agent command-line tools** (Claude Code; the Codex CLI) are supplied by the
  user and are not distributed. They are needed only for optional live agent
  runs.
- **gitleaks 8.24.3** (MIT) is downloaded by CI only, and its tarball is checked
  against the release's published SHA-256.
- Nothing is downloaded at runtime by the package itself.

## Historical measurement records

Task metadata and `docs/RESULTS.md` cite run identifiers such as
`gate-20261005T034944Z-3f6dfbc2`. They name runs in the author's private run
store, which is not published: transcripts, episode packets and usage records
stay private. The published numbers are counts and verdicts only.
