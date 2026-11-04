// MaliAsistan SMMM Kokpit Motoru (Faz 0 - Faz 7)

// 1. Örnek Veri Seti (Demo Modu)
const sampleQueueItems = [
  {
    type: "E-FATURA",
    title: "Yıldız Yem San. A.Ş.",
    subtitle: "Örnek Gıda Ltd.",
    amount: "12.480,00 ₺",
    confidence: 94,
    level: "high",
    date: "03.11.2026",
    reason: "Bu satıcıdan gelen benzer faturalar daha önce 153.01 Ticari Mal hesabına işlendi. Mükellefin NACE bilgisi de bu öneriyi destekliyor.",
    rows: [
      ["153.01.001 TİCARİ MALLAR (%20)", "10.400,00", ""],
      ["191.01.020 İNDİRİLECEK KDV %20", "2.080,00", ""],
      ["320.01.001 YILDIZ YEM SAN. A.Ş.", "", "12.480,00"]
    ]
  },
  {
    type: "BANKA EKSTRESİ",
    title: "Ziraat Bankası",
    subtitle: "Örnek Gıda Ltd.",
    amount: "7.056,00 ₺",
    confidence: 88,
    level: "high",
    date: "02.11.2026",
    reason: "Açıklamadaki satıcı adı ve önceki onaylar 320 Satıcılar hesabıyla eşleşiyor.",
    rows: [
      ["320.01.001 SATICILAR", "7.056,00", ""],
      ["102.01.001 BANKALAR (Ziraat)", "", "7.056,00"]
    ]
  },
  {
    type: "E-FATURA",
    title: "Bölge Elektrik Perakende A.Ş.",
    subtitle: "Örnek Gıda Ltd.",
    amount: "3.480,00 ₺",
    confidence: 97,
    level: "high",
    date: "01.11.2026",
    reason: "Elektrik sağlayıcısı için daha önce onaylanan kayıtlar aynı 770 Genel Yönetim gider hesabını kullanıyor.",
    rows: [
      ["770.01.001 ELEKTRİK GİDERLERİ", "2.900,00", ""],
      ["191.01.020 İNDİRİLECEK KDV %20", "580,00", ""],
      ["320.01.005 BÖLGE ELEKTRİK A.Ş.", "", "3.480,00"]
    ]
  },
  {
    type: "E-FATURA",
    title: "Çağlar Petrol Ltd.",
    subtitle: "Örnek Gıda Ltd.",
    amount: "2.400,00 ₺",
    confidence: 71,
    level: "medium",
    date: "31.10.2026",
    reason: "Binek oto akaryakıtında VUK m.40/5 uyarınca %70 indirilebilir gider, %30 KKEG ayrımı uygulandı.",
    rows: [
      ["770.03.001 AKARYAKIT (%70)", "1.400,00", ""],
      ["689.01.001 KKEG (%30)", "600,00", ""],
      ["191.01.020 İNDİRİLECEK KDV", "400,00", ""],
      ["100.01.001 NAKİT KASA", "", "2.400,00"]
    ]
  },
  {
    type: "Z RAPORU",
    title: "Yazar Kasa 1 (Z No: 142)",
    subtitle: "Mavi Kafe Ltd.",
    amount: "12.000,00 ₺",
    confidence: 90,
    level: "high",
    date: "31.10.2026",
    reason: "Z raporu toplamı POS (108) ve nakit kasa (100) hareketleriyle tutarlı.",
    rows: [
      ["100.01.001 MERKEZ KASA (Nakit)", "4.000,00", ""],
      ["108.01.001 DİĞER HAZIR DEĞERLER (POS)", "8.000,00", ""],
      ["600.01.010 YURTİÇİ SATIŞLAR (%10 KDV)", "", "10.909,09"],
      ["391.01.010 HESAPLANAN KDV %10", "", "1.090,91"]
    ]
  }
];

let items = [...sampleQueueItems];
let selected = 0;
let realMode = false;
let pollTimer = null;
let loadingRealDocuments = false;

const params = new URLSearchParams(location.search);
const tenantHeaders = {
  "X-Buro-ID": params.get("buro_id") || "",
  "X-Mukellef-ID": params.get("mukellef_id") || ""
};

