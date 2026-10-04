#!/usr/bin/env node
const fs = require('fs');
const http = require('http');
const net = require('net');
const path = require('path');
const os = require('os');

const PUPPETEER_ROOT = process.env.GENIE_PUPPETEER_ROOT ||
  '/home/justin/.npm-global/lib/node_modules/@wonderwhy-er/desktop-commander/node_modules/puppeteer';
const puppeteer = require(PUPPETEER_ROOT);
const CHROME = process.env.GENIE_CHROME || '/usr/bin/google-chrome';
const PROFILE = process.env.GENIE_PUTER_PROFILE || path.join(os.homedir(), '.config/genie/puter-profile');
const HTTP_PORT = Number(process.env.GENIE_PUTER_HTTP_PORT || 8789);
const SOCKET = process.env.GENIE_PUTER_SOCKET || path.join(os.homedir(), '.config/genie/puter-bridge.sock');

const HTML = `<!doctype html><html><head><meta charset="utf-8"><title>GENIE Puter Bridge</title></head>
<body><div id="status">GENIE Puter bridge</div><button id="signin">Sign in to Puter</button>
<script src="https://js.puter.com/v2/"></script>
<script>
window.__GENIE_PUTER_READY = false;
window.__GENIE_PUTER_ERROR = null;
(async()=>{ try {
  for(let i=0;i<100 && typeof puter==='undefined';i++) await new Promise(r=>setTimeout(r,100));
  if(typeof puter==='undefined') throw new Error('PUTER_JS_NOT_LOADED');
  window.__GENIE_PUTER_READY = true;
  document.getElementById('status').textContent='ready';
  document.getElementById('signin').onclick = async () => { try { window.__GENIE_LOGIN_PROMISE = puter.auth.signIn(); await window.__GENIE_LOGIN_PROMISE; } catch(e) { window.__GENIE_LOGIN_ERROR = String(e && (e.msg || e.message || e)); } };
} catch(e) { window.__GENIE_PUTER_ERROR=String(e); }})();
</script></body></html>`;

let httpServer;
let browser;
let page;

function ensureDir() { fs.mkdirSync(path.dirname(SOCKET), {recursive:true}); fs.mkdirSync(PROFILE,{recursive:true}); }
function serve() {
  if (httpServer) return;
  httpServer = http.createServer((req,res)=>{
    if(req.url === '/health'){ res.writeHead(200, {'content-type':'application/json'}); return res.end(JSON.stringify({ok:true})); }
    res.writeHead(200, {'content-type':'text/html; charset=utf-8'}); res.end(HTML);
  });
  httpServer.listen(HTTP_PORT,'127.0.0.1');
}
async function ensureBrowser(headless=true) {
  ensureDir(); serve();
  if (browser && page) return;
  browser = await puppeteer.launch({
    headless,
    userDataDir: PROFILE,
    executablePath: CHROME,
    args: ['--no-sandbox','--disable-dev-shm-usage','--disable-background-networking']
  });
  page = await browser.newPage();
  await page.goto('http://127.0.0.1:'+HTTP_PORT+'/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForFunction(() => window.__GENIE_PUTER_READY === true, {timeout:30000});
}
async function signedIn() {
  await ensureBrowser(true);
  return await page.evaluate(async()=>Boolean(puter.auth.isSignedIn()));
}
async function catalog() {
  await ensureBrowser(true);
  return await page.evaluate(async()=>await puter.ai.listModels());
}
async function chat(req) {
  await ensureBrowser(true);
  return await page.evaluate(async (r)=>{
    const options = {
      model:r.model, normalize:true, stream:false,
      max_tokens:r.max_tokens || 12000, temperature:r.temperature ?? 0.2
    };
    if(r.tools && r.tools.length){ options.tools=r.tools; options.tool_choice='auto'; }
    return await puter.ai.chat(r.messages, options);
  }, req);
}
async function login() {
  ensureDir(); serve();
  if (browser) { try { await browser.close(); } catch(e){} browser=null; page=null; }
  browser = await puppeteer.launch({
    headless:false, userDataDir:PROFILE, executablePath:CHROME,
    args:['--no-sandbox','--disable-dev-shm-usage']
  });
  page = await browser.newPage();
  await page.goto('http://127.0.0.1:'+HTTP_PORT+'/', {waitUntil:'domcontentloaded', timeout:30000});
  await page.waitForFunction(() => window.__GENIE_PUTER_READY === true, {timeout:30000});
  const already = await page.evaluate(()=>Boolean(puter.auth.isSignedIn()));
  if(!already) {
    await page.click('#signin');
    const deadline = Date.now()+180000;
    while(Date.now()<deadline) {
      const state = await page.evaluate(()=>({signed:Boolean(puter.auth.isSignedIn()), err:window.__GENIE_LOGIN_ERROR||null}));
      if(state.signed) break;
      if(state.err) throw new Error(state.err);
      await new Promise(r=>setTimeout(r,1000));
    }
  }
  const ok = await page.evaluate(()=>Boolean(puter.auth.isSignedIn()));
  if(!ok) throw new Error('PUTER_LOGIN_TIMEOUT');
  try { await browser.close(); } catch(e){}
  browser=null; page=null;
  return {authenticated:true};
}
async function handle(req) {
  if(req.op==='status') return {authenticated:await signedIn()};
  if(req.op==='models') return {authenticated:await signedIn(), models:await catalog()};
  if(req.op==='chat') {
    if(!(await signedIn())) throw new Error('PUTER_AUTH_REQUIRED');
    return await chat(req);
  }
  if(req.op==='login') return await login();
  throw new Error('PUTER_UNKNOWN_OPERATION');
}
async function daemon() {
  ensureDir();
  try { fs.unlinkSync(SOCKET); } catch(e){}
  const server = net.createServer(sock=>{
    let buf='';
    sock.on('data', async chunk=>{
      buf += chunk.toString();
      let idx;
      while((idx=buf.indexOf('\n'))>=0) {
        const line=buf.slice(0,idx); buf=buf.slice(idx+1);
        if(!line.trim()) continue;
        let req;
        try { req=JSON.parse(line); } catch(e){ sock.write(JSON.stringify({ok:false,error:'PUTER_BAD_JSON'})+'\n'); continue; }
        try { const result=await handle(req); sock.write(JSON.stringify({ok:true,result})+'\n'); }
        catch(e){ sock.write(JSON.stringify({ok:false,error:String(e && (e.message||e))})+'\n'); }
      }
    });
  });
  server.listen(SOCKET);
  process.on('SIGTERM',async()=>{try{if(browser)await browser.close();}catch(e){} try{server.close();}catch(e){} try{fs.unlinkSync(SOCKET);}catch(e){} process.exit(0);});
  process.on('SIGINT',()=>process.kill(process.pid,'SIGTERM'));
  console.log('GENIE_PUTER_BRIDGE_READY');
}
const mode=process.argv[2];
if(mode==='daemon') daemon().catch(e=>{console.error(e);process.exit(1)});
else if(mode==='login') login().then(r=>{console.log(JSON.stringify(r));process.exit(0)}).catch(e=>{console.error(String(e&&e.message||e));process.exit(2)});
else process.exit(2);
