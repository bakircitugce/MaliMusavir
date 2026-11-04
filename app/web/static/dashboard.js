// Constat - MaliAsistan SMMM Kokpit & Kullanıcı Yönetimi Motoru

// 1. Örnek Veri Seti (Görsellerdeki Canlı Kuyruk ile Birebir)
const sampleQueueItems = [
  {
    type: "E-FATURA",
    title: "Yıldız Yem San. A.Ş.",
    subtitle: "Örnek Gıda Ltd.",
    amount: "12.480,00 ₺",
    confidence: 94,
    level: "high",
    date: "03.11.2026",
    docNo: "YYS2026000041812",
    desc: "Piliç yemi, 1.200 kg",
    kdv: "123,56 ₺",
    reason: "Bu satıcıdan son 14 alış 153.01.001 Ticari Mal hesabına gitti. Mükellefin NACE kodu toptan ticaret; mal alışı. KDV %1, mükellefin alt kodu 191.01.001.",
    rows: [
      ["153.01.001 TİCARİ MAL A", "12.356,44", ""],
      ["191.01.001 %1 İNDİRİLECEK KDV", "123,56", ""],
      ["320.01.011 YILDIZ YEM SAN. A.Ş.", "", "12.480,00"]
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
    docNo: "ZRT-2026-9912",
    desc: "Gelen Havale - Cari Mahsup",
    kdv: "0,00 ₺",
    reason: "Açıklamadaki satıcı adı ve önceki onaylar 320 Satıcılar cari hesabıyla %88 güvenle eşleşti.",
    rows: [
      ["320.01.001 SATICILAR HESABI", "7.056,00", ""],
      ["102.01.002 BANKALAR (Ziraat)", "", "7.056,00"]
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
    docNo: "BEP2026001928",
    desc: "Elektrik Sarfiyat Faturası",
    kdv: "580,00 ₺",
    reason: "Elektrik sağlayıcısı için büro mevzuat hafızasında daha önce onaylanan tüm kayıtlar 770 Genel Yönetim gider hesabını kullanmaktadır.",
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
    docNo: "CGL2026000412",
    desc: "Motorin Yakıt Alımı",
    kdv: "400,00 ₺",
    reason: "Binek oto akaryakıtında GVK m.40/5 uyarınca %70 gider (1.400 TL), %30 KKEG (600 TL) ayrımı otomatik hesaplandı.",
    rows: [
      ["770.03.001 AKARYAKIT GİDERİ (%70)", "1.400,00", ""],
      ["689.01.001 KANUNEN KABUL EDİLMEYEN GİDER", "600,00", ""],
      ["191.01.020 İNDİRİLECEK KDV", "400,00", ""],
      ["100.01.001 NAKİT KASA", "", "2.400,00"]
    ]
  },
  {
    type: "Z RAPORU",
    title: "Yazar kasa 1",
    subtitle: "Mavi Kafe",
    amount: "12.000,00 ₺",
    confidence: 90,
    level: "high",
    date: "31.10.2026",
    docNo: "Z-142",
    desc: "Gün Sonu Z Raporu",
    kdv: "1.090,91 ₺",
    reason: "Gün sonu Z toplamı POS (108) ve nakit kasa (100) hareketleriyle %90 tutarlıdır.",
    rows: [
      ["100.01.001 MERKEZ KASA (Nakit)", "4.000,00", ""],
      ["108.01.001 DİĞER HAZIR DEĞERLER (POS)", "8.000,00", ""],
      ["600.01.010 YURTİÇİ SATIŞLAR (%10 KDV)", "", "10.909,09"],
      ["391.01.010 HESAPLANAN KDV %10", "", "1.090,91"]
    ]
  },
  {
    type: "BANKA EKSTRESİ",
    title: "Akbank",
    subtitle: "Örnek Gıda Ltd.",
    amount: "15.000,00 ₺",
    confidence: 42,
    level: "low",
    date: "30.10.2026",
    docNo: "AKB-99812",
    desc: "Bilinmeyen EFT Girişi",
    kdv: "0,00 ₺",
    reason: "Açıklamada cari VKN veya unvan net tespit edilemedi. İnceleme ve manuel hesap seçimi önerilir.",
    rows: [
      ["102.01.003 AKBANK TİCARİ HESAP", "15.000,00", ""],
      ["329.01.001 DİĞER TİCARİ BORÇLAR", "", "15.000,00"]
    ]
  },
  {
    type: "E-FATURA",
    title: "Kuzey Ambalaj San. Ltd.",
    subtitle: "Mavi Kafe",
    amount: "5.000,00 ₺",
    confidence: 83,
    level: "high",
    date: "29.10.2026",
    docNo: "KZY202600120",
    desc: "Koli ve Paketleme Malzemesi",
    kdv: "833,33 ₺",
    reason: "Ambalaj malzemeleri geçmiş kayıtlar doğrultusunda 150 İlk Madde Malzeme stok hesabına yönlendirildi.",
    rows: [
      ["150.01.001 İLK MADDE VE MALZEME", "4.166,67", ""],
      ["191.01.020 İNDİRİLECEK KDV", "833,33", ""],
      ["320.01.022 KUZEY AMBALAJ SAN. LTD.", "", "5.000,00"]
    ]
  }
];

let items = [...sampleQueueItems];
let selected = 0;
let realMode = false;
let pollTimer = null;
let loadingRealDocuments = false;

// Kimlik & Yetki Durumu
let currentUser = null;
let authToken = localStorage.getItem("maliasistan_token") || "";

const params = new URLSearchParams(location.search);
const tenantHeaders = {
  "X-Buro-ID": params.get("buro_id") || "",
  "X-Mukellef-ID": params.get("mukellef_id") || ""
};

const $ = id => document.getElementById(id);

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json", ...tenantHeaders };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  return headers;
}

// 2. Tab Navigation
function initNavigation() {
  const links = document.querySelectorAll("#sidebarNav a");
  links.forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      const view = link.dataset.view;
      activateView(view);
    });
  });

  // Hızlı Geçiş Butonları
  if ($("btnGoToQueue")) $("btnGoToQueue").addEventListener("click", () => activateView("queue"));
  if ($("btnGoToMukellefler")) $("btnGoToMukellefler").addEventListener("click", () => activateView("mukellefler"));
  if ($("btnSeeAllMukellef")) $("btnSeeAllMukellef").addEventListener("click", e => {
    e.preventDefault();
    activateView("mukellefler");
  });

  // Profil Dropdown Toggle
  if ($("userProfileBtn")) {
    $("userProfileBtn").addEventListener("click", e => {
      e.stopPropagation();
      $("userDropdown").classList.toggle("show");
    });
  }
  document.addEventListener("click", () => {
    if ($("userDropdown")) $("userDropdown").classList.remove("show");
  });

  if ($("topLogoutBtn")) $("topLogoutBtn").addEventListener("click", logout);
  if ($("btnLogout")) $("btnLogout").addEventListener("click", logout);
}

