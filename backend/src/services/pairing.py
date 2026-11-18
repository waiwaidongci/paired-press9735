import math
from bisect import bisect_left,bisect_right
from collections import deque
from datetime import datetime,timedelta,timezone

from fastapi import HTTPException
from sqlalchemy import select

from src.models.pairing import ProcessReading,PairingPreview,ActivePairing
from src.models.study import Study
from src.services.audits import log
from src.utils.serializers import checksum,now,record

PROCESSES=('before','after')
PROCESS_LABELS={'before':'原工艺','after':'新工艺'}
MAX_GAP_LIMIT=2592000.0   # 30 days: a business window, not a statistical parameter
MAX_CANDIDATE_EDGES=30000
EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)


# --------------------------------------------------------------- value helpers

def parse_measured_at(value):
    if not isinstance(value,str) or not value.strip(): raise ValueError('测量时刻不能为空')
    text=value.strip()
    if text.endswith(('Z','z')): text=text[:-1]+'+00:00'
    try: dt=datetime.fromisoformat(text)
    except ValueError: raise ValueError('测量时刻必须为 ISO 8601，例如 2026-11-01T09:15:00Z')
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_time(dt): return dt.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def micros(dt): return (dt-EPOCH)//timedelta(microseconds=1)


def _clean_value(value):
    if isinstance(value,bool): raise ValueError('色差必须是数值')
    try: value=float(value)
    except (TypeError,ValueError): raise ValueError('色差必须是数值')
    if not math.isfinite(value): raise ValueError('色差必须为有限数值')
    if value<0: raise ValueError('色差不得为负')
    if value>1e9: raise ValueError('色差超出业务范围')
    return value


def _require_study(db,study_id):
    study=db.get(Study,study_id)
    if not study: raise HTTPException(404,'业务批次不存在')
    return study


# --------------------------------------------------------------- reading I/O

def list_readings(db,study_id):
    _require_study(db,study_id)
    rows=db.scalars(select(ProcessReading).where(ProcessReading.study_id==study_id).order_by(ProcessReading.process,ProcessReading.measured_at,ProcessReading.id))
    return [record(x) for x in rows]


def save_reading(db,study_id,data,user,row_id=None):
    # The study row serialises concurrent reading edits with pairing confirmation.
    study=db.scalar(select(Study).where(Study.id==study_id).with_for_update())
    if not study: raise HTTPException(404,'业务批次不存在')
    if data.process not in PROCESSES: raise HTTPException(422,'工艺只能是 before 或 after')
    label=data.label.strip()
    if not label: raise HTTPException(422,'读数编号不能为空')
    try:
        value=_clean_value(data.value)
        measured_at=iso_time(parse_measured_at(data.measured_at))
    except ValueError as e: raise HTTPException(422,str(e))
    other=db.scalar(select(ProcessReading).where(ProcessReading.study_id==study_id,ProcessReading.process==data.process,ProcessReading.label==label))
    if other and other.id!=row_id: raise HTTPException(409,f'{PROCESS_LABELS[data.process]}读数编号 {label} 已存在')
    if row_id:
        row=db.get(ProcessReading,row_id)
        if not row or row.study_id!=study_id: raise HTTPException(404,'读数不存在')
        row.process=data.process;row.label=label;row.value=value;row.measured_at=measured_at
    else:
        row=ProcessReading(study_id=study_id,process=data.process,label=label,value=value,measured_at=measured_at,created_at=now())
        db.add(row)
    log(db,user,'reading.save',f'{study_id}/{data.process}/{label}')
    db.commit()
    return record(row)


def save_reading_batch(db,study_id,data,user):
    """Upload one process's numbered readings in a single request; all rows validate or none land."""
    study=db.scalar(select(Study).where(Study.id==study_id).with_for_update())
    if not study: raise HTTPException(404,'业务批次不存在')
    if data.process not in PROCESSES: raise HTTPException(422,'工艺只能是 before 或 after')
    seen=set();cleaned=[]
    for index,row in enumerate(data.rows,1):
        label=row.label.strip()
        if not label: raise HTTPException(422,f'第{index}行：读数编号不能为空')
        if label in seen: raise HTTPException(422,f'第{index}行：上传内读数编号 {label} 重复')
        seen.add(label)
        try: value=_clean_value(row.value);measured_at=iso_time(parse_measured_at(row.measured_at))
        except ValueError as e: raise HTTPException(422,f'第{index}行（{label}）：{e}')
        cleaned.append((label,value,measured_at))
    existing=set(db.scalars(select(ProcessReading.label).where(ProcessReading.study_id==study_id,ProcessReading.process==data.process)))
    clash=sorted(existing.intersection(seen))
    if clash: raise HTTPException(409,f'{PROCESS_LABELS[data.process]}读数编号已存在：{", ".join(clash[:5])}{" 等" if len(clash)>5 else ""}；请先核对后再上传')
    for label,value,measured_at in cleaned:
        db.add(ProcessReading(study_id=study_id,process=data.process,label=label,value=value,measured_at=measured_at,created_at=now()))
    log(db,user,'reading.batch',f'{study_id}/{data.process} rows={len(cleaned)}')
    db.commit()
    return {'saved':len(cleaned),'process':data.process}


