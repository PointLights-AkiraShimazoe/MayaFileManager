#!/usr/bin/env node
// inject-paths.mjs — Archify が deliver した HTML に「パッケージ階層ビュー（左サイドバー）」を後付けする。
//   ・ファイル行: フルパスコピー / 相対パスコピー / Editorで開く
//   ・カード(ノード)クリック → 所属ファイルを階層ビューでハイライト＋詳細表示
//   ・ファイルクリック → そのファイルに属する関数/クラスのカードを図でハイライト
// 使い方: node inject-paths.mjs <diagram.html> <paths.json>
// 何度実行しても同じ結果（マーカーで置換）。paths.json の形式は SKILL.md 参照。
import { readFileSync, writeFileSync } from 'node:fs';

const [htmlPath, jsonPath] = process.argv.slice(2);
if (!htmlPath || !jsonPath) {
  console.error('usage: node inject-paths.mjs <diagram.html> <paths.json>');
  process.exit(2);
}

const spec = JSON.parse(readFileSync(jsonPath, 'utf8'));
const root = String(spec.root || '').replace(/[\\/]+$/, '');
const sep = spec.separator || (root.includes('\\') ? '\\' : '/');
const copyMode = spec.copy_mode === 'relative' || !root ? 'relative' : 'full';
const editor = spec.editor || 'vscode'; // vscode | cursor | none | "template with {path} {line}"
const packageName = spec.package || (root ? root.split(/[\\/]+/).pop() : 'package');

const norm = (f) => String(f || '').replace(/^[\\/]+/, '').split(/[\\/]+/).filter(Boolean).join('/');
const files = new Set((spec.files || []).map(norm).filter(Boolean));
const nodes = {};
for (const [id, v] of Object.entries(spec.nodes || {})) {
  const rel = norm(v.file);
  if (!rel) {
    if (v.symbol) nodes[id] = { external: true, symbol: String(v.symbol) };
    continue;
  }
  files.add(rel);
  nodes[id] = { rel, line: v.line || null, symbol: v.symbol || '' };
}

const START = '<!-- tool-diagram:paths:start -->';
const END = '<!-- tool-diagram:paths:end -->';
const cfg = { root, sep, copyMode, editor, packageName, files: [...files].sort(), nodes };

