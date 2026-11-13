// Constat - MaliAsistan SMMM Kokpit & Kullanıcı Yönetimi Motoru

// 1. Kuyruk durumu (gerçek veri: GET /documents/queue)
let items = [];
let selected = 0;
let realMode = false;
let pollTimer = null;
let loadingRealDocuments = false;

// Kimlik & Yetki Durumu
let currentUser = null;
let authToken = localStorage.getItem("maliasistan_token") || "";

const params = new URLSearchParams(location.search);
// Büro kimliği yalnızca token'dan gelir; panel yalnızca seçili mükellefi gönderir.
let selectedMukellefId = params.get("mukellef_id") || localStorage.getItem("maliasistan_mukellef") || "";

const ROLE_LABELS = {
  admin: "👑 Yönetici",
  musavir: "🧾 Müşavir",
  personel: "👤 Personel",
  mukellef: "🔒 Mükellef (salt okuma)"
};

function tenantHeaders() {
  const headers = {};
  if (selectedMukellefId) headers["X-Mukellef-ID"] = selectedMukellefId;
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  return headers;
}

async function ensureMukellefSelected() {
  if (!authToken) return;
  try {
    const res = await fetch("/auth/me/mukellefler", { headers: tenantHeaders() });
    if (!res.ok) return;
    const list = await res.json();
    if (!list.some(m => m.id === selectedMukellefId)) {
      selectedMukellefId = list.length ? list[0].id : "";
      localStorage.setItem("maliasistan_mukellef", selectedMukellefId);
    }
  } catch (_) {}
}

const $ = id => document.getElementById(id);

function getAuthHeaders() {
  return { "Content-Type": "application/json", ...tenantHeaders() };
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
        await ensureMukellefSelected();
        loadRealDocuments();
        loadMizanSummary();
        updateUserUI();
        return;
      }
    } catch (_) {}
  }

  // Geçerli oturum yoksa giriş ekranı açılır. Varsayılan hesap yoktur.
  localStorage.removeItem("maliasistan_token");
  authToken = "";
  showLoginModal();
}

function updateUserUI() {
  if (!currentUser) return;

  const isYonetici = currentUser.rol === "admin";
  const name = currentUser.ad_soyad || "Kullanıcı";
  const initial = name.charAt(0).toUpperCase();

  if ($("userAvatar")) $("userAvatar").textContent = initial;
  if ($("headerUserName")) $("headerUserName").textContent = name;
  if ($("headerUserRole")) $("headerUserRole").textContent = ROLE_LABELS[currentUser.rol] || currentUser.rol;
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
  // Sunucuda kullanıcının tüm token'ları iptal edilir (token_version artar).
  if (authToken) fetch("/auth/logout", { method: "POST", headers: tenantHeaders() }).catch(() => {});
  localStorage.removeItem("maliasistan_token");
  authToken = "";
  currentUser = null;
  showLoginModal();
  showToast("Oturum kapatıldı.");
}