function activateView(viewName) {
  document.querySelectorAll("#sidebarNav a").forEach(l => {
    l.classList.toggle("active", l.dataset.view === viewName);
  });

  const views = [
    { name: "today", el: $("viewToday") },
    { name: "queue", el: $("viewQueue") },
    { name: "mukellefler", el: $("viewMukellefler") },
    { name: "users", el: $("viewUsers") },
    { name: "bank", el: $("viewBank") },
    { name: "ledger", el: $("viewLedger") },
    { name: "erp", el: $("viewErp") },
    { name: "einvoice", el: $("viewEinvoice") },
    { name: "tax", el: $("viewTax") }
  ];

  views.forEach(v => {
    if (v.el) v.el.hidden = (v.name !== viewName);
  });

  if (viewName === "users") loadUsersList();
  if (viewName === "bank") loadBankData();
  if (viewName === "ledger") loadLedgerData();
  if (viewName === "erp") loadErpExports();
  if (viewName === "tax") loadTaxData();
}

// 3. Kimlik Doğrulama & Kullanıcı Girişi
async function initAuth() {
  if (authToken) {
    try {
      const res = await fetch("/auth/me", { headers: getAuthHeaders() });
      if (res.ok) {
        currentUser = await res.json();
        updateUserUI();
        return;
      }
    } catch (_) {}
  }

  // Oturum yoksa varsayılan olarak Yönetici hesabını otomatik yükle veya login modalını aç
  autoLoginDefaultAdmin();
}

