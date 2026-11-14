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
 constructor(tag='div') {this.tagName=tag.toUpperCase();this.nodeName=this.tagName;this.nodeType=1;this.id='';this.children=[];this.childNodes=this.children;this.attributes=[];this.events={};this.value='';this.style={setProperty(){}};this.classList={add(){},toggle(){}};this.dataset={};}
 get options(){return this.children;}
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
 append(...children){for(const child of children)this.insertBefore(child,null);}
 appendChild(child){this.append(child);if(child.selected)this.value=child.value;return child;}
 insertBefore(child,reference){if(child.parentElement)child.remove();const index=reference?this.children.indexOf(reference):-1;this.children.splice(index<0?this.children.length:index,0,child);child.parentElement=this;return child;}
 replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);}
 remove(){if(this.parentElement){const siblings=this.parentElement.children;siblings.splice(siblings.indexOf(this),1);this.parentElement=null;}}
 contains(child){return this===child||this.children.some(item=>item.contains(child));}
 setAttribute(name,value){const current=this.attributes.find(attr=>attr.name===name);if(current)current.value=String(value);else this.attributes.push({name,value:String(value)});}
 getAttribute(name){return this.attributes.find(attr=>attr.name===name)?.value??null;}
 hasAttribute(name){return this.attributes.some(attr=>attr.name===name);}
 removeAttribute(name){this.attributes=this.attributes.filter(attr=>attr.name!==name);}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelectorAll(){return [];}
 querySelector(){return null;}
 replaceWith(node){if(this.parentElement)this.parentElement.insertBefore(node,this);this.remove();}
}
function byId(id){if(!nodes.has(id)){const node=new Element();node.id=id;nodes.set(id,node);}return nodes.get(id);}
global.document={getElementById:byId,querySelector:()=>byId('root'),querySelectorAll:()=>[],createElement:()=>new Element(),createElementNS:()=>new Element(),addEventListener(){}};
global.MutationObserver=class { observe(){} };
global.setInterval=()=>0;
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
 constructor(tag='div') {this.tagName=tag.toUpperCase();this.nodeName=this.tagName;this.nodeType=1;this.children=[];this.childNodes=this.children;this.attributes=[];this.dataset={};this.events={};this.style={setProperty(){}};this.textContent='';}
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
 append(...children) {for(const child of children)this.insertBefore(child,null);}
 insertBefore(child,reference){if(child.parentElement)child.remove();const index=reference?this.children.indexOf(reference):-1;this.children.splice(index<0?this.children.length:index,0,child);child.parentElement=this;return child;}
 replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);}
 remove(){if(this.parentElement){const siblings=this.parentElement.children;siblings.splice(siblings.indexOf(this),1);this.parentElement=null;}}
 contains(child){return this===child||this.children.some(item=>item.contains(child));}
 setAttribute(name,value){const current=this.attributes.find(attr=>attr.name===name);if(current)current.value=String(value);else this.attributes.push({name,value:String(value)});}
 getAttribute(name){return this.attributes.find(attr=>attr.name===name)?.value??null;}
 hasAttribute(name){return this.attributes.some(attr=>attr.name===name);}
 removeAttribute(name){this.attributes=this.attributes.filter(attr=>attr.name!==name);}
 addEventListener(name,fn) {this.events[name]=fn;}
 replaceWith(node){if(this.parentElement)this.parentElement.insertBefore(node,this);this.remove();}
}
const nodes = new Map(['quota-agents','quota-unmeasured','quota-status','quota-refresh'].map(id=>[id,Object.assign(new Element(),{id})]));
global.document={querySelector:()=>new Element(),getElementById:id=>nodes.get(id),createElement:()=>new Element(),addEventListener(){}};
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
const cards=[...host.children,...nodes.get('quota-unmeasured').children];
if(cards.length!==8)throw Error('peers lost');
for(const card of cards){const name=card.children[0].children[0].textContent;const index=ids.indexOf(name);if(card.children[0].children[1].textContent!==states[index])throw Error('state collapsed for '+name);}
const measuredCard=cards.find(card=>card.children[0].children[0].textContent==='agy');
const meta=measuredCard.children.at(-1).children[1].textContent;
 if(!meta.includes('148h ago') || !meta.includes('model old-model'))throw Error('observation age/scope lost');
 if(fetched.some(url=>url.includes('refresh=')))throw Error('implicit network refresh');
 if(timers.length!==1 || timers[0].ms!==15000)throw Error('unexpected timer');
 await timers[0].fn();
 if(fetched.some(url=>url.includes('refresh=')))throw Error('timer requested provider network');
 await nodes.get('quota-refresh').events.click();
 if(fetched.at(-1)!=='/api/usage/quota?refresh=network')throw Error('explicit refresh lost');
 const retainedCard=[...host.children,...nodes.get('quota-unmeasured').children].find(card=>card.children[0].children[0].textContent==='agy');
 if(retainedCard.children[0].children[1].textContent!=='stale' || !retainedCard.children.at(-1).children[1].textContent.includes('148h ago'))throw Error('refresh laundered observation');
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


