(function(){
  const apiBase = '';
  let currentPage = 1;
  const perPage = 25;
  let selectedTx = null;
  const ACCOUNTS = {
    'الكريمي':'حساب حاسب 2325013',
    'محفظة جيب':'محفظة جيب 739942424',
    'نجم حوالة':'نجم حوالة - باسم خالد وليد عبدالله المسني - 784983835',
    'الامتياز':'الامتياز - باسم خالد وليد عبدالله المسني - 784983835',
    'القطيبي':'القطيبي - الشبكة الموحدة (اختر SAR أو USD عند التحويل)'
  };

  const els = {
    tbody: document.querySelector('#txTable tbody'),
    statusFilter: document.getElementById('statusFilter'),
    showHidden: document.getElementById('showHidden'),
    refreshBtn: document.getElementById('refreshBtn'),
    prevPage: document.getElementById('prevPage'),
    nextPage: document.getElementById('nextPage'),
    pageInfo: document.getElementById('pageInfo'),
    details: document.getElementById('details'),
    proofArea: document.getElementById('proofArea'),
    loginBtn: document.getElementById('loginBtn'),
    logoutBtn: document.getElementById('logoutBtn'),
    username: document.getElementById('username'),
    password: document.getElementById('password'),
    approveBtn: document.getElementById('approveBtn'),
    rejectBtn: document.getElementById('rejectBtn'),
    completeBtn: document.getElementById('completeBtn'),
    hideBtn: document.getElementById('hideBtn')
  };

  function getToken(){return localStorage.getItem('admin_token')}
  function setToken(t){localStorage.setItem('admin_token', t)}
  function clearToken(){localStorage.removeItem('admin_token')}

  async function login(){
    const u = els.username.value.trim();
    const p = els.password.value.trim();
    if(!u||!p){alert('يرجى تعبئة اسم المستخدم وكلمة المرور');return}
    try{
      const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u,password:p})});
      if(!r.ok){const t=await r.text(); alert('فشل تسجيل الدخول'); return}
      const j = await r.json(); setToken(j.access_token); renderAuth(); setupWS(); loadPage();
    }catch(e){console.error(e);alert('خطأ في تسجيل الدخول')}
  }

  function renderAuth(){
    if(getToken()){els.loginBtn.style.display='none';els.logoutBtn.style.display='inline-block'; setupWS();}
    else{els.loginBtn.style.display='inline-block';els.logoutBtn.style.display='none';}
  }

  async function fetchTransactions(){
    const status = els.statusFilter.value;
    const show_hidden = els.showHidden.checked ? 'true' : 'false';
    const query = document.getElementById('queryInput') ? document.getElementById('queryInput').value.trim() : '';
    const url = `/api/transactions?page=${currentPage}&per_page=${perPage}&status=${encodeURIComponent(status!=='ALL'?status:'')}&show_hidden=${show_hidden}` + (query?`&q=${encodeURIComponent(query)}`:'');
    try{
      const headers = {};
      const token = getToken();
      if(token) headers['Authorization'] = 'Bearer ' + token;
      const r = await fetch(url, {headers});
      if(!r.ok){console.error('fetch tx failed', r.status); return []}
      const data = await r.json();
      return data;
    }catch(e){console.error(e);return []}
  }

  function renderTable(txs){
    els.tbody.innerHTML='';
    if(!txs||txs.length===0){els.tbody.innerHTML='<tr><td colspan="8">لا توجد بيانات</td></tr>';return}
    const colCount = 13;
    const groupByUser = document.getElementById('groupByUser') && document.getElementById('groupByUser').checked;
    if(groupByUser){
      // group into user folders
      const users = {};
      for(const tx of txs){ users[tx.user_id] = users[tx.user_id] || []; users[tx.user_id].push(tx); }
      els.tbody.innerHTML = '';
      for(const uid of Object.keys(users)){
        const trh = document.createElement('tr'); trh.innerHTML = `<td colspan="${colCount}">مستخدم: ${uid} — ${users[uid].length} طلب</td>`; trh.className='user-row'; els.tbody.appendChild(trh);
        for(const tx of users[uid]){
          const tr = document.createElement('tr');
          tr.dataset.tx = tx.tx_id;
          tr.innerHTML = `
            <td class="col-txid">${tx.tx_id}</td>
            <td class="col-type">${tx.type}</td>
            <td class="col-user">${tx.user_id}</td>
            <td class="col-amount">${Number(tx.amount).toFixed(2)}</td>
            <td class="col-total">${tx.total_with_fee?Number(tx.total_with_fee).toFixed(2):'0.00'}</td>
            <td class="col-network">${tx.network||''}</td>
            <td class="col-wallet">${tx.wallet_address||''}</td>
            <td class="col-payment">${tx.payment_info?tx.payment_info:(tx.payment_method||'')}</td>
            <td class="col-currency">${tx.currency||''}</td>
            <td class="col-afterconv">${tx.total_after_conversion?Number(tx.total_after_conversion).toFixed(2):''}</td>
            <td class="col-hash">${tx.tx_hash_or_code||''}</td>
            <td class="col-status">${tx.status}</td>
            <td class="col-created">${tx.created_at}</td>
              `;
          tr.addEventListener('click', ()=>{selectTx(tx)});
          els.tbody.appendChild(tr);
        }
      }
      return;
    }
    for(const tx of txs){
      const tr = document.createElement('tr');
      tr.dataset.tx = tx.tx_id;
      tr.innerHTML = `
        <td class="col-txid">${tx.tx_id}</td>
        <td class="col-type">${tx.type}</td>
        <td class="col-user">${tx.user_id}</td>
        <td class="col-amount">${Number(tx.amount).toFixed(2)}</td>
        <td class="col-total">${tx.total_with_fee?Number(tx.total_with_fee).toFixed(2):'0.00'}</td>
        <td class="col-network">${tx.network||''}</td>
        <td class="col-wallet">${tx.wallet_address||''}</td>
        <td class="col-payment">${tx.payment_info?tx.payment_info:(tx.payment_method||'')}</td>
        <td class="col-currency">${tx.currency||''}</td>
        <td class="col-afterconv">${tx.total_after_conversion?Number(tx.total_after_conversion).toFixed(2):''}</td>
        <td class="col-hash">${tx.tx_hash_or_code||''}</td>
        <td class="col-status">${tx.status}</td>
        <td class="col-created">${tx.created_at}</td>
      `;
      tr.addEventListener('click', ()=>{selectTx(tx)});
      els.tbody.appendChild(tr);
    }
  }

  // Columns panel handlers
  document.getElementById('colsBtn')?.addEventListener('click', ()=>{
    const panel = document.getElementById('colsPanel'); if(!panel) return; panel.style.display = panel.style.display==='none'?'block':'none';
  });
  document.querySelectorAll('#colsPanel input[type=checkbox]').forEach(cb=>{
    cb.addEventListener('change', ()=>{
      const col = cb.getAttribute('data-col'); const checked = cb.checked;
      // toggle header
      document.querySelectorAll('th.'+col).forEach(h=> h.style.display = checked ? '' : 'none');
      // toggle cells
      document.querySelectorAll('td.'+col).forEach(td=> td.style.display = checked ? '' : 'none');
    });
  });

  function makeField(label, value){
    const row = document.createElement('div'); row.className='field-row';
    const lab = document.createElement('div'); lab.className='field-label';
    // choose icon by field
    const iconMap = {
      'رقم الطلب':'icon-id', 'معرف العميل':'icon-user', 'النوع':'icon-id', 'الكمية (USDT)':'icon-money', 'الإجمالي بعد العمولة':'icon-money', 'الشبكة':'icon-id', 'المحفظة/العنوان':'icon-wallet', 'طريقة الدفع':'icon-id', 'معلومات الدفع':'icon-id', 'العملة':'icon-money', 'المبلغ بعد التحويل':'icon-money', 'رمز/هاش العملية':'icon-id', 'مخفي':'icon-hide', 'الحالة':'icon-reports', 'تاريخ الإنشاء':'icon-calendar'
    };
    const ic = document.createElement('span'); ic.className='material-icons';
    const iconNameMap = {
      'رقم الطلب':'badge', 'معرف العميل':'person', 'النوع':'badge', 'الكمية (USDT)':'attach_money', 'الإجمالي بعد العمولة':'paid', 'الشبكة':'link', 'المحفظة/العنوان':'account_balance_wallet', 'طريقة الدفع':'payment', 'معلومات الدفع':'info', 'العملة':'attach_money', 'المبلغ بعد التحويل':'account_balance', 'رمز/هاش العملية':'vpn_key', 'مخفي':'visibility_off', 'الحالة':'assessment', 'تاريخ الإنشاء':'calendar_today'
    };
    ic.textContent = iconNameMap[label] || 'badge';
    const labText = document.createElement('span'); labText.textContent = label;
    lab.appendChild(ic); lab.appendChild(labText);
    const val = document.createElement('div'); val.className='field-value'; val.textContent = value ?? '';
    // mask sensitive fields (wallet, payment info)
    function maskString(s){ if(!s) return ''; const str = String(s); if(str.length<=8) return str.replace(/.(?=.{2})/g,'*'); return str.slice(0,4) + '••••' + str.slice(-4); }
    if(label === 'المحفظة/العنوان' || label === 'معلومات الدفع'){
      const full = String(value||'');
      val.textContent = maskString(full);
      const reveal = document.createElement('button'); reveal.className='copy-btn'; reveal.type='button'; reveal.textContent='عرض';
      let revealed=false, timeoutId=null;
      reveal.addEventListener('click', ()=>{
        if(!revealed){ val.textContent = full; reveal.textContent='إخفاء'; revealed=true; timeoutId = setTimeout(()=>{ if(revealed){ val.textContent = maskString(full); reveal.textContent='عرض'; revealed=false } }, 8000); }
        else { if(timeoutId) clearTimeout(timeoutId); val.textContent = maskString(full); reveal.textContent='عرض'; revealed=false; }
      });
      // override copy button to copy full
      const originalCopyHandler = async ()=>{ try{ await navigator.clipboard.writeText(full); btn.classList.add('copied'); btn.innerHTML = `<span class="material-icons">content_copy</span> نسخ ✓`; setTimeout(()=>{btn.classList.remove('copied'); btn.innerHTML = `<span class="material-icons">content_copy</span> نسخ`;},1200);}catch(e){console.error(e)} };
      btn.removeEventListener && btn.removeEventListener('click', ()=>{});
      btn.addEventListener('click', originalCopyHandler);
      row.appendChild(lab); row.appendChild(val); row.appendChild(reveal); row.appendChild(btn);
      return row;
    }
    const btn = document.createElement('button'); btn.className='copy-btn'; btn.type='button'; btn.title='نسخ'; btn.innerHTML = `<span class="material-icons">content_copy</span> نسخ`;
    // default copy handler (will be overridden for masked fields)
    const defaultCopyHandler = async ()=>{
      try{
        await navigator.clipboard.writeText(String(value ?? ''));
        btn.classList.add('copied');
        btn.innerHTML = `<span class="material-icons">content_copy</span> نسخ ✓`;
        setTimeout(()=>{btn.classList.remove('copied'); btn.innerHTML = `<span class="material-icons">content_copy</span> نسخ`;},1200);
      }catch(e){console.error(e)}
    };
    btn.addEventListener('click', defaultCopyHandler);
    row.appendChild(lab); row.appendChild(val); row.appendChild(btn);
    return row;
  }

  function copyAllJson(){ if(!selectedTx) return; try{ const json = JSON.stringify(selectedTx, null, 2); navigator.clipboard.writeText(json).then(()=>{alert('تم نسخ JSON')}).catch(e=>{console.error(e);alert('فشل النسخ')}); }catch(e){console.error(e);alert('خطأ')}}

  function selectTx(tx){
    selectedTx = tx;
    // clear details and render labeled fields
    els.details.innerHTML = '';
    const fields = [
      ['رقم الطلب', tx.tx_id],
      ['معرف العميل', tx.user_id],
      ['النوع', tx.type],
      ['الكمية (USDT)', Number(tx.amount).toFixed(2)],
      ['الإجمالي بعد العمولة', tx.total_with_fee?Number(tx.total_with_fee).toFixed(2):'0.00'],
      ['الشبكة', tx.network||''],
      ['المحفظة/العنوان', tx.wallet_address||''],
      ['طريقة الدفع', tx.payment_method||''],
      ['معلومات الدفع', tx.payment_info||''],
      ['العملة', tx.currency||''],
      ['المبلغ بعد التحويل', tx.total_after_conversion||''],
      ['رمز/هاش العملية', tx.tx_hash_or_code||''],
      ['مخفي', tx.hidden ? 'نعم' : 'لا'],
      ['الحالة', tx.status],
      ['تاريخ الإنشاء', tx.created_at]
    ];
    for(const f of fields){ els.details.appendChild(makeField(f[0], String(f[1] ?? ''))); }
    renderProof(tx);
    // if BUY type, show account selection + send button
    const type = (String(tx.type||'')).toUpperCase();
    if(type.includes('BUY') || type.includes('DEPOSIT')){
      const accWrap = document.createElement('div'); accWrap.style.marginTop='8px';
      const sel = document.createElement('select'); sel.id='accountSelect'; sel.style.padding='6px';
      const opt0 = document.createElement('option'); opt0.value=''; opt0.textContent='اختر جهة التحويل'; sel.appendChild(opt0);
      for(const k of Object.keys(ACCOUNTS)){ const o=document.createElement('option'); o.value=k; o.textContent=k; sel.appendChild(o); }
      const note = document.createElement('input'); note.placeholder='ملاحظة اختيارية'; note.style.marginInlineStart='8px';
      const sendBtn = document.createElement('button'); sendBtn.textContent='إرسال بيانات الدفع'; sendBtn.style.marginInlineStart='8px';
      sendBtn.addEventListener('click', async ()=>{
        const key = sel.value; if(!key){ alert('اختر جهة التحويل'); return }
        try{
          const token = getToken(); const headers={'Content-Type':'application/json'}; if(token) headers['Authorization']='Bearer '+token;
          const r = await fetch(`/api/transaction/${tx.tx_id}/send_payment_info`, {method:'POST', headers, body: JSON.stringify({account_key:key, note: note.value||''})});
          if(!r.ok){ const t=await r.text(); alert('فشل الإرسال'); return }
          alert('تم إرسال بيانات الدفع للمستخدم');
        }catch(e){console.error(e); alert('خطأ بالإرسال')}
      });
      accWrap.appendChild(sel); accWrap.appendChild(note); accWrap.appendChild(sendBtn);
      els.details.appendChild(accWrap);
    }
  }

  async function renderProof(tx){
    els.proofArea.innerHTML='';
    if(tx.proof_file_id){
      const ids = String(tx.proof_file_id).split(',').map(s=>s.trim()).filter(Boolean);
      // gallery state
      const galleryImages = [];
      for(const id of ids){
        if(tx.proof_file_type==='photo'){
          const img = document.createElement('img');
          (async ()=>{
            try{
              const headers = {};
              const token = getToken(); if(token) headers['Authorization']='Bearer '+token;
              const r = await fetch(`/proofs/${id}`, {headers});
              if(!r.ok){ const err = document.createElement('div'); err.textContent='فشل تحميل الصورة'; els.proofArea.appendChild(err); return }
              const blob = await r.blob();
              const url = URL.createObjectURL(blob);
              img.src = url;
              galleryImages.push(url);
            }catch(e){console.error(e); const err = document.createElement('div'); err.textContent='فشل تحميل الصورة'; els.proofArea.appendChild(err)}
          })();
          img.className='proof-thumb';
          img.addEventListener('click', ()=>{ openGalleryWith(galleryImages, galleryImages.indexOf(img.src||'')) });
          els.proofArea.appendChild(img);
        }else{
          const a = document.createElement('a');
          a.href = `/proofs/${id}`;
          a.textContent = 'تحميل الإثبات';
          a.target = '_blank';
          els.proofArea.appendChild(a);
        }
      }
      const openBtn = document.getElementById('openGalleryBtn');
      if(openBtn){ if(galleryImages.length){ openBtn.style.display='inline-block'; openBtn.onclick = ()=> openGalleryWith(galleryImages, 0); } else { openBtn.style.display='none'; openBtn.onclick = null; } }
    }else{
      els.proofArea.textContent = 'لا توجد صورة متاحة';
    }
  }

  // gallery functions
  let _galleryState = {imgs:[], idx:0};
  function openGalleryWith(imgs, idx){
    if(!imgs || imgs.length===0) return;
    _galleryState.imgs = imgs; _galleryState.idx = Math.max(0, Math.min(idx||0, imgs.length-1));
    const modal = document.getElementById('galleryModal');
    const imgEl = document.getElementById('galleryImg');
    imgEl.src = _galleryState.imgs[_galleryState.idx];
    modal.style.display = 'flex';
  }
  function closeGallery(){ document.getElementById('galleryModal').style.display='none'; }
  function galleryNext(){ _galleryState.idx = (_galleryState.idx+1)%_galleryState.imgs.length; document.getElementById('galleryImg').src = _galleryState.imgs[_galleryState.idx]; }
  function galleryPrev(){ _galleryState.idx = (_galleryState.idx-1+_galleryState.imgs.length)%_galleryState.imgs.length; document.getElementById('galleryImg').src = _galleryState.imgs[_galleryState.idx]; }

  // copy all / export CSV
  function copyAll(){ if(!selectedTx) return; const map = {
    'رقم الطلب': selectedTx.tx_id,
    'معرف العميل': selectedTx.user_id,
    'النوع': selectedTx.type,
    'الكمية (USDT)': Number(selectedTx.amount).toFixed(2),
    'الإجمالي بعد العمولة': selectedTx.total_with_fee?Number(selectedTx.total_with_fee).toFixed(2):'0.00',
    'الشبكة': selectedTx.network||'',
    'المحفظة/العنوان': selectedTx.wallet_address||'',
    'طريقة الدفع': selectedTx.payment_method||'',
    'معلومات الدفع': selectedTx.payment_info||'',
    'العملة': selectedTx.currency||'',
    'المبلغ بعد التحويل': selectedTx.total_after_conversion||'',
    'رمز/هاش العملية': selectedTx.tx_hash_or_code||'',
    'مخفي': selectedTx.hidden? 'نعم':'لا',
    'الحالة': selectedTx.status,
    'تاريخ الإنشاء': selectedTx.created_at
  }; const parts = []; for(const k of Object.keys(map)){ parts.push(k+': '+map[k]); } const txt = parts.join('\n'); navigator.clipboard.writeText(txt).then(()=>{alert('تم نسخ جميع الحقول')}).catch(e=>{console.error(e);alert('فشل النسخ')}); }

  function exportCsv(){ if(!selectedTx) return; const keys=['tx_id','user_id','type','amount','total_with_fee','network','wallet_address','payment_method','payment_info','currency','total_after_conversion','tx_hash_or_code','hidden','status','created_at']; const vals = keys.map(k=>`"${String(selectedTx[k] ?? '')}"`); const csv = keys.join(',') + '\n' + vals.join(','); const blob = new Blob([csv], {type:'text/csv;charset=utf-8;'}); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `tx_${selectedTx.tx_id}.csv`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url); }

  async function loadPage(){
    const txs = await fetchTransactions();
    renderTable(txs);
    els.pageInfo.textContent = `صفحة ${currentPage}`;
  }

  // Settings
  async function fetchSettings(){
    try{
      const headers = {};
      const token = getToken(); if(token) headers['Authorization'] = 'Bearer '+token;
      const r = await fetch('/api/settings', {headers});
      if(!r.ok) return {};
      return await r.json();
    }catch(e){console.error(e);return {}};
  }

  async function renderSettings(){
    const s = await fetchSettings();
    ['rate_USD','rate_YER','rate_SAR','rate_buy_USD','rate_sell_USD','rate_buy_YER','rate_sell_YER','rate_buy_SAR','rate_sell_SAR','fee_buy_percent','fee_sell_percent'].forEach(k=>{
      const el = document.getElementById(k);
      if(el) el.value = s[k] ?? '';
    });
  }

  async function saveSettings(){
    const token = getToken();
    const headers = {'Content-Type':'application/json'};
    if(token) headers['Authorization']='Bearer '+token;
    const keys = ['rate_USD','rate_YER','rate_SAR','rate_buy_USD','rate_sell_USD','rate_buy_YER','rate_sell_YER','rate_buy_SAR','rate_sell_SAR','fee_buy_percent','fee_sell_percent'];
    for(const k of keys){
      const v = document.getElementById(k).value;
      await fetch('/api/settings', {method:'POST', headers, body: JSON.stringify({key:k, value:v})});
    }
    alert('تم حفظ الإعدادات');
  }

  // Reports (enhanced)
  let _reportChart = null;
  async function fetchReports(period='month', compare=false){
    try{
      const headers = {};
      const token = getToken(); if(token) headers['Authorization'] = 'Bearer '+token;
      const url = `/api/reports?period=${encodeURIComponent(period)}&compare=${compare?1:0}`;
      const r = await fetch(url, {headers});
      if(!r.ok) return null;
      return await r.json();
    }catch(e){console.error(e);return null}
  }

  function formatNum(v){ const n = Number(v||0); const f = new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(Math.abs(n)); return (n<0?'-':'') + f; }

  async function renderReports(){
    const isFull = document.body.classList.contains('fullpage');
    const periodEl = document.getElementById(isFull? 'reportPeriodFull':'reportPeriod');
    const compareEl = document.getElementById(isFull? 'comparePrevFull':'comparePrev');
    const period = periodEl ? periodEl.value : 'month';
    const compare = compareEl ? compareEl.checked : false;
    const data = await fetchReports(period, compare);
    const summary = document.getElementById('reportsSummary');
    const cardsWrap = document.getElementById('reportCards');
    if(!data){summary.textContent='فشل تحميل التقرير'; if(cardsWrap) cardsWrap.innerHTML=''; return}

    const total_ops = data.total_ops || 0;
    const sum_amount = Number(data.sum_amount || 0);
    const sum_total = Number(data.sum_total || 0);
    const sum_fee = Number(data.sum_fee || data.fees || 0);
    const profit = sum_fee || (sum_total - sum_amount);
    const profit_margin = sum_total? (profit / sum_total * 100) : 0;
    const avg = total_ops? (sum_amount / total_ops) : 0;

    summary.innerHTML = `الفترة: <b>${period}</b> — إجمالي الطلبات: <b>${total_ops}</b>`;
    // cards
    if(cardsWrap) cardsWrap.innerHTML = '';
    const cards = [
      {title:'إجمالي العمليات', value: total_ops, sub:''},
      {title:'إجمالي الكمية', value: formatNum(sum_amount), sub:'USDT'},
      {title:'إجمالي المدفوعات', value: formatNum(sum_total), sub:'قيمة'} ,
      {title:'الإجمالي كعمولة/ربح', value: formatNum(profit), sub:''},
      {title:'هامش الربح', value: profit_margin.toFixed(2)+' %', sub:''},
      {title:'متوسط الطلب', value: formatNum(avg), sub:'USDT'}
    ];
    for(const c of cards){ const div=document.createElement('div'); div.className='report-card'; div.innerHTML = `<h4>${c.title}</h4><div class="value">${c.value}</div><div class="sub">${c.sub||''}</div>`; cardsWrap.appendChild(div); }

    // top lists (customers / buyers / sellers)
    const topEl = document.getElementById(isFull? 'topListsFull':'topLists');
    if(topEl){ topEl.innerHTML = ''; const makeList = (title, rows)=>{ const w=document.createElement('div'); w.className='report-card'; w.innerHTML = `<h4>${title}</h4>`; const ul=document.createElement('ul'); ul.style.paddingLeft='14px'; if(!rows || rows.length===0){ ul.innerHTML='<li>لا بيانات</li>'; } else { rows.forEach(r=>{ const li=document.createElement('li'); li.textContent = `المستخدم ${r.user_id} — ${formatNum(r.sum_total)} (${r.count})`; ul.appendChild(li); }); } w.appendChild(ul); return w; };
      if(data.top_customers) topEl.appendChild(makeList('أكبر العملاء (حسب مبلغ المعاملات)', data.top_customers));
      if(data.top_buyers) topEl.appendChild(makeList('أكبر المشترين', data.top_buyers));
      if(data.top_sellers) topEl.appendChild(makeList('أكبر البائعين', data.top_sellers));
    }

    // chart
    const chartEl = document.getElementById('reportsChart');
    const labels = [];
    const values = [];
    if(data.chart_rows && data.chart_rows.length){
      for(const row of data.chart_rows){
        if(Array.isArray(row)){ labels.push(String(row[0])); values.push(Number(row[1])); }
        else if(row.label && row.value){ labels.push(String(row.label)); values.push(Number(row.value)); }
        else { labels.push(Object.keys(row)[0]); values.push(Number(Object.values(row)[0])); }
      }
    }
    if(_reportChart){ _reportChart.data.labels = labels; _reportChart.data.datasets[0].data = values; _reportChart.update(); }
    else{
      const ctx = chartEl.getContext('2d');
      _reportChart = new Chart(ctx, { type:'bar', data:{ labels, datasets:[{ label:'القيمة', data:values, backgroundColor:'rgba(59,130,246,0.6)' }]}, options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}} } });
    }

    // comparison: if backend returned previous totals
    if(compare && data.prev_sum_total!=null){
      const prev = Number(data.prev_sum_total||0);
      const diff = sum_total - prev;
      const pct = prev? (diff/prev*100):0;
      const cmp = document.createElement('div'); cmp.className='report-card'; cmp.innerHTML = `<h4>التغيير مقارنة بالفترة السابقة</h4><div class="value">${formatNum(diff)} (${pct.toFixed(2)}%)</div><div class="sub">السابق: ${formatNum(prev)}</div>`;
      cardsWrap.appendChild(cmp);
    }
    // buy/sell distribution
    if(data.type_counts){
      const bs = data.type_counts; const labelsBS = Object.keys(bs); const valsBS = Object.values(bs);
      const buySellCanvas = document.getElementById('buySellChart');
      if(buySellCanvas){
        try{
          if(window._buySellChart){ window._buySellChart.data.labels = labelsBS; window._buySellChart.data.datasets[0].data = valsBS; window._buySellChart.update(); }
          else{ const ctx2 = buySellCanvas.getContext('2d'); window._buySellChart = new Chart(ctx2, {type:'pie', data:{labels:labelsBS, datasets:[{data:valsBS, backgroundColor:['#0ea5e9','#34d399','#f97316','#f43f5e']}]}, options:{plugins:{legend:{position:'bottom'}}}}); }
        }catch(e){console.error(e)}
      }
      // also create/destroy a full-chart counterpart if in fullpage
      if(document.body.classList.contains('fullpage')){
        const bsFull = document.getElementById('buySellChartFull');
        if(bsFull){ try{ if(window._buySellChartFull){ window._buySellChartFull.data.labels = labelsBS; window._buySellChartFull.data.datasets[0].data = valsBS; window._buySellChartFull.update(); } else { const ctx3 = bsFull.getContext('2d'); window._buySellChartFull = new Chart(ctx3, {type:'pie', data:{labels:labelsBS, datasets:[{data:valsBS, backgroundColor:['#0ea5e9','#34d399','#f97316','#f43f5e']}]}, options:{plugins:{legend:{position:'bottom'}}}}); } }catch(e){console.error(e)} }
      }
    }

    // performance indicator
    const perf = document.getElementById('perfIndicator');
    const perfFull = document.getElementById('perfIndicatorFull');
    const perfHtml = (data.prev_sum_total!=null) ? ( ()=>{ const prev = Number(data.prev_sum_total||0); const diff = sum_total - prev; const pct = prev? (diff/prev*100):0; const up = diff>0; const col = up ? '#16a34a' : '#ef4444'; return `<div style="font-weight:700;color:${col}">${up? '▲':'▼'} ${pct.toFixed(2)}% مقارنة بالفترة السابقة</div><div style="font-size:13px;color:#64748b">التغير: ${formatNum(diff)} — السابق: ${formatNum(prev)}</div>` } )() : '';
    if(perf) perf.innerHTML = perfHtml;
    if(perfFull) perfFull.innerHTML = perfHtml;
  }

  // report controls
  const rp = document.getElementById('reportPeriod'); if(rp) rp.addEventListener('change', renderReports);
  const rc = document.getElementById('comparePrev'); if(rc) rc.addEventListener('change', renderReports);
  const rr = document.getElementById('refreshReports'); if(rr) rr.addEventListener('click', renderReports);

  // view switching
  function showView(view){
    document.getElementById('ordersPane').style.display = view==='orders' ? '' : 'none';
    document.getElementById('reportsPane').style.display = view==='reports' ? '' : 'none';
    document.getElementById('settingsPane').style.display = view==='settings' ? '' : 'none';
  }

  // full page reports toggles
  document.getElementById('openReportsFull').addEventListener('click', ()=>{
    const btn = document.getElementById('openReportsFull');
    const opening = !document.body.classList.contains('fullpage');
    document.body.classList.toggle('fullpage');
    if(opening){
      btn.textContent = 'إغلاق صفحة كاملة';
      renderReports();
      const srcCards = document.getElementById('reportCards');
      const dstCards = document.getElementById('reportCardsFull');
      if(srcCards && dstCards) dstCards.innerHTML = srcCards.innerHTML;
      // copy top lists
      const srcTop = document.getElementById('topLists'); const dstTop = document.getElementById('topListsFull'); if(srcTop && dstTop) dstTop.innerHTML = srcTop.innerHTML;
      // create full chart
      const dstChart = document.getElementById('reportsChartFull');
      try{ if(window._reportChart && dstChart){ const ctx = dstChart.getContext('2d'); window._reportChartFull = new Chart(ctx, { type: window._reportChart.config.type, data: JSON.parse(JSON.stringify(window._reportChart.data)), options: Object.assign({}, window._reportChart.options, {responsive:true, maintainAspectRatio:false}) }); } }catch(e){console.error(e)}
    }else{
      btn.textContent = 'افتح صفحة كاملة';
      // destroy full chart if exists
      try{ if(window._reportChartFull){ window._reportChartFull.destroy(); window._reportChartFull = null; } if(window._buySellChartFull){ window._buySellChartFull.destroy(); window._buySellChartFull = null; } }catch(e){}
    }
  });

  // full page report controls handlers
  const rpf = document.getElementById('reportPeriodFull'); if(rpf) rpf.addEventListener('change', renderReports);
  const rcf = document.getElementById('comparePrevFull'); if(rcf) rcf.addEventListener('change', renderReports);
  const rrf = document.getElementById('refreshReportsFull'); if(rrf) rrf.addEventListener('click', renderReports);

  document.getElementById('btnOrders').addEventListener('click', ()=>{ showView('orders'); loadPage(); });
  document.getElementById('btnReports').addEventListener('click', ()=>{ showView('reports'); renderReports(); });
  document.getElementById('btnSettings').addEventListener('click', ()=>{ showView('settings'); renderSettings(); });
  document.getElementById('saveSettingsBtn').addEventListener('click', saveSettings);

  async function postAction(action){
    if(!selectedTx){alert('حدد طلباً أولاً');return}
    const token = getToken();
    const headers = {'Content-Type':'application/json'};
    if(token) headers['Authorization'] = 'Bearer '+token;
    const url = `/api/transaction/${selectedTx.tx_id}/${action}`;
    try{
      const r = await fetch(url, {method:'POST', headers});
      if(r.status===401){alert('مطلوب تسجيل دخول');return}
      const json = await r.json().catch(()=>null);
      if(!r.ok){ const msg = json && json.detail ? json.detail : 'فشل تنفيذ الإجراء'; alert(msg); return }
      // show detailed success
      const msg = (json && (json.message || json.detail)) ? (json.message || json.detail) : 'تم';
      alert(msg);
      // if send_proof returned proof_sent, show status
      if(json && json.proof_sent === false){ alert('تنبيه: لم يتم العثور على ملف الإثبات — تم إرسال رسالة نصية بدلاً من الصورة.'); }
      loadPage();
    }catch(e){console.error(e);alert('خطأ')}
  }

  // events
  els.refreshBtn.addEventListener('click', ()=>{currentPage=1;loadPage();});
  document.getElementById('searchBtn').addEventListener('click', ()=>{currentPage=1;loadPage();});
  els.prevPage.addEventListener('click', ()=>{if(currentPage>1){currentPage--;loadPage();}});
  els.nextPage.addEventListener('click', ()=>{currentPage++;loadPage();});
  els.loginBtn.addEventListener('click', login);
  els.logoutBtn.addEventListener('click', ()=>{clearToken();renderAuth();});
  els.approveBtn.addEventListener('click', ()=>postAction('complete'));
  els.rejectBtn.addEventListener('click', ()=>postAction('reject'));
  // complete with proof (sends proof to user if available and marks completed)
  els.completeBtn.addEventListener('click', ()=>postAction('send_proof'));
  els.hideBtn.addEventListener('click', ()=>postAction('hide'));
  document.getElementById('copyAllBtn').addEventListener('click', copyAll);
  document.getElementById('copyJsonBtn').addEventListener('click', copyAllJson);
  document.getElementById('exportCsvBtn').addEventListener('click', exportCsv);
  document.getElementById('galleryClose').addEventListener('click', closeGallery);
  document.getElementById('galleryPrev').addEventListener('click', galleryPrev);
  document.getElementById('galleryNext').addEventListener('click', galleryNext);
  document.getElementById('galleryBackdrop').addEventListener('click', closeGallery);

  // init
  renderAuth();
  loadPage();
  startAutoSync();
  
  // WebSocket for live updates (call setupWS() after login)
  function setupWS(){
    const token = getToken();
    if(!token) return;
    try{
      const wsUrl = (location.protocol==='https:'? 'wss://' : 'ws://') + location.host + '/ws/transactions?token=' + encodeURIComponent(token);
      const ws = new WebSocket(wsUrl);
      ws.addEventListener('open', ()=>{console.log('WS connected')});
      ws.addEventListener('message', (ev)=>{
        try{ const msg = JSON.parse(ev.data); if(msg && msg.type==='update') { console.log('tx update', msg); loadPage(); } }catch(e){console.error(e)}
      });
      ws.addEventListener('close', ()=>{console.log('WS closed')});
      ws.addEventListener('close', ()=>{ setTimeout(setupWS, 3000); });
    }catch(e){console.error('WS setup failed', e);}
  }

  // Bot status and admin messages integration
  async function fetchBotStatus(){
    try{
      const token = getToken(); const headers={}; if(token) headers['Authorization']='Bearer '+token;
      const r = await fetch('/api/bot/status', {headers}); if(!r.ok) return {running:false};
      return await r.json();
    }catch(e){return {running:false}}}

  async function updateBotStatusUI(){
    const el = document.getElementById('botToggle'); const label = document.getElementById('botLabel');
    if(!el) return;
    const st = await fetchBotStatus();
    if(st.running){ el.style.background='#16a34a'; label.textContent = `بوت متصل (${st.bot_user||st.bot_id||'active'})`; }
    else { el.style.background='#ef4444'; label.textContent = `بوت متوقف`; }
  }

  document.getElementById('botToggle')?.addEventListener('click', async ()=>{
    const token = getToken(); if(!token){alert('يُطلب تسجيل دخول المشرف');return}
    try{
      const headers={'Content-Type':'application/json'}; if(token) headers['Authorization']='Bearer '+token;
      const r = await fetch('/api/bot/start', {method:'POST', headers});
      if(!r.ok){ const t=await r.text(); alert('فشل بدء البوت'); return }
      alert('تم إرسال أمر بدء البوت');
      setTimeout(updateBotStatusUI, 3000);
    }catch(e){console.error(e);alert('خطأ')}
  });

  async function fetchAdminMessages(){
    try{
      const token = getToken(); const headers={}; if(token) headers['Authorization']='Bearer '+token;
      const r = await fetch('/api/admin/messages?limit=200', {headers}); if(!r.ok) return [];
      const data = await r.json(); return data;
    }catch(e){console.error(e); return []}
  }

  function renderMessages(list){
    const wrap = document.getElementById('messagesList'); if(!wrap) return; wrap.innerHTML='';
    for(const m of (list||[])){
      const div = document.createElement('div'); div.style.padding='6px'; div.style.borderBottom='1px solid #f1f5f9';
      div.innerHTML = `<div style="font-size:12px;color:#64748b">${m.created_at} — ${m.username||m.user_id||m.chat_id}</div><div style="margin-top:4px">${m.incoming? '📥':'📤'} ${m.text}</div>`;
      wrap.appendChild(div);
    }
    wrap.scrollTop = wrap.scrollHeight;
  }

  document.getElementById('sendReplyBtn')?.addEventListener('click', async ()=>{
    const target = document.getElementById('replyTarget').value.trim(); const text = document.getElementById('replyText').value.trim();
    if(!target||!text){alert('حدد الهدف والنص'); return}
    try{
      const token = getToken(); if(!token){alert('مطلوب تسجيل دخول');return}
      const headers={'Content-Type':'application/json', 'Authorization':'Bearer '+token};
      const body = JSON.stringify({chat_id: target.match(/^\d+$/)? target : null, user_id: target.match(/^\d+$/)? null : target, text});
      const r = await fetch('/api/admin/send', {method:'POST', headers, body});
      if(!r.ok){ const t=await r.text(); alert('فشل الإرسال'); return }
      document.getElementById('replyText').value=''; alert('تم الإرسال');
      // refresh messages
      const msgs = await fetchAdminMessages(); renderMessages(msgs);
    }catch(e){console.error(e); alert('خطأ')}
  });

  let _autoSyncInterval = null, _messagesInterval = null, _botStatusInterval = null;
  function startAutoSync(){
    if(_autoSyncInterval) return;
    // poll every 10s for updates (in addition to websocket)
    _autoSyncInterval = setInterval(()=>{ if(getToken()) { loadPage(); renderReports(); } }, 10000);
    _messagesInterval = setInterval(async ()=>{ if(getToken()){ const msgs = await fetchAdminMessages(); renderMessages(msgs); } }, 5000);
    _botStatusInterval = setInterval(()=>{ if(getToken()) updateBotStatusUI(); }, 10000);
    // initial fetches
    updateBotStatusUI(); fetchAdminMessages().then(renderMessages);
  }
})();
