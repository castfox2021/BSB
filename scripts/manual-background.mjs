// Persistent local runtime. Only controls this checkout's exact runner process.
import {spawn,execFileSync} from 'node:child_process';
import {mkdirSync,openSync,closeSync,readFileSync,writeFileSync,unlinkSync} from 'node:fs';
import {resolve} from 'node:path';
import net from 'node:net';
const root=process.cwd(),runner=resolve(root,'scripts/run-manual.mjs'),dir=resolve(root,'data/runtime'),file=resolve(dir,'manual-process.json');
mkdirSync(dir,{recursive:true,mode:0o700});
const command=process.argv[2]??'status';
const port=(value,fallback)=>{const n=Number(value??fallback);if(!Number.isInteger(n)||n<1024||n>65535)throw new Error('invalid local port');return n;};
const api=port(process.env.MANUAL_API_PORT,18000),web=port(process.env.MANUAL_WEB_PORT,13000);
const probe=p=>new Promise(done=>{const socket=net.connect({host:'127.0.0.1',port:p});let finished=false;const finish=v=>{if(!finished){finished=true;socket.destroy();done(v);}};socket.setTimeout(500);socket.on('connect',()=>finish(true));socket.on('error',()=>finish(false));socket.on('timeout',()=>finish(false));});
function owned(){try{const record=JSON.parse(readFileSync(file,'utf8'));if(!Number.isInteger(record.pid)||record.pid<2)return null;const text=execFileSync('/bin/ps',['-p',String(record.pid),'-o','command='],{encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();return text.endsWith(` ${runner}`)?record:null;}catch{return null;}}
if(command==='stop'){
 const record=owned();if(!record){console.log('이 작업의 백그라운드 서버가 실행 중이지 않습니다.');process.exit(0);}
 process.kill(record.pid,'SIGTERM');unlinkSync(file);console.log('로컬 서버 종료 요청 완료.');
}else if(command==='status'){
 console.log(JSON.stringify({managed_process:owned()?.pid??null,api_listening:await probe(api),web_listening:await probe(web)}));
}else if(command==='start'){
 const running=owned();const [a,w]=await Promise.all([probe(api),probe(web)]);
 if(running||a||w){console.log(JSON.stringify({message:'이미 실행 중이거나 포트가 사용 중입니다. 중복 기동하지 않습니다.',managed_process:running?.pid??null,api_listening:a,web_listening:w}));process.exit(a&&w?0:1);}
 const log=resolve(dir,'manual-runtime.log');const fd=openSync(log,'a',0o600);
 const child=spawn(process.execPath,[runner],{cwd:root,env:process.env,detached:true,stdio:['ignore',fd,fd]});
 const pid=await new Promise((done,reject)=>{child.once('spawn',()=>done(child.pid));child.once('error',reject);});
 closeSync(fd);child.unref();writeFileSync(file,JSON.stringify({pid,started_at:new Date().toISOString(),api_port:api,web_port:web})+'\n',{mode:0o600});
 let ok=false;for(let n=0;n<40;n++){if(await probe(api)&&await probe(web)){ok=true;break;}await new Promise(done=>setTimeout(done,250));}
 console.log(JSON.stringify({message:ok?'백그라운드 서버 실행 중':'기동 중입니다. 로그를 확인하세요.',pid,url:`http://127.0.0.1:${web}`,log}));if(!ok)process.exitCode=1;
}else throw new Error('start, stop, status 중 하나를 사용하세요.');