def delete_reading(db,study_id,row_id,user):
    db.scalar(select(Study).where(Study.id==study_id).with_for_update())
    row=db.get(ProcessReading,row_id)
    if not row or row.study_id!=study_id: raise HTTPException(404,'读数不存在')
    db.delete(row);log(db,user,'reading.delete',f'{study_id}/{row.process}/{row.label}');db.commit()
    return {'deleted':True}


def has_readings(db,study_id):
    return db.scalar(select(ProcessReading.id).where(ProcessReading.study_id==study_id).limit(1)) is not None


def readings_state(db,study_id):
    rows=db.scalars(select(ProcessReading).where(ProcessReading.study_id==study_id).order_by(ProcessReading.id))
    state=[{'id':r.id,'process':r.process,'label':r.label,'value':float(r.value),'measured_at':r.measured_at} for r in rows]
    return state,checksum(state)


# ---------------------------------------------------- min-cost max-matching core

class _MCMF:
    """Exact integer min-cost max-flow; unit capacities on the matching edges."""
    def __init__(self,n):
        self.n=n;self.graph=[[] for _ in range(n)]
    def add(self,u,v,cap,cost):
        self.graph[u].append([v,cap,cost,len(self.graph[v])])
        self.graph[v].append([u,0,-cost,len(self.graph[u])-1])
        return self.graph[u][-1]
    def run(self,s,t):
        import heapq
        n=self.n;g=self.graph;INF=10**30;flow=0;cost=0
        # Non-negative arc costs initially, so zero potentials are feasible.
        pot=[0]*n
        while True:
            dist=[INF]*n;prev_v=[-1]*n;prev_e=[-1]*n
            dist[s]=0;pq=[(0,s)]
            while pq:
                d,u=heapq.heappop(pq)
                if d!=dist[u]: continue
                for i,e in enumerate(g[u]):
                    if e[1]<=0: continue
                    nd=d+e[2]+pot[u]-pot[e[0]]
                    if nd<dist[e[0]]:
                        dist[e[0]]=nd;prev_v[e[0]]=u;prev_e[e[0]]=i
                        heapq.heappush(pq,(nd,e[0]))
            if dist[t]>=INF: break
            for v in range(n):
                if dist[v]<INF: pot[v]+=dist[v]
            v=t
            while v!=s:
                u=prev_v[v];e=g[u][prev_e[v]]
                e[1]-=1;g[v][e[3]][1]+=1;cost+=e[2];v=u
            flow+=1
        return flow,cost


def _feasible_potentials(graph):
    """Shortest distances on the residual graph (no negative cycles at an optimum)."""
    n=len(graph);dist=[0]*n;queued=[True]*n;queue=deque(range(n))
    while queue:
        u=queue.popleft();queued[u]=False;du=dist[u]
        for e in graph[u]:
            if e[1]>0 and dist[e[0]]>du+e[2]:
                dist[e[0]]=du+e[2]
                if not queued[e[0]]:
                    queued[e[0]]=True;queue.append(e[0])
    return dist


