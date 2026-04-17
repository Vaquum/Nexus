import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const repoRoot = path.resolve(path.dirname(scriptPath), '..', '..');
const siteRoot = path.resolve(repoRoot, 'docs-site');
const outRoot = path.resolve(siteRoot, '.generated', 'docs');

const repoBlobBaseUrl = 'https://github.com/Vaquum/Nexus/blob/main';

const sectionCategories = [
  {
    dir: 'overview',
    label: 'Overview',
    position: 1,
    slug: '/overview',
    description: 'What Nexus is, how it fits together, and where to start.'
  },
  {
    dir: 'guides',
    label: 'Guides',
    position: 2,
    slug: '/guides',
    description: 'End-to-end workflows for the Nexus decision layer.'
  },
  {
    dir: 'reference',
    label: 'Reference',
    position: 3,
    slug: '/reference',
    description: 'Detailed interface and subsystem reference for Nexus.'
  },
  {
    dir: 'developer',
    label: 'Developer',
    position: 4,
    slug: '/developer',
    description: 'Contributor, release, and documentation maintenance guides.'
  },
  {
    dir: 'packages',
    label: 'Packages',
    position: 5,
    slug: '/packages',
    description: 'Module ownership, boundaries, and canonical entry points.'
  }
];

const docs = [
  {
    source: 'README.md',
    dest: 'index.md',
    slug: '/',
    title: 'Nexus',
    sidebarLabel: 'Home'
  },
  {
    source: 'docs/README.md',
    dest: 'overview/docs-hub.md',
    slug: '/overview/docs-hub',
    sidebarPosition: 1
  },
  {
    source: 'docs/Manifest.md',
    dest: 'guides/manifest.md',
    slug: '/guides/manifest',
    sidebarPosition: 1
  },
  {
    source: 'docs/Signal-And-Strategy-Flow.md',
    dest: 'guides/signal-and-strategy-flow.md',
    slug: '/guides/signal-and-strategy-flow',
    sidebarPosition: 2
  },
  {
    source: 'docs/Validation-Pipeline.md',
    dest: 'guides/validation-pipeline.md',
    slug: '/guides/validation-pipeline',
    sidebarPosition: 3
  },
  {
    source: 'docs/Persistence-And-Recovery.md',
    dest: 'guides/persistence-and-recovery.md',
    slug: '/guides/persistence-and-recovery',
    sidebarPosition: 4
  },
  {
    source: 'docs/Startup-And-Shutdown.md',
    dest: 'guides/startup-and-shutdown.md',
    slug: '/guides/startup-and-shutdown',
    sidebarPosition: 5
  },
  {
    source: 'docs/Reference-Architecture.md',
    dest: 'reference/reference-architecture.md',
    slug: '/reference/reference-architecture',
    sidebarPosition: 1
  },
  {
    source: 'docs/Developer/README.md',
    dest: 'developer/developer-home.md',
    slug: '/developer/home',
    sidebarPosition: 1,
    sidebarLabel: 'Developer Home'
  },
  {
    source: 'docs/Developer/Documentation-System.md',
    dest: 'developer/documentation-system.md',
    slug: '/developer/documentation-system',
    sidebarPosition: 2
  },
  {
    source: 'docs/TechnicalDebt.md',
    dest: 'developer/technical-debt.md',
    slug: '/developer/technical-debt',
    sidebarPosition: 3,
    title: 'Technical Debt'
  },
  {
    source: 'docs-site/README.md',
    dest: 'developer/docsite-operations.md',
    slug: '/developer/docsite-operations',
    sidebarPosition: 4,
    title: 'Docsite Operations'
  }
];

const placeholderDocs = [
  {
    dest: 'guides/index.md',
    title: 'Guides',
    slug: '/guides/start-here',
    sidebarPosition: 1,
    sidebarLabel: 'Start Here',
    body: `# Guides

Nexus guide documentation is being assembled into the docsite now.

Current canonical sources:

- [Product Home](/)
- [Docs Hub](/overview/docs-hub)
- [Documentation System](/developer/documentation-system)
- [Technical Debt](/developer/technical-debt)

Planned guide slices include startup, manifest loading, validation pipeline flow, persistence and recovery, reconciliation, and shutdown sequencing.
`
  },
  {
    dest: 'reference/index.md',
    title: 'Reference',
    slug: '/reference/start-here',
    sidebarPosition: 1,
    sidebarLabel: 'Start Here',
    body: `# Reference

Nexus reference pages are planned but not yet split into standalone docs.

The current reference surface still lives in:

- [Reference Architecture](/reference/reference-architecture)
- [Technical Debt](/developer/technical-debt) for implementation gaps and integration boundaries
- [Documentation System](/developer/documentation-system) for terminology, page types, and information architecture
`
  },
  {
    dest: 'packages/index.md',
    title: 'Packages',
    slug: '/packages/start-here',
    sidebarPosition: 1,
    sidebarLabel: 'Start Here',
    body: `# Packages

Package-level orientation pages have not been added yet.

This section is reserved for package ownership and module-boundary docs described in the documentation-system contract.

Until then, use:

- [Developer Home](/developer/home)
- [Documentation System](/developer/documentation-system)
- the package structure under \`/nexus\`
`
  }
];

