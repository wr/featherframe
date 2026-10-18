// The setup portal's look and script (Featherframe-Setup): WiFiManager's own
// pages, restyled and rearranged in the browser. Kept apart from main.cpp so
// firmware/tools/portal_preview.py can build the same pages on a computer.
#pragma once

#ifndef PROGMEM   // the host preview; on the kit Arduino.h defines it
#define PROGMEM
#endif

// The "Featherframe" wordmark is the plates' script: a WOFF subset of the
// bundled face embedded as a data URI by tools/portal_font.py into
// ff_portal_font.h — the captive portal has no internet, so the face must
// travel with the page. Without that header the @font-face is empty and the
// wordmark falls back to Georgia italic.
#if __has_include("ff_portal_font.h")
#include "ff_portal_font.h"
#else
#define FF_PORTAL_FONT_FACE ""
#endif

#define FF_CLOUD_URL "https://cloud.featherframe.app"
#ifdef FF_HOSTED_DEFAULT
#define FF_PORTAL_DEFAULT "cloud"
#else
#define FF_PORTAL_DEFAULT "self"
#endif

// Every page opens with the wordmark (WiFiManager's body header); the script
// below gathers the rest of the page into one card under it, as the webapp's
// sign-in and phone setup pages are drawn (W-901).
#define FF_PORTAL_BODY_HEADER "<p class='ffmark'>Featherframe</p>"

// A new frame opens on the network list (W-895): there is nothing else to do first.
#define FF_PORTAL_NEW_FRAME_HEAD "<script>if(location.pathname=='/')location.replace('/wifi');</script>"

// A frame already set up (W-903): its server has it, so after Save it goes
// back to its picture, not to a pairing code or "add this frame".
#define FF_PORTAL_ADDED_HEAD "<script>var ffAdded=1;</script>"

// A frame that already knows its network (the KEY2 hold) gets the Wi-Fi form
// without the scan, that network filled in (W-852).
#define FF_PORTAL_MENU_HTML "<form action='/0wifi' method='get'><button>Connect to Wi-Fi</button></form><br/>\n"

