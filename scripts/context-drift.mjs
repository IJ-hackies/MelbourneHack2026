#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, '..');
const chunksRoot = path.join(repositoryRoot, 'Context', 'Chunks');
const lineCap = 150;

const excludedChunkNames = new Set(['STATE.MD', 'CONVENTIONS.MD', 'INDEX.MD']);

function displayPath(filePath) {
  return path.relative(repositoryRoot, filePath).split(path.sep).join('/');
}

function chunkIdForPath(filePath) {
  return displayPath(filePath)
    .replace(/^Context\/Chunks\//, '')
    .replace(/\.md$/i, '');
}

function physicalLines(text) {
  const lines = text.split(/\r?\n/);
  if (lines.at(-1) === '') {
    lines.pop();
  }
  return lines;
}

function stripInlineComment(value) {
  let quote = null;

  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];

    if (quote === null && (character === '"' || character === "'")) {
      quote = character;
      continue;
    }

    if (quote !== null && character === quote) {
      if (quote === "'" && value[index + 1] === "'") {
        index += 1;
      } else if (quote === '"' && value[index - 1] !== '\\') {
        quote = null;
      } else if (quote === "'") {
        quote = null;
      }
      continue;
    }

    if (quote === null && character === '#') {
      return value.slice(0, index).trimEnd();
    }
  }

  return value;
}

function parseScalar(rawValue) {
  const value = stripInlineComment(rawValue.trim());

  if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
    try {
      return JSON.parse(value);
    } catch {
      return value.slice(1, -1);
    }
  }

  if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
    return value.slice(1, -1).replace(/''/g, "'");
  }

  return value;
}

function splitInlineList(value) {
  const inner = value.slice(1, -1).trim();
  if (inner === '') {
    return [];
  }

  const entries = [];
  let entryStart = 0;
  let quote = null;

  for (let index = 0; index < inner.length; index += 1) {
    const character = inner[index];

    if (quote === null && (character === '"' || character === "'")) {
      quote = character;
    } else if (quote !== null && character === quote) {
      if (quote === "'" && inner[index + 1] === "'") {
        index += 1;
      } else if (quote === '"' && inner[index - 1] !== '\\') {
        quote = null;
      } else if (quote === "'") {
        quote = null;
      }
    } else if (quote === null && character === ',') {
      entries.push(parseScalar(inner.slice(entryStart, index)));
      entryStart = index + 1;
    }
  }

  entries.push(parseScalar(inner.slice(entryStart)));
  return entries;
}

function parseValue(rawValue, errors, key) {
  const value = stripInlineComment(rawValue.trim());

  if (value.startsWith('[')) {
    if (!value.endsWith(']')) {
      errors.push(`${key} has an unterminated inline list`);
      return null;
    }
    return splitInlineList(value);
  }

  if (value === 'null' || value === '~') {
    return null;
  }

  return parseScalar(value);
}

function parseFrontmatter(content) {
  const lines = physicalLines(content);
  const errors = [];

  if (lines[0]?.trim() !== '---') {
    return { fields: {}, errors: ['missing opening frontmatter delimiter'] };
  }

  let closingIndex = -1;
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index].trim() === '---') {
      closingIndex = index;
      break;
    }
  }

  if (closingIndex === -1) {
    return { fields: {}, errors: ['missing closing frontmatter delimiter'] };
  }

  const fields = {};
  const fieldLines = lines.slice(1, closingIndex);

  for (let index = 0; index < fieldLines.length;) {
    const line = fieldLines[index];

    if (line.trim() === '' || line.trim().startsWith('#')) {
      index += 1;
      continue;
    }

    const fieldMatch = line.match(/^([A-Za-z][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$/);
    if (!fieldMatch) {
      errors.push(`cannot parse frontmatter line ${index + 2}`);
      index += 1;
      continue;
    }

    const [, key, rawValue = ''] = fieldMatch;
    if (Object.prototype.hasOwnProperty.call(fields, key)) {
      errors.push(`duplicate frontmatter field ${key}`);
    }

    if (rawValue.trim() !== '') {
      fields[key] = parseValue(rawValue, errors, key);
      index += 1;
      continue;
    }

    const list = [];
    let sawListItem = false;
    let nextIndex = index + 1;

    while (nextIndex < fieldLines.length) {
      const nextLine = fieldLines[nextIndex];

      if (nextLine.trim() === '' || nextLine.trim().startsWith('#')) {
        nextIndex += 1;
        continue;
      }

      if (/^[A-Za-z][A-Za-z0-9_-]*:/.test(nextLine)) {
        break;
      }

      const itemMatch = nextLine.match(/^\s*-\s*(.*)$/);
      if (!itemMatch) {
        errors.push(`frontmatter field ${key} must be a list`);
        nextIndex += 1;
        continue;
      }

      sawListItem = true;
      list.push(parseScalar(itemMatch[1]));
      nextIndex += 1;
    }

    fields[key] = sawListItem ? list : null;
    index = nextIndex;
  }

  return { fields, errors };
}

function listNormalChunks() {
  if (!fs.existsSync(chunksRoot)) {
    return [];
  }

  const files = [];
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);

      if (entry.isDirectory()) {
        visit(entryPath);
        continue;
      }

      if (!entry.isFile() || !entry.name.toLowerCase().endsWith('.md')) {
        continue;
      }

      if (excludedChunkNames.has(entry.name.toUpperCase())) {
        continue;
      }

      files.push(entryPath);
    }
  };

  visit(chunksRoot);
  return files.sort((left, right) => displayPath(left).localeCompare(displayPath(right)));
}

