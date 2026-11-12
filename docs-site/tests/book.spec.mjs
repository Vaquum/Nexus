import fs from 'node:fs/promises';
import {test, expect} from '@playwright/test';

test('book opens at the authored introduction', async ({page}) => {
  await page.goto('./#nexus');
  await expect(page.locator('.hero h1')).toHaveAttribute('id', 'nexus');
  await expect(page.locator('.hero-lead')).toContainText('Manifest-driven decision layer');
  await expect(page.locator('main article > :first-child')).toContainText('Nexus consumes prediction series');
});

test('book links open references and return to the same passage', async ({page}) => {
  await page.goto('./');
  await expect(page.getByRole('heading', {level: 1})).toHaveText('Nexus — Decision layer');
  const link = page.getByRole('link', {name: 'TechnicalDebt.md', exact: true});
  await link.scrollIntoViewIfNeeded();
  const before = await link.evaluate(node => node.getBoundingClientRect().top);
  await link.click();
  const reference = page.getByRole('dialog', {name: 'Reference'});
  await expect(reference.getByRole('heading', {level: 1})).toHaveText('Technical Debt');
  await reference.getByRole('button', {name: 'Return to chapter'}).click();
  await expect(reference).toBeHidden();
  await expect(link).toBeFocused();
  expect(Math.abs((await link.boundingBox()).y - before)).toBeLessThan(3);
});

test('reference code links keep the chapter and reference history entries', async ({page}) => {
  await page.goto('./#code-1-L1');
  const chapter = page.url();
  const opener = page.locator('main article').getByRole('link', {name: 'logreg_binary_evsfd.py', exact: true}).first();
  await opener.click();
  const reference = page.getByRole('dialog', {name: 'Reference'});
  await expect(reference.getByRole('heading', {level: 1})).toHaveText('Example Strategy');
  await expect(page).toHaveURL(/\/reference\/example-strategy\/$/);
  const referenceUrl = page.url();

  const code = reference.locator('figure.code');
  await code.getByRole('link', {name: 'Line 1', exact: true}).click();
  await expect(page).toHaveURL(referenceUrl + '#code-1-L1');
  await page.keyboard.press('Shift+ArrowDown');
  await expect(page).toHaveURL(referenceUrl + '#code-1-L1-L2');
  await expect(code.locator('.selected-line')).toHaveCount(2);
  await page.goBack();
  await expect(page).toHaveURL(referenceUrl + '#code-1-L1');
  await expect(code.locator('.selected-line')).toHaveCount(1);
  await page.goBack();
  await expect(page).toHaveURL(referenceUrl);
  await expect(code.locator('.selected-line')).toHaveCount(0);

  await reference.getByRole('button', {name: 'Return to chapter'}).click();
  await expect(reference).toBeHidden();
  await expect(page).toHaveURL(chapter);
  await expect(opener).toBeFocused();
});

test('search opens an authored reference passage', async ({page}) => {
  await page.goto('./');
  await page.getByRole('button', {name: 'Search', exact: true}).click();
  await page.getByRole('searchbox').fill('WAL codec lacks version-dispatched deserialization');
  await page.getByRole('link', {name: 'Technical Debt / TD-001: WAL codec lacks version-dispatched deserialization', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'TD-001: WAL codec lacks version-dispatched deserialization', exact: true})).toBeInViewport();
});

test('example code renders the tracked source and survives a code-range reload', async ({page, context}) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const examples = [
    ['./reference/example-manifest/', 'logreg_binary_evsfd.yaml'],
    ['./reference/example-strategy/', 'strategies/logreg_binary_evsfd.py']
  ];
  for (const [route, file] of examples) {
    await page.goto(route);
    const source = await fs.readFile(new URL('../../examples/' + file, import.meta.url), 'utf8');
    const figure = page.locator('figure.code');
    await expect(figure).toHaveAttribute('data-source', Buffer.from(source).toString('base64'));
    await figure.locator('.line-number').first().focus();
    await page.keyboard.press('Shift+ArrowDown');
    await expect(figure.locator('.selected-line')).toHaveCount(2);
    await page.reload();
    await expect(figure.locator('.selected-line')).toHaveCount(2);
    await figure.getByRole('button', {name: 'Copy', exact: true}).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(source);
  }
});

test('code-only examples resume the reading position without a fragment', async ({page}) => {
  await page.setViewportSize({width: page.viewportSize().width, height: 420});
  for (const route of ['./reference/example-manifest/', './reference/example-strategy/']) {
    await page.goto(route);
    await expect(page.getByRole('button', {name: 'Search', exact: true})).toBeVisible();
    await page.locator('main article figure.code').evaluate(figure => {
      const rect = figure.getBoundingClientRect();
      scrollTo({top: scrollY + rect.top + rect.height / 3, behavior: 'instant'});
    });
    await expect.poll(() => page.evaluate(() => {
      const key = 'reader:' + document.body.dataset.product + ':' + document.body.dataset.content;
      return JSON.parse(localStorage.getItem(key))?.passage;
    })).toBe('code-1');
    const before = await page.evaluate(() => scrollY);
    expect(before).toBeGreaterThan(0);
    await page.reload();
    expect(new URL(page.url()).hash).toBe('');
    await expect.poll(async () => Math.abs((await page.evaluate(() => scrollY)) - before)).toBeLessThan(3);
  }
});