@pytest.mark.parametrize(
    "refresh, expected",
    [
        pytest.param(
            {
                "mode": "quota",
                "started": True,
                "reason": "",
                "agents": [
                    {
                        "agent": "cursor",
                        "status": "unavailable",
                        "detail": "provider offers no authoritative quota source",
                    }
                ],
            },
            ["cursor", "unavailable"],
            id="unsupported",
        ),
        pytest.param(
            {
                "mode": "quota",
                "started": False,
                "reason": "no supported provider requested",
                "agents": [],
            },
            ["refresh not started", "no supported provider requested"],
            id="skipped",
        ),
        pytest.param(
            {
                "mode": "quota",
                "started": True,
                "reason": "collector failed or timed out",
                "agents": [
                    {
                        "agent": "cursor",
                        "status": "timeout",
                        "detail": "collector did not finish",
                    }
                ],
            },
            ["cursor", "timeout"],
            id="failed",
        ),
        pytest.param(
            {
                "mode": "quota",
                "started": False,
                "reason": "collector task failed: private failure",
                "agents": [],
            },
            ["refresh not started", "collector task failed: private failure"],
            id="task-failed",
        ),
    ],
)
def test_quota_board_script_shows_the_refresh_outcome(refresh, expected):
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
 constructor(tag='div') {this.tagName=tag.toUpperCase();this.nodeName=this.tagName;this.nodeType=1;this.id='';this.children=[];this.childNodes=this.children;this.attributes=[];this.events={};this.value='';this.style={setProperty(){}};this.dataset={};this.textContent='';}
 get firstElementChild(){return this.children[0]||null;}
 get nextElementSibling(){if(!this.parentElement)return null;return this.parentElement.children[this.parentElement.children.indexOf(this)+1]||null;}
 append(...children){for(const child of children)this.insertBefore(child,null);}
 appendChild(child){return this.append(child);}
 insertBefore(child,reference){if(child.parentElement)child.remove();const index=reference?this.children.indexOf(reference):-1;this.children.splice(index<0?this.children.length:index,0,child);child.parentElement=this;return child;}
 replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);}
 remove(){if(this.parentElement){const siblings=this.parentElement.children;siblings.splice(siblings.indexOf(this),1);this.parentElement=null;}}
 contains(child){return this===child||this.children.some(item=>item.contains(child));}
 setAttribute(name,value){const current=this.attributes.find(attr=>attr.name===name);if(current)current.value=String(value);else this.attributes.push({name,value:String(value)});}
 getAttribute(name){return this.attributes.find(attr=>attr.name===name)?.value??null;}
 hasAttribute(name){return this.attributes.some(attr=>attr.name===name);}
 removeAttribute(name){this.attributes=this.attributes.filter(attr=>attr.name!==name);}
 addEventListener(name,fn){this.events[name]=fn;}
 querySelectorAll(){return [];}
 querySelector(){return null;}
 replaceWith(node){if(this.parentElement)this.parentElement.insertBefore(node,this);this.remove();}
}
function byId(id){if(!nodes.has(id)){const node=new Element();node.id=id;nodes.set(id,node);}return nodes.get(id);}
global.document={getElementById:byId,querySelector:(sel)=>sel==='[data-quota-board]'?byId('board'):null,createElement:tag=>new Element(tag),addEventListener(){}};
global.setInterval=()=>0;
const plain={generated_at:'2026-10-13T00:00:00Z',agents:[{id:'kimi',name:'Kimi',status:'ok',state:'ok',bars:[],scope:{}}]};
const refreshed={generated_at:'2026-10-13T00:00:10Z',agents:plain.agents,refresh:OUTCOME};
const fetched=[];
global.fetch=async url=>{fetched.push(url);return {ok:true,json:async()=>url.includes('refresh=')?refreshed:plain};};
vm.runInThisContext(SCRIPT);
setImmediate(async()=>{
 byId('quota-refresh').events.click();
 setImmediate(()=>{setImmediate(()=>{
  const text=byId('quota-status').textContent;
  if(!fetched.some(u=>u.includes('refresh=')))throw Error('no refresh request issued');
  for(const expected of EXPECTED)if(!text.includes(expected))throw Error('refresh outcome swallowed: '+text);
  console.log(JSON.stringify({status:text,fetched}));
 });});
});
"""
    result = subprocess.run(
        [
            node,
            "-e",
            harness.replace("SCRIPT", json.dumps(script))
            .replace("OUTCOME", json.dumps(refresh))
            .replace("EXPECTED", json.dumps(expected)),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert all(part in evidence["status"] for part in expected)