function setupAuthEventListeners() {
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
    await ensureMukellefSelected();
    loadRealDocuments();
    loadMizanSummary();
    updateUserUI();
    hideLoginModal();
    showToast(`Hoş geldiniz, ${currentUser.ad_soyad} (${ROLE_LABELS[currentUser.rol] || currentUser.rol})!`);
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

  if (!currentUser || currentUser.rol !== "admin") {
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
      const roleClass = { admin: "admin", musavir: "admin", personel: "user", mukellef: "intern" }[u.rol] || "user";
      const roleBadge = `<span class="role-badge ${roleClass}">${ROLE_LABELS[u.rol] || u.rol}</span>`;

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
            <button class="btn-action-sm" data-action="change-password" data-user-id="${escapeHtml(u.id)}" data-user-name="${escapeHtml(u.ad_soyad)}">🔑 Şifre Belirle</button>
            ${!isMe ? `<button class="btn-action-sm" data-action="toggle-status" data-user-id="${escapeHtml(u.id)}" data-make-active="${!u.is_active}">${u.is_active ? 'Pasife Al' : 'Aktif Et'}</button>` : ''}
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
  // Kendi şifresini değiştiren kullanıcıdan mevcut şifre istenir.
  const isSelf = currentUser && currentUser.id === userId;
  $("currentPasswordInput").value = "";
  $("currentPasswordGroup").hidden = !isSelf;
  $("changePasswordError").hidden = true;
  $("changePasswordModal").hidden = false;
}

async function submitChangePassword() {
  const userId = $("changePasswordUserId").value;
  const new_password = $("newPasswordInput").value;
  const current_password = $("currentPasswordInput").value;

  if (!new_password || new_password.length < 12) {
    $("changePasswordError").textContent = "Şifre en az 12 karakter olmalı; büyük/küçük harf ve rakam içermelidir.";
    $("changePasswordError").hidden = false;
    return;
  }

  try {
    const res = await fetch(`/auth/users/${userId}/password`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: JSON.stringify(currentUser && currentUser.id === userId
        ? { new_password, current_password }
        : { new_password })
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

// 5. Belge Kuyruğu (gerçek veri)
const CAN_APPROVE = () => currentUser && (currentUser.rol === "admin" || currentUser.rol === "musavir");
const CAN_WRITE = () => currentUser && currentUser.rol !== "mukellef";

function formatTL(value) {
  const n = Number(value);
  if (value === null || value === undefined || value === "" || Number.isNaN(n)) return "-";
  return n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " ₺";
}

function formatDate(iso) {
  if (!iso) return "-";
  const [y, m, d] = String(iso).slice(0, 10).split("-");
  return d && m && y ? `${d}.${m}.${y}` : iso;
}

function toViewItem(doc) {
  const inv = doc.invoice;
  const posting = doc.posting || {};
  const direction = posting.direction;
  const party = inv ? (direction === "sales" ? inv.customer : inv.supplier) : null;
  let type = (doc.document_category || doc.document_type || "belge").toUpperCase();
  if (inv) type = direction === "sales" ? "SATIŞ FATURASI" : direction === "purchase" ? "ALIŞ FATURASI" : "E-FATURA";
  else if (doc.document_category === "z_report") type = "Z RAPORU";
  else if (doc.document_type === "image") type = "FİŞ / GÖRSEL";

  let status = "İşleniyor";
  let level = "med";
  if (doc.extraction_status === "failed" || posting.status === "needs_review" || doc.review_status === "needs_review") {
    status = "İnceleme gerekli"; level = "low";
  } else if (posting.status === "draft_ready") {
    status = "Onaya hazır"; level = "high";
  } else if (doc.extraction_status === "pending" || doc.extraction_status === "processing") {
    status = "İşleniyor"; level = "med";
  }
  const reasons = (posting.reasons && posting.reasons.length) ? posting.reasons
    : (doc.extraction_error ? [doc.extraction_error] : ["Belge işleniyor..."]);
  return {
    id: doc.id,
    type,
    status,
    level,
    title: (party && party.name) || doc.filename,
    vkn: party && party.tax_id,
    docNo: inv ? inv.invoice_id : doc.filename,
    date: inv ? formatDate(inv.issue_date) : formatDate(doc.created_at),
    amount: inv ? formatTL(inv.payable_amount) : "-",
    base: inv ? formatTL(inv.tax_exclusive_amount) : "-",
    kdv: inv ? formatTL(inv.tax_total) : "-",
    confidence: posting.confidence ? Math.round(Number(posting.confidence) * 100) : 0,
    reason: reasons.join(" "),
    entry: doc.entry,
    reviewStatus: doc.review_status,
    canCorrect: Boolean(inv)
  };
}

function renderQueue() {
  const q = $("queue");
  if (!q) return;
  if ($("queueCount")) $("queueCount").textContent = items.length;
  if ($("navQueueBadge")) $("navQueueBadge").textContent = items.length;
  if ($("statTodayPending")) $("statTodayPending").textContent = items.length;
  if ($("todayQueueCount")) $("todayQueueCount").textContent = items.length;

  if (items.length === 0) {
    q.innerHTML = '<div class="loading">İncelenecek bekleyen belge bulunmuyor. Sağ üstten "Belge Yükle" ile e-Fatura (XML), PDF veya fiş görseli ekleyebilirsiniz.</div>';
    renderDetail();
    return;
  }
  if (selected >= items.length) selected = items.length - 1;

  q.innerHTML = items.map((item, idx) => `
      <div class="queue-item ${idx === selected ? "active" : ""}" data-action="select-item" data-index="${idx}">
        <div class="queue-item-top">
          <span class="tag-doc">${escapeHtml(item.type)}</span>
          <span class="badge-conf ${item.level}">${escapeHtml(item.status)}</span>
        </div>
        <div class="queue-item-title">${escapeHtml(item.title)}</div>
        <div class="queue-item-sub">
          <span>${escapeHtml(item.docNo)} · ${escapeHtml(item.date)}</span>
          <strong>${escapeHtml(item.amount)}</strong>
        </div>
      </div>`).join("");
  renderDetail();
}

function selectItem(idx) {
  selected = idx;
  if ($("editForm")) $("editForm").hidden = true;
  renderQueue();
}

function renderDetail() {
  const item = items[selected];
  const actions = $("approveButton") ? $("approveButton").parentElement : null;
  if (!item) {
    $("detailType").textContent = "-";
    $("detailTitle").textContent = "Belge seçilmedi";
    $("detailConfidence").textContent = "";
    $("detailMeta").innerHTML = "";
    $("journal").innerHTML = '<div class="loading">Önerilen kayıt yok.</div>';
    $("detailReason").textContent = "";
    if (actions) actions.hidden = true;
    return;
  }
  if (actions) actions.hidden = false;

  $("detailType").textContent = item.type;
  $("detailTitle").textContent = item.title;
  $("detailConfidence").textContent = item.confidence
    ? `● %${item.confidence} · ${item.status}` : `● ${item.status}`;

  $("detailMeta").innerHTML = `
    <div class="meta-field"><span>Karşı Taraf</span><strong>${escapeHtml(item.title)}</strong></div>
    <div class="meta-field"><span>VKN / TCKN</span><strong>${escapeHtml(item.vkn || "-")}</strong></div>
    <div class="meta-field"><span>Tarih</span><strong>${escapeHtml(item.date)}</strong></div>
    <div class="meta-field"><span>Belge No</span><strong>${escapeHtml(item.docNo)}</strong></div>
    <div class="meta-field"><span>Matrah / KDV</span><strong>${escapeHtml(item.base)} / ${escapeHtml(item.kdv)}</strong></div>
    <div class="meta-field"><span>Toplam</span><strong>${escapeHtml(item.amount)}</strong></div>
  `;

  const lines = item.entry ? item.entry.lines : [];
  if (!lines.length) {
    $("journal").innerHTML = '<div class="loading">Bu belge için otomatik kayıt oluşturulamadı. Gerekçe aşağıda.</div>';
  } else {
    let debit = 0, credit = 0;
    const rows = lines.map(l => {
      debit += Number(l.debit); credit += Number(l.credit);
      return `<div class="journal-row">
          <span>${escapeHtml(l.account_code)} ${escapeHtml(l.account_name)}</span>
          <span class="num">${Number(l.debit) ? escapeHtml(formatTL(l.debit)) : "-"}</span>
          <span class="num">${Number(l.credit) ? escapeHtml(formatTL(l.credit)) : "-"}</span>
        </div>`;
    }).join("");
    $("journal").innerHTML = rows + `
      <div class="journal-row total-row">
        <span>TOPLAM ${Math.abs(debit - credit) < 0.005 ? "(DENGELİ)" : "(DENGESİZ!)"}</span>
        <span class="num">${escapeHtml(formatTL(debit))}</span>
        <span class="num">${escapeHtml(formatTL(credit))}</span>
      </div>`;
  }
  $("detailReason").textContent = item.reason;

  const hasDraft = item.entry && item.entry.status === "draft";
  if ($("approveButton")) {
    $("approveButton").disabled = !hasDraft || !CAN_APPROVE();
    $("approveButton").title = !CAN_APPROVE() ? "Onay yalnızca müşavir veya yönetici tarafından yapılabilir."
      : (!hasDraft ? "Onaylanacak taslak yok; önce hesabı düzeltin." : "Taslak fişi yevmiyeye işle");
  }
  if ($("editButton")) $("editButton").disabled = !item.canCorrect || !CAN_WRITE();
  if ($("laterButton")) $("laterButton").disabled = !CAN_WRITE();
  if (item.entry && item.entry.lines.length && $("accountCodeInput")) {
    const main = item.entry.lines[item.type === "SATIŞ FATURASI" ? 1 : 0];
    $("accountCodeInput").value = main.account_code;
    $("accountNameInput").value = main.account_name;
  }
}

// 6. Dosya Yükleme & Kuyruk Yenileme
function uploadSelectedFile(file) {
  if (!file) return;
  if (file.size > 25*1024*1024) {
    showToast("Dosya 25 MB sınırını aşıyor.");
    return;
  }
  showToast("Belge yükleniyor, İşleniyor...");
  const formData = new FormData();
  formData.append("upload", file);

  fetch("/documents/upload", { method: "POST", headers: tenantHeaders(), body: formData })
    .then(async r => {
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof data.detail === "string" ? data.detail : (data.detail && data.detail.message) || "Yükleme başarısız.";
        throw new Error(detail);
      }
      return data;
    })
    .then(doc => fetch(`/documents/${doc.id}/extract`, { method: "POST", headers: tenantHeaders() }))
    .then(() => {
      showToast("✓ Belge okundu ve kuyruğa eklendi.");
      selected = 0;
      loadRealDocuments();
    })
    .catch(err => {
      showToast("Yükleme hatası: " + err.message);
      loadRealDocuments();
    });
}

async function uploadFiles(fileList) {
  for (const file of Array.from(fileList)) {
    await new Promise(resolve => { uploadSelectedFile(file); setTimeout(resolve, 400); });
  }
}

function loadRealDocuments() {
  if (loadingRealDocuments || !authToken || !selectedMukellefId) return;
  loadingRealDocuments = true;
  const currentId = items[selected] ? items[selected].id : null;
  fetch("/documents/queue", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : { items: [] })
    .then(data => {
      loadingRealDocuments = false;
      items = (data.items || []).map(toViewItem);
      const keep = items.findIndex(i => i.id === currentId);
      selected = keep >= 0 ? keep : Math.min(selected, Math.max(0, items.length - 1));
      realMode = true;
      renderQueue();
    })
    .catch(() => { loadingRealDocuments = false; });
}

function startDocumentPolling() {
  if (pollTimer) clearInterval(pollTimer);
  loadRealDocuments();
  pollTimer = setInterval(loadRealDocuments,5000);
}

async function documentAction(path, body) {
  const item = items[selected];
  if (!item) return null;
  const res = await fetch(`/documents/${item.id}/${path}`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast("İşlem yapılamadı: " + (typeof data.detail === "string" ? data.detail : "hata"));
    return null;
  }
  return data;
}

// 7. Onay / Düzelt / Sonra Aksiyonları (gerçek API)
function setupDocumentActions() {
  if ($("approveButton")) {
    $("approveButton").addEventListener("click", async () => {
      const done = await documentAction("approve");
      if (done) { showToast("✓ Fiş yevmiyeye işlendi."); loadRealDocuments(); }
    });
  }
  if ($("laterButton")) {
    $("laterButton").addEventListener("click", async () => {
      const done = await documentAction("later");
      if (done) {
        showToast("⏱ Belge incelemeye bırakıldı.");
        selected = (selected + 1) % Math.max(1, items.length);
        loadRealDocuments();
      }
    });
  }
  if ($("editButton")) {
    $("editButton").addEventListener("click", () => {
      $("editForm").hidden = !$("editForm").hidden;
    });
  }
  if ($("saveEditButton")) {
    $("saveEditButton").addEventListener("click", async () => {
      const account_code = $("accountCodeInput").value.trim();
      const account_name = $("accountNameInput").value.trim();
      const note = $("noteInput").value.trim() || null;
      if (!/^[1-9][0-9]{2}(\.[0-9A-Za-z]{1,20}){0,3}$/.test(account_code) || account_name.length < 2) {
        showToast("Geçerli bir TDHP hesap kodu (ör. 770.01) ve hesap adı girin.");
        return;
      }
      const done = await documentAction("correct", { account_code, account_name, note });
      if (done) {
        $("editForm").hidden = true;
        showToast("✓ Taslak yeni hesapla güncellendi.");
        loadRealDocuments();
      }
    });
  }
  if ($("uploadInput")) {
    $("uploadInput").addEventListener("change", e => {
      if (e.target.files && e.target.files.length) {
        uploadFiles(e.target.files);
        e.target.value = "";
      }
    });
  }
}

// 8. Diğer Modül Yükleyicileri
let bankAccounts = [];

async function loadBankData() {
  try {
    const res = await fetch("/bank/accounts", { headers: getAuthHeaders() });
    const data = res.ok ? await res.json() : { accounts: [] };
    bankAccounts = data.accounts || [];
    if ($("bankAccountsCount")) $("bankAccountsCount").textContent = `${bankAccounts.length} Hesap`;
    if ($("bankAccountsList")) {
      $("bankAccountsList").innerHTML = bankAccounts.length
        ? bankAccounts.map(a => `
          <div class="table-row">
            <div><strong>${escapeHtml(a.ledger_account_code)} · ${escapeHtml(a.bank_name)}</strong><br>
              <small class="muted">${escapeHtml(a.iban)} · ${escapeHtml(a.account_name)}</small></div>
            <span class="status-badge ${a.status === "active" ? "active" : "inactive"}">${a.status === "active" ? "Aktif" : "Pasif"}</span>
          </div>`).join("")
        : '<div class="muted">Henüz banka hesabı yok. "Ekstre Yükle" ile Excel, CSV veya PDF ekstre yüklediğinizde hesap otomatik açılır.</div>';
    }
  } catch (_) {}
  loadBankTransactions();
}

async function loadBankTransactions() {
  const box = $("reconcileQueue");
  if (!box) return;
  try {
    const res = await fetch("/bank/transactions", { headers: getAuthHeaders() });
    const data = res.ok ? await res.json() : { transactions: [] };
    const txs = (data.transactions || [])
      .sort((a, b) => (b.transaction_date || "").localeCompare(a.transaction_date || ""));
    if (!txs.length) {
      box.innerHTML = '<div class="loading">Henüz banka hareketi yok.</div>';
      return;
    }
    const byAccount = Object.fromEntries(bankAccounts.map(a => [a.iban, a]));
    const statusLabel = { unmatched: "Fişle eşleşmedi", matched: "Fişle eşleşti", ignored: "Yok sayıldı" };
    box.innerHTML = `
      <p class="muted">${txs.length} hareket · son 300 gösteriliyor · <b>Fişle eşleşmedi</b>: hareketin karşılığı olan yevmiye fişi (ör. fatura tahsilatı veya ödemesi) henüz bağlanmadı.</p>
      <div class="table-responsive"><table class="data-table">
        <thead><tr><th>Tarih</th><th>Hesap</th><th>Açıklama</th><th class="num">Giriş</th><th class="num">Çıkış</th><th class="num">Bakiye</th><th>Durum</th></tr></thead>
        <tbody>${txs.slice(0, 300).map(t => {
          const acc = byAccount[t.iban];
          return `<tr>
            <td>${escapeHtml(formatDate(t.transaction_date))}</td>
            <td><small>${escapeHtml(acc ? acc.ledger_account_code + " " + acc.bank_name : t.iban)}</small></td>
            <td><small>${escapeHtml((t.description || "").slice(0, 120))}</small></td>
            <td class="num">${Number(t.debit) ? escapeHtml(formatTL(t.debit)) : ""}</td>
            <td class="num">${Number(t.credit) ? escapeHtml(formatTL(t.credit)) : ""}</td>
            <td class="num">${t.balance !== null ? escapeHtml(formatTL(t.balance)) : "-"}</td>
            <td><span title="Banka hareketinin karşılığı olan yevmiye fişi (fatura tahsilatı/ödemesi) bulunup bağlandı mı?" class="status-badge ${t.reconciliation_status === "matched" ? "active" : "inactive"}">${escapeHtml(statusLabel[t.reconciliation_status] || t.reconciliation_status)}</span></td>
          </tr>`;
        }).join("")}</tbody>
      </table></div>`;
  } catch (_) {
    box.innerHTML = '<div class="loading">Banka hareketleri yüklenemedi.</div>';
  }
}

async function importBankStatement(file, iban) {
  if (file.size > 10 * 1024 * 1024) {
    showToast("Ekstre dosyası 10 MB sınırını aşıyor.");
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  const url = "/bank/import" + (iban ? `?iban=${encodeURIComponent(iban)}` : "");
  showToast(`${file.name} okunuyor...`);
  const res = await fetch(url, { method: "POST", headers: tenantHeaders(), body: formData });
  const data = await res.json().catch(() => ({}));
  if (res.status === 422 && data.detail && data.detail.code === "iban_required") {
    const known = bankAccounts.map(a => `${a.ledger_account_code} ${a.bank_name}: ${a.iban}`).join("\n");
    const answer = window.prompt(
      `${data.detail.message}\n\n${known ? "Kayıtlı hesaplar:\n" + known + "\n\n" : ""}IBAN girin (TR ile başlayan):`
    );
    if (answer && answer.trim()) return importBankStatement(file, answer.trim());
    showToast("İçe aktarma iptal edildi.");
    return;
  }
  if (!res.ok) {
    showToast("Ekstre okunamadı: " + (typeof data.detail === "string" ? data.detail : "hata"));
    return;
  }
  showToast("✓ " + data.message + (data.warnings && data.warnings.length ? " ⚠ " + data.warnings.join(" ") : ""));
  loadBankData();
}

function setupBankUpload() {
  const input = $("bankUploadInput");
  if (!input) return;
  input.addEventListener("change", async e => {
    for (const file of Array.from(e.target.files || [])) {
      await importBankStatement(file);
    }
    e.target.value = "";
  });
}

function loadLedgerData() {
  loadTrialBalance();
  loadJournalEntries();
}

function loadTrialBalance() {
  fetch("/ledger/trial-balance", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : null)
    .then(tb => {
      const tbody = $("trialBalanceBody");
      if (!tbody) return;
      const rows = (tb && tb.trial_balance) || [];
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px;">Henüz onaylanmış yevmiye fişi yok. Onay kuyruğundan bir belgeyi onayladığınızda mizan burada oluşur.</td></tr>';
        if ($("trialBalanceFoot")) $("trialBalanceFoot").innerHTML = "";
        if ($("mizanBalanceStatus")) $("mizanBalanceStatus").textContent = "Kayıt yok";
        return;
      }
      tbody.innerHTML = rows.map(r => `
          <tr>
            <td><code>${escapeHtml(r.account_code)}</code></td>
            <td>${escapeHtml(r.account_name)}</td>
            <td class="num">${escapeHtml(formatTL(r.total_debit))}</td>
            <td class="num">${escapeHtml(formatTL(r.total_credit))}</td>
            <td class="num"><strong>${escapeHtml(formatTL(r.balance))}</strong></td>
          </tr>`).join("");
      if ($("trialBalanceFoot")) {
        $("trialBalanceFoot").innerHTML = `
          <tr><th colspan="2">GENEL TOPLAM</th>
            <th class="num">${escapeHtml(formatTL(tb.grand_total_debit))}</th>
            <th class="num">${escapeHtml(formatTL(tb.grand_total_credit))}</th><th></th></tr>`;
      }
      if ($("mizanBalanceStatus")) {
        $("mizanBalanceStatus").textContent = tb.is_balanced
          ? "✓ Fişler Dengede (Borç = Alacak)" : "⚠ Mizan dengesiz!";
      }
    }).catch(() => {});
}

async function loadJournalEntries() {
  const box = $("journalEntriesList");
  if (!box) return;
  try {
    const res = await fetch("/ledger/entries?status=approved", { headers: getAuthHeaders() });
    const data = res.ok ? await res.json() : { entries: [] };
    const entries = (data.entries || []).slice(0, 100);
    if (!entries.length) {
      box.innerHTML = '<div class="loading">Henüz onaylanmış yevmiye fişi yok.</div>';
      return;
    }
    const details = await Promise.all(entries.map(e =>
      fetch(`/ledger/entries/${e.id}`, { headers: getAuthHeaders() }).then(r => r.ok ? r.json() : null)
    ));
    box.innerHTML = details.filter(Boolean).map(d => `
      <div class="card panel-box" style="margin-bottom:12px;">
        <div class="panel-heading">
          <h3>${escapeHtml(formatDate(d.entry_date))} · ${escapeHtml(d.description)}</h3>
          ${CAN_APPROVE() ? `<button class="btn-action-sm" data-action="reverse-entry" data-entry-id="${escapeHtml(d.id)}">↺ Ters Kayıt</button>` : ""}
        </div>
        <table class="data-table">
          <thead><tr><th>Hesap</th><th class="num">Borç</th><th class="num">Alacak</th></tr></thead>
          <tbody>${d.lines.map(l => `
            <tr><td><code>${escapeHtml(l.account_code)}</code> ${escapeHtml(l.account_name)}</td>
              <td class="num">${Number(l.debit) ? escapeHtml(formatTL(l.debit)) : "-"}</td>
              <td class="num">${Number(l.credit) ? escapeHtml(formatTL(l.credit)) : "-"}</td></tr>`).join("")}
          </tbody>
          <tfoot><tr><th>Toplam</th><th class="num">${escapeHtml(formatTL(d.total_debit))}</th>
            <th class="num">${escapeHtml(formatTL(d.total_credit))}</th></tr></tfoot>
        </table>
      </div>`).join("");
  } catch (_) {
    box.innerHTML = '<div class="loading">Yevmiye fişleri yüklenemedi.</div>';
  }
}

async function reverseEntry(entryId) {
  if (!window.confirm("Bu fiş için ters kayıt oluşturulsun mu? (Orijinal fiş silinmez, karşı kayıt atılır.)")) return;
  const res = await fetch(`/ledger/entries/${entryId}/reverse`, { method: "POST", headers: getAuthHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast("Ters kayıt yapılamadı: " + (typeof data.detail === "string" ? data.detail : "hata"));
    return;
  }
  showToast("✓ Ters kayıt taslağı oluşturuldu.");
  loadLedgerData();
}

function setupLedgerTabs() {
  const showTab = journal => {
    if ($("ledgerTrialBalanceView")) $("ledgerTrialBalanceView").hidden = journal;
    if ($("ledgerEntriesView")) $("ledgerEntriesView").hidden = !journal;
    if ($("tabBtnTrialBalance")) $("tabBtnTrialBalance").classList.toggle("active", !journal);
    if ($("tabBtnJournalEntries")) $("tabBtnJournalEntries").classList.toggle("active", journal);
  };
  if ($("tabBtnTrialBalance")) $("tabBtnTrialBalance").addEventListener("click", () => showTab(false));
  if ($("tabBtnJournalEntries")) $("tabBtnJournalEntries").addEventListener("click", () => showTab(true));
  if ($("btnRefreshLedger")) $("btnRefreshLedger").addEventListener("click", () => {
    loadLedgerData();
    showToast("Mizan yenilendi.");
  });
}

function loadErpExports() {
  fetch("/erp/exports", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : [])
    .then(exports => {
      const tbody = $("erpExportsBody");
      if (tbody && Array.isArray(exports)) {
        tbody.innerHTML = exports.map(e => `
          <tr>
            <td><span class="status-badge ${e.status === 'exported' ? 'active' : 'inactive'}">${escapeHtml(e.status)}</span></td>
            <td><code>${escapeHtml(e.journal_entry_id ? String(e.journal_entry_id).substring(0,8) : '-')}</code></td>
            <td>${escapeHtml(e.external_reference || '-')}</td>
            <td><small><code>${escapeHtml(e.idempotency_key ? String(e.idempotency_key).substring(0,12) : '-')}</code></small></td>
            <td>${new Date(e.created_at).toLocaleDateString("tr-TR")}</td>
            <td><button class="btn-action-sm">Detay</button></td>
          </tr>
        `).join("") || '<tr><td colspan="6" class="muted">Henüz aktarım kaydı bulunmuyor.</td></tr>';
      }
    }).catch(() => {});
}

function loadTaxData() {
  fetch("/tax/parameters", { headers: getAuthHeaders() })
    .then(r => r.ok ? r.json() : { parameters: [] })
    .then(data => {
      const params = data.parameters || [];
      if (!$("taxParametersList")) return;
      $("taxParametersList").innerHTML = params.length
        ? params.map(p => `
          <div class="table-row">
            <span>${escapeHtml(p.key)}<br><small class="muted">${escapeHtml(formatDate(p.start))} – ${p.end ? escapeHtml(formatDate(p.end)) : "yürürlükte"}</small></span>
            <strong>${escapeHtml(p.value)}</strong>
          </div>`).join("")
        : '<div class="muted">Bu büro için tanımlı vergi parametresi yok. Oran ve hadler resmi kaynaktan (Resmî Gazete / GİB) doğrulanarak girilmelidir; sistem tahmini değer kullanmaz.</div>';
    }).catch(() => {});
}

// Mizan yükleme (Mükellef kartı)
async function loadMizanSummary() {
  if (!authToken || !selectedMukellefId) return;
  try {
    const res = await fetch("/chart/summary", { headers: getAuthHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    if ($("mizanAccountCount")) $("mizanAccountCount").textContent = `${data.accounts} hesap`;
    if ($("mizanStatusPill")) {
      $("mizanStatusPill").textContent = !data.accounts ? "Mizan yüklenmedi"
        : (data.balanced ? "✓ Mizan tutuyor" : "⚠ Mizan tutmuyor");
      $("mizanStatusPill").className = data.accounts && data.balanced ? "pill-green" : "pill-gray";
    }
  } catch (_) {}
}

function setupMizanUpload() {
  const button = $("btnUploadMizan");
  const input = $("mizanUploadInput");
  if (!button || !input) return;
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", async e => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    showToast("Mizan okunuyor...");
    const res = await fetch("/chart/mizan", { method: "POST", headers: tenantHeaders(), body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast("Mizan okunamadı: " + (typeof data.detail === "string" ? data.detail : "hata"));
      return;
    }
    showToast("✓ " + data.message);
    loadMizanSummary();
  });
}

function showToast(msg) {
  const t = $("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 3500);
}

// Satır içi onclick/onsubmit kullanılmaz (CSP script-src 'self'); tıklamalar burada yakalanır.
function setupDelegatedActions() {
  document.addEventListener("click", event => {
    const target = event.target.closest("[data-action]");
    if (!target) return;
    const action = target.dataset.action;
    if (action === "select-item") {
      selectItem(Number(target.dataset.index));
    } else if (action === "change-password") {
      openChangePasswordModal(target.dataset.userId, target.dataset.userName);
    } else if (action === "toggle-status") {
      toggleUserStatus(target.dataset.userId, target.dataset.makeActive === "true");
    } else if (action === "reverse-entry") {
      reverseEntry(target.dataset.entryId);
    }
  });
  document.querySelectorAll("form[data-no-submit]").forEach(form => {
    form.addEventListener("submit", event => event.preventDefault());
  });
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
  setupDelegatedActions();
  setupLedgerTabs();
  setupBankUpload();
  setupMizanUpload();
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
