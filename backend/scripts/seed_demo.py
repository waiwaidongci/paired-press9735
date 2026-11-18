import json
import numpy as np
from sqlalchemy import select,func
from datetime import datetime,timedelta,timezone
from src.config.database import Base,engine,Session
from src.config.domain import DOMAIN
from src.models.user import User
from src.models.study import Study
from src.models.observation import Observation
from src.models.pairing import ProcessReading,ActivePairing,PairingPreview
from src.models import run,review,audit
from src.utils.security import hash_password
from src.services import pairing as pairing_service

def _iso(minutes):
    return (datetime(2026,11,2,8,0,tzinfo=timezone.utc)+timedelta(minutes=minutes)).isoformat(timespec='microseconds').replace('+00:00','Z')

def seed():
    Base.metadata.create_all(engine)
    rng=np.random.default_rng(42)
    i=DOMAIN['index']
    with Session() as db:
        for role in ('admin','analyst','viewer'):
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)): db.add(User(email=email,role=role,password_hash=hash_password('Research2026!')))
        for k,name in enumerate(DOMAIN['names']):
            study=db.scalar(select(Study).where(Study.name==name))
            if not study:
                study=Study(name=name,source_filename=DOMAIN['slug']+f'-202611-{k+1}.csv',description='独立合成业务观测 / 固定种子42 / 仅用于方法与流程演示');db.add(study);db.flush()
            rows=[]
            if i==0:
                for n in range(120): rows.append({'moisture':round(float(np.clip(rng.normal(5.2+k*.7,.7),0,100)),3),'extract':round(float(np.clip(rng.normal(34+k*2,2.4),0,100)),3)})
            elif i==1:
                rows=[{'group':'A','success':87+k*2,'failure':13-k*2},{'group':'B','success':96-k,'failure':4+k}]
            elif i==2:
                for n in range(24):
                    b=float(rng.uniform(3,6));rows.append({'before':round(b,3),'after':round(max(.1,b+rng.normal(-.45+k*.3,.5)),3)})
            elif i==3:
                for n in range(90):
                    x=round(float(rng.uniform(4,28)),1);rows.append({'porosity':x,'absorption':round(max(0,x*(.18+k*.08)+rng.normal(0,1.5)),2)})
            else: rows=[{'p0':.08+k*.02,'p1':.1+k*.025,'cost_per_contact':.12+k*.04,'budget':3000+k*500}]
            for n,values in enumerate(rows):
                key=f'DEMO-{k+1}-{n+1:04}'
                if not db.scalar(select(Observation.id).where(Observation.study_id==study.id,Observation.source_key==key)):
                    db.add(Observation(study_id=study.id,source_key=key,values=values))
            if i==2 and k==0:
                # Two independently numbered uploads with jittered timestamps; labels deliberately differ.
                for n,values in enumerate(rows):
                    drift=int(rng.integers(-150,151))
                    for process,minutes in (('before',n*40),('after',n*40+drift)):
                        label=('OLD-%03d' if process=='before' else 'NEW-%03d')%(n+1)
                        if not db.scalar(select(ProcessReading.id).where(ProcessReading.study_id==study.id,ProcessReading.process==process,ProcessReading.label==label)):
                            db.add(ProcessReading(study_id=study.id,process=process,label=label,value=float(values[process]),measured_at=_iso(minutes),created_at=_iso(0)))
                extra=[('after','NEW-X01',int(rng.integers(0,900)),3.9),('before','OLD-X02',int(rng.integers(2000,2200)),4.4)]
                for process,label,minutes,value in extra:
                    if not db.scalar(select(ProcessReading.id).where(ProcessReading.study_id==study.id,ProcessReading.process==process,ProcessReading.label==label)):
                        db.add(ProcessReading(study_id=study.id,process=process,label=label,value=value,measured_at=_iso(minutes),created_at=_iso(0)))
        db.commit()
        # Give the demo batch a usable confirmed pairing built by the real solver, so the
        # 09:20 analysis step works out of the box. Insert-only: never overwrite operator work.
        demo=db.scalar(select(Study).where(Study.name==DOMAIN['names'][0]))
        if demo and not db.scalar(select(ActivePairing).where(ActivePairing.study_id==demo.id)):
            from src.utils.serializers import now
            owner=db.scalar(select(User).where(User.email=='admin@demo.local'))
            if owner:
                snapshot,fingerprint=pairing_service.readings_state(db,demo.id)
                result=pairing_service.solve(snapshot,3600*1_000_000)
                if result['pairs']:
                    preview=pairing_service.PairingPreview(
                        study_id=demo.id,max_gap_seconds=3600,input_fingerprint=fingerprint,
                        readings_snapshot=snapshot,result=result,status='confirmed',created_by=owner.email,
                        created_at=now(),confirmed_at=now())
                    db.add(preview);db.flush()
                    db.add(ActivePairing(study_id=demo.id,preview_id=preview.id,confirmed_at=now()))
                    db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in (User,Study,Observation,ProcessReading,PairingPreview,ActivePairing)}
        print(json.dumps(counts));return counts
if __name__=='__main__': seed()
