// Report filenames only. Never print matching secret values.
import {readdir,readFile} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {execFileSync} from 'node:child_process';
const root=process.cwd(), findings=[];
const skip=new Set(['node_modules','.git','.next','.venv','.python','.tooling','.uv-cache','.mypy_cache','__pycache__','.pytest_cache','.ruff_cache','data','dist','build']);
const suspicious=/^[ \t]*(?:BITHUMB_ACCESS_KEY|BITHUMB_SECRET_KEY)[ \t]*=[ \t]*["']?([A-Za-z0-9+/_=-]{24,})/m;
async function scan(dir){for(const entry of await readdir(dir,{withFileTypes:true})){if(skip.has(entry.name)||entry.isSymbolicLink())continue;const path=join(dir,entry.name);if(entry.isDirectory())await scan(path);else if(entry.isFile()&&!entry.name.startsWith('.env')&&/\.(?:py|ts|tsx|js|mjs|json|md|txt|ya?ml|sh)$/.test(entry.name)){const value=await readFile(path,'utf8');if(suspicious.test(value)||/eyJhbGciOiJIUzI1NiJ9\.[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}/.test(value))findings.push(relative(root,path));}}}
await scan(root);
try{const paths=execFileSync('git',['ls-files','-z'],{encoding:'utf8',stdio:['ignore','pipe','ignore']}).split('\0');for(const path of paths)if(/(?:^|\/)\.env(?:$|\.)/.test(path)&&!path.endsWith('.env.example'))findings.push(path);console.log('Git tracked-file inspection completed.');}catch{console.log('Git repository absent: checked source files and .gitignore; no tracked-file claim.');}
const ignore=(await readFile(join(root,'.gitignore'),'utf8')).split(/\r?\n/);
if(!ignore.includes('.env')||!ignore.includes('.env.*'))findings.push('.gitignore');
if(findings.length){console.error('Potential secret exposure in files:',...new Set(findings));process.exitCode=1;}else console.log('No credential assignments or JWT literals detected in source. Local .env was not read.');
