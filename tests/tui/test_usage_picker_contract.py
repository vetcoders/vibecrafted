"""Exercise the shipped usage picker script and API wire values without capture."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "vibecrafted-server/web/src/app.rs"


def test_actual_picker_script_roundtrips_known_and_unknown_selection():
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for actual usage picker script acceptance")
    source = APP.read_text()
    start = source.index("fn usage_dashboard_script()")
    script = source[start:].split('r#"', 1)[1].split('"#', 1)[0]
    providers = dict(
        re.findall(
            r'<option value="([^"]+)">"([^"]+)"</option>',
            source[
                source.index('<select id="usage-provider"') : source.index(
                    "</select>", source.index('<select id="usage-provider"')
                )
            ],
        )
    )
    assert set(providers) == {
        "google",
        "anthropic",
        "openai",
        "github",
        "anysphere",
        "xai",
        "jetbrains",
        "moonshot",
    }
    harness = r"""
const vm=require('vm');
const nodes=new Map();
class Element {
 constructor(id='') {this.id=id;this.children=[];this.events={};this.value='';this.style={setProperty(){}};this.classList={add(){},toggle(){}};this.dataset={};}
 get options(){return this.children;}
 append(...children){this.children.push(...children);}
 appendChild(child){this.children.push(child);if(child.selected)this.value=child.value;}
 replaceChildren(){this.children=[];}
 setAttribute(){}
 getAttribute(){return '';}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelectorAll(){return [];}
 querySelector(){return null;}
}
function byId(id){if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);}
global.document={getElementById:byId,querySelector:()=>byId('root'),querySelectorAll:()=>[],createElement:()=>new Element(),createElementNS:()=>new Element()};
global.location={search:'?provider=openai&agent=codex&model=unobserved-model&window=all'};
global.history={replaceState(_a,_b,url){global.lastUrl=url;}};
global.FormData=class { constructor(){this.values=['window','provider','agent','model'].map(k=>[k,byId('usage-'+k).value]);} [Symbol.iterator](){return this.values[Symbol.iterator]();} };
const fetched=[];
global.fetch=async url=>{fetched.push(url);return {ok:true,json:async()=>({filter:{since:'all'},totals:{},runs:[],dimensions:{},pickers:{providers:[{name:'openai'}],agents:[{name:'codex'}],models:[{name:'known-model'},{name:'unknown'}]}})};};
byId('usage-window').value='all';
vm.runInThisContext(SCRIPT);
setImmediate(async()=>{
 if(!fetched[0].includes('provider=openai') || !fetched[0].includes('model=unobserved-model'))throw Error('initial query lost');
 if(byId('usage-model').value!=='unobserved-model')throw Error('unknown selection lost');
 if(!byId('usage-model').options.some(o=>o.value==='unknown'))throw Error('unknown unavailable');
 byId('usage-model').value='known-model';
 byId('usage-provider').value='anthropic';byId('usage-agent').value='claude';
 byId('usage-filter-form').events.submit({preventDefault(){}});
 setImmediate(()=>{
  if(!fetched[1].includes('provider=anthropic') || !fetched[1].includes('agent=claude') || !fetched[1].includes('model=known-model'))throw Error('picker API mapping failed');
  if(byId('usage-model').value!=='known-model')throw Error('apply restored stale query');
  console.log(JSON.stringify({fetched,lastUrl,model:byId('usage-model').value}));
 });
});
"""
    result = subprocess.run(
        [node, "-e", harness.replace("SCRIPT", json.dumps(script))],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert len(evidence["fetched"]) == 2
    assert "provider=anthropic" in evidence["lastUrl"]


def test_quota_board_script_shows_the_refresh_outcome():
    """An explicit quota ask must be answered visibly: the server rides the
    collector outcome on the response as `refresh`; the board may not swallow it."""
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for actual quota board script acceptance")
    source = APP.read_text()
    start = source.index("fn quota_dashboard_script()")
    script = source[start:].split('r#"', 1)[1].split('"#', 1)[0]
    harness = r"""
const vm=require('vm');
const nodes=new Map();
class Element {
 constructor(id='') {this.id=id;this.children=[];this.events={};this.value='';this.style={setProperty(){}};this.dataset={};this.textContent='';}
 append(...children){this.children.push(...children);}
 appendChild(child){this.children.push(child);}
 replaceChildren(){this.children=[];}
 setAttribute(){}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelectorAll(){return [];}
}
function byId(id){if(!nodes.has(id))nodes.set(id,new Element(id));return nodes.get(id);}
global.document={getElementById:byId,querySelector:(sel)=>sel==='[data-quota-board]'?byId('board'):null,createElement:()=>new Element()};
global.setInterval=()=>0;
const plain={generated_at:'2026-10-13T00:00:00Z',agents:[{id:'kimi',name:'Kimi',status:'ok',state:'ok',bars:[],scope:{}}]};
const refreshed={generated_at:'2026-10-13T00:00:10Z',agents:plain.agents,refresh:{mode:'quota',started:true,reason:'',agents:[{agent:'kimi',status:'ok',detail:''},{agent:'cursor',status:'unavailable',detail:'provider offers no authoritative quota source'}]}};
const fetched=[];
global.fetch=async url=>{fetched.push(url);return {ok:true,json:async()=>url.includes('refresh=')?refreshed:plain};};
vm.runInThisContext(SCRIPT);
setImmediate(async()=>{
 byId('quota-refresh').events.click();
 setImmediate(()=>{setImmediate(()=>{
  const text=byId('quota-status').textContent;
  if(!fetched.some(u=>u.includes('refresh=')))throw Error('no refresh request issued');
  if(!text.includes('kimi'))throw Error('refresh outcome swallowed: '+text);
  if(!text.includes('unavailable'))throw Error('unsupported provider answer invisible: '+text);
  console.log(JSON.stringify({status:text,fetched}));
 });});
});
"""
    result = subprocess.run(
        [node, "-e", harness.replace("SCRIPT", json.dumps(script))],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert "kimi" in evidence["status"]
    assert "unavailable" in evidence["status"]
