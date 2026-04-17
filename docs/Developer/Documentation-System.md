# Documentation System Contract

## Purpose

This page defines how Nexus documentation should be structured, written, built, and improved. It is the operating contract for the docs overhaul and the reference point for later documentation changes.

The goal is not only to improve individual pages. The goal is to make Nexus documentation behave like one coherent product.

## What 10/10 Means

For Nexus, 10/10 docs means the full system is:

- accurate to the code and current Vaquum architecture
- easy to enter for a new user
- deep enough for serious integration and contributor work
- coherent across product pages, reference pages, and package README files
- grounded in real validation, persistence, startup, shutdown, and execution flows
- ready to power a standalone Nexus docs site now and a Vaquum docs portal later

## Product Docs Model

### Current Site Direction

Nexus should get its own standalone docs site from this repository.

- The initial site build target is Docusaurus.
- The site should be built in this repository.
- The site should deliver a complete Nexus-only docs experience.
- The site should work as a standalone product site first.
- The site should later be portable into a broader Vaquum docs system without rewriting the content model.
- Canonical content should remain owned by repository markdown, not by site-only copies of the docs.

### Future Vaquum Docs Direction

The long-term target is `docs.vaquum.fi` as the Vaquum documentation entry point.

- `docs.vaquum.fi` should present all Vaquum product docs: Origo, Limen, Nexus, Praxis, and Veritas.
- Each product should still feel discrete, self-contained, and product-native.
- The first Vaquum-wide version should behave like a portal to product docs, not like one merged blob of markdown.
- The Nexus docs system should therefore be designed to plug into a future Vaquum docs shell without losing its own identity.

## Canonical Source Rules

Nexus documentation should have one clear ownership model.

- [README.md](../../README.md) is the product home page and primary entry point.
- [docs/README.md](../README.md) is the canonical public docs hub.
- `/docs` is the canonical source for public concepts, workflows, boundaries, and reference pages.
- `/docs/Developer` is the canonical source for contributor and maintainer process docs.
- package `README` files under `/nexus` are orientation pages for module ownership and boundaries, not the main public reference.
- examples should be derived from real runnable flows in this repository, not imagined pseudo-usage.

Content should be authored once whenever possible. If the same explanation appears in multiple places, one page should be canonical and the others should route to it.

## Information Architecture

The docs site should be organized into five top-level sections:

- `Overview`
- `Guides`
- `Reference`
- `Developer`
- `Packages`

The source files can remain in their current repository layout, but the built site should present them through this information architecture.

### Section Responsibilities

- `Overview` explains what Nexus is, what it is not, and how it fits into the wider Vaquum architecture.
- `Guides` teach workflows such as startup, validation, action handling, persistence, reconciliation, and shutdown from start to finish.
- `Reference` documents interfaces, manifest conventions, state models, validation stages, and connector contracts.
- `Developer` documents contribution, release, maintenance, and internal documentation rules.
- `Packages` explains module ownership, boundaries, entry points, and where to read next.

## Narrative Spine

Every major public page should reinforce the same core Nexus story:

1. Limen or another upstream source produces signals or strategy intent.
2. Nexus accepts strategy actions inside one per-account Manager instance.
3. Nexus applies deterministic intake, risk, price, capital, health, and platform validation.
4. Allowed actions reserve capital atomically, update tracked lifecycle state, and translate into Praxis trade commands.
5. Praxis returns outcomes that update capital, positions, risk, and runtime state.
6. Nexus persists state through write-ahead logging, snapshots, replay, and crash recovery.
7. Startup, reconciliation, and shutdown keep the Manager instance recoverable and operationally safe.
8. Oversight and broader audit responsibilities still sit outside Nexus in the wider Vaquum stack.

If a page does not help a reader understand its place in that story, it should route clearly to the pages that do.

## Register And Writing Rules

All Nexus documentation should use the same register:

- precise
- technical
- concise
- accessible to an informed new user
- direct rather than academic
- product-truthful rather than hype-driven

### Writing Rules

- Start with what the thing is and why a reader would use it.
- Prefer concrete behavior over abstract framing.
- Keep theory only where it directly improves practical understanding.
- Explain current surface area honestly; do not imply future behavior as present behavior.
- Prefer examples that show inputs, outputs, and runtime artefacts.
- Do not use unexplained internal jargon.
- Do not duplicate large sections of content across pages.
- End pages with explicit reading routes or next steps when useful.

## Page Types And Required Blocks

Every page should fit one primary page type.

### Home Page

Purpose: product framing and system boundary.

Required blocks:

- what Nexus is
- what Nexus is not
- capability summary
- architecture position between upstream signal generation and downstream execution
- clear routes into the rest of the docs

### Docs Hub

Purpose: route readers by task and audience.

Required blocks:

- system overview
- reading order by user type
- high-level architecture map
- explicit routes into guides, reference, developer docs, and package docs

### Guide

Purpose: teach a job or workflow from start to finish.

Required blocks:

- what this guide covers
- prerequisites
- current scope
- at least one concrete example
- expected outputs or state changes
- related pages or next steps

### Reference

Purpose: document an interface or surface comprehensively and predictably.

Required blocks:

- short intro and scope
- conventions or naming rules
- structured entry documentation
- state transitions, return behavior, or output fields where relevant
- edge cases or caveats where relevant

### Developer Page

Purpose: guide contributors and maintainers.

Required blocks:

- page purpose
- required reading or prerequisites
- process or checklist
- failure cases or review notes where relevant
- linked related maintenance pages

### Package README