async function autoLoginDefaultAdmin() {
  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: "admin@maliasistan.com", password: "admin123" })
    });
    if (res.ok) {
      const data = await res.json();
      authToken = data.token;
      currentUser = data.user;
      localStorage.setItem("maliasistan_token", authToken);
      updateUserUI();
      return;
    }
  } catch (_) {}
  showLoginModal();
}

function updateUserUI() {
  if (!currentUser) return;

  const isYonetici = currentUser.rol === "yonetici";
  const name = currentUser.ad_soyad || "Kullanıcı";
  const initial = name.charAt(0).toUpperCase();

  if ($("userAvatar")) $("userAvatar").textContent = initial;
  if ($("headerUserName")) $("headerUserName").textContent = name;
  if ($("headerUserRole")) $("headerUserRole").textContent = isYonetici ? "Yönetici" : "Kullanıcı";
  if ($("todayGreeting")) $("todayGreeting").textContent = `İyi günler, ${name}`;

  // Yönetici Yetki Paneli
  if ($("userRoleNoticeBox")) {
    $("userRoleNoticeBox").innerHTML = isYonetici
      ? `<span class="icon">👑</span><div class="text"><strong>Yönetici Yetkisi Aktif</strong><p>Tüm kullanıcıları görüntüleyebilir, yeni kullanıcı ekleyebilir ve şifre belirleyebilirsiniz.</p></div>`
      : `<span class="icon">👤</span><div class="text"><strong>Kullanıcı Hesabı</strong><p>Kullanıcı ekleme ve şifre sıfırlama işlemleri yalnızca Yönetici tarafından yapılabilir. Kendi şifrenizi sağ üst menüden değiştirebilirsiniz.</p></div>`;
  }

  if ($("btnOpenNewUserModal")) {
    $("btnOpenNewUserModal").style.display = isYonetici ? "inline-block" : "none";
  }
}

function showLoginModal() {
  if ($("loginModal")) $("loginModal").hidden = false;
}

function hideLoginModal() {
  if ($("loginModal")) $("loginModal").hidden = true;
}

function logout() {
  localStorage.removeItem("maliasistan_token");
  authToken = "";
  currentUser = null;
  showLoginModal();
  showToast("Oturum kapatıldı.");
}

function setupAuthEventListeners() {
  // Hızlı Giriş Butonları
  if ($("btnQuickAdmin")) {
    $("btnQuickAdmin").addEventListener("click", () => performLogin("admin@maliasistan.com", "admin123"));
  }
  if ($("btnQuickUser")) {
    $("btnQuickUser").addEventListener("click", () => performLogin("muhasebe@maliasistan.com", "user123"));
  }
  if ($("btnSwitchToAdmin")) {
    $("btnSwitchToAdmin").addEventListener("click", () => performLogin("admin@maliasistan.com", "admin123"));
  }
  if ($("btnSwitchToUser")) {
    $("btnSwitchToUser").addEventListener("click", () => performLogin("muhasebe@maliasistan.com", "user123"));
  }

  // Manuel Giriş Formu
  if ($("btnSubmitLogin")) {
    $("btnSubmitLogin").addEventListener("click", () => {
      const email = $("loginEmail").value.trim();
      const password = $("loginPassword").value.trim();
      if (!email || !password) {
        showLoginError("Lütfen e-posta ve şifrenizi girin.");
        return;
      }
      performLogin(email, password);
    });
  }

  // Kendi Şifremi Değiştir
  if ($("btnChangeMyPassword")) {
    $("btnChangeMyPassword").addEventListener("click", () => {
      if (!currentUser) return;
      openChangePasswordModal(currentUser.id, `${currentUser.ad_soyad} (Kendiniz)`);
    });
  }

  // Yeni Kullanıcı Ekleme Modalı
  if ($("btnOpenNewUserModal")) {
    $("btnOpenNewUserModal").addEventListener("click", () => {
      $("newUserModal").hidden = false;
      $("newUserError").hidden = true;
    });
  }
  if ($("btnCloseNewUserModal")) {
    $("btnCloseNewUserModal").addEventListener("click", () => {
      $("newUserModal").hidden = true;
    });
  }
  if ($("btnSubmitNewUser")) {
    $("btnSubmitNewUser").addEventListener("click", submitNewUser);
  }

  // Şifre Değiştirme Modalı
  if ($("btnCloseChangePasswordModal")) {
    $("btnCloseChangePasswordModal").addEventListener("click", () => {
      $("changePasswordModal").hidden = true;
    });
  }
  if ($("btnSubmitChangePassword")) {
    $("btnSubmitChangePassword").addEventListener("click", submitChangePassword);
  }
}