def _zero_reachability(zero,n):
    """SCC-condensed reachability of the zero-reduced-cost residual graph as bitmasks."""
    # Kosaraju, iterative to stay safe on large batches.
    seen=bytearray(n);order=[]
    for start in range(n):
        if seen[start]: continue
        seen[start]=1;stack=[(start,0)]
        while stack:
            u,idx=stack[-1]
            if idx<len(zero[u]):
                v=zero[u][idx];stack[-1]=(u,idx+1)
                if not seen[v]: seen[v]=1;stack.append((v,0))
            else:
                order.append(u);stack.pop()
    reverse=[[] for _ in range(n)]
    for u in range(n):
        for v in zero[u]: reverse[v].append(u)
    comp=[-1]*n;comps=0
    for start in reversed(order):
        if comp[start]!=-1: continue
        comp[start]=comps;stack=[start]
        while stack:
            u=stack.pop()
            for v in reverse[u]:
                if comp[v]==-1: comp[v]=comps;stack.append(v)
        comps+=1
    dag=[set() for _ in range(comps)]
    for u in range(n):
        for v in zero[u]:
            if comp[u]!=comp[v]: dag[comp[u]].add(comp[v])
    indeg=[0]*comps
    for c in range(comps):
        for d in dag[c]: indeg[d]+=1
    queue=deque(c for c in range(comps) if indeg[c]==0);topo=[]
    while queue:
        c=queue.popleft();topo.append(c)
        for d in dag[c]:
            indeg[d]-=1
            if indeg[d]==0: queue.append(d)
    mask=[0]*comps
    for c in reversed(topo):
        m=0
        for d in dag[c]: m|=mask[d]
        mask[c]=m|(1<<c)
    return comp,mask


def solve(snapshot,max_gap_us):
    left=[r for r in snapshot if r['process']=='before']
    right=sorted((r for r in snapshot if r['process']=='after'),key=lambda r:micros(parse_measured_at(r['measured_at'])))
    right_times=[micros(parse_measured_at(r['measured_at'])) for r in right]
    left_times=[micros(parse_measured_at(r['measured_at'])) for r in left]

    adj_left=[];adj_right=[[] for _ in right]
    for i,t in enumerate(left_times):
        neighbours=[]
        for j in range(bisect_left(right_times,t-max_gap_us),bisect_right(right_times,t+max_gap_us)):
            neighbours.append((j,abs(t-right_times[j])))
            adj_right[j].append(i)
        adj_left.append(neighbours)
    if sum(len(x) for x in adj_left)>MAX_CANDIDATE_EDGES:
        raise HTTPException(422,'候选配对过多；请缩小批次或缩短时差上限')

    nL,nR=len(left),len(right)
    s=0;t=1+nL+nR
    mcmf=_MCMF(t+1)
    for i in range(nL): mcmf.add(s,1+i,1,0)
    for j in range(nR): mcmf.add(1+nL+j,t,1,0)
    edge_ref={}
    for i in range(nL):
        for j,gap in adj_left[i]:
            edge_ref[(i,j)]=mcmf.add(1+i,1+nL+j,1,gap)

    flow,cost=mcmf.run(s,t)
    h=_feasible_potentials(mcmf.graph)

    # Zero reduced-cost residual graph: its circulations are exactly the moves that keep
    # both the maximum pair count and the minimum total time gap unchanged.
    zero=[[] for _ in range(mcmf.n)]
    for u in range(mcmf.n):
        for e in mcmf.graph[u]:
            if e[1]>0 and e[2]+h[u]-h[e[0]]==0: zero[u].append(e[0])
    comp,mask=_zero_reachability(zero,mcmf.n)
    def can_reach(u,v): return bool((mask[comp[u]]>>comp[v])&1)

    used_left={i:j for (i,j),e in edge_ref.items() if e[1]==0}
    used_right={j:i for i,j in used_left.items()}

    def edge_usable(i,j):
        """Is pair (i,j) part of at least one optimal solution?"""
        if used_left.get(i)==j: return True
        e=edge_ref.get((i,j))
        if e is None: return False
        ln=1+i;rn=1+nL+j
        return e[2]+h[ln]-h[rn]==0 and can_reach(rn,ln)   # add it, then close a zero-cost cycle

    def edge_fixed(i,j):
        """Is pair (i,j) used by every optimal solution?"""
        if used_left.get(i)!=j: return False
        ln=1+i;rn=1+nL+j
        gap=edge_ref[(i,j)][2]
        if -gap+h[rn]-h[ln]!=0: return True              # removing it would raise the total gap
        return not can_reach(ln,rn)                      # no zero-cost re-route away from it

    # Can this reading be left unpaired in some optimum? Exactly when its source/sink arc can
    # be rotated out, i.e. it shares a zero-reduced-cost SCC with the source/sink.
    left_free=[comp[1+i]==comp[s] for i in range(nL)]
    right_free=[comp[1+nL+j]==comp[t] for j in range(nR)]

    readings=[]
    def entry(row,process,i_or_j,state,reason,partners,partner,gap_us):
        return {'id':row['id'],'process':process,'label':row['label'],'value':row['value'],
                'measured_at':row['measured_at'],'candidate_count':i_or_j,
                'state':state,'reason':reason,'optimal_partners':partners,
                'paired_with':partner,'gap_seconds':gap_us}
    for i,row in enumerate(left):
        partners=[right[j2]['label'] for j2,_ in adj_left[i] if edge_usable(i,j2)]
        j=used_left.get(i)
        if j is not None and edge_fixed(i,j):
            state,reason,partner,gap='confirmed','',right[j]['label'],abs(left_times[i]-right_times[j])/1e6
        elif partners:
            state,partner,gap='ambiguous',None,None
            reason=('存在同样最优的配对方案不使用该读数，当前配对并非所有最优方案共有' if left_free[i]
                    else '所有最优方案都使用该读数，但配对编号不唯一，当前配对并非所有最优方案共有')
        elif not adj_left[i]:
            state,reason,partner,gap='no_candidate','时差上限内没有新工艺读数',None,None
        else:
            state,reason,partner,gap='unmatched','候选存在，但在最大配对数下未被任何最优方案使用',None,None
        readings.append(entry(row,'before',len(adj_left[i]),state,reason,partners,partner,gap))
    for j,row in enumerate(right):
        partners=[left[i2]['label'] for i2 in adj_right[j] if edge_usable(i2,j)]
        i=used_right.get(j)
        if i is not None and edge_fixed(i,j):
            state,reason,partner,gap='confirmed','',left[i]['label'],abs(left_times[i]-right_times[j])/1e6
        elif partners:
            state,partner,gap='ambiguous',None,None
            reason=('存在同样最优的配对方案不使用该读数，当前配对并非所有最优方案共有' if right_free[j]
                    else '所有最优方案都使用该读数，但配对编号不唯一，当前配对并非所有最优方案共有')
        elif not adj_right[j]:
            state,reason,partner,gap='no_candidate','时差上限内没有原工艺读数',None,None
        else:
            state,reason,partner,gap='unmatched','候选存在，但在最大配对数下未被任何最优方案使用',None,None
        readings.append(entry(row,'after',len(adj_right[j]),state,reason,partners,partner,gap))

    pairs=[]
    for i,j in sorted((i,j) for i in used_left for j in [used_left[i]] if edge_fixed(i,j)):
        b,a=left[i],right[j]
        pairs.append({
            'key':f"{b['label']} ↔ {a['label']}",
            'before_id':b['id'],'before_label':b['label'],'before_value':b['value'],'before_measured_at':b['measured_at'],
            'after_id':a['id'],'after_label':a['label'],'after_value':a['value'],'after_measured_at':a['measured_at'],
            'gap_seconds':abs(left_times[i]-right_times[j])/1e6,
        })
    unpaired=[x for x in readings if x['state']!='confirmed']
    return {
        'algorithm':'时差候选 → 最大配对数、同数时总时差最小 → 仅保留所有最优方案共有的配对',
        'max_gap_seconds':max_gap_us/1e6,
        'candidate_edges':sum(len(x) for x in adj_left),
        'maximum_pairs':flow,
        'minimum_total_gap_seconds':cost/1e6,
        'common_pairs':len(pairs),
        'pairs':pairs,
        'readings':readings,
        'unpaired':unpaired,
        'reading_counts':{'before':nL,'after':nR},
    }


