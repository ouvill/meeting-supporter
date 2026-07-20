# Contributing to Meeting Supporter

Thank you for contributing. Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). Meeting Supporter is distributed under `AGPL-3.0-only`, and every human contributor must accept the [Meeting Supporter Contributor License Agreement](CLA.md) before a pull request can be merged.

## Before opening an issue

- Use the GitHub issue forms for reproducible bugs and proposed product changes.
- Search existing issues first and describe one observable outcome per issue.
- Report security vulnerabilities through the private process in [SECURITY.md](SECURITY.md), not a public issue.
- Do not post credentials, tokens, personal information, meeting transcripts or audio, raw stderr, or absolute home-directory paths in an issue, pull request, log, screenshot, or fixture.
- Reproduce problems with synthetic data and redact environment-specific details before sharing output.

## Development

Install the project dependencies:

```bash
npm install
uv sync --directory python --locked
```

Run checks relevant to the change. The complete public checks include:

```bash
npm run check:public
npm run licenses:check
npm run build
npm run test
uv run --directory python poe check
uv run --directory python poe test
cargo test --locked --manifest-path src-tauri/Cargo.toml
npm run test:tauri
```

Generated API artifacts must be updated with `npm run generate:api`; do not edit `openapi.json` or `src/api/generated` by hand.

## Pull requests

Each pull request must:

1. link a GitHub issue with `Closes #<number>` or explain why no issue is needed;
2. state the observable change from a user or contributor perspective;
3. list the commands and real scenarios actually run, with their observed results;
4. identify unverified scope or platform limitations;
5. confirm that no sensitive or meeting data was added to code, tests, logs, screenshots, or discussion;
6. identify third-party code, assets, models, or data and the applicable license;
7. update the PRD, Product Surfaces, ADRs, or generated artifacts when their public contract changes;
8. pass required review, CI, license checks, and `license/cla`.

Keep changes focused. Tests must defend observable behavior or a boundary that can plausibly regress; do not test source text or incidental wiring.

## License and CLA

The project uses a CLA because contributions may be offered both under AGPL-3.0-only and under commercial or proprietary licenses. The CLA:

- leaves ownership of your contribution with you;
- keeps your contribution available under the project license in effect when you submit it;
- grants Ouvill the additional rights needed to sublicense and relicense the contribution;
- includes a patent license for patent claims necessarily exercised by the contribution.

Acceptance is recorded by [CLA Assistant](https://cla-assistant.io/) using your authenticated GitHub account. The pull request check named `license/cla` must pass for every human contributor represented in the pull request. A pull-request checkbox is not a signature.

When CLA Assistant asks you to sign:

1. Read the current [CLA](CLA.md).
2. Provide your legal name, email address, signing capacity, and organization where applicable.
3. Confirm that you are at least 18 and have authority to grant the stated rights.
4. Accept the agreement through CLA Assistant.

If your employer owns your work, obtain its authorization before contributing and sign for the legal entity only if you are authorized to bind it. Otherwise, ask the entity to arrange acceptance with `contact@ouvill.net`.

Do not submit code, media, or other material whose rights you cannot grant. Clearly identify third-party material and its license in the pull request. Mark discussion-only communications **Not a Contribution** when you do not intend them to be covered by the CLA.

Automated dependency-update accounts approved by the maintainer may be exempted in CLA Assistant. A human-authored change is never exempt merely because a bot opened or updated the pull request.

By accepting the CLA, you cover eligible contributions associated with your authenticated GitHub account, including earlier contributions for which you hold the required rights. CLA Assistant requests renewed acceptance when the linked CLA Gist changes.