Purpose: orient readers inside a module without replacing canonical public docs.

Required blocks:

- what the package owns
- what it does not own
- key entry points
- major dependencies or adjacent modules
- link to canonical public docs

## Navigation And Cross-Link Rules

Navigation should reduce guesswork.

- The home page and docs hub must both provide reading paths by task.
- Large pages should be indexed near the top.
- Public workflow pages should link forward through the narrative spine.
- Package README files should link outward to canonical docs rather than trying to become their own mini-sites.
- Cross-links should prefer the next page a reader should open, not every vaguely related page.

## Terminology Rules

Use one terminology set across the whole docs system.

- Product name: `Nexus`
- Runtime owner: `Manager` or `Manager instance`
- Upstream research and signal layer: `Limen`
- Downstream execution layer: `Praxis`
- Public strategy intent surface: `Action`
- Persistence primitives: `write-ahead log`, `WAL`, `snapshot`, `replay`
- Safety modes: `ACTIVE`, `REDUCE_ONLY`, `HALTED`

### Naming Rules

- Use `Manager` consistently for the per-account runtime instance.
- Treat `Manager` as an architectural runtime concept, not the name of one concrete class. In the current codebase it is expressed through `InstanceConfig`, `InstanceState`, lifecycle controllers, validation, and startup or shutdown sequencing working together.
- Do not describe Limen research logic as part of Nexus.
- Do not describe Praxis venue execution logic as if it lives inside Nexus.
- Do not describe Veritas oversight responsibilities as if they are owned by Nexus.
- Use `capital_pool`, `allocated_capital`, and strategy `capital_pct` with their code-level meanings rather than as loose synonyms.

## Example And Artefact Rules

Examples should be operationally real.

- Prefer examples that can be run locally in this repository.
- Prefer examples validated against actual Nexus code paths and tests.
- When an example depends on stubs, missing integrations, or known limitations, say so explicitly.
- Show state changes, WAL records, snapshots, trade commands, or outcomes where they are important to understanding the workflow.
- Avoid examples that imply execution guarantees that the current code does not yet provide.

## Site Build Rules

These rules should guide the site build implemented in the next slice.

- Nexus should get a standalone docs site built in this repository.
- The initial implementation should use Docusaurus.
- The site should support local development and static build.
- The site should support both standalone deployment and later subpath deployment.
- The site base URL should be environment-driven so the same content can support both modes.
- Search should work across the full Nexus docs corpus.
- Broken internal links should fail the build.
- The site navigation should reflect the five top-level sections in this contract.

## Future Vaquum Portal Contract

The Nexus docs system should expose a minimal product-docs contract that can later be consumed by a Vaquum-wide docs portal.

That contract should include:

- product id
- product name
- short product tagline
- current docs version label
- deployment base path
- primary navigation sections
- source repository URL

This does not mean Nexus should wait for a Vaquum-wide shell. Nexus should be excellent as a standalone docs product first.

## Rewrite Slices

The overhaul should be tracked in the following order.

| Slice | Name | Scope | Definition of Done |
| --- | --- | --- | --- |
| 1 | Docs System Contract | Define structure, voice, page types, ownership, navigation model, site boundary, and rewrite slices. | This contract exists, is linked from developer docs, and is accepted as the operating manual for later slices. |
| 2 | Docs-Site Build | Add the site build, docs assembly model, product metadata, local dev/build/check commands, and navigation shell. | The Nexus docs site builds locally, renders the current corpus, and enforces link integrity. |
| 3 | Top-Level Narrative | Rewrite the product home and docs hub so Nexus has one clear entry story and reading flow. | A new reader can enter the docs without guessing what to read next. |
| 4 | Core Workflow Guides | Rewrite startup, manifest, validation, action flow, persistence, reconciliation, and shutdown pages as one connected workflow layer. | A reader can understand how intent becomes execution-safe commands using only the guide layer. |
| 5 | Runtime State And Outcomes | Rewrite capital, risk, health, order lifecycle, trade outcomes, and recovery pages. | A reader can understand what Nexus stores, updates, and recovers after live operation. |
| 6 | Reference Layer | Rewrite Manager, manifest, validation, connector, and persistence reference pages as coordinated technical references. | The reference layer is scannable, consistent, and trusted. |
| 7 | Developer Layer | Rewrite contributor, release, and versioning docs around current practice. | A contributor can follow the maintenance workflow without relying on tribal knowledge. |
| 8 | Package README Alignment | Align package README files with the same contract and route them to canonical docs. | Package README files feel like part of one system rather than isolated notes. |
| 9 | Final Cohesion Pass | Sweep the full corpus for terminology, duplication, examples, links, navigation, and consistency. | The docs read as one coherent product and meet the 10/10 acceptance bar. |

## Acceptance Bar For The Whole Overhaul

The overhaul should be considered complete when all of the following are true:

- a new user can understand where Nexus sits between upstream signals and downstream execution without guessing
- a serious user can understand the action validation and capital-control path well enough to integrate or extend it
- a contributor can find the canonical page for any subsystem quickly
- examples are grounded in real Nexus state transitions, persistence flows, and integration boundaries
- large reference pages are easy to navigate and trustworthy
- the standalone Nexus docs site feels complete
- the same docs system can later plug into a Vaquum-wide docs portal without conceptual rework

## How To Use This Page

Before rewriting any major docs slice:

- confirm the target page type
- confirm where the page sits in the narrative spine
- confirm whether the page is canonical or secondary
- confirm which slice the change belongs to

If a proposed docs change conflicts with this contract, update this page first or explicitly document the exception.