// The portal's script (W-888, W-899, W-901):
// - Every page: one card under the wordmark. WiFiManager's status line goes to
//   the top of it, said plainly after a failed join, and not at all when no
//   network is saved yet ("No AP set").
// - The Wi-Fi page: a heading with Refresh beside it, then the networks as a
//   dropdown (W-1073, strongest first, as the scan sorts them; "Other…" shows
//   a Network name field for a hidden network), Password (Show inside the
//   field; gone for an open network). The server is the frame's own current
//   one, else the one this build starts on. On Cloud a new owner sees only
//   their Wi-Fi: "I have my own Featherframe server" under Save opens one
//   address field (blank finds the server on the network, posted as "find")
//   and becomes "Use Featherframe Cloud instead".
//   Under Save, what the frame shows next for that choice: its picture again
//   for a frame already set up that stays on its server (W-903).
// - The page after Save asks the frame how joining goes (/ffstate) and says
//   so, then what comes next (kept in sessionStorage).
//   The frame closes Featherframe-Setup once the phone has seen it joined.
// ES5 and DOM calls only: a phone's captive-portal sheet can be an old WebView.
// An SSID is never written back through innerHTML.
#define FF_PORTAL_SCRIPT R"JS(<script>document.addEventListener('DOMContentLoaded',function(){
var CLOUD=')JS" FF_CLOUD_URL R"JS(',DEF=')JS" FF_PORTAL_DEFAULT R"JS(',d=document,p=location.pathname,st=null;
try{st=window.sessionStorage;}catch(e){}
function q(s,r){return (r||d).querySelector(s);}
function qa(s,r){return (r||d).querySelectorAll(s);}
function el(t,c,h){var e=d.createElement(t);if(c)e.className=c;if(h)e.innerHTML=h;return e;}
function txt(e,s){e.appendChild(d.createTextNode(s));return e;}
function gone(e){if(e&&e.parentNode)e.parentNode.removeChild(e);}
d.title='Featherframe';
var mark=q('.ffmark');if(!mark)return;
var card=el('div','ffcard');while(mark.nextSibling)card.appendChild(mark.nextSibling);mark.parentNode.appendChild(card);
var NEXT={cloud:'Featherframe-Setup will close, and your phone will go back to its usual Wi‑Fi. Then scan the QR code on your frame with your phone’s camera to finish setting up.',
self:'Featherframe-Setup will close, and your phone will go back to its usual Wi‑Fi. Then add the frame in your Featherframe webapp.',
back:'Featherframe-Setup will close, and your phone will go back to its usual Wi‑Fi. Your frame will go back to its picture.'};
var AFTER={cloud:'After you save, your frame will show a QR code. Scan it with your phone’s camera to finish setting up.',
self:'After you save, your frame will ask to connect in your Featherframe webapp.',
back:'After you save, your frame will go back to its picture.'};
if(p=='/wifisave'){var m=q('.msg',card);if(!m)return;card.className+=' ffsaved';
var ch=(st&&st.getItem('ffserver'))||(window.ffAdded?'back':DEF);
m.className='msg ffwait';m.textContent='Connecting to your Wi‑Fi…';
var t0=Date.now(),done=false,errs=0;
function ok(){done=true;m.className='msg S';m.innerHTML='<strong>Connected.</strong> '+(NEXT[ch]||NEXT.cloud);}
function bad(){done=true;m.className='msg D';m.innerHTML='<strong>Couldn’t join that network.</strong> Check the password, then <a href="/wifi">try again</a>.';}
(function poll(){if(done)return;var x=new XMLHttpRequest();x.open('GET','/ffstate?t='+Date.now());x.timeout=4000;
x.onload=function(){errs=0;if(x.responseText=='joined')ok();else if(Date.now()-t0>40000)bad();else setTimeout(poll,1500);};
x.onerror=x.ontimeout=function(){errs++;if(errs>=3&&Date.now()-t0>8000)ok();else setTimeout(poll,1500);};
x.send();})();return;}
var ms=qa('.msg',card),sm=ms.length?ms[ms.length-1]:null;
if(sm&&sm.textContent.replace(/\s/g,'')=='NoAPset'){gone(sm);sm=null;}
if(sm&&/\bD\b/.test(sm.className)){var why=sm.textContent,ss=sm.childNodes.length>1?sm.childNodes[1].textContent.replace(/^\s*to\s+/,''):'',b=el('strong');
sm.innerHTML='';sm.appendChild(b);
if(/AP not found/.test(why)){txt(b,'Couldn’t find '+ss+'.');txt(sm,' Check its name, or bring the frame closer to your router.');}
else{txt(b,'Couldn’t join '+ss+'.');txt(sm,/Authentication/.test(why)?' Check the password.':' Try again.');}}
if(sm)card.insertBefore(sm,card.firstChild);
var NAMES={'/wifi':'Connect to Wi-Fi','/0wifi':'Connect to Wi-Fi','/info':'Info','/exit':'Exit setup','/update':'Update firmware','/erase':'Erase Wi-Fi'};
var fs=qa('form',card);for(var i=0;i<fs.length;i++){var a=fs[i].getAttribute('action'),bt=q('button',fs[i]);
if(bt&&NAMES[a]){bt.textContent=NAMES[a];fs[i].className='ffmenu';if(!/wifi$/.test(a))bt.className+=' ff2';}}
var ssid=q('#s');if(!ssid)return;
var hd=el('div','ffhead');txt(hd.appendChild(el('h2')),'Connect to Wi-Fi');card.insertBefore(hd,card.firstChild);
var as=qa('a[data-ssid]',card),list=null,ls=q('label[for=s]'),pw=q('#p'),lp=q('label[for=p]');
for(i=0;i<card.childNodes.length;i++){var tn=card.childNodes[i];if(tn.nodeType==3&&/No networks found/.test(tn.textContent)){
var none=el('p','ffnote');txt(none,'No networks found.');card.replaceChild(none,tn);list=none;}}
if(ls)ls.textContent='Network';
if(pw){var pb=el('div','ffpw');pw.parentNode.insertBefore(pb,pw);pb.appendChild(pw);
var sb=el('button','ffshow');sb.type='button';txt(sb,'Show');pb.appendChild(sb);
sb.onclick=function(){var h=pw.type=='password';pw.type=h?'text':'password';sb.textContent=h?'Hide':'Show';};
gone(q('#showpass'));gone(q('label[for=showpass]'));}
if(as.length&&ls){list=el('select','ffsel');list.id='ffnet';list.required=true;
var lk={},o=el('option');o.value='';o.disabled=true;txt(o,'Choose a network');list.appendChild(o);
for(i=0;i<as.length;i++){var nm=as[i].getAttribute('data-ssid')||as[i].textContent,qi=q('.q[role=img]',as[i].parentNode);
if(qi&&/(^|\s)l(\s|$)/.test(qi.className))lk[nm]=1;o=el('option');o.value=nm;txt(o,nm);list.appendChild(o);gone(as[i].parentNode);}
o=el('option');o.value='';o.setAttribute('data-other','1');txt(o,'Other…');list.appendChild(o);
var nl=el('label');nl.htmlFor='ffnet';txt(nl,'Network');ls.parentNode.insertBefore(nl,ls);ls.parentNode.insertBefore(list,ls);
txt((ls.innerHTML='',ls),'Network name');
var pick0=ssid.value||ssid.placeholder,hit=0;for(i=1;i<list.options.length-1;i++)if(list.options[i].value==pick0){list.selectedIndex=i;hit=1;}
if(!hit)list.selectedIndex=ssid.value?list.options.length-1:0;
function net(){var op=list.options[list.selectedIndex],oth=!!op.getAttribute('data-other'),on=list.selectedIndex>0;
ls.style.display=ssid.style.display=oth?'':'none';ssid.required=oth;if(!oth)ssid.value=op.value;
var open=on&&!oth&&!lk[op.value];if(pw){pb.style.display=lp.style.display=open?'none':'';if(open)pw.value='';}
if(oth&&list._u)ssid.focus();list._u=1;}
list.onchange=net;net();}
var rf=q('form[action*="refresh"]',card);if(rf){var rb=q('button',rf);rb.className='ffbtn';if(!list)rb.textContent='Show networks';hd.appendChild(rf);}
var inp=q('#server');if(!inp)return;var f=inp.form,cur=inp.value.replace(/\/+$/,'');
var pick=cur==CLOUD?'cloud':(cur?'self':DEF),mode=pick,box=el('div','ffserver');
box.innerHTML='<label for="ffurl">Your Featherframe server</label><input id="ffurl" type="text" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="http://birdnet.local:8181">'+
'<small>Leave it blank to find it on your network.</small>';
inp.parentNode.insertBefore(box,inp);inp.type='hidden';gone(q('label[for="server"]'));
var url=q('#ffurl'),after=el('p','ffnote'),sw=el('button','fflink');sw.type='button';
if(pick=='self')url.value=cur;
function next(c){return window.ffAdded&&c==pick&&(c=='cloud'||url.value.trim().replace(/\/+$/,'')==cur)?'back':c;}
function show(){box.style.display=mode=='self'?'':'none';sw.textContent=mode=='self'?'Use Featherframe Cloud instead':'I have my own Featherframe server';after.textContent=AFTER[next(mode)];}
sw.onclick=function(){mode=mode=='self'?'cloud':'self';show();if(mode=='self')url.focus();};
url.oninput=show;var sv=q('button[type=submit]',f);if(sv){f.insertBefore(after,sv);sv.parentNode.insertBefore(sw,sv.nextSibling);}show();
f.addEventListener('submit',function(){inp.value=mode=='cloud'?CLOUD:(url.value.trim()||'find');try{st&&st.setItem('ffserver',next(mode));}catch(e){}});});</script>)JS"