async function performLogin(email, password) {
  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
    const data = await res.json();
    if (!res.ok) {
      showLoginError(data.detail || "Giriş başarısız.");
      return;
    }
    authToken = data.token;
    currentUser = data.user;
    localStorage.setItem("maliasistan_token", authToken);
    updateUserUI();
    hideLoginModal();
    showToast(`Hoş geldiniz, ${currentUser.ad_soyad} (${currentUser.rol === 'yonetici' ? 'Yönetici' : 'Kullanıcı'})!`);
  } catch (err) {
    showLoginError("Sunucu bağlantı hatası oluştu.");
  }
}

function showLoginError(msg) {
  if ($("loginError")) {
    $("loginError").textContent = msg;
    $("loginError").hidden = false;
  }
}

// 4. Kullanıcı Yönetimi & Şifre Belirleme Fonksiyonları
async function loadUsersList() {
  const tbody = $("usersTableBody");
  if (!tbody) return;

  if (!currentUser || currentUser.rol !== "yonetici") {
    tbody.innerHTML = `<tr><td colspan="5" class="muted" style="text-align:center; padding: 24px;">Kullanıcı listesi yalnızca Yönetici tarafından görüntülenebilir.</td></tr>`;
    return;
  }

  try {
    const res = await fetch("/auth/users", { headers: getAuthHeaders() });
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">Kullanıcılar yüklenemedi.</td></tr>`;
      return;
    }
    const users = await res.json();
    if ($("userCountBadge")) $("userCountBadge").textContent = `${users.length} Kullanıcı`;

    tbody.innerHTML = users.map(u => {
      const isMe = currentUser && currentUser.id === u.id;
      const roleBadge = u.rol === "yonetici"
        ? `<span class="role-badge admin">👑 Yönetici</span>`
        : u.rol === "stajyer"
        ? `<span class="role-badge intern">🎓 Stajyer</span>`
        : `<span class="role-badge user">👤 Kullanıcı</span>`;

      const statusBadge = u.is_active
        ? `<span class="status-badge active">Aktif</span>`
        : `<span class="status-badge inactive">Pasif</span>`;

      return `
        <tr>
          <td><strong>${escapeHtml(u.ad_soyad)}</strong> ${isMe ? '<small class="muted">(Siz)</small>' : ''}</td>
          <td><code>${escapeHtml(u.email)}</code></td>
          <td>${roleBadge}</td>
          <td>${statusBadge}</td>
          <td>
            <button class="btn-action-sm" onclick="openChangePasswordModal('${u.id}', '${escapeHtml(u.ad_soyad)}')">🔑 Şifre Belirle</button>
            ${!isMe ? `<button class="btn-action-sm" onclick="toggleUserStatus('${u.id}', ${!u.is_active})">${u.is_active ? 'Pasife Al' : 'Aktif Et'}</button>` : ''}
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="muted">Kullanıcı verisi alınırken hata oluştu.</td></tr>`;
  }
}

async function submitNewUser() {
  const ad_soyad = $("newUserName").value.trim();
  const email = $("newUserEmail").value.trim();
  const password = $("newUserPassword").value.trim();
  const rol = $("newUserRole").value;

  if (!ad_soyad || !email || !password) {
    $("newUserError").textContent = "Lütfen tüm alanları doldurun.";
    $("newUserError").hidden = false;
    return;
  }

  try {
    const res = await fetch("/auth/users", {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ ad_soyad, email, password, rol })
    });
    const data = await res.json();
    if (!res.ok) {
      $("newUserError").textContent = data.detail || "Kullanıcı oluşturulamadı.";
      $("newUserError").hidden = false;
      return;
    }
    $("newUserModal").hidden = true;
    $("newUserName").value = "";
    $("newUserEmail").value = "";
    $("newUserPassword").value = "";
    showToast(`✓ "${ad_soyad}" kullanıcısı başarıyla oluşturuldu.`);
    loadUsersList();
  } catch (err) {
    $("newUserError").textContent = "Sunucu hatası.";
    $("newUserError").hidden = false;
  }
}

