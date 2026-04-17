# Nexus Docsite

This directory contains the standalone Nexus documentation site, following the same repo-owned Docusaurus model used in Limen.

## What Lives Here

- `package.json`: docsite dependencies and local run/build commands
- `docusaurus.config.js`: site configuration, navigation, search, and theme wiring
- `product-docs.json`: product metadata for the site shell
- `scripts/assemble-docs.mjs`: copies canonical repo markdown into `docs-site/.generated/docs`
- `src/css/custom.css`: site styling

## Canonical Source Model

The canonical documentation source remains the repository markdown:

- [README.md](../README.md)
- [docs/README.md](../docs/README.md)
- [docs/Developer/README.md](../docs/Developer/README.md)
- [docs/Developer/Documentation-System.md](../docs/Developer/Documentation-System.md)
- [docs/TechnicalDebt.md](../docs/TechnicalDebt.md)

The site build assembles those files into generated Docusaurus routes. Do not hand-edit `docs-site/.generated/docs`.

## Local Commands

Run these from `docs-site/`:

```bash
npm install
npm run prepare-docs
npm run start
```

For a static verification pass:

```bash
npm run check
```

## Deployment Notes

- `DOCS_BASE_URL` overrides the default `/nexus/` base path
- `DOCS_SITE_URL` overrides the default `https://docs.vaquum.fi` site URL
- broken internal links are configured to fail the build