# ------------------------------------------------------------------- previews

def _gap_us(value):
    try: gap=float(value)
    except (TypeError,ValueError): raise HTTPException(422,'时差上限必须是数值')
    if not math.isfinite(gap): raise HTTPException(422,'时差上限必须为有限数值')
    if gap<0: raise HTTPException(422,'时差上限不得为负')
    if gap>MAX_GAP_LIMIT: raise HTTPException(422,'时差上限不得超过30天')
    return int(round(gap*1_000_000))


def _preview_view(db,preview):
    view=record(preview)
    _,current=readings_state(db,preview.study_id)
    view['stale']=current!=preview.input_fingerprint
    study=db.get(Study,preview.study_id)
    view['study_name']=study.name if study else ''
    return view


def create_preview(db,study_id,data,user):
    _require_study(db,study_id)
    gap_us=_gap_us(data.max_gap_seconds)
    snapshot,fingerprint=readings_state(db,study_id)
    result=solve(snapshot,gap_us)
    preview=PairingPreview(
        study_id=study_id,max_gap_seconds=gap_us/1e6,input_fingerprint=fingerprint,
        readings_snapshot=snapshot,result=result,status='preview',created_by=user.email,created_at=now(),
    )
    db.add(preview);db.flush()
    log(db,user,'pairing.preview',f'{study_id}/preview#{preview.id} gap={gap_us/1e6}s max={result["maximum_pairs"]} common={result["common_pairs"]}')
    db.commit()
    return _preview_view(db,preview)


