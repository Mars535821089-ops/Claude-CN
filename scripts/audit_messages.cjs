const fs = require('fs');
const acorn = require('acorn');
const { parse } = require('@formatjs/icu-messageformat-parser');

function features(nodes, strictSelections = false) {
  const args = new Set(), tags = new Set(), selections = [];
  const visit = list => {
    for (const node of list) {
      if ([1, 2, 3, 4, 5, 6].includes(node.type)) args.add(node.value);
      if (node.type === 8) tags.add(node.value);
      if (strictSelections && [5, 6].includes(node.type)) {
        selections.push(JSON.stringify({
          argument: node.value, type: node.type, pluralType: node.pluralType,
          offset: node.offset,
          choices: Object.keys(node.options).filter(
            choice => node.type === 5 || choice.startsWith('='),
          ).sort(),
        }));
      }
      if (node.options) Object.values(node.options).forEach(option => visit(option.value));
      if (node.children) visit(node.children);
    }
  };
  visit(nodes);
  return JSON.stringify({ args: [...args].sort(), tags: [...tags].sort(),
    ...(strictSelections ? { selections: selections.sort() } : {}) });
}

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = { catalogs: {}, changedJavaScript: 0, errors: [] };
for (const [name, { en, zh, strictSelections = false }] of Object.entries(input.catalogs)) {
  let count = 0;
  for (const [key, value] of Object.entries(en)) {
    try {
      if (features(parse(value), strictSelections) !== features(parse(zh[key]), strictSelections)) {
        throw new Error('ICU argument, selection or rich-text tag mismatch');
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
