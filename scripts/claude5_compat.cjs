// Static AST inspection/patching only. Never evaluates a vendor bundle.
const acorn=require('acorn'),fs=require('fs'),crypto=require('crypto');
const parse=s=>acorn.parse(s,{ecmaVersion:'latest',sourceType:'module'});
const key=p=>p.key?.name??p.key?.value;
function walk(n,fn){if(!n||typeof n!=='object')return;fn(n);for(const v of Object.values(n)){if(Array.isArray(v))v.forEach(x=>walk(x,fn));else if(v&&typeof v==='object')walk(v,fn);}}
function gated(source){
 const ast=parse(source),found=[];
 walk(ast,n=>{if(n.type==='ObjectExpression'&&n.properties.some(p=>key(p)==='en-US')&&n.properties.some(p=>key(p)==='zh-Hans'))found.push(n);});
 if(found.length!==1)throw Error('gated dictionary anchor changed');
 const node=found[0],en=node.properties.find(p=>key(p)==='en-US').value;
 if(en.type!=='ObjectExpression')throw Error('nonliteral gated dictionary');
 const english={};
 for(const p of en.properties){if(p.value.type!=='Literal'||typeof p.value.value!=='string'||key(p) in english)throw Error('invalid gated message');english[key(p)]=p.value.value;}
 return {node,english};
}
function clean(n){if(Array.isArray(n))return n.map(clean);if(!n||typeof n!=='object')return n;return Object.fromEntries(Object.entries(n).filter(([k])=>!['start','end','raw'].includes(k)).map(([k,v])=>[k,clean(v)]));}
function skeleton(source){const ast=parse(source),{node}=gated(source);let target;walk(ast,n=>{if(n.start===node.start&&n.type==='ObjectExpression')target=n;});target.properties=target.properties.filter(p=>!['zh-Hans','zh-CN'].includes(key(p)));return JSON.stringify(clean(ast));}
function patchGated(source,chinese){
 const {node,english}=gated(source);
 if(JSON.stringify(Object.keys(english).sort())!==JSON.stringify(Object.keys(chinese).sort())||Object.values(chinese).some(v=>typeof v!=='string'||!v))throw Error('gated Chinese incomplete');
 const old=node.properties.find(p=>key(p)==='zh-Hans');
 if(old.value.type!=='ObjectExpression'||old.value.properties.length||node.properties.some(p=>key(p)==='zh-CN'))throw Error('gated locale already changed');
 const text='"zh-Hans":'+JSON.stringify(chinese)+',"zh-CN":'+JSON.stringify(chinese);
 const out=source.slice(0,old.start)+text+source.slice(old.end);
 if(skeleton(source)!==skeleton(out))throw Error('gated edit changed other code/data');
 return out;
}
const SIDE_RULES=[
 ['var Vy={collapsed:!1,sidebarWidth:Fy}','var Vy={collapsed:!0,sidebarWidth:Fy}'],
 ['return{collapsed:!!r.collapsed,sidebarWidth:','return{collapsed:!0,sidebarWidth:'],
 ['J=vi()(gi((e,t)=>({collapsed:!1,','J=vi()(gi((e,t)=>({collapsed:!0,'],
 ['return{...t,...n}}}));function BS()', 'return{...t,...n,collapsed:!0,composedSidebarOpen:!1,composedSidebarMounted:!1}}}));function BS()'],
];
function patchSidebar(source){let out=source;for(const [before,after]of SIDE_RULES){if(out.split(before).length!==2)throw Error('sidebar anchor changed');out=out.replace(before,after);}parse(out);return out;}
function patchFallback(source){const old='n??(m||r?.listTitle||m)??(no(t)?"New chat":"New task")';if(source.split(old).length!==2)throw Error('chat fallback anchor changed');const out=source.replace(old,'n??(m||r?.listTitle||m)??(no(t)?"新建聊天":"新建任务")');parse(out);return out;}
const COWORK_MARKER='local-auto-compact-policy:5';
function patchCowork(source){
 const anchor='t.MTt.info("[HostLoop] sdkOptions after patch: %o",';
 if(source.split(anchor).length!==2||source.split('async function ih(e,r){let{sessionId:i,').length!==2||source.includes(COWORK_MARKER))throw Error('HostLoop fingerprint/binding/marker changed');
 const hook='/*'+COWORK_MARKER+'*/(await(async()=>{try{const p=process.env.CLAUDE_CN_SESSION_COMPACTION_POLICY;if(!p)return;const{pathToFileURL}=await import("node:url");const m=await import(pathToFileURL(require("node:path").join(__dirname,"claude-cn-compaction-policy.cjs")).href);const d=(m.default??m).applyCompactionPolicy(e,{sessionId:i,policyPath:p});t.MTt.info("[LocalAutoCompact] policy %o",d)}catch(z){t.MTt.warn("[LocalAutoCompact] policy unavailable (%s); native defaults retained",z?.name??"Error")}})()),';
 const out=source.replace(anchor,hook+anchor);parse(out);return out;
}
function sidebarParts(source){const out={};walk(parse(source),n=>{
 if(n.type==='FunctionDeclaration'&&['Uy','uS','VS'].includes(n.id?.name))out[n.id.name]=source.slice(n.start,n.end);
 if(n.type==='ObjectExpression'&&n.properties.some(p=>key(p)==='merge')&&n.properties.some(p=>key(p)==='partialize'))for(const p of n.properties)if(['merge','partialize'].includes(key(p)))out[key(p)]=source.slice(p.value.start,p.value.end);
 if(n.type==='Property'&&key(n)==='setCollapsedPref')out.setCollapsedPref=source.slice(n.value.start,n.value.end);
});return out;}
module.exports={gated,patchGated,patchSidebar,sidebarParts,patchFallback,patchCowork,COWORK_MARKER};
if(require.main===module){const input=JSON.parse(fs.readFileSync(0,'utf8'));let out;
 if(input.action==='gated')out=gated(input.source).english;
 else if(input.action==='patch-gated')out=patchGated(input.source,input.chinese);
 else if(input.action==='sidebar-parts')out=sidebarParts(input.source);
 else if(input.action==='patch-sidebar')out=patchSidebar(input.source);
 else if(input.action==='patch-fallback')out=patchFallback(input.source);
 else if(input.action==='patch-cowork')out=patchCowork(input.source);
 else throw Error('unknown action');
 process.stdout.write(JSON.stringify(out));}