const mappingBySource = new Map(docs.map((doc) => [normalizePath(doc.source), doc]));

function normalizePath(value) {
  return value.split(path.sep).join('/');
}

async function ensureDir(dir) {
  await fs.mkdir(dir, {recursive: true});
}

async function writeJson(filePath, value) {
  await ensureDir(path.dirname(filePath));
  await fs.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function buildFrontMatter(doc) {
  const lines = ['---'];

  if (doc.slug) {
    lines.push(`slug: ${doc.slug}`);
  }

  if (doc.title) {
    lines.push(`title: ${doc.title}`);
  }

  if (typeof doc.sidebarPosition === 'number') {
    lines.push(`sidebar_position: ${doc.sidebarPosition}`);
  }

  if (doc.sidebarLabel) {
    lines.push(`sidebar_label: ${doc.sidebarLabel}`);
  }

  if (doc.dest === 'index.md') {
    lines.push('pagination_next: null');
    lines.push('pagination_prev: null');
  }

  if (doc.source) {
    lines.push(`custom_edit_url: ${repoBlobBaseUrl}/${doc.source}`);
  }

  lines.push('---', '');
  return lines.join('\n');
}

function resolveDocLink(fromSource, target) {
  if (
    !target ||
    target.startsWith('http://') ||
    target.startsWith('https://') ||
    target.startsWith('mailto:') ||
    target.startsWith('#')
  ) {
    return target;
  }

  const [targetPath, targetHash] = target.split('#');
  if (!targetPath) {
    return target;
  }

  if (targetPath.startsWith('/')) {
    return target;
  }

  const resolvedSource = normalizePath(
    path.posix.normalize(
      path.posix.join(path.posix.dirname(normalizePath(fromSource)), targetPath)
    )
  );

  let targetDoc = mappingBySource.get(resolvedSource);
  if (!targetDoc && !path.posix.extname(resolvedSource)) {
    targetDoc = mappingBySource.get(normalizePath(path.posix.join(resolvedSource, 'README.md')));
  }

  if (!targetDoc) {
    return target;
  }

  const currentDoc = mappingBySource.get(normalizePath(fromSource));
  const fromDest = normalizePath(currentDoc ? currentDoc.dest : '');
  const toDest = normalizePath(targetDoc.dest);
  let relative = normalizePath(path.posix.relative(path.posix.dirname(fromDest), toDest));

  if (!relative) {
    relative = path.posix.basename(toDest);
  }

  return targetHash ? `${relative}#${targetHash}` : relative;
}

function rewriteLinks(content, fromSource) {
  return content.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, target) => {
    const rewritten = resolveDocLink(fromSource, target.trim());
    return `[${label}](${rewritten})`;
  });
}

function rewriteOutsideCode(content, transform) {
  let out = '';
  let index = 0;
  let inFence = false;
  let plainStart = 0;

  while (index < content.length) {
    if (content.startsWith('```', index)) {
      if (plainStart < index) {
        out += transform(content.slice(plainStart, index));
      }
      inFence = !inFence;
      out += '```';
      index += 3;
      plainStart = index;
      continue;
    }

    index += 1;
  }

  if (plainStart < content.length) {
    out += transform(content.slice(plainStart));
  }

  return out;
}

function normalizeForMdx(content) {
  return rewriteOutsideCode(content, (chunk) =>
    chunk
      .replace(/<p align="center">([\s\S]*?)<\/p>/g, '<div align="center">$1</div>')
      .replace(/<br>/g, '<br />')
      .replace(/<hr>/g, '<hr />')
      .replace(/<img([^>]*?)(?<!\/)>/g, '<img$1 />')
  );
}

async function copyDoc(doc) {
  const sourcePath = path.resolve(repoRoot, doc.source);
  const destPath = path.resolve(outRoot, doc.dest);
  const raw = await fs.readFile(sourcePath, 'utf8');
  const rewritten = normalizeForMdx(rewriteLinks(raw, doc.source));
  const output = `${buildFrontMatter(doc)}${rewritten}`;

  await ensureDir(path.dirname(destPath));
  await fs.writeFile(destPath, output);
}

async function writePlaceholderDoc(doc) {
  const destPath = path.resolve(outRoot, doc.dest);
  await ensureDir(path.dirname(destPath));
  await fs.writeFile(destPath, `${buildFrontMatter(doc)}${doc.body}`);
}

async function writeCategoryFiles() {
  for (const category of sectionCategories) {
    const categoryPath = path.resolve(outRoot, category.dir, '_category_.json');
    await writeJson(categoryPath, {
      label: category.label,
      position: category.position,
      collapsible: true,
      collapsed: false,
      link: {
        type: 'generated-index',
        slug: category.slug,
        title: category.label,
        description: category.description
      }
    });
  }
}

async function main() {
  await fs.rm(outRoot, {recursive: true, force: true});
  await ensureDir(outRoot);
  await writeCategoryFiles();

  for (const doc of docs) {
    await copyDoc(doc);
  }

  for (const doc of placeholderDocs) {
    await writePlaceholderDoc(doc);
  }
}

await main();