function isRepoRelativeFilePath(value) {
  if (value.length === 0 || value.startsWith('/') || /^[A-Za-z]:[\\/]/.test(value)) {
    return false;
  }

  const normalized = value.replaceAll('\\', '/');
  return !normalized.split('/').includes('..');
}

function sourcePathOnDisk(source) {
  if (!isRepoRelativeFilePath(source)) {
    return null;
  }

  const sourcePath = path.resolve(repositoryRoot, ...source.replaceAll('\\', '/').split('/'));
  const relative = path.relative(repositoryRoot, sourcePath);
  if (relative === '' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    return null;
  }

  return sourcePath;
}

function runGit(args, options = {}) {
  return spawnSync('git', args, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    stdio: options.capture ? ['ignore', 'pipe', 'pipe'] : 'ignore',
    windowsHide: true,
  });
}

function inspectGit() {
  const versionResult = runGit(['--version']);
  if (versionResult.error || versionResult.status !== 0) {
    return { available: false, hasHead: false, head: null };
  }

  const headResult = runGit(['rev-parse', '--verify', 'HEAD'], { capture: true });
  if (headResult.status !== 0) {
    return { available: true, hasHead: false, head: null };
  }

  return {
    available: true,
    hasHead: true,
    head: headResult.stdout.trim(),
  };
}

function commitExists(commit) {
  const result = runGit(['rev-parse', '--verify', `${commit}^{commit}`], { capture: true });
  return result.status === 0;
}

function gitPathChangedSince(commit, sourcePaths) {
  const sourceArgs = sourcePaths.map((sourcePath) => displayPath(sourcePath));
  const committed = runGit(['diff', '--quiet', `${commit}..HEAD`, '--', ...sourceArgs]);
  if (committed.status !== 0 && committed.status !== 1) {
    return { changed: false, unavailable: true };
  }

  if (committed.status === 1) {
    return { changed: true, reason: 'committed changes since verified commit' };
  }

  const workingTree = runGit(['diff', '--quiet', 'HEAD', '--', ...sourceArgs]);
  if (workingTree.status !== 0 && workingTree.status !== 1) {
    return { changed: false, unavailable: true };
  }

  if (workingTree.status === 1) {
    return { changed: true, reason: 'uncommitted changes' };
  }

  const status = runGit(['status', '--short', '--untracked-files=all', '--', ...sourceArgs], { capture: true });
  if (status.status !== 0) {
    return { changed: false, unavailable: true };
  }

  if (status.stdout.trim() !== '') {
    return { changed: true, reason: 'untracked source changes' };
  }

  return { changed: false };
}

function validateSource(source) {
  if (typeof source !== 'string' || source.trim() === '') {
    return 'source entries must be non-empty strings';
  }

  const sourcePath = sourcePathOnDisk(source);
  if (sourcePath === null) {
    return `source path is not repo-relative: ${source}`;
  }

  if (!fs.existsSync(sourcePath)) {
    return `source does not exist: ${source}`;
  }

  if (!fs.statSync(sourcePath).isFile()) {
    return `source is not a file: ${source}`;
  }

  return null;
}

