import {DOMAIN} from '../constants/domain.js';
import {store,load} from '../stores/research.js';
import {readings,pairing} from '../api/research.js';
import {studySelect} from '../components/common/study-select.js';
import {table} from '../components/common/table.js';
import {bindForm} from '../hooks/forms.js';
import {esc,fmt} from '../utils/format.js';

const PROCESS_LABEL={before:'原工艺',after:'新工艺'};
const STATE_LABEL={confirmed:'共有配对',ambiguous:'存在并列最优',unmatched:'最优方案未用',no_candidate:'无候选'};

function statusTag(state){return `<span class="status ${state==='confirmed'?'completed':state==='no_candidate'?'failed':''}">${STATE_LABEL[state]||state}</span>`;}

function gapText(seconds){return seconds==null?'—':fmt(seconds)+' 秒';}

function previewResult(preview){
  const r=preview.result;
  const pairs=table(['原工艺读数','新工艺读数','配对时差'],r.pairs.map(p=>[p.before_label,p.after_label,gapText(p.gap_seconds)]));
  const unpaired=table(['工艺','读数编号','读数时刻 (UTC)','色差 ΔE','状态','原因'],
    r.unpaired.map(x=>[PROCESS_LABEL[x.process],x.label,x.measured_at,fmt(x.value),{html:statusTag(x.state)},x.reason]));
  const readingsTable=table(['工艺','读数编号','测量时刻 (UTC)','色差 ΔE','候选数','最优方案可配编号'],
    r.readings.map(x=>[PROCESS_LABEL[x.process],x.label,x.measured_at,fmt(x.value),x.candidate_count,
      x.optimal_partners.length?x.optimal_partners.join(' / '):'—']));
  return `<div class="method-note"><strong>最大配对数 ${r.maximum_pairs} · 所有最优方案共有 ${r.common_pairs} 对</strong>
    <p>候选 ${r.candidate_edges} 条（时差上限 ${fmt(r.max_gap_seconds)} 秒）；最优方案最小总时差 ${fmt(r.minimum_total_gap_seconds)} 秒。
    ${r.pairs.length?'确认后本批分析将一次性切换到下列共有配对。':'没有所有最优方案共有的配对，不能确认；请调整时差上限或核对读数。'}</p>
    <small>${esc(preview.created_by)} · ${esc(preview.created_at)} · 输入指纹 ${esc(preview.input_fingerprint.slice(0,16))}…</small></div>
  <h2>共有配对 <small>${r.pairs.length} 对</small></h2>${pairs||'<p class="method-note">本次没有所有最优方案共有的配对。</p>'}
  <h2>未配读数与原因 <small>${r.unpaired.length} 条</small></h2>${r.unpaired.length?unpaired:'<p>所有读数均有共有配对。</p>'}
  <h2>全部读数判定 <small>仅“共有配对”会在确认后进入分析</small></h2>${readingsTable}`;
}

