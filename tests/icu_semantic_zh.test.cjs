'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');
const helper = path.join(__dirname, '../scripts/icu_literals.cjs');

function audit(source, translated, locale = 'zh-CN') {
  const child = spawnSync(process.execPath, [helper], {
    input: JSON.stringify({operation:'validate-batch-locale', locale, source, translated}),
    encoding:'utf8', timeout:10000,
  });
  return child.status === 0 ? JSON.parse(child.stdout) : {error:child.stderr};
}

test('zh-CN may omit unreachable plural one but retains reachable other and #', () => {
  const source = {x:'{n, plural, one {# item} other {# items}}'};
  assert.deepEqual(audit(source, {x:'{n, plural, other {# 项}}'}).invalid, []);
  assert.deepEqual(audit(source, {x:'{n, plural, other {项目}}'}).invalid, ['x']);
});

test('explicit numeric plural and select branches are never collapsed', () => {
  const source = {numeric:'{n, plural, =1 {Only one} other {# more}}',
                  selector:'{kind, select, one {First} other {Other}}'};
  const translated = {numeric:'{n, plural, other {# 项}}',
                      selector:'{kind, select, other {其他}}'};
  assert.deepEqual(audit(source,translated).invalid, ['numeric','selector']);
});

test('retained unreachable branches must still preserve their original placeholders', () => {
  const source={x:'{n, plural, one {# item} other {# items}}'};
  assert.deepEqual(audit(source,{x:'{n, plural, one {项目} other {# 项}}'}).invalid,['x']);
});

test('locale-aware operation is restricted to real zh-CN rules', () => {
  const source={x:'{n, plural, one {# item} other {# items}}'};
  assert.match(audit(source,{x:'{n, plural, other {# 项}}'},'en-US').error,
               /icu_literal_error/);
});
