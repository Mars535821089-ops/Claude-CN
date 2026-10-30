const acorn = require('acorn');
const readline = require('node:readline');

const UI_KEYS = new Set([
  'children', 'label', 'title', 'description', 'subtitle', 'placeholder',
  'tooltip', 'tooltipText', 'helpText', 'helperText', 'emptyText', 'buttonText',
  'text', 'message', 'aria-label', 'aria-description', 'alt', 'detail',
  'buttons', 'confirmLabel', 'cancelLabel', 'loadingText', 'errorMessage',
  'withClaude', 'heading', 'subheading', 'hint', 'caption', 'successMessage',
  'checkboxLabel',
  'searchLanguages', 'noLanguagesFound', 'oneLanguageFound', 'nLanguagesFound',
]);

function propertyName(node) {
  if (!node || node.type !== 'Property') return null;
  return node.computed && node.key.type !== 'Literal'
    ? null
    : node.key.name ?? node.key.value;
}

function englishText(text) {
  return /[A-Za-z]{2}/.test(text) && !/[\u3400-\u9fff]/u.test(text);
}

function scan(source, filename) {
  const tree = acorn.parse(source, {
    ecmaVersion: 'latest', sourceType: 'module', allowHashBang: true,
    allowReturnOutsideFunction: true,
  });
  const findings = [];
  const actions = [];
  const stack = [{ node: tree, ancestors: [] }];
  while (stack.length) {
    const { node, ancestors } = stack.pop();
    const parent = ancestors.at(-1);
    if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier'
      && node.init?.type === 'ConditionalExpression'
      && node.init.test.type === 'BinaryExpression' && node.init.test.operator === '==='
      && node.init.test.right.value === 'read'
      && node.init.consequent.type === 'Literal' && node.init.consequent.value === 'preview'
      && node.init.alternate.type === 'Literal' && node.init.alternate.value === 'open') {
      const scope = ancestors.findLast(item => item.type === 'BlockStatement');
      if (scope) actions.push({ node, scope });
    }
    let value;
    if (node.type === 'Literal' && typeof node.value === 'string') value = node.value;
    if (node.type === 'TemplateLiteral') {
      value = node.quasis.map(part => part.value.cooked ?? part.value.raw).join('{…}');
    }
    if (value && englishText(value) && value.length < 3000) {
      let context = null;
      // Only trace value-bearing expressions, never function bodies or call arguments
      // back to a surrounding UI prop (e.g. handlers, formatMessage descriptors).
      for (let index = ancestors.length - 1; index >= 0; index--) {
        const ancestor = ancestors[index];
        const child = index === ancestors.length - 1 ? node : ancestors[index + 1];
        if (ancestor.type === 'ConditionalExpression' && ancestor.test === child) break;
        if (ancestor.type === 'LogicalExpression' && ancestor.left === child) break;
        if (ancestor.type === 'BinaryExpression' && ancestor.operator !== '+') break;
        if (ancestor.type === 'Property') {
          if (ancestor.key === node) break;
          const key = propertyName(ancestor);
          const owner = ancestors[index - 1];
          if (key === 'description' && owner?.type === 'ObjectExpression'
            && owner.properties.some(prop => propertyName(prop) === 'defaultMessage')) break;
          if (UI_KEYS.has(key)) context = key;
          if (['message', 'detail', 'body'].includes(key) && owner?.type === 'ObjectExpression') {
            const fields = owner.properties.map(propertyName);
            if (fields.includes('title') && (fields.includes('buttons')
              || (fields.includes('body') && fields.includes('silent')))) {
              context = `dialog:${key}`;
            }
          }
          break;
        }
        if (!['ArrayExpression', 'ConditionalExpression', 'LogicalExpression',
          'BinaryExpression', 'TemplateLiteral'].includes(ancestor.type)) break;
      }
      if (parent?.type === 'CallExpression') {
        const callee = parent.callee;
        const method = callee.type === 'MemberExpression'
          ? callee.property.name ?? callee.property.value : callee.name;
        if (['alert', 'confirm', 'setTitle', 'setToolTip'].includes(method)
          && parent.arguments[0] === node) context = method;
        if (method === 'setAttribute' && parent.arguments[1] === node
          && ['aria-label', 'title', 'placeholder', 'alt'].includes(parent.arguments[0]?.value)) {
          context = `setAttribute:${parent.arguments[0].value}`;
        }
        if (method === 'createElement' && parent.arguments.indexOf(node) >= 2) {
          context = 'createElement:children';
        }
      }
      if (parent?.type === 'AssignmentExpression' && parent.right === node
        && parent.left.type === 'MemberExpression'
        && ['textContent', 'innerText', 'placeholder', 'title'].includes(parent.left.property.name)) {
        context = parent.left.property.name;
      }
      if (context) {
        findings.push({
          file: filename, start: node.start, end: node.end, context, text: value,
          kind: node.type,
          ...(node.type === 'TemplateLiteral'
            ? { expressions: node.expressions.map(expression => source.slice(expression.start, expression.end)) }
            : {}),
          ...(node.type === 'TemplateLiteral'
            ? { quasis: node.quasis.map(part => ({ start: part.start, end: part.end })) }
            : {}),
          snippet: source.slice(Math.max(0, node.start - 110), Math.min(source.length, node.end + 110)),
        });
      }
    }
    for (const [key, child] of Object.entries(node)) {
      if (key === 'start' || key === 'end') continue;
      const nodes = Array.isArray(child) ? child : [child];
      for (const item of nodes) {
        if (item && typeof item === 'object' && typeof item.type === 'string') {
          stack.push({ node: item, ancestors: [...ancestors, node] });
        }
      }
    }
  }
  for (const { node: declaration, scope } of actions) {
    let safe = true, rendered = 0;
    const pending = [{ node: scope, parent: null, grandparent: null }];
    while (pending.length && safe) {
      const { node, parent, grandparent } = pending.pop();
      if (node.type === 'Identifier' && node.name === declaration.id.name
        && node !== declaration.id
        && !(parent?.type === 'MemberExpression' && parent.property === node && !parent.computed)
        && !(parent?.type === 'Property' && parent.key === node && !parent.computed && !parent.shorthand)) {
        const template = parent?.type === 'TemplateLiteral' && parent.expressions.includes(node);
        const visible = template && findings.some(item => item.start === parent.start
          && ['dialog:message', 'dialog:detail'].includes(item.context));
        const logged = template && grandparent?.type === 'CallExpression'
          && grandparent.arguments.includes(parent) && grandparent.callee.type === 'MemberExpression'
          && grandparent.callee.property.name === 'info' && !grandparent.callee.computed
          && grandparent.callee.object.type === 'MemberExpression'
          && ['$ot', 'Yut'].includes(grandparent.callee.object.property.name)
          && !grandparent.callee.object.computed;
        if (visible) rendered++;
        if (!visible && !logged) safe = false;
      }
      for (const child of Object.values(node)) {
        for (const item of Array.isArray(child) ? child : [child]) {
          if (item && typeof item === 'object' && typeof item.type === 'string') {
            pending.push({ node: item, parent: node, grandparent: parent });
          }
        }
      }
    }
    // A local preview/open alias may be localized only when every use is display-only.
    if (safe && rendered > 0) {
      for (const literal of [declaration.init.consequent, declaration.init.alternate]) {
        findings.push({
          file: filename, start: literal.start, end: literal.end, context: 'dialog:action',
          text: literal.value, kind: 'Literal',
          snippet: source.slice(Math.max(0, literal.start - 110), literal.end + 110),
        });
      }
    }
  }
  return findings.sort((a, b) => a.start - b.start);
}

async function main() {
  const report = { files: 0, findings: [], errors: [] };
  for await (const line of readline.createInterface({ input: process.stdin, crlfDelay: Infinity })) {
    const { file, source } = JSON.parse(line);
    try {
      report.findings.push(...scan(source, file));
      report.files++;
    } catch (error) {
      report.errors.push({ file, error: error.message });
    }
  }
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.errors.length) process.exitCode = 1;
}

module.exports = { scan, englishText };
if (require.main === module) main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