const block = `${START}
<style>
:root{--td-w:300px}
html.td-open body{margin-left:var(--td-w) !important}
#td-side{position:fixed;left:0;top:0;bottom:0;width:var(--td-w);z-index:99998;display:flex;flex-direction:column;font:12px/1.5 system-ui,"Segoe UI","Yu Gothic UI",sans-serif;color:#e6e6e6;background:#181a20;border-right:1px solid rgba(255,255,255,.12);box-shadow:4px 0 18px rgba(0,0,0,.25)}
html:not(.td-open) #td-side{transform:translateX(calc(-1 * var(--td-w)))}
#td-toggle{position:fixed;left:0;top:50%;z-index:99999;transform:translateY(-50%);writing-mode:vertical-rl;padding:10px 4px;background:#2b6cb0;color:#fff;border:0;border-radius:0 8px 8px 0;cursor:pointer;font:11px system-ui,sans-serif;letter-spacing:.1em}
html.td-open #td-toggle{left:var(--td-w)}
#td-side .td-head{padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.1)}
#td-side .td-head b{display:block;font-size:13px}
#td-side .td-head small{opacity:.6;word-break:break-all}
#td-side .td-tools{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
#td-side button{cursor:pointer;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);color:#fff;border-radius:6px;padding:2px 7px;font:inherit;font-size:11px;white-space:nowrap}
#td-side button:hover{background:rgba(255,255,255,.16)}
#td-side button.td-ok{background:#2f8f5b;border-color:#2f8f5b}
#td-side a.td-open{display:inline-block;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.06);color:#fff;border-radius:6px;padding:2px 7px;font-size:11px;text-decoration:none;white-space:nowrap}
#td-side a.td-open:hover{background:rgba(255,255,255,.16)}
#td-tree{flex:1;overflow:auto;padding:6px 0}
#td-tree .td-dir,#td-tree .td-file{display:flex;align-items:center;gap:4px;padding:2px 8px;cursor:pointer;white-space:nowrap}
#td-tree .td-dir:hover,#td-tree .td-file:hover{background:rgba(255,255,255,.06)}
#td-tree .td-dir>span,#td-tree .td-file>span:first-child{flex:1;overflow:hidden;text-overflow:ellipsis}
#td-tree .td-dir::before{content:"▾";width:12px;opacity:.6}
#td-tree .td-dir.td-closed::before{content:"▸"}
#td-tree .td-dir.td-closed + .td-kids{display:none}
#td-tree .td-file::before{content:"";width:12px}
#td-tree .td-file .td-act{display:none;gap:4px}
#td-tree .td-file:hover .td-act,#td-tree .td-file.td-sel .td-act{display:flex}
#td-tree .td-file.td-sel{background:rgba(255,159,26,.22);box-shadow:inset 3px 0 0 #ff9f1a}
#td-tree .td-file.td-linked{color:#ffd28a}
#td-tree .td-file .td-count{opacity:.5;font-size:10px}
#td-detail{border-top:1px solid rgba(255,255,255,.1);padding:8px 12px;max-height:34vh;overflow:auto}
#td-detail:empty{display:none}
#td-detail .td-label{font-weight:600;font-size:13px}
#td-detail .td-sym{opacity:.75;margin-bottom:4px}
#td-detail code{display:block;font:11px/1.4 ui-monospace,Consolas,monospace;background:rgba(0,0,0,.35);padding:5px 7px;border-radius:6px;word-break:break-all;user-select:all;margin:4px 0 6px}
#td-detail .td-btns{display:flex;flex-wrap:wrap;gap:5px}
#td-detail ul{margin:4px 0 0;padding-left:16px}
#td-detail li{cursor:pointer}
#td-detail li:hover{text-decoration:underline}
#td-hint{padding:6px 12px;opacity:.55;border-top:1px solid rgba(255,255,255,.08)}
/* 図側ハイライト */
html.td-filter [data-node-id]:not(.td-hl){opacity:.28;transition:opacity .15s}
[data-node-id].td-hl{filter:drop-shadow(0 0 5px #ff9f1a) drop-shadow(0 0 2px #ff9f1a)}
:root[data-theme="light"] #td-side,html[data-theme="light"] #td-side,body[data-theme="light"] #td-side{background:#f7f7fa;color:#1c1e26;border-color:rgba(0,0,0,.12)}
:root[data-theme="light"] #td-side button,:root[data-theme="light"] #td-side a.td-open,html[data-theme="light"] #td-side button,html[data-theme="light"] #td-side a.td-open,body[data-theme="light"] #td-side button,body[data-theme="light"] #td-side a.td-open{color:#1c1e26;border-color:rgba(0,0,0,.2);background:rgba(0,0,0,.04)}
:root[data-theme="light"] #td-detail code,html[data-theme="light"] #td-detail code,body[data-theme="light"] #td-detail code{background:rgba(0,0,0,.06)}
:root[data-theme="light"] #td-tree .td-file.td-linked,html[data-theme="light"] #td-tree .td-file.td-linked,body[data-theme="light"] #td-tree .td-file.td-linked{color:#9a4b00}
</style>
<script>
(function(){
  var C = ${JSON.stringify(cfg)};
  var SEP=C.sep, ROOT=C.root;
  function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function full(rel){ return ROOT ? ROOT+SEP+rel.split('/').join(SEP) : rel.split('/').join(SEP); }
  function copyPath(rel){ return C.copyMode==='full' ? full(rel) : rel.split('/').join(SEP); }
  function editorUrl(rel,line){
    if(!ROOT||C.editor==='none') return '';
    var p=full(rel).replace(/\\\\/g,'/'); if(p.charAt(0)!=='/') p='/'+p;
    var tpl = C.editor==='vscode' ? 'vscode://file{path}{line}' : C.editor==='cursor' ? 'cursor://file{path}{line}' : C.editor;
    return tpl.replace('{path}', p).replace('{line}', line?':'+line:'');
  }
  function copy(text, btn){
    var done=function(ok){ if(!btn)return; var t=btn.textContent; btn.textContent= ok?'✓':'×'; btn.classList.toggle('td-ok',ok); setTimeout(function(){btn.textContent=t;btn.classList.remove('td-ok');},1000); };
    if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(text).then(function(){done(true);},function(){done(fb(text));}); } else done(fb(text));
  }
  function fb(text){ try{ var ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select(); var ok=document.execCommand('copy'); ta.remove(); return ok; }catch(e){ return false; } }
  function label(id){ var g=document.querySelector('[data-node-id="'+id+'"]'); return g?g.getAttribute('data-node-label'):id; }
  var byFile={}; Object.keys(C.nodes).forEach(function(id){ var n=C.nodes[id]; if(n.rel){ (byFile[n.rel]=byFile[n.rel]||[]).push(id); } });
  var tree={}; C.files.forEach(function(f){ var parts=f.split('/'), cur=tree; parts.forEach(function(p,i){ if(i===parts.length-1){ cur.__files=cur.__files||[]; cur.__files.push(f); } else { cur[p]=cur[p]||{}; cur=cur[p]; } }); });
  function render(node, depth){
    var h=''; Object.keys(node).filter(function(k){return k!=='__files';}).sort().forEach(function(k){
      h+='<div class="td-dir" style="padding-left:'+(8+depth*12)+'px"><span>'+esc(k)+'</span></div><div class="td-kids">'+render(node[k],depth+1)+'</div>'; });
    (node.__files||[]).sort().forEach(function(f){ var cnt=(byFile[f]||[]).length, nm=f.split('/').pop(), eu=editorUrl(f);
      h+='<div class="td-file'+(cnt?' td-linked':'')+'" data-file="'+esc(f)+'" style="padding-left:'+(8+depth*12)+'px" title="'+esc(full(f))+'"><span>'+esc(nm)+'</span>'+(cnt?'<span class="td-count">'+cnt+'</span>':'')
        +'<span class="td-act"><button type="button" data-copy="'+esc(copyPath(f))+'" title="'+(C.copyMode==='full'?'フルパス':'相対パス')+'をコピー">📋</button>'+(eu?'<a class="td-open" href="'+esc(eu)+'" title="Editorで開く">開く</a>':'')+'</span></div>'; });
    return h;
  }
  document.documentElement.classList.add('td-open');
  var side=document.createElement('div'); side.id='td-side';
  side.innerHTML='<div class="td-head"><b>'+esc(C.packageName)+'</b><small>'+esc(ROOT||'（相対パス運用）')+'</small><div class="td-tools">'
    +(ROOT?'<button type="button" data-copy="'+esc(ROOT)+'">ルートをコピー</button>':'')+'<button type="button" data-td="clear">ハイライト解除</button></div></div>'
    +'<div id="td-tree">'+render(tree,0)+'</div><div id="td-detail"></div>'
    +'<div id="td-hint">カード→所属ファイル / ファイル→所属カード を相互にハイライト。Alt+P で開閉、Esc で解除</div>';
  document.body.appendChild(side);
  var tg=document.createElement('button'); tg.id='td-toggle'; tg.type='button'; tg.textContent='FILES'; document.body.appendChild(tg);
  var detail=side.querySelector('#td-detail');
  function clearHL(){ document.querySelectorAll('[data-node-id].td-hl').forEach(function(g){g.classList.remove('td-hl');}); document.documentElement.classList.remove('td-filter'); side.querySelectorAll('.td-file.td-sel').forEach(function(r){r.classList.remove('td-sel');}); }
  function hlNodes(ids){ ids.forEach(function(id){ var g=document.querySelector('[data-node-id="'+id+'"]'); if(g) g.classList.add('td-hl'); }); if(ids.length) document.documentElement.classList.add('td-filter'); }
  function selFile(rel){ var row=null; side.querySelectorAll('.td-file').forEach(function(r){ if(r.getAttribute('data-file')===rel) row=r; }); if(row){ row.classList.add('td-sel'); row.scrollIntoView({block:'nearest'}); } }
  function showNode(id){
    clearHL(); var n=C.nodes[id]; var lb=label(id);
    if(!n){ detail.innerHTML='<div class="td-label">'+esc(lb)+'</div><div class="td-sym">所属ファイル未登録（paths.json に追加）</div>'; return; }
    if(n.external){ detail.innerHTML='<div class="td-label">'+esc(lb)+'</div><div class="td-sym">'+esc(n.symbol)+'（外部・ファイルなし）</div>'; return; }
    hlNodes([id]); selFile(n.rel);
    var cp=copyPath(n.rel), withLine=n.line?cp+':'+n.line:cp, eu=editorUrl(n.rel,n.line);
    detail.innerHTML='<div class="td-label">'+esc(lb)+'</div>'+(n.symbol?'<div class="td-sym">'+esc(n.symbol)+'</div>':'')+'<code>'+esc(withLine)+'</code>'
      +'<div class="td-btns"><button type="button" data-copy="'+esc(full(n.rel))+'">フルパス</button><button type="button" data-copy="'+esc(n.rel.split('/').join(SEP))+'">相対パス</button>'
      +'<button type="button" data-copy="'+esc(n.rel.split('/').pop())+'">ファイル名</button>'+(n.line?'<button type="button" data-copy="'+esc(full(n.rel)+':'+n.line)+'">パス:行</button>':'')
      +(eu?'<a class="td-open" href="'+esc(eu)+'">Editorで開く</a>':'')+'</div>';
  }
  function showFile(rel){
    clearHL(); var ids=byFile[rel]||[]; hlNodes(ids); selFile(rel);
    var eu=editorUrl(rel);
    detail.innerHTML='<div class="td-label">'+esc(rel.split('/').pop())+'</div><code>'+esc(copyPath(rel))+'</code>'
      +'<div class="td-btns"><button type="button" data-copy="'+esc(full(rel))+'">フルパス</button><button type="button" data-copy="'+esc(rel.split('/').join(SEP))+'">相対パス</button>'+(eu?'<a class="td-open" href="'+esc(eu)+'">Editorで開く</a>':'')+'</div>'
      +(ids.length?'<div class="td-sym" style="margin-top:6px">このファイルのカード ('+ids.length+')</div><ul>'+ids.map(function(id){ var n=C.nodes[id]; return '<li data-node="'+esc(id)+'">'+esc(label(id))+(n.symbol?' <small>'+esc(n.symbol)+'</small>':'')+'</li>'; }).join('')+'</ul>':'<div class="td-sym" style="margin-top:6px">図に対応するカードなし</div>');
  }
  document.addEventListener('click', function(ev){
    var t=ev.target; if(!t.closest) return;
    var b=t.closest('[data-copy]'); if(b){ ev.preventDefault(); ev.stopPropagation(); copy(b.getAttribute('data-copy'), b); return; }
    if(t.closest('a.td-open')){ ev.stopPropagation(); return; }
    if(t.closest('#td-toggle')){ document.documentElement.classList.toggle('td-open'); return; }
    var c=t.closest('[data-td="clear"]'); if(c){ clearHL(); detail.innerHTML=''; return; }
    var d=t.closest('#td-tree .td-dir'); if(d){ d.classList.toggle('td-closed'); return; }
    var f=t.closest('#td-tree .td-file'); if(f){ showFile(f.getAttribute('data-file')); return; }
    var li=t.closest('#td-detail li[data-node]'); if(li){ var id=li.getAttribute('data-node'); var g=document.querySelector('[data-node-id="'+id+'"]'); if(g&&g.focus) g.focus(); showNode(id); return; }
    var g2=t.closest('[data-node-id]'); if(g2){ showNode(g2.getAttribute('data-node-id')); }
  }, true);
  document.addEventListener('focusin', function(ev){ var g=ev.target.closest&&ev.target.closest('[data-node-id]'); if(g) showNode(g.getAttribute('data-node-id')); });
  document.addEventListener('keydown', function(ev){ if(ev.key==='p'&&ev.altKey){ document.documentElement.classList.toggle('td-open'); } if(ev.key==='Escape'){ clearHL(); } });
})();
</script>
${END}`;

let html = readFileSync(htmlPath, 'utf8');
const s = html.indexOf(START), e = html.indexOf(END);
if (s !== -1 && e !== -1) html = html.slice(0, s) + html.slice(e + END.length);
const at = html.lastIndexOf('</body>');
html = at === -1 ? html + '\n' + block : html.slice(0, at) + block + '\n' + html.slice(at);
writeFileSync(htmlPath, html);
console.log(`injected: ${cfg.files.length} files, ${Object.keys(nodes).length} nodes (${copyMode}, editor=${editor}) -> ${htmlPath}`);
