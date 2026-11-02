"""Build a local data-adapted copy without modifying the author's checkout."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
UPSTREAM = BASE / "upstream" / "agent-display"
EXPECTED = "62709b8e26858a5d0e21d3c2b2bbe8090f6f0f32f6e58c3048b599ec1bb4d4b9"

HOOK = r'''
/* Local read-only data adapter. Author's shaders, models, CSS and draw routines remain intact. */
var __forgeLive = null;
if (window.AgentDisplayLive) {
  __forgeLive = window.AgentDisplayLive.mountForge({
    host:host, SR:SR, ST:ST, TEL:TEL, AG:AG, DP:DP, AGENTS:AGENTS, AGI:AGI,
    JOBS:JOBS, FORMS:FORMS, OP:OP, CODE:CODE, WAVE:WAVE, WAVEV:WAVEV,
    stepEls:stepEls, PDUR:PDUR, PHASES:PHASES,
    getNow:function(){return now;}, job:job, form:form,
    getDom:function(){return {cards:cardEls,labels:partLabels,orbitLabels:orbitLabels};},
    buildJobDom:buildJobDom, renderWeb:renderWeb, renderDeliv:renderDeliv,
    setDeliveries:function(items){DELIV.splice.apply(DELIV,[0,DELIV.length].concat(items));renderDeliv();},
    setLog:function(items){LOG.splice.apply(LOG,[0,LOG.length].concat(items));},
    originalState:updateState, originalCodeStep:codeStep,
    cPush:cPush, cNewLine:cNewLine, cFinish:cFinish, setCur:setCur,
    getLayout:function(){return L;}, layout:layout
  });
  if (__forgeLive) {
    updateState = __forgeLive.updateState;
    updateUI = __forgeLive.updateUI;
    codeStep = __forgeLive.codeStep;
    heartbeat = function(){};
    pushHist = function(){};
    codeFill = function(){};
    codePhase = function(){};
    codePart = function(){};
    codeCommit = function(){};
    var __originalDrawComets = drawComets;
    drawComets = function(c,dt){
      var a=TEL.abs,b=TEL.absBucket;
      __originalDrawComets(c,dt);
      TEL.abs=a;TEL.absBucket=b;
    };
  }
}
'''

WORKS_HOOK = r'''
/* Live information is independent from the author's decorative weaving clock. */
var __loomLive = null;
if (window.AgentDisplayLive) {
  __loomLive = window.AgentDisplayLive.mountLoom({
    host:host, SR:SR, ST:ST, JOBS:JOBS, AGENTS:AGENTS, SW:SW, SLAB:SLAB, CODE:CODE,
    PHASES:PHASES, stepEls:stepEls, stepIndex:stepIndex, stepProgress:stepProgress,
    originalState:updateState, originalSpider:spiderState, originalCodeStep:codeStep,
    getDom:function(){return {cards:cardEls,secs:secEls};},
    buildJobDom:buildJobDom, buildStrands:buildStrands, syncCols:syncCols,
    cPush:cPush, cNewLine:cNewLine, cFinish:cFinish, setCur:setCur, renderHeat:renderHeat,
    setDeliveries:function(items){DELIV.splice.apply(DELIV,[0,DELIV.length].concat(items));renderDeliv();}
  });
  if (__loomLive) {
    updateState=__loomLive.updateState; updateUI=__loomLive.updateUI;
    spiderState=__loomLive.spiderState; codeStep=__loomLive.codeStep;
    log=function(){}; heartbeat=function(){}; pushHist=function(){}; addDelivery=function(){};
    codePhase=function(){}; codeFill=function(){};
    codeSecStart=function(){}; codeSecDone=function(){};
  }
}
'''


def function_text(source, name):
    # Function boundaries here start at the beginning of a line; compare original draw routines.
    start = source.index("function " + name + "(")
    end = source.find("\nfunction ", start + 1)
    return source[start:end if end >= 0 else len(source)]


def main():
    original_bytes = (UPSTREAM / "index.html").read_bytes()
    digest = hashlib.sha256(original_bytes).hexdigest()
    if digest != EXPECTED:
        raise RuntimeError("Original author version changed; inspect the adapter before rebuilding.")
    original = original_bytes.decode("utf-8")
    start = original.index("function mountForge(host, SR){")
    end = original.index("/* ================= 主题 07 · WORKS", start)
    forge = original[start:end]
    marker = "/* ---------- 开场 ---------- */"
    if forge.count(marker) != 1:
        raise RuntimeError("FORGE entry point is ambiguous")
    forge = forge.replace(marker, HOOK + "\n" + marker, 1)
    seed_start = forge.index("renderDeliv();\nbuildJobDom();\nST.jobT0 = 0;")
    seed_end = forge.index("function clockTick()", seed_start)
    seed = forge[seed_start:seed_end]
    forge = forge[:seed_start] + "if (__forgeLive) { __forgeLive.start(); } else {\n" + seed + "}\n" + forge[seed_end:]
    # Escape recorded text at the two original innerHTML construction sites.
    dom_start = forge.index("function buildJobDom()")
    dom_end = forge.index("function enterPhase()", dom_start)
    dom = forge[dom_start:dom_end]
    for field in ("p.n", "p.en", "p.task"):
        dom = dom.replace("+ " + field + " +", "+ esc(" + field + ") +")
    forge = forge[:dom_start] + dom + forge[dom_end:]
    # Disable the author's simulation-only debug controls in real mode.
    forge = forge.replace("host.__jump = function (phase) {", "host.__jump = function (phase) { if (__forgeLive) return;")
    forge = forge.replace("host.__next = function () { nextJob(); };", "host.__next = function () { if (!__forgeLive) nextJob(); };")
    forge = forge.replace("host.__ff = function (sec) {", "host.__ff = function (sec) { if (__forgeLive) return;")
    # Keep original drawing/shaders; extend only emission routing for real parallel work.
    forge = forge.replace('rate = i === owner ? 18 : 4.5;',
        'rate = (ST.liveActiveOwners || [owner]).indexOf(i) >= 0 ? 18 : 4.5;')
    forge = forge.replace('var hotP = k === "build" && ST.active === i;',
        'var hotP = k === "build" && (ST.liveActiveParts || [ST.active]).indexOf(i) >= 0;')
    forge = forge.replace('(k === "build" && ST.active >= 0 && Math.random() < 0.55) ? ST.active',
        '(k === "build" && ST.liveActiveParts && ST.liveActiveParts.length && Math.random() < 0.55) ? ST.liveActiveParts[Math.floor(Math.random()*ST.liveActiveParts.length)]')
    modified = original[:start] + forge + original[end:]
    works_start = modified.index("function mountLoom(host, SR){")
    works_end = modified.index("/* ================= 主题 08 · ION", works_start)
    original_works = original[original.index("function mountLoom(host, SR){"):original.index("/* ================= 主题 08 · ION")]
    works = modified[works_start:works_end]
    works = works.replace(marker, WORKS_HOOK + "\n" + marker, 1)
    seed_start = works.index('renderDeliv();\nplanJob(); syncCols(); buildJobDom(); buildStrands(); uploadModel();')
    seed_end = works.index('setTxt($("clock"), hms());', seed_start)
    seed = works[seed_start:seed_end]
    works = works[:seed_start] + '''if (__loomLive) {
      DELIV.length=0;TEL.deliv=0;
      planJob();syncCols();buildJobDom();buildStrands();uploadModel();
      __loomLive.start();
    } else {\n''' + seed + '}\n' + works[seed_end:]
    works = works.replace('host.__jump = function (phase) {', 'host.__jump = function (phase) { if (__loomLive) return;')
    works = works.replace('host.__next = function () { nextJob(); };', 'host.__next = function () { if (!__loomLive) nextJob(); };')
    works = works.replace('host.__ff = function (sec) {', 'host.__ff = function (sec) { if (__loomLive) return;')
    modified = modified[:works_start] + works + modified[works_end:]
    modified = modified.replace("<head>", '<head>\n<meta name="display-key" content="DISPLAY_LOCAL_SESSION_KEY"><meta name="display-thread" content="DISPLAY_SELECTED_THREAD_ID"><meta name="display-title" content="DISPLAY_SELECTED_TITLE">\n<script src="/forge-live.js"></script>', 1)
    modified = modified.replace("let start = location.hash.slice(1);", "let start = location.hash.slice(1) || 'forge';", 1)
    # All ten author templates must remain byte-identical; the adapter updates data at runtime.
    templates = re.findall(r'<template id="([^"]+)">([\s\S]*?)</template>', original)
    for template_id, content in templates:
        match = re.search(r'<template id="' + re.escape(template_id) + r'">([\s\S]*?)</template>', modified)
        if not match or match.group(1) != content:
            raise RuntimeError("Author template changed: " + template_id)
    preserved = ("drawGL", "drawComets", "drawHud", "renderWeb", "viewMat", "placeLabels", "placeOrbitLabels")
    hashes = {}
    original_forge = original[start:end]
    for name in preserved:
        before = function_text(original_forge, name)
        after = function_text(forge, name)
        if before != after:
            raise RuntimeError("Original renderer changed: " + name)
        hashes[name] = hashlib.sha256(before.encode()).hexdigest()
    works_hashes = {}
    for name in ('drawGL','drawMeshes','drawStrandsGL','drawDrops','drawBeam','drawUnitFx','drawMasterFx','updateMachines','viewMat'):
        before, after = function_text(original_works, name), function_text(works, name)
        if before != after:
            raise RuntimeError('WORKS renderer changed: ' + name)
        works_hashes[name] = hashlib.sha256(before.encode()).hexdigest()
    # The theme chooser, its order, and all eight unconnected mount functions remain original.
    shell_start = original.index('/* ================= 系列外壳：')
    shell_before = original[shell_start:]
    shell_after = modified[modified.index('/* ================= 系列外壳：'):]
    if shell_after != shell_before.replace("let start = location.hash.slice(1);", "let start = location.hash.slice(1) || 'forge';", 1):
        raise RuntimeError('Theme switching shell changed')
    for mount in ('mountOps','mountSpider','mountNeural','mountFactory','mountNebula','mountIon','mountPark','mountHQ'):
        # Each theme mount ends at the next theme separator or series shell.
        a = original.index('function ' + mount + '(')
        b = original.find('\n/* =================', a)
        chunk = original[a:b]
        if chunk not in modified:
            raise RuntimeError('Unconnected theme changed: ' + mount)
    web = BASE / "web"
    web.mkdir(parents=True, exist_ok=True)
    (web / "index.html").write_text(modified, "utf-8", newline="\n")
    (BASE / "LICENSE").write_bytes((UPSTREAM / "LICENSE").read_bytes())
    manifest = {"source": "cyberdaddyleo/agent-display", "sourceCommit": "ff44824a741d9b2a767b82f39d3dd34c75c87e3f", "sourceSha256": digest,
                "adapterVersion": "2.1.0", "driverChanges": ["work-item binding", "parallel emission routing", "per-part return offsets", "decorative projection cycle", "separate WORKS visual clock"], "adaptedThemes": ["forge","works"], "originalTemplateCount": len(templates), "templatesUnchanged": True,
                "preservedRenderFunctions": hashes, "preservedWorksRenderFunctions":works_hashes, "unconnectedThemesUnchanged":True, "themeSwitcherUnchanged":True, "readOnly": True, "threadSelection": "local configuration only"}
    (BASE / "upstream-verification.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"built": str(web / "index.html"), "originalTemplatesPreserved": len(templates), "renderFunctionsPreserved": len(hashes)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