// The webapp's own tokens (server/templates/index.html), light and dark,
// after WiFiManager's stock sheet so these rules win. The stock sheet pads and
// spaces every div and draws signal from a sprite; both are undone here.
static const char PORTAL_CSS[] PROGMEM = R"CSS(<style>)CSS" FF_PORTAL_FONT_FACE R"CSS(
:root{--bg:#ececea;--surface:#fcfcfb;--inset:#e9e9e5;--field:#fff;--btn-end:#eeeeec;--ink:#201e1a;--ink-2:#474540;--muted:#827e76;--faint:#b1aea6;
--border:#e6e4dd;--border-strong:#d5d2ca;--hair:#eeece6;--accent:#6b4a2c;--accent-press:#523821;--on-accent:#f7efe2;--ring:rgba(107,74,44,.24);
--good:#5c8a46;--bad:#b6472e;--ok-bg:#e9eee5;--ok-bd:#bac7ad;--bad-bg:#f4e8e4;--bad-bd:#d8b5a8;
--sh-sm:0 1px 1px rgba(74,54,28,.05);--sh-card:0 1px 2px rgba(74,54,28,.045),0 4px 12px rgba(74,54,28,.05);color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#1a1916;--surface:#232220;--inset:#1d1c19;--field:#2c2b27;--btn-end:#262523;--ink:#ece8e0;--ink-2:#c6c1b6;
--muted:#8e897f;--faint:#5b574f;--border:#33312c;--border-strong:#4a463f;--hair:#2b2a26;--accent:#cfa878;--accent-press:#e0bc8e;--on-accent:#1a1916;
--ring:rgba(207,168,120,.3);--good:#7fae66;--bad:#d9705a;--ok-bg:#2e3328;--ok-bd:#4b593f;--bad-bg:#372b26;--bad-bd:#65443a;
--sh-sm:0 1px 1px rgba(0,0,0,.25);--sh-card:0 1px 2px rgba(0,0,0,.3),0 4px 14px rgba(0,0,0,.28);color-scheme:dark}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;padding:28px 16px 48px;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;text-align:left;-webkit-font-smoothing:antialiased}
.wrap{display:block;width:100%;min-width:0;max-width:400px;margin:0 auto;text-align:left}
div{padding:0;margin:0}
.ffmark{font-family:'FFScript',Georgia,serif;font-style:italic;font-weight:500;font-size:44px;line-height:1.15;text-align:center;margin:0 0 20px;font-feature-settings:'liga' 1,'calt' 1,'kern' 1}
.ffcard{background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--sh-card);padding:20px 20px 22px}
h1,h3{display:none}
h2{font-size:17px;font-weight:600;line-height:1.3;margin:0}
.ffhead{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 14px}
.wrap>br,.ffcard>br,form>br,form>hr{display:none}
hr{border:0;border-top:1px solid var(--hair);margin:14px 0}
form{margin:0}
a{color:var(--accent);font-weight:600;text-decoration:none}
label,.fflab{display:block;font-size:13px;color:var(--muted);margin:16px 0 6px}
input,select{display:block;width:100%;margin:0;padding:11px 12px;font:inherit;font-size:16px;color:var(--ink);background:var(--field);border:1px solid var(--border-strong);border-radius:8px;box-shadow:var(--sh-sm);-webkit-appearance:none;appearance:none}
input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--ring)}
input::placeholder{color:var(--faint)}
input[type=radio],input[type=checkbox]{display:inline-block;width:auto;padding:0;box-shadow:none;accent-color:var(--accent)}
input[type=radio]{-webkit-appearance:radio;appearance:auto}input[type=checkbox]{-webkit-appearance:checkbox;appearance:auto}
input[type=file]{margin-top:8px;padding:9px 10px;font-size:14px;border:1px solid var(--border-strong)}
button{display:block;width:100%;margin:0;padding:12px;font:inherit;font-weight:600;line-height:1.3;color:var(--on-accent);background:var(--accent);border:0;border-radius:8px;cursor:pointer;transition:none}
button:hover{background:var(--accent-press)}
button[type=submit]{margin-top:18px}
button.ff2,button.ffbtn{color:var(--ink-2);background:linear-gradient(var(--field),var(--btn-end));border:1px solid var(--border-strong);box-shadow:var(--sh-sm);font-weight:500}
button.ff2:hover,button.ffbtn:hover{color:var(--ink);background:linear-gradient(var(--field),var(--btn-end))}
button.ffbtn{width:auto;padding:6px 12px;font-size:13px}
button.D{color:var(--bad);background:none;border:1px solid var(--bad);margin-top:12px}
.ffmenu~.ffmenu{margin-top:10px}
select{padding-right:38px;background:var(--field) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8'%3E%3Cpath d='M1 1.5l5 5 5-5' fill='none' stroke='%23827e76' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat right 14px center/12px 8px}
select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--ring)}
select:invalid{color:var(--faint)}select option{color:var(--ink)}
.ffpw{position:relative}
.ffpw input{padding-right:66px}
button.ffshow{position:absolute;top:0;right:0;bottom:0;width:auto;padding:0 14px;font-size:14px;color:var(--accent);background:none;border-radius:0 8px 8px 0}
.ffpw input:disabled+.ffshow{display:none}
#showpass{margin:12px 6px 0 2px;vertical-align:middle}
label[for=showpass]{display:inline-block;margin:12px 0 0;vertical-align:middle;color:var(--ink-2)}
button.fflink{display:block;width:auto;margin:14px auto 0;padding:4px 2px;font-size:14px;font-weight:500;color:var(--accent);background:none;text-decoration:underline;text-underline-offset:3px}
button.fflink:hover{background:none;color:var(--accent-press)}
.ffserver small{display:block;margin-top:6px;font-size:13px;line-height:1.45;color:var(--muted)}
.ffnote{font-size:13px;line-height:1.45;color:var(--muted);margin:14px 0 0}
.ffhead+.ffnote{margin:0}
.msg{display:block;width:auto;margin:0 0 16px;padding:10px 12px;font-size:14px;line-height:1.45;color:var(--ink);background:var(--inset);border:1px solid var(--border);border-radius:8px}
.msg.S{background:var(--ok-bg);border-color:var(--ok-bd)}
.msg.D{background:var(--bad-bg);border-color:var(--bad-bd)}
.msg em,.msg small{font-style:normal;color:var(--muted)}
.ffhead+.msg{margin-top:-2px}
.ffsaved .msg{margin:0;padding:0;font-size:15px;background:none;border:0}
.ffsaved .msg.S strong{color:var(--good)}
.ffsaved .msg.D strong{color:var(--bad)}
.ffwait:before{content:'';display:inline-block;width:13px;height:13px;margin:0 9px -2px 0;border:2px solid var(--border-strong);border-top-color:var(--accent);border-radius:50%;animation:ffspin .8s linear infinite}
@keyframes ffspin{to{transform:rotate(360deg)}}
body.info h3{display:block;margin:18px 0 0;padding-bottom:6px;border-bottom:1px solid var(--hair);font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;text-align:left;color:var(--muted)}
body.info hr{display:none}
dl{margin:0 0 18px}dt{margin-top:10px;font-size:13px;font-weight:400;color:var(--muted)}dd{margin:1px 0 0;min-height:0;padding:0;color:var(--ink)}
small{color:var(--muted)}
.ffcard>small{display:block;margin-top:14px;font-size:12.5px}.ffcard>small a{font-weight:400;color:var(--muted)}
:disabled{opacity:.5}
</style>)CSS" FF_PORTAL_SCRIPT;
