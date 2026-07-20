# AGENTS.md — meeting-supporter

Meeting Supporter is a Japanese desktop AI meeting assistant. Keep changes within the public desktop application boundary.

## Product directories

| Directory | Responsibility |
| --- | --- |
| `src/` | React 19 + Vite 7 + TypeScript frontend. Consumes `src/api/generated`. |
| `src-tauri/` | Tauri 2 Rust shell and Python sidecar lifecycle. |
| `python/` | Main FastAPI/WebSocket/Pydantic AI backend bundled with the desktop app. |
| `python-server/` | Optional remote Google Cloud STT relay. |
| `doc/` | Public product, UI, and architecture authorities. |
| `public/` | Static web assets. |
| `test/` | Public integration and desktop test support. |
| `scripts/` | Public checks, generation, and test runners. |

Do not confuse `python/` with `python-server/`. Meeting history and user context are stored in the platform app-data directory, not in this repository.

Meeting Supporter-operated hosted-service server code and operations are outside this repository. A normal OSS build must remain fail closed when the hosted endpoint is not configured.

## Safe data handling

- Never provide production credentials to code, tests, agents, or candidate processes.
- Never record credentials, tokens, personal information, prompts, meeting transcripts/audio, secret-bearing raw stderr, or absolute home paths in tracked files, issues, pull requests, logs, screenshots, or fixtures.
- Use synthetic data in tests and examples. Redact external output before sharing it.
- Do not execute production deployment or destructive production operations.
- Validate JSON and external boundary data with explicit parsers, type guards, `TypedDict`, or Pydantic models. A cast is not validation.

## Generated artifacts

- Never hand-edit `openapi.json` or `src/api/generated`; run `npm run generate:api`.
- Regenerate `THIRD-PARTY-NOTICES.txt` with `npm run licenses:generate`.
- Keep generated changes in the same pull request as the contract or dependency change.

## Public verification

Run the checks relevant to the change. The complete public command set is:

```bash
npm run check:public
npm run licenses:check
npm run build
npm run test
npm run generate:api
git diff --exit-code -- openapi.json src/api/generated
uv run --directory python poe check
uv run --directory python poe test
cargo test --locked --manifest-path src-tauri/Cargo.toml
npm run test:tauri
```