const $ = id => document.getElementById(id);

// 2. Tab Navigation
function initNavigation() {
  const links = document.querySelectorAll("#sidebarNav a");
  links.forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      links.forEach(l => l.classList.remove("active"));
      link.classList.add("active");

      const viewName = link.dataset.view;
      document.querySelectorAll(".tab-view").forEach(tab => {
        tab.classList.remove("active");
        tab.hidden = true;
      });

      const target = {
        queue: "viewQueue",
        bank: "viewBank",
        ledger: "viewLedger",
        erp: "viewErp",
        einvoice: "viewEinvoice",
        tax: "viewTax"
      }[viewName];

      if (target && $(target)) {
        $(target).hidden = false;
        $(target).classList.add("active");
      }

      // Tab bazlı veri yükleme
      if (viewName === "bank") loadBankData();
      if (viewName === "ledger") loadLedgerData();
      if (viewName === "erp") loadErpData();
      if (viewName === "tax") loadTaxData();
    });
  });
}

// 3. Onay Kuyruğu İşlemleri
function renderQueue() {
  const filter = $("queueFilter").value;
  const visible = items.filter(x => filter === "all" || (filter === "high" ? x.confidence >= 80 : x.confidence < 80));
  $("queue").innerHTML = visible.length ? visible.map((x, i) => `
    <article class="queue-item ${items.indexOf(x) === selected ? "selected" : ""}" data-index="${items.indexOf(x)}">
      <div class="queue-line">
        <span class="tag">${x.type}</span>
        <span class="confidence ${x.level}">%${x.confidence} · ${x.level === "high" ? "Yüksek" : x.level === "medium" ? "Kontrol" : "Düşük"}</span>
      </div>
      <h3>${x.title}</h3>
      <div class="queue-line">
        <p>${x.subtitle}</p>
        <p><strong>${x.amount}</strong></p>
      </div>
    </article>
  `).join("") : `<div class="empty">Bu filtrede bekleyen belge yok.</div>`;

  document.querySelectorAll(".queue-item").forEach(el => {
    el.onclick = () => {
      selected = Number(el.dataset.index);
      renderQueue();
      renderDetail();
      loadSelectedDetail();
    };
  });

  $("queueCount").textContent = visible.length;
  $("navQueueBadge").textContent = visible.length;
  $("statPendingCount").textContent = visible.length;
}

function renderDetail() {
  const x = items[selected];
  if (!x) {
    $("detailTitle").textContent = "Kuyruk boş";
    $("detailSubtitle").textContent = "İncelenecek belge bulunmuyor.";
    $("detailMeta").innerHTML = "";
    $("journal").innerHTML = "";
    $("detailReason").textContent = "Yeni belge geldiğinde burada sistem önerisini göreceksiniz.";
    return;
  }
  $("detailType").textContent = x.type;
  $("detailTitle").textContent = x.title;
  $("detailSubtitle").textContent = x.subtitle;
  $("detailConfidence").textContent = `%${x.confidence} · ${x.level === "high" ? "Yüksek Güven" : x.level === "medium" ? "Kontrol Gerekli" : "İnceleme Gerekli"}`;
  $("detailConfidence").className = `confidence ${x.level}`;
  $("detailMeta").innerHTML = `
    <div>Belge Türü<strong>${x.type}</strong></div>
    <div>Tarih<strong>${x.date}</strong></div>
    <div>Tutar<strong>${x.amount || "-"}</strong></div>
  `;
  $("journal").innerHTML = x.rows.map((r, i) => `
    <div class="journal-row ${i === x.rows.length - 1 ? "total" : ""}">
      <span>${r[0]}</span>
      <span>${r[1]}</span>
      <span>${r[2]}</span>
    </div>
  `).join("") + `<div class="journal-row total"><span>Toplam</span><span>${x.amount || "-"}</span><span>${x.amount || "-"}</span></div>`;
  $("detailReason").textContent = x.reason;
}

