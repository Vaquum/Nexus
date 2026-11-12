# Nexus docs site

Nexus uses the Limen static reader as shipped in Vaquum/Praxis at `05d2e02`. The reader uses the repository README, changelog, docs tree and strategy example files. `reader/content.json` maps these sources to the Book, Reference and Developer pages. The Reference also links to the shared Praxis and Nexus wiki.

Run these commands from `docs-site` with Node.js 22.

```sh
npm ci
npm start
```

Open [the Nexus book](http://127.0.0.1:4330/nexus/). Run `npm run build` after editing a source. Use `DOCS_BASE_URL=/ npm start` for a root-mounted preview and `READER_PORT` to choose a port.

```sh
npm run check
npm run security:audit
npx playwright install chromium webkit
npm run check:reader
```

The build writes static assets to `reader-build/`. `routes.json` records each source and its SHA-256 digest. `build.json` records the checkout commit and the generated file hashes. `npm run check` verifies source coverage, links and search against the built pages. The browser checks exercise the reader on desktop and mobile using the same asset routing and headers as `src/worker.js`.
