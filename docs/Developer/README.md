# Developer Home

This is the starting point for contributing to Nexus itself. Use it to find the right maintenance path before you change code, docs, or release metadata.

For cross-product Vaquum process and organization-wide norms, see the external [Vaquum Developer Docs](https://github.com/Vaquum/dev-docs/blob/main/src/README.md). Release process and versioning guidance now also live there. Use the pages below for Nexus-specific contribution and maintenance rules that still belong in this repo.

## Read This First

Before opening or updating a Nexus PR:

- read the relevant Nexus page for the task you are doing
- check the repo PR template and satisfy every applicable item
- update docs, changelog, tests, and version metadata when the change requires it

## Route By Task

| If you are doing this | Read this next | Why |
|---|---|---|
| changing docs structure, navigation, or page roles | [Documentation System Contract](Documentation-System.md) | Defines the docs architecture, page types, site model, and rewrite rules. |
| updating or adding public functions, classes, or modules | [Documentation System Contract](Documentation-System.md) | Nexus still routes doc-style guidance through the docs system contract until package-level contributor docs land. |
| checking current implementation gaps or deferred work | [Technical Debt](../TechnicalDebt.md) | Lists known limitations, stubs, and migration paths already tracked in this repo. |
| preparing a docs-site change or checking local docsite behavior | [Docsite Operations](../../docs-site/README.md) | Explains the repo-owned Docusaurus site, generated docs flow, and local run commands. |
| preparing a release or checking release automation | [Making a Release](https://github.com/Vaquum/dev-docs/blob/main/src/Making-Release.md) | Uses the shared Vaquum release process that the Nexus release workflow follows. |
| deciding how to bump the version | [Semantic Versioning](https://github.com/Vaquum/dev-docs/blob/main/src/Semantic-Versioning.md) | Uses the shared Vaquum versioning guidance rather than a repo-local copy. |

## Common Contributor Workflow

1. Understand the affected subsystem and read the canonical page for it.
2. Make the code change, doc change, or release metadata change together when they belong together.
3. Run the relevant validation locally.
4. Review the full GitHub diff yourself before requesting review.
5. Make sure the PR template items are genuinely true, not just checked.

## Scope Notes

- `/docs` is the canonical public docs layer.
- `/docs/Developer` is the canonical Nexus contributor layer.
- package `README`s under `/nexus` are orientation pages, not the main contributor process docs.

## Read Next

- [Documentation System Contract](Documentation-System.md)
- [Technical Debt](../TechnicalDebt.md)
- [Docsite Operations](../../docs-site/README.md)
- [Making a Release](https://github.com/Vaquum/dev-docs/blob/main/src/Making-Release.md)
- [Semantic Versioning](https://github.com/Vaquum/dev-docs/blob/main/src/Semantic-Versioning.md)
