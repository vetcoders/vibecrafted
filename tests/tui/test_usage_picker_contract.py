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


def test_actual_quota_script_keeps_old_observation_age_and_explicit_refresh():
    node = shutil.which("node")
    if not node:
        pytest.fail("Node is required for actual quota script acceptance")
    source = APP.read_text()
    script = (
        source[source.index("fn quota_dashboard_script()") :]
        .split('r#"', 1)[1]
        .split('"#', 1)[0]
    )
    harness = r"""
const vm = require('vm');
class Element {
 constructor() {this.children=[];this.dataset={};this.events={};this.style={setProperty(){}};this.textContent='';}
 append(...children) {this.children.push(...children);}
 replaceChildren() {this.children=[];}
 addEventListener(name,fn) {this.events[name]=fn;}
}
const nodes = new Map(['quota-agents','quota-status','quota-refresh'].map(id=>[id,new Element()]));
global.document={querySelector:()=>new Element(),getElementById:id=>nodes.get(id),createElement:()=>new Element()};
const ids=['agy','claude','codex','copilot','cursor','grok','junie','kimi'];
const states=['stale','unknown','exhausted','malformed','absent','low','ok','unknown'];
const agents=ids.map((id,i)=>({id,name:id,state:states[i],status:states[i]==='exhausted'?'blocked':'warn',age_s:i===0?148*3600:null,scope:{model:i===0?'old-model':null},source:'private fixture',tokens:null,cost_usd:null,bars:[]}));
const fetched=[];const timers=[];
global.fetch=async url=>{fetched.push(url);return {ok:true,json:async()=>({generated_at:new Date().toISOString(),agents})};};
global.setInterval=(fn,ms)=>{timers.push({fn,ms});};
vm.runInThisContext(SCRIPT);
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await settle();
 const host=nodes.get('quota-agents');
 if(host.children.length!==8)throw Error('peers lost');
 host.children.forEach((card,i)=>{if(card.children[0].children[1].textContent!==states[i])throw Error('state collapsed for '+ids[i]);});
 const meta=host.children[0].children.at(-1).textContent;
 if(!meta.includes('148h ago') || !meta.includes('model old-model'))throw Error('observation age/scope lost');
 if(fetched.some(url=>url.includes('refresh=')))throw Error('implicit network refresh');
 if(timers.length!==1 || timers[0].ms!==15000)throw Error('unexpected timer');
 await timers[0].fn();
 if(fetched.some(url=>url.includes('refresh=')))throw Error('timer requested provider network');
 await nodes.get('quota-refresh').events.click();
 if(fetched.at(-1)!=='/api/usage/quota?refresh=network')throw Error('explicit refresh lost');
 if(host.children[0].children[0].children[1].textContent!=='stale' || !host.children[0].children.at(-1).textContent.includes('148h ago'))throw Error('refresh laundered observation');
 console.log(JSON.stringify({ids,states,meta,fetched}));
})().catch(error=>{console.error(error);process.exitCode=1;});
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
    assert len(evidence["ids"]) == 8
    assert evidence["states"][0] == "stale"
    assert "148h ago" in evidence["meta"]
