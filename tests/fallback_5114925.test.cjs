'use strict';

const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const {patchFallback,patchPinned}=require('../scripts/fallback_5114925.cjs');
const source='function title(t,n,m,r){return n??(m||r?.listTitle||m)??(qi(t)?"New chat":"New task")}';

test('current new-chat title fallback preserves explicit and stored titles',()=>{
  const patched=patchFallback(source);
  assert.equal(patched.includes('qi(t)?"新建聊天":"新建任务"'),true);
  const title=vm.runInNewContext(patched+';title',{qi:x=>x==='chat'});
  assert.equal(title('chat','User title',null,{}),'User title');
  assert.equal(title('chat',null,null,{listTitle:'Stored title'}),'Stored title');
  assert.equal(title('chat',null,null,{}),'新建聊天');
  assert.equal(title('task',null,null,{}),'新建任务');
  assert.throws(()=>patchFallback(patched));
  assert.throws(()=>patchPinned(source,'0'.repeat(64)));
});