async function loadSelectedDetail() {
  if (!realMode || !items[selected]) return;
  try {
    const item = items[selected];
    const featureValue = `${item.type}:${item.subtitle}`;
    const [detailResponse, suggestionResponse] = await Promise.all([
      fetch(`/documents/${item.id}`, { headers: tenantHeaders }),
      fetch(`/learning/suggestions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...tenantHeaders },
        body: JSON.stringify({ feature_type: "document_category", feature_value: featureValue })
      })
    ]);
    if (!detailResponse.ok) throw new Error("Belge detayı yüklenemedi.");
    const document = await detailResponse.json();
    const suggestions = suggestionResponse.ok ? (await suggestionResponse.json()).suggestions || [] : [];
    item.reason = (document.classification_reasons || ["Sistem açıklaması bulunamadı."]).join(" ");
    if (suggestions.length) {
      item.reason += ` Öğrenilmiş öneri: ${suggestions[0].label} (%${Math.round(Number(suggestions[0].confidence) * 100)} güven).`;
    }
    item.subtitle = document.document_category || item.subtitle;
    renderDetail();
  } catch (error) {
    toast(error.message);
  }
}

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2800);
}

function mapDocument(document) {
  const confidence = Math.round(Number(document.classification_confidence || 0) * 100);
  const level = confidence >= 80 ? "high" : confidence >= 60 ? "medium" : "low";
  const categoryMap = { ubl_xml: "invoice", pdf: "invoice", image: "unknown", spreadsheet: "bank_statement" };
  const processing = document.extraction_status === "pending" ? "İşleniyor" : document.review_status === "needs_review" ? "İnceleme gerekli" : "Hazır";
  return {
    id: document.id,
    category: categoryMap[document.document_type] || document.document_category || "unknown",
    type: (document.document_category || document.document_type || "BELGE").replace("_", " ").toUpperCase(),
    title: document.filename || "Belgesiz kayıt",
    subtitle: `${document.document_category || "Belge"} · ${processing}`,
    processingStatus: document.extraction_status,
    amount: "",
    confidence,
    level,
    date: document.created_at ? new Date(document.created_at).toLocaleDateString("tr-TR") : "-",
    reason: (document.classification_reasons || ["Sistem açıklaması bulunamadı."]).join(" "),
    rows: [["Belge sınıflandırması", "", level === "high" ? "Önerildi" : "Kontrol gerekli"]]
  };
}

function startDocumentPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (realMode && items.some(item => item.processingStatus === "pending")) {
    pollTimer = setInterval(loadRealDocuments,5000);
  }
}

async function loadRealDocuments() {
  if (!tenantHeaders["X-Buro-ID"] || !tenantHeaders["X-Mukellef-ID"] || loadingRealDocuments) return;
  loadingRealDocuments = true;
  const selectedId = items[selected]?.id;
  try {
    const response = await fetch("/documents?limit=50", { headers: tenantHeaders });
    if (!response.ok) throw new Error("Belgeler yüklenemedi.");
    const data = await response.json();
    items.splice(0, items.length, ...(data.documents || []).filter(x => x.review_status !== "approved").map(mapDocument));
    realMode = true;
    $("modeLabel").textContent = "Canlı Tenant Modu";
    $("modeLabel").style.color = "#48c774";
    $("sourceNote").textContent = "Gerçek belge kuyruğu · Tenant bilgileri URL parametrelerinden alındı.";
    const selectedIndex = items.findIndex(item => item.id === selectedId);
    selected = selectedIndex >= 0 ? selectedIndex : 0;
    renderQueue();
    renderDetail();
    loadSelectedDetail();
    startDocumentPolling();
  } catch (error) {
    toast(error.message);
  } finally {
    loadingRealDocuments = false;
  }
}

async function uploadSelectedFile(file) {
  if (!file) return;
  if (!realMode) {
    toast("Demo modunda yükleme simüle edildi. Canlı kayıt için tenant kimlikleriyle açın.");
    return;
  }
  if (file.size > 25*1024*1024) {
    toast("Dosya boyutu 25 MB sınırını aşamaz.");
    $("uploadInput").value = "";
    return;
  }
  const button = $("uploadButton");
  button.textContent = "⏳ Yükleniyor...";
  button.classList.add("uploading");
  button.setAttribute("aria-disabled", "true");
  const form = new FormData();
  form.append("upload", file);
  try {
    const response = await fetch("/documents/upload", { method: "POST", headers: tenantHeaders, body: form });
    if (!response.ok) throw new Error("Belge yüklenemedi.");
    const document = await response.json();
    toast("Belge yüklendi, kural motoru ayrıştırıyor...");
    const extractResponse = await fetch(`/documents/${document.id}/extract`, { method: "POST", headers: tenantHeaders });
    if (!extractResponse.ok) throw new Error("Belge yüklendi ancak işleme başlatılamadı.");
    toast("Belge işlendi ve onay kuyruğuna eklendi.");
    await loadRealDocuments();
  } catch (error) {
    toast(error.message);
  } finally {
    button.textContent = "＋ Belge Yükle";
    button.classList.remove("uploading");
    button.removeAttribute("aria-disabled");
    $("uploadInput").value = "";
  }
}

async function submitReview(category, note) {
  const item = items[selected];
  if (!item) return;
  if (!realMode) {
    toast("✓ Demo: Fiş onaylandı ve öğrenme motoruna aktarıldı.");
    items.splice(selected, 1);
    selected = Math.max(0, selected - 1);
    $("editForm").hidden = true;
    renderQueue();
    renderDetail();
    return;
  }
  try {
    const response = await fetch(`/documents/${item.id}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...tenantHeaders },
      body: JSON.stringify({ category, status: "approved", note: note || null })
    });
    if (!response.ok) throw new Error("Belge incelemesi kaydedilemedi.");
    const learningResponse = await fetch("/learning/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...tenantHeaders },
      body: JSON.stringify({
        feature_type: "document_category",
        feature_value: `${item.type}:${item.subtitle}`,
        label: category,
        accepted: category === item.category
      })
    });
    if (!learningResponse.ok) throw new Error("İnceleme kaydedildi ancak öğrenme geri bildirimi kaydedilemedi.");
    toast("İnceleme onaylandı ve öğrenme kuralı güncellendi.");
    items.splice(selected, 1);
    selected = Math.max(0, selected - 1);
    $("editForm").hidden = true;
    renderQueue();
    renderDetail();
  } catch (error) {
    toast(error.message);
  }
}

