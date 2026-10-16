(() => {
  "use strict";

  // ------------------------------------------------------------------ constants
  const PLATFORMS = {
    youtube:  { label: "YouTube",  badge: "bg-[#E5322D] text-white" },
    tiktok:   { label: "TikTok",   badge: "bg-[#25F4EE] text-black" },
    rednote:  { label: "Rednote",  badge: "bg-[#FF2442] text-white" },
    bilibili: { label: "Bilibili", badge: "bg-[#0094C6] text-white" },
  };
  const TABS = [
    ["all", "All"], ["tiktok", "TikTok"], ["youtube", "YouTube"], ["rednote", "Rednote"], ["bilibili", "Bilibili"],
  ];
  const AUDIO_BITRATES = ["128", "192", "320"];
  const POLL_MS = 800;

  // ---------------------------------------------------------------------- state
  const state = {
    query: "",          // "" = home feed
    mode: "feed",       // feed | search | url
    filter: "all",      // platform tab
    kind: "all",        // all | shorts
    results: [],
    seen: new Set(),
    notices: [],
    page: 0,
    hasMore: true,
    loading: false,     // first page of a new listing
    loadingMore: false, // later pages
    error: null,
    emptyStreak: 0,     // consecutive pages that added nothing visible
    reqId: 0,
    pinned: false,
  };

  const viewer = { open: false, index: 0, list: [], autoNext: true, opener: null };
  const modal = { item: null, info: null, infoError: null, kind: "video", quality: "best", phase: "options", pollToken: 0, opener: null };

  // -------------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function fmtDuration(sec) {
    if (!sec || !isFinite(sec)) return "";
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function fmtViews(n) {
    if (n == null || !isFinite(n)) return "";
    const f = (v, u) => `${v >= 10 ? Math.round(v) : Math.round(v * 10) / 10}${u} views`;
    if (n >= 1e9) return f(n / 1e9, "B");
    if (n >= 1e6) return f(n / 1e6, "M");
    if (n >= 1e3) return f(n / 1e3, "K");
    return `${n} views`;
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON error body */ }
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status}).`);
    }
    return data;
  }

  const badge = (platform) => {
    const p = PLATFORMS[platform] || { label: platform, badge: "bg-raised text-fg" };
    return { label: p.label, cls: p.badge };
  };

  const matches = (r) =>
    (state.filter === "all" || r.platform === state.filter) && (state.kind !== "shorts" || r.short);
  const visibleResults = () => state.results.filter(matches);

  function syncScrollLock() {
    const modalOpen = !$("#modal").classList.contains("hidden");
    document.body.style.overflow = viewer.open || modalOpen ? "hidden" : "";
  }

  // ------------------------------------------------------------------ filter bar
  function renderTabs() {
    const platformTabs = TABS.map(([key, label]) => {
      const active = state.filter === key;
      return `<button type="button" data-filter="${key}" aria-pressed="${active}"
        class="shrink-0 rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${active ? "bg-fg text-black" : "text-dim hover:bg-raised hover:text-fg"}">${label}</button>`;
    }).join("");
    const shorts = state.kind === "shorts";
    $("#tabs").innerHTML = platformTabs + `
      <span class="mx-2 h-5 w-px shrink-0 bg-line" aria-hidden="true"></span>
      <button type="button" id="shorts-toggle" aria-pressed="${shorts}"
        class="flex shrink-0 items-center gap-1.5 rounded-full px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${shorts ? "bg-accent text-white" : "text-dim hover:bg-raised hover:text-fg"}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="3"/></svg>Shorts
      </button>`;
  }

  // -------------------------------------------------------------------- results
  function renderNotices() {
    const items = [];
    if (state.error) items.push({ text: state.error, tone: "error" });
    // The "can't keyword-search TikTok/Rednote" hint only matters for keyword searches.
    if (state.mode === "search") state.notices.forEach((t) => items.push({ text: t, tone: "info" }));
    $("#notices").innerHTML = items.map(({ text, tone }) => `
      <div role="${tone === "error" ? "alert" : "status"}" class="rounded-xl border px-4 py-3 text-sm ${tone === "error" ? "border-red-900 bg-red-950/60 text-red-200" : "border-line bg-panel text-dim"}">${esc(text)}</div>
    `).join("");
  }

  function gridClass() {
    return state.kind === "shorts"
      ? "grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-6"
      : "grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4";
  }

  function skeletonGrid() {
    const aspect = state.kind === "shorts" ? "aspect-[9/16]" : "aspect-video";
    const n = state.kind === "shorts" ? 10 : 8;
    return `<div class="${gridClass()}" aria-hidden="true">` + Array.from({ length: n }, () => `
      <div class="overflow-hidden rounded-xl border border-line bg-panel motion-safe:animate-pulse">
        <div class="${aspect} bg-raised"></div>
        <div class="space-y-3 p-4"><div class="h-4 w-4/5 rounded bg-raised"></div><div class="h-4 w-2/5 rounded bg-raised"></div></div>
      </div>`).join("") + `</div>`;
  }

  function emptyState(title, body) {
    return `<div class="rounded-2xl border border-dashed border-line px-6 py-14 text-center">
      <p class="font-display text-xl font-bold">${esc(title)}</p>
      <p class="mx-auto mt-2 max-w-md text-dim">${esc(body)}</p></div>`;
  }

  function card(item) {
    const b = badge(item.platform);
    const portrait = state.kind === "shorts";
    const duration = fmtDuration(item.duration);
    const idx = state.results.indexOf(item);
    return `
    <article class="group flex flex-col overflow-hidden rounded-xl border border-line bg-panel transition-colors hover:border-accent/60">
      <button type="button" data-play="${idx}" aria-label="Play ${esc(item.title)}"
              class="relative block w-full overflow-hidden bg-raised text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${portrait ? "aspect-[9/16]" : "aspect-video"}">
        ${item.thumbnail ? `<img src="${esc(item.thumbnail)}" alt="" loading="lazy" referrerpolicy="no-referrer" class="h-full w-full object-cover" onerror="this.remove()">` : ""}
        <span class="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/30 group-focus-within:bg-black/30">
          <svg class="opacity-0 drop-shadow transition-opacity group-hover:opacity-100 group-focus-within:opacity-100" width="44" height="44" viewBox="0 0 24 24" fill="white" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z"/></svg>
        </span>
        <span class="absolute left-2.5 top-2.5 rounded-md px-2 py-0.5 text-xs font-semibold ${b.cls}">${esc(b.label)}</span>
        ${duration ? `<span class="absolute bottom-2.5 right-2.5 rounded-md bg-black/80 px-1.5 py-0.5 text-xs font-medium tabular-nums text-white">${duration}</span>` : ""}
      </button>
      <div class="flex flex-1 flex-col p-3 sm:p-4">
        <h3 class="line-clamp-2 font-display text-base font-bold leading-snug" title="${esc(item.title)}">${esc(item.title)}</h3>
        <p class="mt-1 truncate text-sm text-dim">${esc(item.author || "Unknown author")}</p>
        ${item.views != null ? `<p class="text-sm text-dim/80">${fmtViews(item.views)}</p>` : ""}
        <div class="mt-auto flex items-center gap-2 pt-3">
          <button type="button" data-download="${idx}"
            class="h-10 flex-1 rounded-lg bg-accent text-sm font-semibold text-white transition-colors hover:bg-accent-hi focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-hi focus-visible:ring-offset-2 focus-visible:ring-offset-panel">Download</button>
          ${item.source ? `<button type="button" data-unpin="${esc(item.source)}" title="Remove this source from home" aria-label="Remove this source from home"
            class="flex h-10 w-10 items-center justify-center rounded-lg border border-line text-dim hover:border-dim hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg></button>` : ""}
        </div>
      </div>
    </article>`;
  }

  function headingHtml() {
    if (state.mode === "feed" || !state.searched) return "";
    const title = state.mode === "url" ? "Video from your link" : `Results for “${state.query}”`;
    const pin = state.mode === "url"
      ? `<button type="button" id="pin-btn" ${state.pinned ? "disabled" : ""}
           class="rounded-full border border-line px-4 py-2 text-sm font-medium hover:border-dim focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:text-dim disabled:hover:border-line">${state.pinned ? "Added to home" : "Add to home"}</button>`
      : "";
    return `<div class="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 class="font-display text-xl font-bold">${esc(title)}</h2>${pin}</div>`;
  }

  function emptyMessage() {
    const label = PLATFORMS[state.filter] ? PLATFORMS[state.filter].label : "";
    const linkOnly = state.filter === "tiktok" || state.filter === "rednote";
    if (linkOnly && state.mode === "feed")
      return emptyState(`No ${label} videos yet`, `Paste a ${label} video or profile link above, then choose Add to home. It will show up here.`);
    if (linkOnly && state.mode === "search")
      return emptyState(`${label} needs a link`, `${label} can't be searched by keyword. Paste a ${label} link above instead.`);
    if (state.kind === "shorts")
      return emptyState("No shorts found", "Try another platform tab, or search for something else.");
    return emptyState("No videos found", "Try different words, or paste a link.");
  }

  function renderResults() {
    const el = $("#results");
    if (state.loading) { el.innerHTML = skeletonGrid(); return; }
    const list = visibleResults();
    if (!list.length) {
      el.innerHTML = state.loadingMore || (state.hasMore && state.emptyStreak < 3 && state.page > 0 && state.mode !== "url")
        ? skeletonGrid() : headingHtml() + emptyMessage();
      return;
    }
    el.innerHTML = headingHtml() + `<div class="${gridClass()}">${list.map(card).join("")}</div>`;
  }

  function renderStatus() {
    $("#more-status").textContent = state.loadingMore ? "Loading more videos"
      : (!state.hasMore && visibleResults().length > 0) ? "That's everything for now." : "";
  }

  function renderAll() { renderTabs(); renderNotices(); renderResults(); renderStatus(); }

  // -------------------------------------------------------------------- loading
  async function load(reset) {
    if (!reset && (state.loading || state.loadingMore || !state.hasMore)) return;
    const reqId = ++state.reqId;
    if (reset) {
      Object.assign(state, { results: [], seen: new Set(), notices: [], page: 0, hasMore: true, error: null, emptyStreak: 0, pinned: false, loading: true, loadingMore: false, searched: false });
      $("#search-btn").disabled = true;
    } else {
      state.loadingMore = true;
    }
    renderAll();

    const page = state.page + 1;
    const params = new URLSearchParams({ page, kind: state.kind, platform: state.filter });
    if (state.query) params.set("q", state.query);
    try {
      const data = await api(`${state.query ? "/api/search" : "/api/feed"}?${params}`);
      if (reqId !== state.reqId) return;
      state.mode = data.mode; state.page = page; state.hasMore = !!data.has_more; state.searched = true;
      if (page === 1) state.notices = data.notices || [];
      let added = 0;
      for (const r of data.results || []) {
        if (state.seen.has(r.url)) continue;
        state.seen.add(r.url); state.results.push(r);
        if (matches(r)) added++;
      }
      state.emptyStreak = added ? 0 : state.emptyStreak + 1;
    } catch (err) {
      if (reqId !== state.reqId) return;
      state.error = err.message; state.hasMore = false; state.searched = true;
    } finally {
      if (reqId === state.reqId) {
        state.loading = false; state.loadingMore = false;
        $("#search-btn").disabled = false;
        if (viewer.open) { viewer.list = visibleResults(); updateNav(); }
        renderAll();
        maybeLoadMore();
      }
    }
  }

  function maybeLoadMore() {
    if (state.loading || state.loadingMore || !state.hasMore || state.emptyStreak >= 3 || !state.searched) return;
    if (state.mode === "url" && state.filter !== "all") return;
    const top = $("#sentinel").getBoundingClientRect().top;
    if (top < window.innerHeight + 600) load(false);
  }

  function startQuery(q) {
    state.query = q.trim();
    state.mode = state.query ? "search" : "feed";
    state.filter = "all";
    load(true);
  }

  // --------------------------------------------------------------------- viewer
  const ytId = (u) => (u.match(/(?:v=|youtu\.be\/|shorts\/|embed\/)([\w-]{11})/) || [])[1];
  const biliParam = (u) => {
    const bv = (u.match(/BV[0-9A-Za-z]+/) || [])[0];
    if (bv) return `bvid=${bv}`;
    const av = (u.match(/av(\d+)/i) || [])[1];
    return av ? `aid=${av}` : null;
  };

  function playerHtml(item) {
    const portrait = item.short && item.platform !== "bilibili";
    const boxStyle = portrait
      ? "height:100%;aspect-ratio:9/16;max-width:100%"
      : "width:100%;max-width:1100px;aspect-ratio:16/9;max-height:100%";
    const frame = (src) => `<iframe class="h-full w-full rounded-xl bg-black" src="${esc(src)}" title="${esc(item.title)}"
      allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe>`;

    let inner;
    const yt = item.platform === "youtube" && ytId(item.url);
    const bili = item.platform === "bilibili" && biliParam(item.url);
    if (yt) {
      inner = frame(`https://www.youtube-nocookie.com/embed/${yt}?autoplay=1&playsinline=1&rel=0&enablejsapi=1&origin=${encodeURIComponent(location.origin)}`);
    } else if (bili) {
      inner = frame(`https://player.bilibili.com/player.html?isOutside=true&${bili}&p=1&autoplay=1&danmaku=0`);
    } else {
      // TikTok, Rednote and anything else: the server proxies the media file (CDNs block hot-linking).
      inner = `<video class="h-full w-full rounded-xl bg-black object-contain" src="/api/stream?url=${encodeURIComponent(item.url)}" autoplay playsinline controls></video>`;
    }
    return `<div class="relative" style="${boxStyle}">${inner}
      <p id="v-error" class="absolute inset-x-4 bottom-4 hidden rounded-lg bg-black/85 p-3 text-center text-sm text-fg"></p></div>`;
  }

  function showViewerItem(i) {
    const item = viewer.list[i];
    if (!item) return;
    viewer.index = i;
    const stage = $("#v-stage");
    stage.innerHTML = playerHtml(item);

    const b = badge(item.platform);
    $("#v-badge").textContent = b.label;
    $("#v-badge").className = `shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${b.cls}`;
    $("#v-title").textContent = item.title;
    $("#v-open").href = item.url;

    const video = $("video", stage);
    if (video) {
      video.addEventListener("ended", () => { if (viewer.autoNext) step(1); });
      video.addEventListener("error", () => showPlayerError("This video can't be played here. Try Download or Open original."));
      video.play().catch(() => { video.muted = true; video.play().catch(() => {}); });
    }
    const frame = $("iframe", stage);
    if (frame && item.platform === "youtube") {
      // Ask the YouTube player to report state changes so "autoplay next" works.
      frame.addEventListener("load", () => {
        try { frame.contentWindow.postMessage(JSON.stringify({ event: "listening", id: 1, channel: "widget" }), "*"); } catch { /* ignore */ }
      });
    }
    updateNav();
    if (i >= viewer.list.length - 3) load(false);
  }

  function showPlayerError(text) {
    const el = $("#v-error");
    if (el) { el.textContent = text; el.classList.remove("hidden"); }
  }

  function updateNav() {
    $("#v-prev").disabled = viewer.index <= 0;
    $("#v-next").disabled = viewer.index >= viewer.list.length - 1;
  }

  function step(delta) {
    const next = viewer.index + delta;
    if (next < 0 || next >= viewer.list.length) return;
    showViewerItem(next);
  }

  function openViewer(item, opener) {
    viewer.list = visibleResults();
    const i = viewer.list.indexOf(item);
    if (i < 0) return;
    viewer.open = true; viewer.opener = opener;
    $("#viewer").classList.remove("hidden");
    syncScrollLock();
    showViewerItem(i);
    $("#v-close").focus();
  }

  function closeViewer() {
    viewer.open = false;
    $("#v-stage").innerHTML = ""; // removing the player also stops playback
    $("#viewer").classList.add("hidden");
    syncScrollLock();
    if (viewer.opener && document.contains(viewer.opener)) viewer.opener.focus();
  }

  window.addEventListener("message", (e) => {
    if (!viewer.open || !viewer.autoNext) return;
    let host = "";
    try { host = new URL(e.origin).hostname; } catch { return; }
    if (!/(^|\.)youtube(-nocookie)?\.com$/.test(host)) return;
    try {
      const d = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (d && d.event === "onStateChange" && d.info === 0) step(1); // 0 = ended
    } catch { /* not a player message */ }
  });

  // ---------------------------------------------------------------------- modal
  const segClass = (on) =>
    `rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${on ? "border-accent bg-accent/10" : "border-line hover:border-dim"}`;
  const chipClass = (on) =>
    `rounded-full border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${on ? "border-accent bg-accent text-white" : "border-line text-fg hover:border-dim"}`;

  function renderKind() {
    const labels = { video: ["Video", "MP4 with sound"], audio: ["Audio only", "MP3 file"] };
    $$("#m-kind [data-kind]").forEach((btn) => {
      const on = btn.dataset.kind === modal.kind;
      const [title, sub] = labels[btn.dataset.kind];
      btn.className = segClass(on);
      btn.setAttribute("aria-pressed", on);
      btn.innerHTML = `<span class="block text-sm font-semibold">${title}</span><span class="block text-sm text-dim">${sub}</span>`;
    });
  }

  function renderQuality() {
    const isVideo = modal.kind === "video";
    $("#m-quality-label").textContent = isVideo ? "Resolution" : "Audio quality";

    let options;
    if (!isVideo) {
      options = AUDIO_BITRATES.map((b) => [b, `${b} kbps`]);
    } else if (!modal.info && !modal.infoError) {
      $("#m-quality").innerHTML = Array.from({ length: 4 }, () => `<span class="h-9 w-20 rounded-full bg-raised motion-safe:animate-pulse"></span>`).join("");
      $("#m-note").textContent = "Checking which resolutions are available";
      updateConfirm();
      return;
    } else {
      options = [["best", "Best available"], ...((modal.info && modal.info.qualities) || []).map((h) => [String(h), `${h}p`])];
    }

    $("#m-quality").innerHTML = options.map(([value, label]) =>
      `<button type="button" data-q="${value}" aria-pressed="${modal.quality === value}" class="${chipClass(modal.quality === value)}">${label}</button>`
    ).join("");

    $("#m-note").textContent = modal.infoError
      ? `Couldn't read the resolution list (${modal.infoError}). You can still download the best available.`
      : isVideo
        ? "Only resolutions this video offers are shown. Higher ones are merged from separate streams, so they take a little longer."
        : "Audio is extracted from the video and converted to MP3.";
    updateConfirm();
  }

  function updateConfirm() {
    const btn = $("#m-confirm");
    if (modal.kind === "audio") btn.textContent = `Download MP3 (${modal.quality} kbps)`;
    else btn.textContent = modal.quality === "best" ? "Download MP4" : `Download MP4 (${modal.quality}p)`;
    btn.disabled = modal.phase === "options" && modal.kind === "video" && !modal.info && !modal.infoError;
  }

  function setPhase(phase) {
    modal.phase = phase;
    $("#m-options").classList.toggle("hidden", phase !== "options");
    $("#m-progress").classList.toggle("hidden", phase === "options");
    $("#m-confirm").classList.toggle("hidden", phase === "running" || phase === "done");
    $("#m-cancel").textContent = phase === "done" ? "Close" : phase === "running" ? "Hide" : "Cancel";
    if (phase === "error") $("#m-confirm").textContent = "Try again";
    if (phase === "options") updateConfirm();
  }

  function setProgress(pct, status, sub = "") {
    $("#m-bar").style.width = `${pct}%`;
    $("#m-bar-wrap").setAttribute("aria-valuenow", Math.round(pct));
    $("#m-status").textContent = status;
    $("#m-sub").innerHTML = sub;
  }

  async function openModal(item, opener) {
    Object.assign(modal, { item, info: null, infoError: null, kind: "video", quality: "best", opener, pollToken: modal.pollToken + 1 });

    const b = badge(item.platform);
    const thumb = $("#m-thumb");
    thumb.style.display = item.thumbnail ? "" : "none";
    if (item.thumbnail) thumb.src = item.thumbnail;
    const badgeEl = $("#m-badge");
    badgeEl.textContent = b.label;
    badgeEl.className = `inline-block rounded-md px-2 py-0.5 text-xs font-semibold ${b.cls}`;
    $("#m-title").textContent = item.title;
    $("#m-author").textContent = item.author || "";

    renderKind(); setPhase("options"); renderQuality();
    $("#modal").classList.remove("hidden");
    syncScrollLock();
    $("#m-confirm").focus();

    try {
      const info = await api(`/api/info?url=${encodeURIComponent(item.url)}`);
      if (modal.item !== item) return;
      modal.info = info;
    } catch (err) {
      if (modal.item !== item) return;
      modal.infoError = err.message;
    }
    if (modal.phase === "options") renderQuality();
  }

  function closeModal() {
    modal.pollToken++;  // stops any running poll loop
    modal.item = null;
    $("#modal").classList.add("hidden");
    syncScrollLock();
    const back = viewer.open ? $("#v-download") : modal.opener;
    if (back && document.contains(back)) back.focus();
  }

  async function startDownload() {
    const item = modal.item;
    if (!item) return;
    const token = ++modal.pollToken;
    setPhase("running");
    setProgress(2, "Starting");

    try {
      const { job_id } = await api("/api/downloads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: item.url, kind: modal.kind, quality: modal.quality }),
      });

      while (token === modal.pollToken) {
        const job = await api(`/api/downloads/${job_id}`);
        if (token !== modal.pollToken) return;

        if (job.status === "queued") setProgress(2, "Waiting for a free slot");
        else if (job.status === "downloading") setProgress(Math.max(job.progress, 3), `Downloading ${Math.round(job.progress)}%`);
        else if (job.status === "processing") setProgress(Math.max(job.progress, 92), modal.kind === "audio" ? "Converting to MP3" : "Merging video and audio");
        else if (job.status === "error") throw new Error(job.error || "The download failed.");
        else if (job.status === "done") {
          const fileUrl = `/api/downloads/${job_id}/file`;
          setProgress(100, "Done. Your download is starting.",
            `If nothing happens, <a class="font-medium text-accent-hi underline underline-offset-2" href="${fileUrl}" download>save the file</a>. It stays available for a few minutes.`);
          setPhase("done");
          triggerSave(fileUrl);
          return;
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    } catch (err) {
      if (token !== modal.pollToken) return;
      setPhase("error");
      setProgress(0, "Download failed", esc(err.message));
    }
  }

  function triggerSave(url) {
    const a = document.createElement("a");
    a.href = url; a.download = ""; a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
  }

  // --------------------------------------------------------------------- events
  $("#search-form").addEventListener("submit", (e) => { e.preventDefault(); startQuery($("#q").value); });

  $("#home-btn").addEventListener("click", () => { $("#q").value = ""; startQuery(""); window.scrollTo({ top: 0 }); });

  $("#tabs").addEventListener("click", (e) => {
    const shortsBtn = e.target.closest("#shorts-toggle");
    const tab = e.target.closest("[data-filter]");
    if (!shortsBtn && !tab) return;
    if (shortsBtn) state.kind = state.kind === "shorts" ? "all" : "shorts";
    else state.filter = tab.dataset.filter;

    // A pasted link is a fixed set of videos, so filter it locally. Everything else is re-fetched
    // so "load more" keeps pulling videos for the chosen platform / shorts view.
    if (state.mode === "url") { renderAll(); maybeLoadMore(); }
    else load(true);
    const focusTarget = shortsBtn ? $("#shorts-toggle") : $(`#tabs [data-filter="${state.filter}"]`);
    if (focusTarget) focusTarget.focus();
  });

  $("#results").addEventListener("click", async (e) => {
    const play = e.target.closest("[data-play]");
    if (play) { openViewer(state.results[Number(play.dataset.play)], play); return; }

    const dl = e.target.closest("[data-download]");
    if (dl) { openModal(state.results[Number(dl.dataset.download)], dl); return; }

    if (e.target.closest("#pin-btn")) {
      try {
        await api("/api/sources", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: state.query }) });
        state.pinned = true;
      } catch (err) { state.error = err.message; }
      renderAll();
      return;
    }

    const unpin = e.target.closest("[data-unpin]");
    if (unpin) {
      const src = unpin.dataset.unpin;
      try {
        await api(`/api/sources?url=${encodeURIComponent(src)}`, { method: "DELETE" });
        state.results = state.results.filter((r) => r.source !== src);
      } catch (err) { state.error = err.message; }
      renderAll();
    }
  });

  // player controls
  $("#v-close").addEventListener("click", closeViewer);
  $("#v-prev").addEventListener("click", () => step(-1));
  $("#v-next").addEventListener("click", () => step(1));
  $("#v-download").addEventListener("click", (e) => { const item = viewer.list[viewer.index]; if (item) openModal(item, e.currentTarget); });
  $("#v-auto").addEventListener("click", (e) => {
    viewer.autoNext = !viewer.autoNext;
    e.currentTarget.setAttribute("aria-pressed", viewer.autoNext);
    e.currentTarget.textContent = `Autoplay next: ${viewer.autoNext ? "on" : "off"}`;
  });

  // swipe / scroll to change video (like Shorts and TikTok)
  let wheelLock = false, touchY = null;
  $("#viewer").addEventListener("wheel", (e) => {
    if (wheelLock || Math.abs(e.deltaY) < 40 || !$("#modal").classList.contains("hidden")) return;
    wheelLock = true; setTimeout(() => (wheelLock = false), 600);
    step(e.deltaY > 0 ? 1 : -1);
  }, { passive: true });
  $("#viewer").addEventListener("touchstart", (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  $("#viewer").addEventListener("touchend", (e) => {
    if (touchY == null) return;
    const dy = touchY - e.changedTouches[0].clientY; touchY = null;
    if (Math.abs(dy) > 70) step(dy > 0 ? 1 : -1);
  }, { passive: true });

  // download modal
  $("#modal").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) { closeModal(); return; }

    const kindBtn = e.target.closest("[data-kind]");
    if (kindBtn && modal.kind !== kindBtn.dataset.kind) {
      modal.kind = kindBtn.dataset.kind;
      modal.quality = modal.kind === "audio" ? "192" : "best";
      renderKind(); renderQuality();
      return;
    }

    const q = e.target.closest("[data-q]");
    if (q) {
      modal.quality = q.dataset.q;
      $$("#m-quality [data-q]").forEach((b) => {
        const on = b.dataset.q === modal.quality;
        b.className = chipClass(on);
        b.setAttribute("aria-pressed", on);
      });
      updateConfirm();
    }
  });

  $("#m-confirm").addEventListener("click", () => {
    if (modal.phase === "error") { setPhase("options"); renderKind(); renderQuality(); return; }
    startDownload();
  });

  document.addEventListener("keydown", (e) => {
    const modalOpen = !$("#modal").classList.contains("hidden");
    if (e.key === "Escape") {
      if (modalOpen) closeModal(); else if (viewer.open) closeViewer();
      return;
    }
    const root = modalOpen ? $("#modal") : viewer.open ? $("#viewer") : null;
    if (!root) return;

    if (e.key === "Tab") { // keep focus inside the open dialog
      const focusable = $$("button:not([disabled]), a[href]", root).filter((el) => el.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      return;
    }
    if (viewer.open && !modalOpen) {
      if (["ArrowDown", "ArrowRight", "j"].includes(e.key)) { e.preventDefault(); step(1); }
      else if (["ArrowUp", "ArrowLeft", "k"].includes(e.key)) { e.preventDefault(); step(-1); }
    }
  });

  new IntersectionObserver(() => maybeLoadMore(), { rootMargin: "600px" }).observe($("#sentinel"));

  // ----------------------------------------------------------------------- init
  state.searched = false;
  renderAll();
  $("#q").focus();
  load(true);
})();
