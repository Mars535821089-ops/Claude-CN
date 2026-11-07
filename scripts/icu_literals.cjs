"use strict";

// Translate only ICU literal spans; selectors, branches, tags and arguments
// remain byte-for-byte in the original message until final syntax validation.
const fs = require("node:fs");
const { parse } = require("@formatjs/icu-messageformat-parser");

function literals(source) {
  const found = [];
  function visit(nodes) {
    for (const node of nodes) {
      if (node.type === 0 && /[\p{L}\p{N}]/u.test(node.value)) {
        const start = node.location?.start?.offset;
        const end = node.location?.end?.offset;
        if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) {
          throw new Error("invalid literal offsets");
        }
        found.push({ start, end, text: source.slice(start, end) });
      }
      if (node.options) for (const branch of Object.values(node.options)) visit(branch.value);
      if (node.children) visit(node.children);
    }
  }
  visit(parse(source, { captureLocation: true }));
  found.sort((a, b) => a.start - b.start);
  for (let i = 0; i < found.length; i++) {
    if (found[i].start < (found[i - 1]?.end ?? 0)) throw new Error("overlapping literal offsets");
  }
  return found;
}

function structure(nodes) {
  return nodes.map(node => {
    if (node.type === 0) return [0];
    if (node.options) return [node.type, node.value,
      Object.entries(node.options).map(([key, branch]) => [key, structure(branch.value)])];
    if (node.children) return [node.type, node.value, structure(node.children)];
    return [node.type, node.value, node.style ?? null];
  });
}

function compose(source, replacements) {
  const spans = literals(source);
  if (!Array.isArray(replacements) || replacements.length !== spans.length ||
      replacements.some(value => typeof value !== "string" || !value.trim())) {
    throw new Error("replacement count or type mismatch");
  }
  let text = source;
  for (let i = spans.length - 1; i >= 0; i--) {
    const { start, end } = spans[i];
    text = text.slice(0, start) + replacements[i] + text.slice(end);
  }
  if (JSON.stringify(structure(parse(source))) !== JSON.stringify(structure(parse(text)))) {
    throw new Error("ICU grammar changed");
  }
  return text;
}

if (require.main === module) {
  try {
    const request = JSON.parse(fs.readFileSync(0, "utf8"));
    if (!request || typeof request.source !== "string") throw new Error("source required");
    const result = request.operation === "segments"
      ? { segments: literals(request.source) }
      : request.operation === "compose"
        ? { text: compose(request.source, request.replacements) }
        : request.operation === "validate" && typeof request.translated === "string"
          ? { valid: JSON.stringify(structure(parse(request.source))) ===
              JSON.stringify(structure(parse(request.translated))) }
        : (() => { throw new Error("unsupported operation"); })();
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch (error) {
    console.error(`icu_literal_error=${error.name}`);
    process.exitCode = 1;
  }
}

module.exports = { literals, compose };
