const fs = require('fs');
const acorn = require('acorn');
const { parse } = require('@formatjs/icu-messageformat-parser');

function features(nodes) {
  const args = new Set(), tags = new Set();
  const visit = list => {
    for (const node of list) {
      if ([1, 2, 3, 4, 5, 6].includes(node.type)) args.add(node.value);
      if (node.type === 8) tags.add(node.value);
      if (node.options) Object.values(node.options).forEach(option => visit(option.value));
      if (node.children) visit(node.children);
    }
  };
  visit(nodes);
  return JSON.stringify({ args: [...args].sort(), tags: [...tags].sort() });
}

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = { catalogs: {}, changedJavaScript: 0, errors: [] };
for (const [name, { en, zh }] of Object.entries(input.catalogs)) {
  let count = 0;
  for (const [key, value] of Object.entries(en)) {
    try {
      if (features(parse(value)) !== features(parse(zh[key]))) {
        throw new Error('ICU argument or rich-text tag mismatch');
      }
      count++;
    } catch (error) {
      result.errors.push({ catalog: name, id: key, error: error.message });
    }
  }
  result.catalogs[name] = count;
}
for (const [name, source] of Object.entries(input.scripts || {})) {
  try {
    acorn.parse(source, { ecmaVersion: 'latest', sourceType: 'module', allowHashBang: true });
    result.changedJavaScript++;
  } catch (error) {
    result.errors.push({ file: name, error: error.message });
  }
}
process.stdout.write(JSON.stringify(result, null, 2) + '\n');
if (result.errors.length) process.exitCode = 1;
