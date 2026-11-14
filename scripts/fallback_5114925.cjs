'use strict';

const acorn=require('acorn'),crypto=require('node:crypto');
const SOURCE_SHA256='17330f89022d40b4ff05d5894378fd4fdfb67a96a8a29dad2eb02e4c39f2597e';
const BEFORE='n??(m||r?.listTitle||m)??(qi(t)?"New chat":"New task")';
const AFTER='n??(m||r?.listTitle||m)??(qi(t)?"新建聊天":"新建任务")';

function patchFallback(source){
  if(source.split(BEFORE).length!==2 || source.includes(AFTER))
    throw new Error('new-chat title binding changed');
  acorn.parse(source,{ecmaVersion:'latest',sourceType:'module'});
  const out=source.replace(BEFORE,AFTER);
  acorn.parse(out,{ecmaVersion:'latest',sourceType:'module'});
  return out;
}
function patchPinned(source,expected=SOURCE_SHA256){
  if(expected!==SOURCE_SHA256||crypto.createHash('sha256').update(source).digest('hex')!==SOURCE_SHA256)
    throw new Error('new-chat source fingerprint differs');
  return patchFallback(source);
}
module.exports={patchFallback,patchPinned,SOURCE_SHA256};