function validateChunk(chunk, chunksById, git) {
  const findings = [];
  const fields = chunk.frontmatter.fields;
  const expectedId = chunkIdForPath(chunk.filePath);

  if (typeof fields.id !== 'string' || fields.id.trim() === '') {
    findings.push('missing id');
  } else if (fields.id !== expectedId) {
    findings.push(`id must be ${expectedId}`);
  }

  if (typeof fields.title !== 'string' || fields.title.trim() === '') {
    findings.push('missing title');
  }

  if (!Array.isArray(fields.sources) || fields.sources.length === 0) {
    findings.push('sources must be a non-empty list');
  } else {
    for (const source of fields.sources) {
      const sourceFinding = validateSource(source);
      if (sourceFinding !== null) {
        findings.push(sourceFinding);
      }
    }
  }

  if (!Array.isArray(fields.links)) {
    findings.push('links must be a list');
  } else {
    for (const link of fields.links) {
      if (typeof link !== 'string' || link.trim() === '') {
        findings.push('link entries must be non-empty strings');
      } else if (!chunksById.has(link)) {
        findings.push(`link target does not exist: ${link}`);
      }
    }
  }

  if (typeof fields.verified !== 'string' || fields.verified.trim() === '') {
    findings.push('missing verified');
  } else if (fields.verified !== 'initial' && !/^[0-9a-f]{4,40}$/i.test(fields.verified)) {
    findings.push(`verified must be a git hash or initial: ${fields.verified}`);
  }

  findings.push(...chunk.frontmatter.errors);

  const oversize = chunk.lineCount > lineCap;
  let freshness = { status: 'fresh' };

  if (findings.length === 0 && Array.isArray(fields.sources)) {
    const sourcePaths = fields.sources
      .map((source) => sourcePathOnDisk(source))
      .filter((sourcePath) => sourcePath !== null);

    if (fields.verified === 'initial') {
      freshness = {
        status: 'unverified',
        reason: git.hasHead
          ? 'freshness unavailable (verified: initial; no commit baseline)'
          : 'freshness unavailable (no Git HEAD; verified: initial)',
      };
    } else if (!git.available) {
      freshness = { status: 'unverified', reason: 'freshness unavailable (Git is not installed)' };
    } else if (!git.hasHead) {
      findings.push(`verified commit cannot be checked without a Git HEAD: ${fields.verified}`);
    } else if (!commitExists(fields.verified)) {
      findings.push(`verified commit does not exist: ${fields.verified}`);
    } else {
      const changed = gitPathChangedSince(fields.verified, sourcePaths);
      if (changed.unavailable) {
        freshness = { status: 'unverified', reason: 'freshness unavailable (git diff failed)' };
      } else if (changed.changed) {
        freshness = { status: 'stale', reason: changed.reason };
      }
    }
  }

  return {
    findings,
    freshness,
    oversize,
  };
}

function main() {
  const chunkFiles = listNormalChunks();
  if (chunkFiles.length === 0) {
    console.log('context:drift — no normal chunk documents found under Context/Chunks.');
    console.log('Summary: 0 fresh, 0 stale, 0 invalid, 0 oversize, 0 unverified');
    return 0;
  }

  const chunks = chunkFiles.map((filePath) => {
    const content = fs.readFileSync(filePath, 'utf8');
    return {
      filePath,
      content,
      lineCount: physicalLines(content).length,
      frontmatter: parseFrontmatter(content),
    };
  });

  const chunksById = new Map();
  for (const chunk of chunks) {
    const id = chunk.frontmatter.fields.id;
    if (typeof id === 'string' && id.trim() !== '') {
      if (chunksById.has(id)) {
        chunksById.get(id).duplicate = true;
        chunk.duplicate = true;
      } else {
        chunksById.set(id, chunk);
      }
    }
  }

  for (const chunk of chunks) {
    if (chunk.duplicate) {
      chunk.frontmatter.errors.push(`duplicate chunk id: ${chunk.frontmatter.fields.id}`);
    }
  }

  const git = inspectGit();
  const results = chunks.map((chunk) => ({
    chunk,
    ...validateChunk(chunk, chunksById, git),
  }));

  const summary = {
    fresh: 0,
    stale: 0,
    invalid: 0,
    oversize: 0,
    unverified: 0,
  };

  for (const result of results) {
    const labels = [];
    const details = [...result.findings];

    if (result.findings.length > 0) {
      labels.push('INVALID');
      summary.invalid += 1;
    } else if (result.freshness.status === 'stale') {
      labels.push('STALE');
      details.push(result.freshness.reason);
      summary.stale += 1;
    } else if (result.freshness.status === 'unverified') {
      labels.push('UNVERIFIED');
      details.push(result.freshness.reason);
      summary.unverified += 1;
    } else {
      labels.push('FRESH');
      summary.fresh += 1;
    }

    if (result.oversize) {
      labels.push('OVERSIZE');
      details.push(`${result.chunk.lineCount} lines > ${lineCap}`);
      summary.oversize += 1;
    }

    const detailText = details.length > 0 ? ` — ${details.join('; ')}` : '';
    console.log(`${labels.join(', ')} ${displayPath(result.chunk.filePath)}${detailText}`);
  }

  console.log(
    `Summary: ${summary.fresh} fresh, ${summary.stale} stale, ${summary.invalid} invalid, ` +
      `${summary.oversize} oversize, ${summary.unverified} unverified`,
  );

  return summary.invalid > 0 || summary.stale > 0 || summary.oversize > 0 ? 1 : 0;
}

process.exitCode = main();