def get_preview(db,preview_id):
    preview=db.get(PairingPreview,preview_id)
    if not preview: raise HTTPException(404,'配对预览不存在')
    return _preview_view(db,preview)


def list_previews(db,study_id):
    _require_study(db,study_id)
    rows=db.scalars(select(PairingPreview).where(PairingPreview.study_id==study_id).order_by(PairingPreview.id.desc()))
    return [_preview_view(db,p) for p in rows]


def confirm_preview(db,preview_id,user):
    preview=db.scalar(select(PairingPreview).where(PairingPreview.id==preview_id).with_for_update())
    if not preview: raise HTTPException(404,'配对预览不存在')
    # Lock the batch: reading edits and confirmation cannot interleave.
    db.scalar(select(Study).where(Study.id==preview.study_id).with_for_update())
    active=db.scalar(select(ActivePairing).where(ActivePairing.study_id==preview.study_id).with_for_update())

    if preview.status=='invalidated': raise HTTPException(409,'该预览已作废，请重新生成预览后再确认')
    if preview.status=='confirmed':
        return {'switched':bool(active and active.preview_id==preview.id),'preview':_preview_view(db,preview),
                'active_pairing':_active_view(db,active) if active else None}
    if not preview.result.get('pairs'):
        raise HTTPException(409,'该预览没有所有最优方案共有的配对；确认会让本批无可分析配对，已拒绝。请调整时差上限或核对读数')
    if not preview.result.get('pairs'):
        raise HTTPException(409,'该预览没有所有最优方案共有的配对；确认会让本批无可分析配对，已拒绝。请调整时差上限或核对读数')

    _,current=readings_state(db,preview.study_id)
    if current!=preview.input_fingerprint:
        preview.status='invalidated';preview.invalidated_at=now()
        preview.invalidate_reason='确认时读数已被并发修改，预览作废；该批原配对保持不变'
        log(db,user,'pairing.invalid',f'{preview.study_id}/preview#{preview.id} readings changed; active pairing retained')
        db.commit()
        kept=f'该批原配对（预览#{active.preview_id}）保持不变' if active else '该批此前没有已确认的配对'
        raise HTTPException(409,f'读数在预览生成后已被修改，旧预览作废；{kept}。请重新生成预览后再确认')

    preview.status='confirmed';preview.confirmed_at=now()
    previous=active.preview_id if active else None
    if active: active.preview_id=preview.id;active.confirmed_at=now()
    else: active=ActivePairing(study_id=preview.study_id,preview_id=preview.id,confirmed_at=now());db.add(active);db.flush()
    # One transaction switches the whole batch: analysis now sees exactly this pairing set.
    log(db,user,'pairing.confirm',f'{preview.study_id}/preview#{preview.id} common_pairs={len(preview.result.get("pairs",[]))} previous={previous}')
    db.commit()
    return {'switched':True,'preview':_preview_view(db,preview),'active_pairing':_active_view(db,active)}


def _active_view(db,active):
    view=record(active)
    preview=db.get(PairingPreview,active.preview_id)
    view['pairs']=preview.result.get('pairs',[]) if preview else []
    view['preview_status']=preview.status if preview else 'missing'
    return view


def active_pairing(db,study_id):
    _require_study(db,study_id)
    active=db.scalar(select(ActivePairing).where(ActivePairing.study_id==study_id))
    if not active: return None
    preview=db.get(PairingPreview,active.preview_id)
    _,current=readings_state(db,study_id)
    view=_active_view(db,active)
    view['study_id']=study_id
    view['stale']=bool(preview and current!=preview.input_fingerprint)
    return view


def frozen_snapshot(db,study_id):
    """Confirmed pairs this batch exposes to new analysis, or None if no pairing was confirmed.

    Raises 409 when the confirmed pairing was built from readings that no longer match,
    so analysis never silently freezes stale values.
    """
    active=db.scalar(select(ActivePairing).where(ActivePairing.study_id==study_id))
    if not active: return None
    preview=db.get(PairingPreview,active.preview_id)
    if not preview: return None
    _,current=readings_state(db,study_id)
    if current!=preview.input_fingerprint:
        raise HTTPException(409,f'该批已确认的配对（预览#{preview.id}）基于旧读数；请重新预览并确认后再发起分析')
    return [{'source_key':p['key'],'values':{'before':p['before_value'],'after':p['after_value']}}
            for p in preview.result.get('pairs',[])]
