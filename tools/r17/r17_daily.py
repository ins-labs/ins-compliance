import json,sys,hashlib,time
sys.path.insert(0,'/opt/ins-compliance')
import checker
rp='/opt/ins-compliance/rules.yaml'
rules=checker.load_rules(rp,hashlib.sha256(open(rp,'rb').read()).hexdigest())
rows=[json.loads(l) for l in open(sys.argv[1])]
reg=checker.parse_registry(sys.argv[2])
gh={"issues":[r for r in rows if r['kind']=='issue'],"prs":[r for r in rows if r['kind']!='issue'],"registry":reg}
o=checker.run(rules,[],gh_data=gh,r17_baseline=True)
c=o["r17_baseline"]
c["owned_pct"]=round(100*c["owned"]/max(c["open_issues"],1),1)
c["due_pct"]=round(100*(c["open_issues"]-c["no_due"])/max(c["open_issues"],1),1)
print(json.dumps({"measured":True,"generated_utc":time.strftime("%FT%TZ",time.gmtime()),"r17":c},sort_keys=True))