async function deferSelected() {
  const item = items[selected];
  if (!item) return;
  if (!realMode) {
    toast("Belge daha sonra incelenmek üzere ertelendi.");
    items.splice(selected, 1);
    selected = Math.max(0, selected - 1);
    renderQueue();
    renderDetail();
    return;
  }
  try {
    const response = await fetch(`/documents/${item.id}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...tenantHeaders },
      body: JSON.stringify({ category: item.category, status: "needs_review", note: "Daha sonra incelenecek." })
    });
    if (!response.ok) throw new Error("Belge beklemeye alınamadı.");
    toast("Belge beklemeye alındı; onaylanmadı.");
    items.splice(selected, 1);
    selected = Math.max(0, selected - 1);
    renderQueue();
    renderDetail();
  } catch (error) {
    toast(error.message);
  }
}

// 4. TAB 2: BANKA & POS VERİLERİ
function loadBankData() {
  if (!realMode) {
    $("bankAccountsList").innerHTML = `
      <div class="list-row">
        <div><strong>TR12 0001 0000 1234 5678 9000 01</strong><small>Ziraat Bankası · Vadesiz Ticari (102.01.001)</small></div>
        <span class="badge">Aktif</span>
      </div>
      <div class="list-row">
        <div><strong>TR64 0006 2000 9876 5432 1000 02</strong><small>Garanti BBVA · POS Bloke Hesabı (102.02.001)</small></div>
        <span class="badge">Aktif</span>
      </div>
    `;
    $("posAgingTable").innerHTML = `
      <div class="list-row"><div><strong>0 - 30 Gün</strong><small>Cari dönem POS alacakları</small></div><strong>42.500,00 ₺</strong></div>
      <div class="list-row"><div><strong>31 - 60 Gün</strong><small>Vadesi yaklaşan POS bakiyesi</small></div><strong>18.200,00 ₺</strong></div>
      <div class="list-row"><div><strong>61+ Gün</strong><small>Gecikmiş / Bloke POS</small></div><strong style="color:var(--orange)">0,00 ₺</strong></div>
    `;
    $("reconcileQueue").innerHTML = `
      <div class="reconcile-item">
        <div>
          <span class="tag">02.11.2026</span>
          <h3 style="margin:4px 0">POS Gün Sonu Tahsilat Yatışı</h3>
          <p class="muted">Ziraat Bankası · 108 POS hesabından aktarım</p>
        </div>
        <div style="text-align:right">
          <strong style="font-size:16px;display:block">8.000,00 ₺</strong>
          <span class="candidate-badge">✓ Aday Fiş: YV-20261031 (Z Raporu 142)</span>
          <button class="action-sm" onclick="toast('✓ POS hareketi onaylı Z raporu yevmiye fişiyle mutabık edildi.')" style="margin-top:6px">Eşleştir</button>
        </div>
      </div>
    `;
    return;
  }

  fetch("/bank/accounts", { headers: tenantHeaders })
    .then(r => r.json())
    .then(data => {
      const list = data.accounts || [];
      $("bankAccountsCount").textContent = `${list.length} Hesap`;
      $("bankAccountsList").innerHTML = list.length ? list.map(a => `
        <div class="list-row">
          <div><strong>${a.iban}</strong><small>${a.bank_name} · ${a.account_name} (${a.ledger_account_code})</small></div>
          <span class="badge">${a.is_active ? "Aktif" : "Pasif"}</span>
        </div>
      `).join("") : `<div class="empty">Tanımlı banka hesabı yok.</div>`;
    }).catch(e => toast(e.message));

  fetch("/bank/reconciliation/queue?limit=20", { headers: tenantHeaders })
    .then(r => r.json())
    .then(data => {
      const list = data.queue || [];
      $("navBankBadge").textContent = list.length;
      $("reconcileQueue").innerHTML = list.length ? list.map(q => `
        <div class="reconcile-item">
          <div>
            <span class="tag">${q.transaction_date}</span>
            <h3 style="margin:4px 0">${q.description || "Banka Hareketi"}</h3>
            <p class="muted">IBAN: ${q.iban}</p>
          </div>
          <div style="text-align:right">
            <strong style="font-size:16px;display:block">${q.amount} ₺</strong>
            <button class="action-sm" onclick="toast('Mutabakat adayı aranıyor...')">Adayları Gör</button>
          </div>
        </div>
      `).join("") : `<div class="empty">Tüm banka hareketleri mutabık! Bekleyen hareket yok.</div>`;
    }).catch(e => toast(e.message));
}

// 5. TAB 3: YEVMİYE & MİZAN VERİLERİ
function loadLedgerData() {
  if (!realMode) {
    const demoRows = [
      { code: "100.01.001", name: "Merkez Kasa (Nakit)", debit: "16.000,00", credit: "2.400,00", bal: "13.600,00" },
      { code: "102.01.001", name: "Ziraat Bankası Vadesiz", debit: "7.056,00", credit: "7.056,00", bal: "0,00" },
      { code: "108.01.001", name: "POS Bekleyen Alacaklar", debit: "8.000,00", credit: "0,00", bal: "8.000,00" },
      { code: "153.01.001", name: "Ticari Mallar (%20)", debit: "10.400,00", credit: "0,00", bal: "10.400,00" },
      { code: "191.01.020", name: "İndirilecek KDV %20", debit: "3.060,00", credit: "0,00", bal: "3.060,00" },
      { code: "320.01.001", name: "Satıcılar Cari", debit: "7.056,00", credit: "15.960,00", bal: "-8.904,00" },
      { code: "391.01.010", name: "Hesaplanan KDV %10", debit: "0,00", credit: "1.090,91", bal: "-1.090,91" },
      { code: "600.01.010", name: "Yurtiçi Satışlar %10", debit: "0,00", credit: "10.909,09", bal: "-10.909,09" },
      { code: "689.01.001", name: "KKEG (Binek Oto %30)", debit: "600,00", credit: "0,00", bal: "600,00" },
      { code: "770.01.001", name: "Genel Yönetim / Elektrik", debit: "2.900,00", credit: "0,00", bal: "2.900,00" },
      { code: "770.03.001", name: "Akaryakıt Giderleri (%70)", debit: "1.400,00", credit: "0,00", bal: "1.400,00" }
    ];

    $("trialBalanceBody").innerHTML = demoRows.map(r => `
      <tr>
        <td><code>${r.code}</code></td>
        <td><strong>${r.name}</strong></td>
        <td class="num">${r.debit} ₺</td>
        <td class="num">${r.credit} ₺</td>
        <td class="num"><strong>${r.bal} ₺</strong></td>
      </tr>
    `).join("");

    $("trialBalanceFoot").innerHTML = `
      <tr style="font-weight:700;background:#f8fafc">
        <td colspan="2">GENEL MİZAN TOPLAMI</td>
        <td class="num">56.472,00 ₺</td>
        <td class="num">56.472,00 ₺</td>
        <td class="num" style="color:var(--green)">0,00 ₺ (Dengede)</td>
      </tr>
    `;

    $("journalEntriesList").innerHTML = `
      <div class="list-row">
        <div><strong>YV-20261103-01 · Alış Faturası</strong><small>Yıldız Yem San. A.Ş. · 153/191/320</small></div>
        <div><span class="badge" style="background:var(--green-bg);color:var(--green)">Onaylı</span> <button class="action-sm" onclick="toast('Ters kayıt taslağı oluşturuldu.')">Ters Kayıt</button></div>
      </div>
      <div class="list-row">
        <div><strong>YV-20261031-02 · Gün Sonu Z Raporu</strong><small>Yazar Kasa 1 · 100/108/600/391</small></div>
        <div><span class="badge" style="background:var(--green-bg);color:var(--green)">Onaylı</span> <button class="action-sm" onclick="toast('Ters kayıt taslağı oluşturuldu.')">Ters Kayıt</button></div>
      </div>
    `;
    return;
  }

  fetch("/ledger/trial-balance", { headers: tenantHeaders })
    .then(r => r.json())
    .then(data => {
      const rows = data.trial_balance || [];
      $("trialBalanceBody").innerHTML = rows.map(r => `
        <tr>
          <td><code>${r.account_code}</code></td>
          <td><strong>${r.account_name}</strong></td>
          <td class="num">${r.total_debit} ₺</td>
          <td class="num">${r.total_credit} ₺</td>
          <td class="num"><strong>${r.balance} ₺</strong></td>
        </tr>
      `).join("");
      $("trialBalanceFoot").innerHTML = `
        <tr style="font-weight:700;background:#f8fafc">
          <td colspan="2">TOPLAM</td>
          <td class="num">${data.grand_total_debit} ₺</td>
          <td class="num">${data.grand_total_credit} ₺</td>
          <td class="num" style="color:${data.is_balanced ? 'var(--green)' : 'var(--red)'}">${data.is_balanced ? '✓ Dengede' : '⚠ Fiş Dengesi Bozuk!'}</td>
        </tr>
      `;
    }).catch(e => toast(e.message));
}

// 6. TAB 4: ERP VERİLERİ
function loadErpData() {
  if (!realMode) {
    $("erpExportsBody").innerHTML = `
      <tr>
        <td><span class="badge" style="background:var(--green-bg);color:var(--green)">exported</span></td>
        <td><code>YV-20261103-01</code></td>
        <td><strong>LOGO-GL-104928</strong></td>
        <td><small class="muted">buro1:mukellef1:entry1</small></td>
        <td>04.11.2026 09:12</td>
        <td><button class="action-sm" onclick="toast('Logo XML indirildi: GL_SLIP_104928.xml')">XML İndir</button></td>
      </tr>
      <tr>
        <td><span class="badge" style="background:var(--green-bg);color:var(--green)">exported</span></td>
        <td><code>YV-20261031-02</code></td>
        <td><strong>CSV-GL-302194</strong></td>
        <td><small class="muted">buro1:mukellef1:entry2</small></td>
        <td>04.11.2026 09:15</td>
        <td><button class="action-sm" onclick="toast('Universal CSV indirildi.')">CSV İndir</button></td>
      </tr>
    `;
    return;
  }

  fetch("/erp/exports", { headers: tenantHeaders })
    .then(r => r.json())
    .then(data => {
      const exports = data.exports || [];
      $("erpExportsBody").innerHTML = exports.length ? exports.map(e => `
        <tr>
          <td><span class="badge">${e.status}</span></td>
          <td><code>${e.journal_entry_id.substring(0, 8)}...</code></td>
          <td><strong>${e.external_reference || "-"}</strong></td>
          <td><small class="muted">${e.idempotency_key}</small></td>
          <td>${new Date(e.created_at).toLocaleString("tr-TR")}</td>
          <td>${e.status === "failed" ? `<button class="action-sm" onclick="retryErpExport('${e.id}')">Yeniden Dene</button>` : "-"}</td>
        </tr>
      `).join("") : `<tr><td colspan="6" class="empty">Henüz ERP aktarımı yapılmadı.</td></tr>`;
    }).catch(e => toast(e.message));
}

// 7. TAB 5: E-FATURA
$("btnSyncEInvoice")?.addEventListener("click", () => {
  const provider = "fake";
  const start = $("einvoiceStartDate").value;
  const end = $("einvoiceEndDate").value;
  toast(`⚡ ${provider.toUpperCase()} entegratöründen ${start} - ${end} faturaları çekiliyor...`);
  setTimeout(() => {
    toast("✓ 4 yeni fatura çekildi ve onay kuyruğuna alındı.");
  }, 1200);
});

// 8. TAB 6: VERGİ VE HADLER
function loadTaxData() {
  $("taxParametersList").innerHTML = `
    <div class="list-row"><div><strong>Binek Araç Akaryakıt Gider Sınırı</strong><small>GVK m.40/5 uyarınca azami indirilebilir oran</small></div><strong>%70</strong></div>
    <div class="list-row"><div><strong>Binek Araç KKEG Oranı</strong><small>Gider kabul edilmeyen kısım</small></div><strong>%30</strong></div>
    <div class="list-row"><div><strong>KDV Tevkifat Standart Hadler</strong><small>İşgücü, temizlik, servis taşımacılığı</small></div><strong>5/10 - 7/10</strong></div>
  `;
  $("taxSummaryBox").innerHTML = `
    <div class="list-row"><div><strong>%20 Teslim ve Hizmet Matrahı</strong><small>153 & 770 hesaplar</small></div><strong>13.300,00 ₺</strong></div>
    <div class="list-row"><div><strong>%10 Teslim ve Hizmet Matrahı</strong><small>600.01 Satışlar</small></div><strong>10.909,09 ₺</strong></div>
    <div class="list-row"><div><strong>İndirilecek KDV Toplamı (191)</strong><small>Bu dönem indirim konusu</small></div><strong style="color:var(--green)">3.060,00 ₺</strong></div>
    <div class="list-row"><div><strong>Hesaplanan KDV Toplamı (391)</strong><small>Beyan edilen KDV</small></div><strong style="color:var(--blue)">1.090,91 ₺</strong></div>
  `;
}

// 9. Initial Event Bindings
$("queueFilter").onchange = () => { renderQueue(); renderDetail(); loadSelectedDetail(); };
$("approveButton").onclick = () => submitReview(items[selected]?.category || "invoice", "Dashboard üzerinden onaylandı.");
$("editButton").onclick = () => {
  $("categoryInput").value = items[selected]?.category || "unknown";
  $("noteInput").value = "";
  $("editForm").hidden = !$("editForm").hidden;
};
$("saveEditButton").onclick = () => submitReview($("categoryInput").value, $("noteInput").value);
$("laterButton").onclick = deferSelected;
$("helpButton").onclick = () => toast("Sistem kural motoruyla önerir; güven rozeti ve satır detayları kararınızı hızlandırır.");
$("menuButton").onclick = () => $("sidebar").classList.toggle("open");
$("uploadInput").onchange = e => uploadSelectedFile(e.target.files[0]);
$("btnSyncAll").onclick = () => {
  toast("Tüm modüller senkronize ediliyor...");
  loadRealDocuments();
  loadBankData();
  loadLedgerData();
  loadErpData();
  loadTaxData();
};

// Start
initNavigation();
renderQueue();
renderDetail();
loadRealDocuments();
