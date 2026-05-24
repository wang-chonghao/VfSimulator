import glob,re,os
runs=glob.glob('/home/lenovo/msprof_run/*vdup*vecsrc*_native_simexec/core0.veccore0.rvec.EXU.dump')
pat=re.compile(r'instr_name\s+(RV_[A-Z0-9_]+).*?exu_id:(\d+)')
all_ids=set(); per=[]
for p in runs:
    ids=[]
    with open(p,errors='ignore') as f:
        for ln in f:
            m=pat.search(ln)
            if m and m.group(1)=='RV_VDUP':
                ids.append(int(m.group(2)))
    if ids:
        s=sorted(set(ids)); all_ids.update(s); per.append((os.path.basename(os.path.dirname(p)),s,len(ids)))
print('RUNS_WITH_RV_VDUP=',len(per))
print('GLOBAL_EXU_IDS=',sorted(all_ids))
for name,s,cnt in sorted(per):
    print(name, 'exu_ids=',s,'count=',cnt)