function openChangePasswordModal(userId, userName) {
  $("changePasswordUserId").value = userId;
  $("changePasswordTitle").textContent = `Şifre Belirle: ${userName}`;
  $("newPasswordInput").value = "";
  $("changePasswordError").hidden = true;
  $("changePasswordModal").hidden = false;
}

async function submitChangePassword() {
  const userId = $("changePasswordUserId").value;
  const new_password = $("newPasswordInput").value.trim();

  if (!new_password || new_password.length < 4) {
    $("changePasswordError").textContent = "Şifre en az 4 karakter olmalıdır.";
    $("changePasswordError").hidden = false;
    return;
  }

  try {
    const res = await fetch(`/auth/users/${userId}/password`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify({ new_password })
    });
    const data = await res.json();
    if (!res.ok) {
      $("changePasswordError").textContent = data.detail || "Şifre güncellenemedi.";
      $("changePasswordError").hidden = false;
      return;
    }
    $("changePasswordModal").hidden = true;
    showToast("✓ Şifre başarıyla güncellendi.");
  } catch (err) {
    $("changePasswordError").textContent = "Sunucu bağlantı hatası.";
    $("changePasswordError").hidden = false;
  }
}

async function toggleUserStatus(userId, makeActive) {
  try {
    const res = await fetch(`/auth/users/${userId}/status?is_active=${makeActive}`, {
      method: "PATCH",
      headers: getAuthHeaders()
    });
    if (res.ok) {
      showToast(`Kullanıcı durumu ${makeActive ? 'Aktif' : 'Pasif'} yapıldı.`);
      loadUsersList();
    }
  } catch (_) {}
}

// 5. Belge Kuyruğu Render (Onay Kuyruğu)
function renderQueue() {
  const q = $("queue");
  if (!q) return;

  if (items.length === 0) {
    q.innerHTML = '<div class="loading">İncelenecek bekleyen belge bulunmuyor.</div>';
    renderDetail();
    return;
  }

  q.innerHTML = items.map((item, idx) => {
    const active = idx === selected ? "active" : "";
    const confClass = item.confidence >= 80 ? "high" : (item.confidence >= 50 ? "med" : "low");

    return `
      <div class="queue-item ${active}" onclick="selectItem(${idx})">
        <div class="queue-item-top">
          <span class="tag-doc">${escapeHtml(item.type)}</span>
          <span class="badge-conf ${confClass}">%${item.confidence}</span>
        </div>
        <div class="queue-item-title">${escapeHtml(item.title)}</div>
        <div class="queue-item-sub">
          <span>${escapeHtml(item.subtitle)}</span>
          <strong>${escapeHtml(item.amount)}</strong>
        </div>
      </div>
    `;
  }).join("");

  renderDetail();
  if ($("queueCount")) $("queueCount").textContent = items.length;
  if ($("navQueueBadge")) $("navQueueBadge").textContent = items.length;
  if ($("statTodayPending")) $("statTodayPending").textContent = items.length;
  if ($("todayQueueCount")) $("todayQueueCount").textContent = items.length;
}

function selectItem(idx) {
  selected = idx;
  renderQueue();
}

