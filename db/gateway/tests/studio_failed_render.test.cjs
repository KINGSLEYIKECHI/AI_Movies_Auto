const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const assert=require('node:assert/strict');
const source=readFileSync(require('node:path').join(__dirname,'../studio.js'),'utf8');
const save=source.slice(source.indexOf('async function saveEditor(render)'),source.indexOf("$('saveRevision').onclick"));
const direct=source.slice(source.indexOf("$('renderPrompt').onclick="),source.indexOf("$('deleteProject').onclick="));
async function check(status, rejectRetry=false, editor=true){
 const calls=[],elements={};
 const $=id=>elements[id]??=(id==='revisionPrompt'?{value:'Improved action'}:{hidden:true,value:'',checked:false});
 const ctx={$,editorJob:{id:81,job_type:'shot_video',status},selectedPrompt:{id:81,job_type:'shot_video',status},editorBase:'/projects/film',editorReferences:[],editorAssetReferences:[],base:()=>'/projects/film',tab:()=>{},notify:()=>{},refresh:async()=>{},runWorker:async()=>calls.push('worker'),api:async(path,body,method)=>{calls.push(path);if(rejectRetry&&path.endsWith('/retry'))throw Error('Saved render unresolved');return{id:'run'};}};
 $('assetEditor').close=()=>{};
 runInNewContext(save+direct,ctx);
 if(editor)await ctx.saveEditor(true);else await $('renderPrompt').onclick();
 const retry=calls.findIndex(c=>c.endsWith('/retry'));
 const worker=calls.findIndex(c=>c==='worker'||c.includes('/workers/'));
 if(status==='failed'){assert(retry>=0);if(rejectRetry)assert.equal(worker,-1);else assert(worker>retry);}
 else {assert.equal(retry,-1);assert(worker>=0);}
}
(async()=>{for(const editor of [true,false]){await check('failed',false,editor);await check('failed',true,editor);await check('queued',false,editor);}console.log('6 failed/queued render UI checks passed');})().catch(e=>{console.error(e);process.exitCode=1});
