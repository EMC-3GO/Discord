let managerToken=null, saved=null;
const $=id=>document.getElementById(id);
function show(view){document.querySelectorAll('main>section').forEach(s=>s.hidden=s.id!==view);$('message').hidden=true;window.scrollTo(0,0);}
function message(text){$('message').textContent=text;$('message').hidden=false;}
async function api(path,data){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'OneTimeFile'},body:JSON.stringify(data)});let result;try{result=await response.json();}catch{throw Error('通信に失敗しました。時間をおいてお試しください。');}if(!response.ok)throw Error((result.error||'処理に失敗しました。')+(result.code?'（'+result.code+'）':''));return result;}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{if(location.hash)history.replaceState(null,'',location.pathname);show(b.dataset.view);}));
function renderDocument(data,manager=false){show('document');$('timer').hidden=true;$('mode-label').textContent=manager?'OWNER / 作成者専用':'PRIVATE FILE';$('doc-title').textContent=data.name;$('doc-content').textContent=data.content;$('delete').hidden=!manager;$('manager-hint').hidden=!manager;}

async function formAction(form,action){const button=form.querySelector('[type=submit]');button.disabled=true;try{await action(Object.fromEntries(new FormData(form)));}catch(e){message(e.message);}finally{button.disabled=false;}}
$('create-form').addEventListener('submit',e=>{e.preventDefault();formAction(e.target,async data=>{saved=await api('/api/files',data);e.target.reset();show('saved');$('saved-number').textContent=saved.number;$('manager-url').value=location.origin+'/#manage='+saved.manager;});});
$('find-form').addEventListener('submit',e=>{e.preventDefault();formAction(e.target,async data=>{const result=await api('/api/open',data);e.target.reset();renderDocument(result);});});
async function copy(text){try{await navigator.clipboard.writeText(text);message('コピーしました。');}catch{message('コピーできませんでした。表示された内容を選択してコピーしてください。');}}
$('copy-number').onclick=()=>copy(saved.number);$('copy-manager').onclick=()=>copy($('manager-url').value);$('go-manager').onclick=()=>{location.hash='manage='+saved.manager;};
async function loadManager(){if(!location.hash.startsWith('#manage='))return;managerToken=location.hash.slice(8);try{renderDocument(await api('/api/manage',{token:managerToken}),true);}catch(e){show('find');message(e.message);}}
window.addEventListener('hashchange',loadManager);loadManager();
$('delete').onclick=async()=>{if(!confirm('ファイルを削除します。この操作は取り消せません。'))return;try{await api('/api/manage',{token:managerToken,delete:true});$('doc-content').textContent='ファイルを削除しました。';$('doc-title').textContent='削除完了';$('delete').hidden=true;$('timer').hidden=true;}catch(e){message(e.message);}};

async function checkService(){
  try{
    const response=await fetch('/api/status',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok||!data.ready){
      $('service-status').textContent=(data.error||'現在、保存・閲覧機能を利用できません。')+(data.code?'（'+data.code+'）':'');
      $('service-status').hidden=false;
    }
  }catch{
    $('service-status').textContent='保存サービスの状態を確認できませんでした。通信状況を確認して再読み込みしてください。';
    $('service-status').hidden=false;
  }
}
checkService();