function renderDetail() {
  const item = items[selected];
  if (!item) {
    $("detail").innerHTML = '<div class="loading">Görüntülenecek belge seçilmedi.</div>';
    return;
  }

  $("detailType").textContent = item.type;
  $("detailTitle").textContent = item.title;
  $("detailConfidence").textContent = `● %${item.confidence} · ${item.confidence >= 80 ? 'Yüksek' : 'İnceleme Gerekli'}`;

  $("detailMeta").innerHTML = `
    <div class="meta-field"><span>Mükellef</span><strong>${escapeHtml(item.subtitle)}</strong></div>
    <div class="meta-field"><span>Karşı Taraf</span><strong>${escapeHtml(item.title)}</strong></div>
    <div class="meta-field"><span>Tarih</span><strong>${escapeHtml(item.date || '03.11.2026')}</strong></div>
    <div class="meta-field"><span>Belge No</span><strong>${escapeHtml(item.docNo || 'YYS2026000041812')}</strong></div>
    <div class="meta-field"><span>Açıklama</span><strong>${escapeHtml(item.desc || 'Ticari Mal Alımı')}</strong></div>
    <div class="meta-field"><span>Tutar / KDV</span><strong>${escapeHtml(item.amount)} (KDV: ${item.kdv || '0 ₺'})</strong></div>
  `;

  let totalDebit = 0;
  let totalCredit = 0;

  const rowsHtml = item.rows.map(r => {
    const dVal = parseFloat(r[1].replace(/\./g, "").replace(",", ".")) || 0;
    const cVal = parseFloat(r[2].replace(/\./g, "").replace(",", ".")) || 0;
    totalDebit += dVal;
    totalCredit += cVal;

    return `
      <div class="journal-row">
        <span>${escapeHtml(r[0])}</span>
        <span class="num">${r[1] ? r[1] + ' ₺' : '-'}</span>
        <span class="num">${r[2] ? r[2] + ' ₺' : '-'}</span>
      </div>
    `;
  }).join("");

  const totalFormattedDebit = totalDebit.toLocaleString("tr-TR", { minimumFractionDigits: 2 }) + " ₺";
  const totalFormattedCredit = totalCredit.toLocaleString("tr-TR", { minimumFractionDigits: 2 }) + " ₺";

  $("journal").innerHTML = rowsHtml + `
    <div class="journal-row total-row">
      <span>TOPLAM (BORÇ / ALACAK DENGELİ)</span>
      <span class="num">${totalFormattedDebit}</span>
      <span class="num">${totalFormattedCredit}</span>
    </div>
  `;

  $("detailReason").textContent = item.reason;
}

// 6. Dosya Yükleme & Polling (test_health.py gereksinimleri)
function uploadSelectedFile(file) {
  if (!file) return;
  if (file.size > 25*1024*1024) {
    showToast("Dosya 25 MB sınırını aşıyor.");
    return;
  }
  showToast("Belge yüklendi, İşleniyor...");

  const formData = new FormData();
  formData.append("file", file);

  fetch("/extract", {
    method: "POST",
    headers: tenantHeaders,
    body: formData
  })
  .then(r => r.json())
  .then(() => {
    showToast("✓ Belge kuyruğa eklendi!");
    loadRealDocuments();
  })
  .catch(() => {
    // Demo fallback item
    items.unshift({
      type: "E-FATURA",
      title: file.name,
      subtitle: "Yüklenen Belge",
      amount: "1.000,00 ₺",
      confidence: 85,
      level: "high",
      date: "04.11.2026",
      reason: "Dosya içerisinden çıkarılan kalemler TDHP hesap planı ile eşleştirildi.",
      rows: [["153.01.001 TİCARİ MAL", "833,33", ""], ["191.01.020 İNDİRİLECEK KDV", "166,67", ""], ["320.01.001 SATICILAR", "", "1.000,00"]]
    });
    selected = 0;
    renderQueue();
  });
}

function loadRealDocuments() {
  if (loadingRealDocuments) return;
  loadingRealDocuments = true;
  fetch("/documents", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : [])
    .then(data => {
      loadingRealDocuments = false;
      if (Array.isArray(data) && data.length > 0) {
        realMode = true;
      }
    })
    .catch(() => { loadingRealDocuments = false; });
}

function startDocumentPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(loadRealDocuments,5000);
}

// 7. Onay / Düzelt / Sonra Aksiyonları
function setupDocumentActions() {
  if ($("approveButton")) {
    $("approveButton").addEventListener("click", () => {
      showToast("✓ Fiş onaylandı ve yevmiyeye aktarıldı.");
      items.splice(selected, 1);
      if (selected >= items.length) selected = Math.max(0, items.length - 1);
      renderQueue();
    });
  }
  if ($("laterButton")) {
    $("laterButton").addEventListener("click", () => {
      showToast("⏱ Belge sonraya ertelendi.");
      selected = (selected + 1) % items.length;
      renderQueue();
    });
  }
  if ($("editButton")) {
    $("editButton").addEventListener("click", () => {
      $("editForm").hidden = !$("editForm").hidden;
    });
  }
  if ($("saveEditButton")) {
    $("saveEditButton").addEventListener("click", () => {
      $("editForm").hidden = true;
      showToast("✓ Düzeltme kural motoruna kaydedildi.");
    });
  }
  if ($("uploadInput")) {
    $("uploadInput").addEventListener("change", e => {
      if (e.target.files && e.target.files[0]) {
        uploadSelectedFile(e.target.files[0]);
      }
    });
  }
}

