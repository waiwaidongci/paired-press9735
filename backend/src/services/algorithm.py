import math
import numpy as np
from scipy import stats

def finite_rows(snapshot,keys):
    if not snapshot: raise ValueError('空批次无法分析')
    result=[]
    for row in snapshot:
        values=row['values']
        if any(k not in values for k in keys): raise ValueError('观测字段不完整')
        x=[float(values[k]) for k in keys]
        if not all(math.isfinite(v) for v in x): raise ValueError('观测必须为有限数值')
        result.append(x)
    return np.asarray(result,dtype=float)

def sign_flip(differences,B=10000,seed=42):
    d=np.asarray(differences,dtype=float)
    if len(d)<2 or not np.isfinite(d).all(): raise ValueError('至少2对完整有限观测')
    observed=float(np.mean(d));threshold=abs(observed)-1e-12
    if len(d)<=18:
        count=0;total=2**len(d)
        for start in range(0,total,2048):
            codes=np.arange(start,min(total,start+2048),dtype=np.uint64)
            signs=2*((codes[:,None]>>np.arange(len(d),dtype=np.uint64))&1).astype(float)-1
            count+=int(np.sum(np.abs(signs@d/len(d))>=threshold))
        return observed,count/total,'精确枚举',total
    if not 200<=B<=20000: raise ValueError('置换次数超出范围')
    rng=np.random.default_rng(seed);count=0
    for start in range(0,B,200):
        signs=rng.choice([-1.,1.],size=(min(200,B-start),len(d)))
        count+=int(np.sum(np.abs(signs@d/len(d))>=threshold))
    return observed,(count+1)/(B+1),'Monte Carlo加一校正',B

def calculate(snapshot,params):
    x=finite_rows(snapshot,['before','after']);d=x[:,1]-x[:,0]
    mean,p,method,B=sign_flip(d,int(params.get('resamples',10000)))
    sd=float(np.std(d,ddof=1));se=sd/math.sqrt(len(d));q=float(stats.t.ppf(.975,len(d)-1));ci=[mean-q*se,mean+q*se]
    return {'metrics':{'配对样本量':len(d),'原工艺平均ΔE':float(x[:,0].mean()),'新工艺平均ΔE':float(x[:,1].mean()),'平均差(新减原)':mean,'差值标准差':sd,'双侧符号翻转p':p,'均值差95%t区间':ci,'计算方法':method,'枚举或模拟次数':B},'interpretation':f"新工艺比原工艺平均色差{'增加' if mean>=0 else '减少'}{abs(mean):.4f} ΔE；双侧符号翻转 p={p:.6g}（{method}）。",'caveat':'每对来自同一纸张，不同纸张相互独立。符号翻转检验要求零假设下差值符号可交换（如对称性）；t区间另依赖均值正态近似。未拒绝不意味着等效。','columns':['纸样编号','原工艺ΔE','新工艺ΔE','差(新−原)'],'rows':[[r['source_key'],float(v[0]),float(v[1]),float(v[1]-v[0])] for r,v in zip(snapshot,x)],'table_title':'每张纸样的配对差与完整性'}
