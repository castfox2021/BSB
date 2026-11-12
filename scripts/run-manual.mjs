import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {resolve} from 'node:path';
const root=process.cwd();
const python=resolve(root,'services/manual-assistant/.venv/bin/python');
if(!existsSync(python)) { console.error('먼저 npm run manual:setup 을 실행하세요.');process.exit(1); }
const apiPort=process.env.MANUAL_API_PORT ?? '18000', webPort=process.env.MANUAL_WEB_PORT ?? '13000';
if(![apiPort,webPort].every(p=>/^\d+$/.test(p)&&Number(p)>=1024&&Number(p)<=65535)||apiPort===webPort) throw new Error('invalid local ports');
const env={...process.env,NEXT_TELEMETRY_DISABLED:'1',MANUAL_API_URL:`http://127.0.0.1:${apiPort}`,NEXT_PUBLIC_MANUAL_API_PORT:apiPort,BSB_MANUAL_ORIGINS:`http://127.0.0.1:${webPort},http://localhost:${webPort}`};
const browserEnv={...env};
for(const key of Object.keys(browserEnv)) if(/BITHUMB|SECRET|ACCESS_KEY|AUTHORIZATION/i.test(key)) delete browserEnv[key];
const children=[spawn(python,['-m','uvicorn','manual_assistant.app:app','--host','127.0.0.1','--port',apiPort,'--no-access-log'],{cwd:root,env,stdio:'inherit',detached:true}),spawn('npm',['run','dev','-w','@bsb/web','--','--port',webPort],{cwd:root,env:browserEnv,stdio:'inherit',detached:true})];
let stopping=false;
function stop(code=0){
 if(stopping)return;stopping=true;
 for(const child of children){try{if(child.pid)process.kill(-child.pid,'SIGTERM');}catch{/* already exited */}}
 setTimeout(()=>{for(const child of children){try{if(child.pid)process.kill(-child.pid,'SIGKILL');}catch{/* already exited */}}process.exit(code);},1500);
}
for(const child of children){child.on('error',error=>{console.error(error.message);stop(1);});child.on('exit',code=>stop(code??1));}
process.on('SIGINT',()=>stop());process.on('SIGTERM',()=>stop());
console.log(`수동 매매 도우미: http://127.0.0.1:${webPort} · Ctrl+C로 종료`);