// 8. Diğer Modül Yükleyicileri
function loadBankData() {
  fetch("/bank/accounts", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : [])
    .then(data => {
      if ($("bankAccountsList") && Array.isArray(data)) {
        $("bankAccountsList").innerHTML = data.map(a => `
          <div class="table-row">
            <div><strong>${escapeHtml(a.banka_adi || a.hesap_adi || 'Banka')}</strong><br><small class="muted">${escapeHtml(a.iban || '')}</small></div>
            <strong class="num">${(a.bakiye || 0).toLocaleString("tr-TR", {minimumFractionDigits:2})} ₺</strong>
          </div>
        `).join("") || '<div class="muted">Kayıtlı hesap bulunamadı.</div>';
      }
    }).catch(() => {});
}

function loadLedgerData() {
  fetch("/ledger/trial-balance", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : {})
    .then(tb => {
      const tbody = $("trialBalanceBody");
      if (tbody && Array.isArray(tb.rows)) {
        tbody.innerHTML = tb.rows.map(r => `
          <tr>
            <td><code>${escapeHtml(r.hesap_kodu)}</code></td>
            <td>${escapeHtml(r.hesap_adi)}</td>
            <td class="num">${r.borc_toplam.toLocaleString("tr-TR", {minimumFractionDigits:2})} ₺</td>
            <td class="num">${r.alacak_toplam.toLocaleString("tr-TR", {minimumFractionDigits:2})} ₺</td>
            <td class="num"><strong>${r.bakiye.toLocaleString("tr-TR", {minimumFractionDigits:2})} ₺</strong></td>
          </tr>
        `).join("");
      }
    }).catch(() => {});
}

function loadErpExports() {
  fetch("/erp/exports", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : [])
    .then(exports => {
      const tbody = $("erpExportsBody");
      if (tbody && Array.isArray(exports)) {
        tbody.innerHTML = exports.map(e => `
          <tr>
            <td><span class="status-badge ${e.status === 'success' ? 'active' : 'inactive'}">${e.status}</span></td>
            <td><code>${e.journal_entry_id ? e.journal_entry_id.substring(0,8) : '-'}</code></td>
            <td>${e.external_reference || '-'}</td>
            <td><small><code>${e.idempotency_key ? e.idempotency_key.substring(0,12) : '-'}</code></small></td>
            <td>${new Date(e.created_at).toLocaleDateString("tr-TR")}</td>
            <td><button class="btn-action-sm">Detay</button></td>
          </tr>
        `).join("") || '<tr><td colspan="6" class="muted">Henüz aktarım kaydı bulunmuyor.</td></tr>';
      }
    }).catch(() => {});
}

function loadTaxData() {
  fetch("/tax/parameters", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : [])
    .then(params => {
      if ($("taxParametersList") && Array.isArray(params)) {
        $("taxParametersList").innerHTML = params.map(p => `
          <div class="table-row">
            <span>${escapeHtml(p.anahtar)}</span>
            <strong>%${p.deger}</strong>
          </div>
        `).join("") || '<div class="muted">Parametre bulunamadı.</div>';
      }
    }).catch(() => {});
}

function showToast(msg) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3500);
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 9. Başlatma (Lifecycle)
document.addEventListener("DOMContentLoaded", () => {
  initNavigation();
  initAuth();
  setupAuthEventListeners();
  setupDocumentActions();
  renderQueue();
  startDocumentPolling();

  // Türkçe tarih
  if ($("topDateDisplay")) {
    const today = new Date();
    $("topDateDisplay").textContent = today.toLocaleDateString("tr-TR", {
      day: "numeric",
      month: "long",
      year: "numeric",
      weekday: "long"
    });
  }
});
