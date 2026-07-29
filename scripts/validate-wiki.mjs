import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";

const repoRoot = path.resolve(import.meta.dirname, "..");
const docsRoot = path.join(repoRoot, "docs");

async function walkMarkdown(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries
      .sort((left, right) => left.name.localeCompare(right.name))
      .map(async (entry) => {
        const absolute = path.join(directory, entry.name);
        if (entry.isDirectory()) return walkMarkdown(absolute);
        return entry.isFile() && entry.name.endsWith(".md") ? [absolute] : [];
      }),
  );
  return nested.flat();
}

function lineNumber(text, offset) {
  return text.slice(0, offset).split("\n").length;
}

function normalizeLink(rawLink) {
  let link = rawLink.trim();
  if (link.startsWith("<") && link.endsWith(">")) link = link.slice(1, -1);
  const titleSeparator = link.match(/\s+["']/);
  if (titleSeparator) link = link.slice(0, titleSeparator.index);
  try {
    return decodeURIComponent(link);
  } catch {
    return link;
  }
}

const markdownFiles = await walkMarkdown(docsRoot);
const failures = [];
let localLinkCount = 0;

for (const file of markdownFiles) {
  const relative = path.relative(repoRoot, file).replaceAll(path.sep, "/");
  const content = await fs.readFile(file, "utf8");

  if (!content.trim() || !content.replace(/^\s*#{1,6}.*$/gm, "").trim()) {
    failures.push(`${relative}: empty or heading-only document`);
  }
  if (content.includes("\uFFFD")) {
    failures.push(`${relative}: contains UTF-8 replacement character`);
  }

  const fenceCount = (content.match(/^\s*```/gm) ?? []).length;
  if (fenceCount % 2 !== 0) {
    failures.push(`${relative}: has an unclosed fenced code block`);
  }

  const suspiciousSecretPatterns = [
    /\bsk-[A-Za-z0-9_-]{20,}\b/g,
    /\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b/g,
    /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g,
  ];
  for (const pattern of suspiciousSecretPatterns) {
    const match = pattern.exec(content);
    if (match) failures.push(`${relative}:${lineNumber(content, match.index)}: possible secret material`);
  }

  const linkPattern = /(?<!!)\[[^\]]*\]\(([^)]+)\)/g;
  for (const match of content.matchAll(linkPattern)) {
    const link = normalizeLink(match[1]);
    if (!link || link.startsWith("#") || /^(?:https?:|mailto:|data:)/i.test(link)) continue;

    const withoutAnchor = link.split("#", 1)[0];
    if (!withoutAnchor) continue;
    localLinkCount += 1;
    const target = path.resolve(path.dirname(file), withoutAnchor.replaceAll("/", path.sep));
    try {
      await fs.access(target);
    } catch {
      failures.push(`${relative}:${lineNumber(content, match.index)}: broken local link '${link}'`);
    }
  }
}

if (failures.length) {
  console.error(`Wiki validation failed (${failures.length} issue(s)):`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Wiki validation passed: ${markdownFiles.length} Markdown files, ${localLinkCount} local links, ` +
      "0 broken links, 0 empty documents, 0 unclosed fences, 0 replacement characters, 0 secret signatures.",
  );
}
