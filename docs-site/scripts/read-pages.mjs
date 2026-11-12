import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import {content, hash, pageLabel} from '../reader/render.mjs';
import {documentTitle} from '../reader/headings.mjs';

export async function readPages(root) {
  const tracked = execFileSync('git', ['ls-files', '-z', '--', '*.md', 'examples/'], {cwd: root, encoding: 'utf8'})
    .split('\0')
    .filter(source => source === 'README.md' || source === 'CHANGELOG.md' ||
      source.startsWith('docs/') || /^nexus\/(?:.*\/)?README\.md$/.test(source) ||
      /^examples\/.+\.(?:md|py|ya?ml)$/.test(source));
  const settings = new Map(Object.entries(content.pages));

  const unmapped = tracked.filter(source => !settings.has(source));
  const untracked = [...settings.keys()].filter(source => !tracked.includes(source));
  if (unmapped.length || untracked.length) {
    throw Error('Reader source map differs from tracked documentation\n' +
      [...unmapped.map(source => 'Unmapped: ' + source), ...untracked.map(source => 'Untracked: ' + source)].join('\n'));
  }
  const routes = [...settings.values()].map(page => page.route);
  if (routes.includes('/reference/') || new Set(routes).size !== routes.length ||
      routes.some(route => !/^(?:\/[\w-]+)*\/$/.test(route))) {
    throw Error('Canonical reader routes must be unique absolute directory paths');
  }

  const pages = await Promise.all([...settings].map(async ([source, chosen]) => {
    const bytes = await fs.readFile(path.join(root, source));
    const raw = bytes.toString('utf8');
    const markdown = chosen.language
      ? `# ${chosen.title}\n\n~~~${chosen.language}\n${raw}${raw.endsWith('\n') ? '' : '\n'}~~~\n`
      : raw;
    const title = documentTitle(markdown);
    if (!title) throw Error('Missing document title: ' + source);
    return {source, ...chosen, url: content.product.basePath + chosen.route, title, markdown, sha256: hash(bytes)};
  }));

  const referenceGroups = new Map();
  for (const page of pages.filter(page => page.part === 'Reference' && page.listed !== false)
    .sort((a, b) => pageLabel(a).localeCompare(pageLabel(b)))) {
    const group = page.navigationGroup;
    if (!referenceGroups.has(group)) referenceGroups.set(group, []);
    referenceGroups.get(group).push(page);
  }
  for (const [group, entries] of Object.entries(content.referenceLinks)) {
    referenceGroups.set(group, [...(referenceGroups.get(group) ?? []), ...entries]);
  }
  const links = [...referenceGroups].map(([group, entries]) => '## ' + group + '\n\n' +
    entries.map(page => '- [' + pageLabel(page) + '](' + page.url + ')').join('\n')).join('\n\n');
  pages.push({
    source: null,
    route: '/reference/',
    url: content.product.basePath + '/reference/',
    part: 'Reference',
    title: 'Reference',
    markdown: '# Reference\n\n' + links + '\n'
  });
  return pages;
}
