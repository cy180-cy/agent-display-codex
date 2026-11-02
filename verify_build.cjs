'use strict';
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const root=__dirname,html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
let count=0;
for(const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)){
 if(/\bsrc\s*=/.test(match[1])||!match[2].trim())continue;
 new vm.Script(match[2],{filename:'author-and-adapter-inline-'+count+'.js'});count++;
}
new vm.Script(fs.readFileSync(path.join(root,'web/forge-live.js'),'utf8'),{filename:'forge-live.js'});
if(!html.includes('DISPLAY_LOCAL_SESSION_KEY'))throw Error('Session bootstrap missing');
if(!html.includes("location.hash.slice(1) || 'forge'"))throw Error('Default theme mismatch');
console.log('Parsed inline scripts:',count,'; client syntax and bootstrap passed.');