export async function render(id){
  if(!store.studyId){await load();}
  if(!store.studyId)return `<div class="eyebrow">01 / PAIRING WORKSHEET</div><h1>两道工艺读数配对</h1><p class="method-note">尚无试印研究批次。请先在“来源录入”创建批次，再回到本页上传读数。</p>`;
  const [rows,active,previews]=await Promise.all([readings.list(store.studyId),pairing.active(store.studyId),pairing.previews(store.studyId)]);
  store.readings=rows;
  const writable=localStorage.getItem('research-role')!=='viewer';
  const pending=previews.filter(p=>p.status==='preview'&&!p.stale);
  const open=id?await pairing.get(+id):(pending[0]||null);
  const activeBlock=active?`<div class="method-note"><strong>本批当前分析配对：预览 #${active.preview_id}</strong>
    <p>${active.pairs.length} 对 · 确认于 ${esc(active.confirmed_at)}${active.stale?' · <b>读数已改动，新分析会被拒绝，请重新预览确认</b>':''}</p></div>`:
    '<div class="method-note">本批尚未确认任何配对；已有读数时，分析执行会被拒绝，直到确认配对。</div>';
  const before=rows.filter(x=>x.process==='before').length,after=rows.filter(x=>x.process==='after').length;
  const history=table(['预览','时差上限','最大/共有','状态','生成','动作'],previews.map(p=>[
    '#'+p.id,fmt(p.max_gap_seconds)+' 秒',`${p.result.maximum_pairs} / ${p.result.common_pairs}`,
    {html:p.status==='confirmed'?'<span class="status completed">已确认</span>':p.status==='invalidated'?'<span class="status failed">已作废</span>':p.stale?'<span class="status failed">读数已改，需重做</span>':'<span class="status">预览草稿</span>'},
    esc(p.created_at),{html:`<a href="#pairing/${p.id}">查看</a>`}]));
  return `<div class="eyebrow">01 / PAIRING WORKSHEET</div><h1>两道工艺读数配对</h1>
  <p class="lead">原工艺与新工艺各自上传带编号的色差读数和测量时刻；编号不必相同，由时差与一对一约束配对。</p>
  <div class="method-note">${DOMAIN.note}<br>配对规则：先按给定时差上限找出候选，再做一对一配对；配对数尽量多，同数时总时差最小。页面只确认<strong>所有最优方案共有</strong>的配对；其余读数列出未配原因。</div>
  <div class="toolbar">${studySelect()}</div>
  ${activeBlock}
  ${writable?`<form id="reading-form" class="inline-form"><label>工艺<select name="process"><option value="before">原工艺</option><option value="after">新工艺</option></select></label><label>读数编号<input name="label" required maxlength="100" placeholder="如 OLD-014 / NEW-09"></label><label>色差 ΔE<input name="value" type="number" step="any" min="0" required></label><label>测量时刻<input name="measured_at" type="datetime-local" required></label><button type="submit">保存读数</button></form>
  <details><summary>批量上传某道工艺的读数（CSV：编号,色差,测量时刻）</summary><form id="batch-form"><label>工艺<select name="process"><option value="before">原工艺</option><option value="after">新工艺</option></select></label><label>粘贴 CSV<textarea name="csv" required placeholder="OLD-001,4.12,2026-11-01T09:00:00Z"></textarea></label><button type="submit">整批上传</button></form></details>
  <form id="preview-form" class="inline-form"><label>时差上限（秒）<input name="max_gap_seconds" type="number" step="any" min="0" value="3600" required></label><button type="submit">生成配对预览 →</button></form>`:'<p class="method-note">观察员可阅读读数与配对记录；分析师与负责人可以上传读数、预览与确认。</p>'}
  <h2>读数清单 <small>原工艺 ${before} 条 · 新工艺 ${after} 条</small></h2>
  ${table(['工艺','读数编号','测量时刻 (UTC)','色差 ΔE',...(writable?['操作']:[])],
    rows.map(r=>[PROCESS_LABEL[r.process],r.label,r.measured_at,fmt(r.value),...(writable?[{html:`<button class="text" data-delete="${r.id}">移除</button>`}]:[])]))}
  ${open?`<h2>预览 #${open.id} ${open.stale?'<small>读数已改动，请重新生成</small>':''}</h2>${previewResult(open)}
    ${writable&&open.status==='preview'&&open.result.pairs.length?`<div class="toolbar"><button id="confirm-pairing">确认并切换本批配对</button></div><p><small>确认时若读数已被并发修改，旧预览作废，本批原配对整批保留。</small></p>`:open.status==='invalidated'?`<p class="error">已作废：${esc(open.invalidate_reason)}</p>`:open.status==='confirmed'?'<p class="method-note">该预览已确认。</p>':''}`:''}
  <h2>预览记录</h2>${previews.length?history:'<p>尚无预览。设定时差上限后生成第一份配对预览。</p>'}`;
}

function parseCsv(text){
  return text.split(/\r?\n/).map(x=>x.trim()).filter(Boolean).map(line=>{
    const cells=line.split(',').map(x=>x.trim());
    return {label:cells[0],value:Number(cells[1]),measured_at:cells[2]};
  });
}

export function bind(refresh){
  const sel=document.getElementById('study-select');if(sel)sel.onchange=()=>{store.studyId=+sel.value;refresh();};
  bindForm('reading-form',async data=>{await readings.save(store.studyId,{process:data.process,label:data.label,value:+data.value,measured_at:data.measured_at?new Date(data.measured_at).toISOString():''});refresh();});
  bindForm('batch-form',async data=>{await readings.batch(store.studyId,{process:data.process,rows:parseCsv(data.csv)});refresh();});
  bindForm('preview-form',async data=>{const p=await pairing.preview(store.studyId,+data.max_gap_seconds);location.hash='pairing/'+p.id;refresh();});
  const confirm=document.getElementById('confirm-pairing');
  if(confirm)confirm.onclick=async()=>{const id=Number(location.hash.split('/')[1]);confirm.disabled=true;try{const r=await pairing.confirm(id);document.getElementById('notice').textContent=`已切换本批分析配对：预览 #${r.active_pairing.preview_id}，共 ${r.active_pairing.pairs.length} 对`;refresh();}catch(e){document.getElementById('notice').textContent=e.message;refresh();}finally{confirm.disabled=false;}};
  document.querySelectorAll('[data-delete]').forEach(el=>el.onclick=async()=>{try{await readings.remove(store.studyId,+el.dataset.delete);refresh();}catch(e){document.getElementById('notice').textContent=e.message;}});
}
