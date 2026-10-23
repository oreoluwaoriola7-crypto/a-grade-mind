const letters='abcde';
const subjectName=(s:string)=>s.replace(' (CRS)','').replace(' (IRS)','');
async function request(path:string,body?:any,method?:string){const token=sessionStorage.getItem('agm-token');const r=await fetch('/api/'+path,{method:method||(body?'POST':'GET'),headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body&&!(body instanceof FormData)?{'Content-Type':'application/json'}:{})},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Unable to complete request. Please check your details.');return data;}
const question=(q:any,i:number)=>({id:String(q.id??i),subject:q.s,text:q.q,options:Object.fromEntries(q.o.map((v:string,j:number)=>[letters[j],v])),correctAnswer:q.a==null?undefined:letters[q.a],imageUrl:q.img});
const result=(r:any)=>({...r,score:r.total,out_of:r.max,breakdown:r.subjects?.map((b:any)=>({subject:b.s,correct:b.c,total:b.n,score:b.p}))});
const attempt=(a:any)=>a?({...a,kind:a.kind==='test'?'objective':'cbt',subjects:[...new Set(a.questions.map((q:any)=>q.s))],questions:a.questions.map(question),answers:Object.fromEntries(Object.entries(a.answers||{}).map(([k,v]:any)=>[k,letters[v]])),expires_at:new Date(a.deadline*1000).toISOString()}):null;
const answers=(b:any)=>({answers:Object.fromEntries(Object.entries(b.answers||{}).map(([k,v]:any)=>[k,letters.indexOf(v)]))});
let papers:any[]=[];
let saving:Promise<any>=Promise.resolve();
const paper=(p:any)=>({...p,id:String(p.id),status:p.published?'published':'needs_review',question_count:p.count,duration_minutes:p.minutes,questions:[]});
export async function api(path:string,body?:any,method?:string):Promise<any>{
 if(['auth/login','auth/register','auth/change-password'].includes(path)){const r=await request(path,body,method);sessionStorage.setItem('agm-token',r.token);return r.user;}
 if(path==='auth/logout'){sessionStorage.removeItem('agm-token');return {};}
 if(path==='cbt/active')return attempt(await request('attempts/active'));
 if(path==='cbt/start')return attempt(await request('exams/start',{subjects:body.subjects.map(subjectName)}));
 let match=path.match(/^(?:cbt|objectives\/attempts)\/(\d+)\/(answers|submit)$/);
 if(match){const id=match[1];if(match[2]==='answers'){saving=saving.catch(()=>{}).then(()=>request(`attempts/${id}/save`,answers(body),'PUT'));return saving;}await saving.catch(()=>{});return result(await request(`attempts/${id}/submit`,answers(body),'POST'));}
 if(path==='objectives')return (await request('tests')).map(paper);
 match=path.match(/^objectives\/(\d+)\/start$/);if(match)return attempt(await request(`tests/${match[1]}/start`,{}));
 if(path==='teacher/submissions')return [];
 if(path==='teacher/integrations'){await request('health');return {database:'connected',phone_auth:'phone and password',aloc:'configured'};}
 if(path==='teacher/integrations/check-aloc'){const r=await request('aloc/test');return {checks:[{subject:'Chemistry',status:r.got?'available':'no questions'}],note:'Chemistry API request completed. Other subjects are checked when starting practice.'};}
 if(path==='teacher/objectives'){papers=(await request('teacher/tests')).map(paper);return papers;}
 if(path==='teacher/objectives/upload'){body.set('minutes',body.get('duration_minutes'));body.set('publish','false');const r=await request('teacher/tests/upload',body);await api('teacher/objectives');return api(`teacher/objectives/${r.id}`);}
 match=path.match(/^teacher\/objectives\/(\d+)(?:\/(publish|results))?$/);
 if(match){const id=match[1];if(match[2]==='publish'){for(const q of body.questions)await request(`teacher/tests/${id}/questions/${q.id}`,{a:letters.indexOf(q.correctAnswer)},'PUT');return request(`teacher/tests/${id}/toggle`,{});}
 if(match[2]==='results')return (await request(`teacher/tests/${id}/results`)).map((r:any)=>({...r,result:{score:r.score}}));
 if(!papers.length)await api('teacher/objectives');const p=papers.find(p=>p.id===id);return {...p,questions:(await request(`teacher/tests/${id}`)).map(question)};}
 return request(path,body,method);
}
