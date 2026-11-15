'use strict';

let currentView = 'home';
let lastPreviewRows = [];
let inited = false;
let searchTimer = null;
let currentTheme = 'light';
let embeddedMode = true;            // 软件内播放开关（设置存 SQLite）；失败自动降级外部播放
let playerStageActive = false;      // 播放舞台是否已展开
let currentPlaying = { fileId: null, path: null, title: '' };  // 上一集/下一集据此定位
let embedSyncTimer = null;          // 仅内嵌播放期间存在，退出立即清掉
let embedResizeTimer = null;
let daemonAutoStart = true;         // 设置里是否允许自动开启后台记录
let daemonBootUntil = 0;            // 启动宽限期截止时间戳：刚 Popen 时先显示「启动中」，别误报未启动

// 展示风格：黑 / 白两套。存在 SQLite 设置表里（WebView2 用 private_mode，localStorage 不持久）。
function applyTheme(theme) {
  currentTheme = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', currentTheme);
  const icon = document.getElementById('themeIcon');
  const label = document.getElementById('themeLabel');
  if (icon) icon.textContent = currentTheme === 'dark' ? '☀' : '☾';
  if (label) label.textContent = currentTheme === 'dark' ? '浅色' : '深色';
}

// 看过的列表：一次载入全量后本地即时过滤，行内改名只局部刷新表格
let watchedRows = [];
let watchedKeyword = '';
let watchedEditing = 0;
let watchedGrouped = true;          // 同一作品的多季收拢成一组，避免列表散成一片
const watchedOpen = new Set();     // 已展开的分组 key

const VIEW_META = {
  home: ['总览', '你的播放进度一目了然'],
  continue: ['继续观看', '从上次中断的位置接着看'],
  history: ['历史记录', '每次观看的时间与进度'],
  library: ['媒体库', '扫描到的本地视频文件'],
  posters: ['剧集墙', '按作品展示封面、季数与续播'],
  quicksearch: ['全盘快搜', '整台电脑的视频文件，一搜即播'],
  watched: ['看过的列表', '你导入的已观看作品'],
  watchlist: ['追剧列表', '已下载 / 未下载清单，可调整顺序并对应本地剧集'],
  import: ['导入列表', '把看过的动漫一次性记录到数据库'],
  scan: ['扫描目录', '添加已下载视频的存放位置'],
  processes: ['后台进程', '启停后台记录、查看剧集图片缓存进度'],
  settings: ['设置', '播放器、记录与中文名匹配'],
};

const MATCH_LABEL = {
  builtin: ['离线词典', 'green'],
  local: ['本地词典', 'green'],
  online: ['在线匹配', 'green'],
  native: ['中文原名', 'blue'],
  user: ['手动指定', 'green'],
  base: ['主标题匹配', 'blue'],
  none: ['未匹配', 'amber'],
};

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtMs(ms) {
  const s = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${m}:${String(sec).padStart(2, '0')}`;
}

function shortDate(text) {
  return text ? String(text).slice(5, 16) : '—';
}

function num(v) { return Number(v || 0); }

function apiReady() { return !!(window.pywebview && window.pywebview.api); }

async function call(name, ...args) {
  if (!apiReady()) throw new Error('本地服务尚未就绪');
  const fn = window.pywebview.api[name];
  if (typeof fn !== 'function') throw new Error(`未实现的方法：${name}`);
  return await fn(...args);
}

function toast(message, ok) {
  const el = document.getElementById('toast');
  el.textContent = (ok === false ? '×  ' : '✓  ') + message;
  el.classList.add('show');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), 3200);
}

function setContent(html) { document.getElementById('content').innerHTML = html; }

function empty(text, sub) {
  return `<div class="empty"><div class="big">${esc(text)}</div><div>${esc(sub || '')}</div></div>`;
}

// 危险操作的二次确认：列出全部受影响路径，默认焦点在「取消」，Esc / 点遮罩都算取消。
// WebView2 的原生 confirm 不可靠且放不下一长串路径，所以用自己的模态框。
function confirmDialog({ title, message, note = '', items = [],
                         confirmText = '确认删除', danger = true }) {
  return new Promise(resolve => {
    const layer = document.createElement('div');
    layer.className = 'modal-layer';
    layer.innerHTML = `<div class="modal-box" role="dialog" aria-modal="true">
      <h3>${esc(title)}</h3>
      ${danger ? '<div class="modal-warning">⚠️ 此操作非常危险，可能导致不可逆的数据丢失！</div>' : ''}
      <p class="modal-msg">${esc(message)}</p>
      ${note ? `<p class="modal-note">${esc(note)}</p>` : ''}
      ${items.length ? `<div class="modal-list">${items.map(t => `<div class="mono">${esc(t)}</div>`).join('')}</div>` : ''}
      <div class="modal-actions">
        <button class="btn" data-modal="cancel">取消</button>
        <button class="btn btn-danger" data-modal="ok">${esc(confirmText)}</button>
      </div>
    </div>`;
    document.body.appendChild(layer);
    const done = (value) => {
      layer.remove();
      document.removeEventListener('keydown', onKey);
      resolve(value);
    };
    const onKey = (e) => { if (e.key === 'Escape') done(false); };
    layer.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-modal]');
      if (btn) done(btn.dataset.modal === 'ok');
      else if (e.target === layer) done(false);
    });
    document.addEventListener('keydown', onKey);
    const cancel = layer.querySelector('[data-modal="cancel"]');
    if (cancel) cancel.focus();
  });
}

function fmtSize(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024 * 1024) return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

// 删除本地文件的统一流程：先向后端取权威清单 → 二次确认 → 才真正移入回收站。
async function confirmAndDeleteLocalFiles(ids, groupId) {
  const preview = await call('get_delete_candidates', ids, groupId);
  if (!preview.ok) { toast(preview.error || '无法准备删除清单', false); return; }
  const usable = (preview.files || []).filter(f => f.deletable);
  const skipped = (preview.files || []).length - usable.length;
  if (!usable.length) { toast('没有可删除的本地文件（可能已移除或尚未看完）'); return; }
  const total = usable.reduce((sum, f) => sum + num(f.size), 0);
  const ok = await confirmDialog({
    title: groupId ? `删除「${preview.title || '这部作品'}」的本地文件` : '删除本地视频文件',
    message: `以下 ${usable.length} 个文件（合计 ${fmtSize(total)}）将被移入 Windows 回收站，可从回收站恢复。`
      + '观看进度、历史记录、看过的列表和媒体库记录会全部保留，不会自动删除。',
    note: skipped ? `另有 ${skipped} 个文件已移除或不符合条件，将自动跳过。` : '',
    items: usable.map(f => f.path),
    confirmText: `移入回收站（${usable.length} 个）`,
  });
  if (!ok) return;
  const r = await call('delete_local_files', usable.map(f => f.id), groupId);
  if (r.deleted) toast(`已移入回收站 ${r.deleted} 个文件 · 观看记录全部保留`, r.failed === 0);
  else toast((r.errors && r.errors[0]) || '没有文件被删除', false);
  if (r.failed) toast(`另有 ${r.failed} 个文件删除失败，记录已保留`, false);
  await refresh();
}

// ---------- 软件内播放舞台 ----------
// PotPlayer 是原生窗口：这里只负责给出一块「舞台」的屏幕矩形，
// 由 Python 侧用 SetParent 把播放器摆进去；失败会自动降级为独立窗口播放。
function hostRect() {
  const host = document.getElementById('playerHost');
  if (!host) return { x: 0, y: 74, w: 960, h: 540, dpr: 1 };
  const r = host.getBoundingClientRect();
  return { x: r.left, y: r.top, w: r.width, h: r.height, dpr: window.devicePixelRatio || 1 };
}

function openPlayerStage(title) {
  const stage = document.getElementById('playerStage');
  if (!stage) return;
  document.getElementById('playerStageTitle').textContent = title || '正在播放';
  document.getElementById('playerStageHint').textContent = '播放器已嵌入本窗口，进度照常自动记录';
  const holder = stage.querySelector('.player-holder');
  if (holder) holder.textContent = '正在启动 PotPlayer…';
  stage.hidden = false;
  playerStageActive = true;
}

function startEmbedSync() {
  clearInterval(embedSyncTimer);
  embedSyncTimer = setInterval(async () => {
    if (!playerStageActive) return;
    try { await call('embed_sync', hostRect()); } catch (e) { /* 窗口关闭时忽略 */ }
  }, 700);
}

async function stopEmbedded(silent) {
  if (!playerStageActive) return;
  playerStageActive = false;
  clearInterval(embedSyncTimer);
  embedSyncTimer = null;
  const stage = document.getElementById('playerStage');
  if (stage) stage.hidden = true;
  try { await call('embed_stop'); } catch (e) { /* 已退出时忽略 */ }
  if (!silent) toast('已退出内嵌，PotPlayer 在独立窗口继续播放');
}

async function refreshEmbedState() {
  try {
    const s = await call('embed_status');
    embeddedMode = !!s.enabled;
    const btn = document.getElementById('embedToggle');
    if (!btn) return;
    btn.classList.toggle('on', embeddedMode);
    document.getElementById('embedLabel').textContent = embeddedMode ? '内嵌播放' : '独立窗口';
    btn.title = embeddedMode ? '当前：软件内播放（点击改为独立窗口）'
      : (s.available ? '当前：独立窗口播放（点击改为软件内）' : '当前系统不支持窗口内嵌');
  } catch (e) { /* 后端尚未就绪时忽略 */ }
}

async function playSmart(kind, value, fromStart) {
  const byPath = kind === 'path';
  const started = { fileId: byPath ? null : Number(value), path: byPath ? String(value) : null,
    title: byPath ? String(value).split(/[\\/]/).pop() : '' };
  if (!embeddedMode) {
    const r = byPath
      ? await call('play_external', value)
      : await call('play_file', Number(value), fromStart);
    if (r.ok) currentPlaying = started;
    toast(r.ok ? (r.position_ms ? `从 ${fmtMs(r.position_ms)} 继续播放` : '已开始播放')
      : (r.error || '播放失败'), r.ok);
    return;
  }
  // 先把舞台展开再发起调用：Python 侧要等 PotPlayer 窗口出现，属于异步等待，
  // 界面此时仍可交互，不会像以前那样「点了没反应」。
  openPlayerStage(byPath ? started.title : '正在载入…');
  const rect = hostRect();
  const r = byPath
    ? await call('play_embedded_path', value, rect)
    : await call('play_embedded', Number(value), fromStart, rect);
  if (!r.ok) {
    await stopEmbedded(true);
    toast(r.error || '播放失败', false);
    return;
  }
  if (!r.embedded) {
    currentPlaying = started;
    await stopEmbedded(true);
    toast(r.reason || '已自动降级为独立窗口播放', true);
    return;
  }
  currentPlaying = { ...started, title: r.title || started.title };
  const titleEl = document.getElementById('playerStageTitle');
  if (titleEl) titleEl.textContent = currentPlaying.title || '正在播放';
  const hintEl = document.getElementById('playerEpisodeHint');
  if (hintEl) hintEl.textContent = currentPlaying.title || '';
  const holder = document.querySelector('#playerStage .player-holder');
  if (holder) holder.textContent = '';
  startEmbedSync();
  toast(r.position_ms ? `从 ${fmtMs(r.position_ms)} 继续播放（软件内）` : '已在软件内开始播放');
}

// ---------- 播放控制条（保留 PotPlayer，快捷键注入 + 命令行换片） ----------
async function playerControl(action) {
  const r = await call('player_control', action);
  toast(r.ok ? `已执行：${action}` : (r.error || '播放控制失败'), r.ok);
  return r;
}

async function playerAdjacent(direction) {
  if (!currentPlaying.fileId) { toast('请先从媒体库或剧集墙播放一集', false); return; }
  const r = await call('player_adjacent', currentPlaying.fileId, direction);
  if (!r.ok) { toast(r.error || '没有相邻剧集', false); return; }
  toast(`${direction === 'prev' ? '上一集' : '下一集'}：${r.title}（${r.index}/${r.total}）`);
  await playSmart('id', r.file_id, true);
}

async function setPlayerSpeed(value) {
  const r = await call('player_speed', Number(value));
  if (!r.ok) { toast(r.error || '倍速设置失败', false); return; }
  toast(r.exact ? `倍速已设为 ${r.actual}×`
    : `已设为 ${r.actual}×（本机步进 ${r.step}，${value}× 无法精确到达；可在「设置 → 播放控制」把步进改成 0.25）`, true);
}

function toggleStageZoom() {
  const stage = document.getElementById('playerStage');
  if (!stage || stage.hidden) return;
  // 内嵌时 PotPlayer 是子窗口，触发它自身全屏会脱离父窗口并破坏布局。
  // 因此只把播放区放大到整个窗口；独立窗口模式才把全屏键投给播放器。
  const zoomed = stage.classList.toggle('stage-zoom');
  const btn = document.getElementById('playerFullscreen');
  if (btn) btn.textContent = zoomed ? '⛶ 还原' : '⛶ 全屏';
  if (zoomed && playerStageActive) call('embed_sync', hostRect()).catch(() => {});
  if (!embeddedMode) call('player_control', 'fullscreen').catch(() => {});
}

function progressItem(r, actions) {
  const name = r.display_title || r.title_guess || r.filename || '未知文件';
  const ep = r.episode_no ? ` · 第 ${r.episode_no} 集` : '';
  const season = r.season_no ? ` · 第 ${r.season_no} 季` : '';
  const pct = Math.min(100, Math.max(0, num(r.percent)));
  const pos = fmtMs(r.position_ms), dur = fmtMs(r.duration_ms);
  const done = num(r.completed) === 1;
  return `<div class="item">
    <div class="thumb">▶</div>
    <div class="body">
      <div class="name">${esc(name)}${esc(ep)}${esc(season)}
        ${done ? '<span class="badge green">已看完</span>' : '<span class="badge blue">在看</span>'}</div>
      <div class="meta">${esc(r.filename || '')}</div>
      <div class="bar ${done ? 'done' : ''}"><span style="width:${pct}%"></span></div>
      <div class="meta">${pos} / ${dur} · ${pct.toFixed(0)}% · 最近 ${shortDate(r.last_played_at)}</div>
    </div>
    <div class="right">${actions === false ? '' : `<div class="btn-row">
      <button class="btn btn-primary btn-sm" data-action="play" data-id="${r.file_id}">继续观看</button>
      <button class="btn btn-sm" data-action="play-start" data-id="${r.file_id}">从头播放</button>
      <button class="btn btn-sm" data-action="mark-watched" data-id="${r.file_id}">标记已看</button>
    </div>`}</div>
  </div>`;
}

function statsBlock(s) {
  const items = [
    ['媒体库文件', s.files], ['导入的已看作品', s.watched],
    ['正在追的进度', s.in_progress], ['观看记录', s.sessions],
  ];
  return `<div class="stats">${items.map(([label, value]) =>
    `<div class="stat"><div class="label">${label}</div><div class="value">${num(value)}</div></div>`).join('')}</div>`;
}

async function renderHome() {
  const [s, cont, st] = await Promise.all([
    call('get_stats'), call('get_continue_watching', 6), call('get_player_status'),
  ]);
  const playing = st.available
    ? `<div class="card"><h3>正在播放</h3>
        <p class="hint">${esc(st.title_cn || st.media_name)} · ${fmtMs(st.position_ms)} / ${fmtMs(st.duration_ms)} · ${st.state === 'playing' ? '播放中' : st.state === 'paused' ? '已暂停' : '已停止'}
        ${st.healthy ? '<span class="badge green">进度回读正常</span>' : '<span class="badge amber">消息接口自检异常</span>'}</p>
        <div class="bar"><span style="width:${Math.min(100, num(st.percent))}%"></span></div></div>`
    : `<div class="card"><h3>正在播放</h3><p class="hint">PotPlayer 当前未运行。打开任意视频后，这里会实时显示进度，并自动写入历史。</p></div>`;

  setContent(`${statsBlock(s)}${playing}
    <div class="card"><div class="section-head"><h3>继续观看</h3>
      <button class="btn btn-sm" data-action="goto" data-view="continue">查看全部</button></div>
      <div class="list">${cont.length ? cont.map(r => progressItem(r)).join('') : empty('还没有播放记录', '在下方导入列表或扫描视频目录后即可开始')}</div>
    </div>
    <div class="card"><h3>快速开始</h3><p class="hint">三步用起来：导入看过的作品 → 添加视频目录 → 从「继续观看」点开任意一集。</p>
      <div class="btn-row">
        <button class="btn btn-primary" data-action="goto" data-view="import">导入看过的列表</button>
        <button class="btn" data-action="goto" data-view="scan">添加视频目录</button>
      </div></div>`);
}

async function renderContinue() {
  const [list, done] = await Promise.all([
    call('get_continue_watching', 100), call('get_completed', 20),
  ]);
  setContent(`<div class="card"><h3>继续观看</h3>
    <p class="hint">点击「继续观看」会用 PotPlayer 从上次的位置打开，播放过程按设置页的间隔自动记录。</p>
    <div class="list">${list.length ? list.map(r => progressItem(r)).join('') : empty('暂无进行中的播放', '先扫描目录并播放一集试试')}</div></div>
    <div class="card"><h3>已看完（可纠错 / 可清理本地文件）</h3>
    <p class="hint">标错或想重看时点「标记未看」即可恢复；观看历史不会被删除。已看完的单集可人工把本地文件移入回收站，
      <b>观看进度、历史记录与看过的列表全部保留</b>。整部剧全部看完后，「剧集墙」和「媒体库 → 剧集展示」里会出现整部清理入口。</p>
    <div class="list">${done.length ? done.map(r => `<div class="item">
      <div class="thumb">✓</div>
      <div class="body"><div class="name">${esc(r.display_title || r.filename)}
        ${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}
        ${r.deleted_at ? '<span class="badge amber">本地文件已移除</span>' : ''}</div>
        <div class="meta">${esc(r.filename || '')} · 最近 ${esc(shortDate(r.last_played_at))}</div></div>
      <div class="right"><div class="btn-row" style="justify-content:flex-end">
        <button class="btn btn-primary btn-sm" data-action="mark-unwatched" data-id="${r.file_id}">标记未看</button>
        <button class="btn btn-sm" data-action="reset-progress" data-id="${r.file_id}">重置进度</button>
        ${r.deleted_at ? '' : `<button class="btn btn-sm btn-danger" data-action="delete-local-file"
          data-id="${r.file_id}" title="把这一集的本地文件移入回收站">删除本地文件</button>`}
      </div></div></div>`).join('') : empty('还没有已看完的条目', '进度达到设置页的阈值后会自动出现在这里')}</div></div>`);
}

async function renderHistory() {
  const list = await call('get_history', 200);
  setContent(`<div class="card"><h3>历史记录</h3>
    <p class="hint">共 ${list.length} 次观看会话，按时间倒序。可人工删除单条记录；
      <b>软件不会自动删除任何观看记录</b>，删除一条会话也不会影响播放进度、本地文件或看过的列表。</p>
    <div class="list">${list.length ? list.map(r => `<div class="item">
      <div class="thumb">⌚</div>
      <div class="body"><div class="name">${esc(r.display_title || r.filename || '未知文件')}
        ${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}</div>
        <div class="meta">${esc(shortDate(r.started_at))} → ${esc(shortDate(r.ended_at))}
          ${r.path ? `<span class="mono" title="${esc(r.path)}"> · ${esc(r.filename || '')}</span>` : ''}</div></div>
      <div class="right">${fmtMs(r.from_ms)} → ${fmtMs(r.to_ms)}<br><span class="mono">最远 ${fmtMs(r.max_ms)}</span>
        <div class="btn-row" style="justify-content:flex-end;margin-top:6px">
          <button class="btn btn-sm btn-danger" data-action="delete-history" data-id="${num(r.id)}"
            data-name="${esc(r.display_title || r.filename || '未知文件')}">删除</button>
        </div></div>
    </div>`).join('') : empty('还没有历史记录', '播放任意视频后会自动出现在这里')}</div></div>`);
}

// ---- 全量文件列表：「扫描结果」与「媒体库」共用 ---------------------------
const fileListState = {
  keyword: '', page: 1, page_size: 200,
  directory_id: 0, match_state: '', sort: 'newest',
  view_mode: 'flat', mode_touched: false,
};

// 树状视图：只缓存当前分组结果 + 已展开节点，展开/折叠不重复查库
const treeState = { data: null, open: new Set() };

function fileViewModeHandler(value) {
  const mode = ['flat', 'series', 'folder'].includes(value) ? value : 'flat';
  if (mode !== fileListState.view_mode) treeState.open.clear();
  fileListState.view_mode = mode;
  fileListState.mode_touched = true;
}

function option(value, label, current) {
  return `<option value="${esc(value)}" ${String(value) === String(current) ? 'selected' : ''}>${esc(label)}</option>`;
}

function fileListCard(title, hint, directories) {
  const s = fileListState;
  // 旧状态里可能残留「目录树」视图，统一归一到剧集展示，避免刷新后仍是目录层级
  if (s.view_mode === 'folder') s.view_mode = 'series';
  const dirs = ['<option value="0">全部目录</option>']
    .concat((directories || []).map(d => option(d.id, `${d.path}（${num(d.files)}）`, s.directory_id)));
  return `<div class="card"><div class="section-head">
      <h3>${esc(title)}</h3><span class="badge blue" id="fileListCount">正在统计…</span></div>
    <p class="hint">${hint}</p>
    <div class="file-toolbar">
      <input class="input" id="fileSearch" placeholder="搜索文件名 / 中文名 / 原名 / 路径…" value="${esc(s.keyword)}">
      <select class="input" id="fileDir">${dirs.join('')}</select>
      <select class="input" id="fileMatch">
        ${option('', '全部状态', s.match_state)}${option('matched', '已匹配中文', s.match_state)}
        ${option('unmatched', '未匹配中文', s.match_state)}${option('progress', '有播放进度', s.match_state)}
      </select>
      <select class="input" id="fileSort">
        ${option('newest', '最近扫描', s.sort)}${option('name', '文件名', s.sort)}
        ${option('cn', '中文名', s.sort)}${option('size', '文件体积', s.sort)}
        ${option('progress', '观看进度', s.sort)}
      </select>
      <select class="input" id="filePageSize">
        ${option(100, '每页 100 条', s.page_size)}${option(200, '每页 200 条', s.page_size)}
        ${option(500, '每页 500 条', s.page_size)}${option(0, '一次显示全部', s.page_size)}
      </select>
    </div>
    <div class="tree-toolbar">
      <span class="tree-toolbar-label">展示方式</span>
      <select class="input" id="fileViewMode">
        ${option('series', '剧集展示（作品 → 季 → 集）', s.view_mode)}
        ${option('flat', '平铺列表', s.view_mode)}
      </select>
      <span class="hint" id="treeSummary">按剧集分组，展开季即可看到每一集</span>
      <button class="btn btn-sm" data-action="tree-expand-all">展开全部分组</button>
      <button class="btn btn-sm" data-action="tree-collapse-all">全部折叠</button>
    </div>
    <div class="btn-row" style="margin-bottom:10px">
      <button class="btn btn-sm" data-action="file-refresh">刷新列表</button>
      <button class="btn btn-sm" data-action="goto" data-view="scan">管理扫描目录</button>
    </div>
    <div id="fileListPager" class="pager-row"></div>
    <div id="fileListBox" class="list">${empty('正在加载文件列表', '默认每页 200 条；切换「一次显示全部」可查看全部文件')}</div>
  </div>`;
}

function fileListRows(rows) {
  if (!rows.length) return empty('没有符合条件的文件', '换个关键词，或把筛选条件改成「全部」');
  return rows.map(r => {
    const [label, tone] = r.match_by ? (MATCH_LABEL[r.match_by] || ['—', '']) : ['未匹配', 'amber'];
    const pct = num(r.percent);
    const ep = r.episode_no ? esc(` · 第 ${r.episode_no} 集`) : '';
    return `<div class="item file-row">
      <button class="thumb thumb-btn" data-action="play" data-id="${r.id}" title="用 PotPlayer 打开">▶</button>
      <div class="body"><div class="name">
        <button class="link-btn" data-action="play" data-id="${r.id}" title="点击直接播放">${esc(r.display_title || r.filename)}</button>
        ${ep}${r.title_cn ? '<span class="badge green">已匹配中文</span>' : ''}
        <span class="badge ${tone}">${esc(label)}</span>
        ${num(r.completed) === 1 ? '<span class="badge green">已看完</span>' : ''}
        ${r.deleted_at ? '<span class="badge amber">本地文件已移除</span>' : ''}
      </div>
      <div class="meta">${esc(r.filename)}</div>
      <div class="mono">${esc(r.path)}</div></div>
      <div class="right">${pct ? `${pct.toFixed(0)}%<br>` : ''}
        <div class="btn-row" style="justify-content:flex-end;margin-top:6px">
          <button class="btn btn-primary btn-sm" data-action="play" data-id="${r.id}">播放</button>
          <button class="btn btn-sm" data-action="play-start" data-id="${r.id}">从头播放</button>
          ${num(r.completed) === 1 && !r.deleted_at
            ? `<button class="btn btn-sm btn-danger" data-action="delete-local-file" data-id="${r.id}"
                title="把这一集的本地文件移入回收站，观看记录保留">删除文件</button>` : ''}
        </div></div>
    </div>`;
  }).join('');
}

function fileListPagerHtml(data) {
  if (data.page_size <= 0) {
    return `<span>已一次显示全部 <b>${num(data.total)}</b> 个文件</span>
      <button class="btn btn-sm" data-action="file-paged">回到分页</button>`;
  }
  const prev = num(data.page) > 1, next = num(data.page) < num(data.pages);
  return `<button class="btn btn-sm" data-action="file-prev" ${prev ? '' : 'disabled'}>上一页</button>
    <span>第 <b>${num(data.page)}</b> / ${num(data.pages)} 页 · 显示 ${num(data.from)}–${num(data.to)} / ${num(data.total)} 条</span>
    <button class="btn btn-sm" data-action="file-next" ${next ? '' : 'disabled'}>下一页</button>
    <input class="input pager-input" id="filePageJump" type="number" min="1" max="${num(data.pages)}" value="${num(data.page)}">
    <button class="btn btn-sm" data-action="file-jump">跳转</button>
    <button class="btn btn-sm" data-action="file-all">显示全部 ${num(data.total_all)} 个</button>`;
}

function treeNodeHtml(node, depth) {
  const open = treeState.open.has(node.id);
  const icon = node.kind === 'series' ? '▣' : node.kind === 'season' ? '▤' : '▱';
  const badges = `${num(node.watched_count) ? `<span class="badge green">已看 ${num(node.watched_count)}</span>` : ''}
    ${num(node.playing_count) ? `<span class="badge amber">在看 ${num(node.playing_count)}</span>` : ''}`;
  const play = node.next_file_id
    ? `<button class="btn btn-sm" data-action="play" data-id="${num(node.next_file_id)}">${num(node.playing_count) ? '继续观看' : '播放下一集'}</button>`
    : '';
  // 整部剧全部看完后才出现人工清理入口；已移除的文件不算，避免重复点
  const cleanup = node.kind === 'series' && node.all_done && num(node.removable_count) > 0
    ? `<button class="btn btn-sm btn-danger" data-action="delete-series-files"
        data-node="${esc(node.id)}" title="把这部作品的全部本地文件移入回收站，观看记录保留">删除整部本地文件</button>`
    : '';
  const children = open
    ? `<div class="tree-children">
        ${node.children.map(c => treeNodeHtml(c, depth + 1)).join('')}
        ${node.files.length ? `<div class="tree-leaf">${fileListRows(node.files)}</div>` : ''}
      </div>` : '';
  return `<div class="tree-node tree-node-${esc(node.kind)}" data-depth="${num(depth)}">
    <div class="tree-head">
      <button class="tree-caret" data-action="tree-toggle" data-node="${esc(node.id)}"
        title="展开 / 折叠">${open ? '▾' : '▸'}</button>
      <span class="tree-icon">${icon}</span>
      <span class="tree-label">${esc(node.label)}</span>
      <span class="tree-sub">${esc(node.sub || '')}</span>
      ${badges}
      ${node.kind === 'series' && node.all_done && num(node.removable_count) === 0
        ? '<span class="badge amber">本地文件已移除</span>' : ''}
      <span class="tree-actions">${play}${cleanup}</span>
    </div>${children}</div>`;
}

function treeHtml(data) {
  if (!data.groups || !data.groups.length) {
    return empty('没有符合条件的文件', '换个关键词，或把筛选条件改成「全部」');
  }
  return `<div class="tree-root">${data.groups.map(g => treeNodeHtml(g, 0)).join('')}</div>`;
}

function treePagerHtml(data) {
  const label = '剧集展示（作品 → 季 → 集）';
  return `<span>${esc(label)} · 当前筛选 <b>${num(data.total)}</b> 个文件 ·
      <b>${num(data.group_count)}</b> 个分组（不受每页条数限制，节点按需展开）</span>
    <button class="btn btn-sm" data-action="file-flat">切回平铺列表</button>`;
}

// 搜索时自动把命中的分组展开；结果很多时只展开第一层，避免一次渲染上千行
function autoExpandHits(data) {
  if (!fileListState.keyword) return;
  const full = num(data.total) <= 120;
  const walk = (node, depth) => {
    if (depth === 0 || full) treeState.open.add(node.id);
    if (full) node.children.forEach(c => walk(c, depth + 1));
  };
  data.groups.forEach(g => walk(g, 0));
}

function renderTree() {
  const box = document.getElementById('fileListBox');
  if (box && treeState.data) box.innerHTML = treeHtml(treeState.data);
}

async function loadFileList() {
  const box = document.getElementById('fileListBox');
  if (!box) return;
  const mode = fileListState.view_mode;
  let data;
  try {
    data = await call(mode === 'flat' ? 'get_media_files_page' : 'get_media_tree',
      { ...fileListState, mode });
  } catch (err) {
    box.innerHTML = empty('列表加载失败', String(err && err.message || err));
    return;
  }
  const count = document.getElementById('fileListCount');
  if (count) count.textContent =
    `库内 ${num(data.total_all)} 个文件 · 已匹配中文 ${num(data.matched)} · 当前筛选 ${num(data.total)} 个`;
  const pager = document.getElementById('fileListPager');
  const summary = document.getElementById('treeSummary');
  if (mode === 'flat') {
    treeState.data = null;
    if (pager) pager.innerHTML = fileListPagerHtml(data);
    if (summary) summary.textContent = '当前是平铺列表，可切换成剧集展示';
    box.innerHTML = fileListRows(data.rows || []);
  } else {
    treeState.data = data;
    if (pager) pager.innerHTML = treePagerHtml(data);
    if (summary) summary.textContent = '同一部作品归为一组，按第 1 季 / 第 2 季… 展开到每一集';
    autoExpandHits(data);
    box.innerHTML = treeHtml(data);
  }
}

async function renderLibrary() {
  if (!fileListState.mode_touched) fileListState.view_mode = 'series';
  const dirs = await call('get_directories');
  setContent(fileListCard(
    '媒体库 · 按作品分季浏览',
    '默认按「剧集展示」分组：第一季、第二季、第三季各自成组，十几集的动漫展开季就能看到每一集。也可以切换回原来的平铺列表；搜索、筛选与点击播放两种视图都可用。',
    dirs,
  ));
  await loadFileList();
  const input = document.getElementById('fileSearch');
  if (input) input.focus();
}

// 双击进入编辑后，Enter / 点保存 / 光标移开三条路径共用同一套保存逻辑
let watchedSaving = false;
async function saveWatchedEdit(rid) {
  if (watchedSaving) return;
  const titleEl = document.getElementById('watchedEditTitle');
  const cnEl = document.getElementById('watchedEditCn');
  if (!titleEl || !cnEl) return;
  watchedSaving = true;
  try {
    const r = await call('update_watched', Number(rid), titleEl.value, cnEl.value);
    if (!r.ok) { toast(r.error || '保存失败', false); return; }  // 保留编辑态供重试
    const i = watchedRows.findIndex(x => Number(x.id) === Number(rid));
    if (i >= 0) watchedRows[i] = r.row;
    watchedEditing = 0;
    renderWatchedRows();
    toast(r.learned ? '名称已更新，中文名已记住' : '名称已更新');
  } finally { watchedSaving = false; }
}

function watchedRowHtml(r) {
  const [label, tone] = MATCH_LABEL[r.match_by] || ['—', ''];
  if (watchedEditing === Number(r.id)) {
    return `<tr class="watched-edit">
      <td><input class="input" id="watchedEditTitle" value="${esc(r.title)}" autocomplete="off"></td>
      <td><input class="input" id="watchedEditCn" value="${esc(r.title_cn || '')}" placeholder="留空则清除中文名" autocomplete="off"></td>
      <td colspan="4" class="hint">保存后中文名会被记住，下次导入同名作品自动使用；导入原文保留，旧名字也能搜到。</td>
      <td><div class="watched-actions">
        <button class="btn btn-primary btn-sm" data-action="save-watched" data-id="${r.id}">保存</button>
        <button class="btn btn-sm" data-action="cancel-watched">取消</button>
      </div></td>
    </tr>`;
  }
  return `<tr>
    <td data-edit-watched="${r.id}" data-field="title" title="双击编辑">${esc(r.title)}</td>
    <td data-edit-watched="${r.id}" data-field="title_cn" title="双击编辑">${esc(r.title_cn || '—')}</td>
    <td><span class="badge ${tone}">${esc(label)}</span></td>
    <td>${r.season_no ? esc(r.season_no) : '—'}</td>
    <td>${r.episode_from ? esc(`${r.episode_from}–${r.episode_to || r.episode_from}`) : '—'}</td>
    <td>${esc(shortDate(r.imported_at))}</td>
    <td><div class="watched-actions">
      <button class="btn btn-sm" data-action="edit-watched" data-id="${r.id}">改名</button>
      <button class="btn btn-sm btn-danger" data-action="delete-watched" data-id="${r.id}">删除</button>
    </div></td>
  </tr>`;
}

// 与后端 importer.group_watched 同构：按中文名（回退作品名）折叠
function groupWatchedRows(rows) {
  const map = new Map();
  rows.forEach(r => {
    const key = String(r.title_cn || r.title || '').trim() || '(未命名)';
    if (!map.has(key)) map.set(key, { key, label: key, title: r.title, seasons: new Set(), rows: [] });
    const g = map.get(key);
    g.rows.push(r);
    if (r.season_no) g.seasons.add(Number(r.season_no));
  });
  return [...map.values()];
}

// 只重写 tbody：搜索框不重建，输入焦点与光标始终保留
function renderWatchedRows() {
  const box = document.getElementById('watchedListBody');
  if (!box) return;
  const kw = watchedKeyword.trim().toLowerCase();
  const rows = kw ? watchedRows.filter(r =>
    [r.title, r.title_cn, r.raw_text, r.note].some(v =>
      String(v == null ? '' : v).toLowerCase().includes(kw))) : watchedRows;
  const count = document.getElementById('watchedCount');
  const groups = groupWatchedRows(rows);
  if (count) count.textContent = (kw ? `匹配 ${rows.length} / 共 ${watchedRows.length} 条`
    : `共 ${watchedRows.length} 条`) + (watchedGrouped ? ` · ${groups.length} 部作品` : '');
  if (!rows.length) {
    box.innerHTML = `<tr><td colspan="7">${empty(kw ? '没有匹配的作品' : '还没有导入',
      kw ? '换个关键词，或点「清空搜索」' : '到「导入列表」粘贴你的动漫清单')}</td></tr>`;
    return;
  }
  if (!watchedGrouped) { box.innerHTML = rows.map(watchedRowHtml).join(''); return; }
  // 搜索时自动展开命中的分组，结果多时也不至于一屏全是组头
  const autoOpen = !!kw && groups.length <= 12;
  box.innerHTML = groups.map(g => {
    const open = autoOpen || watchedOpen.has(g.key) || g.rows.length === 1;
    const seasons = [...g.seasons].sort((a, b) => a - b);
    const summary = seasons.length ? `${seasons.length} 季（S${seasons.join(' / S')}）` : '未标注季';
    const head = `<tr class="watched-group-row"><td colspan="7">
      <button class="watched-group-toggle" data-action="watched-group-toggle" data-key="${esc(g.key)}">
        <span class="tree-caret">${open ? '▾' : '▸'}</span>
        <b>${esc(g.label)}</b><span class="watched-group-meta">${g.rows.length} 条 · ${esc(summary)}</span>
      </button></td></tr>`;
    return head + (open ? g.rows.map(watchedRowHtml).join('') : '');
  }).join('');
}

async function renderWatched() {
  watchedRows = await call('get_watched_list', 0);
  watchedEditing = 0;
  setContent(`<div class="card watched-card"><div class="section-head"><h3>看过的列表</h3>
      <span class="badge blue" id="watchedCount">正在统计…</span></div>
    <p class="hint">你手动导入的已观看作品。中文名在导入那一刻已自动匹配；<b>双击作品名或中文名单元格即可直接编辑，光标移开自动保存</b>，导入原文始终保留。</p>
    <div class="watched-toolbar">
      <input class="input" id="watchedSearch" placeholder="搜索作品名 / 中文名 / 导入原文 / 备注…" value="${esc(watchedKeyword)}" autocomplete="off">
      <button class="btn btn-sm" data-action="watched-view-mode">${watchedGrouped ? '切换为平铺' : '按作品分组'}</button>
      <button class="btn btn-sm" data-action="sync-watched-history" title="把播放记录里已看完的作品回补到本列表">从播放记录同步</button>
      <button class="btn btn-sm" data-action="clean-watched" title="修正旧版导入残留的脏标题与「- 完」垃圾行">清洗脏数据</button>
      <button class="btn btn-sm" data-action="watched-clear-search">清空搜索</button>
    </div>
    <div class="table-wrap"><table><thead><tr><th>作品名</th><th>中文名</th><th>匹配方式</th><th>季</th><th>集数范围</th><th>导入时间</th><th></th></tr></thead>
    <tbody id="watchedListBody"></tbody></table></div></div>`);
  renderWatchedRows();
  const input = document.getElementById('watchedSearch');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}

async function renderImport() {
  const dict = await call('dictionary_status');
  setContent(`<div class="card"><h3>导入看过的动漫列表</h3>
    <p class="hint">每行一个作品，支持 <b>作品名</b>、<b>作品名 S2 01-12</b>、<b>作品名 第1季 1-25集</b>，
    也支持 CSV「作品名,季,起始集,结束集」。以 # 开头的行会被忽略。</p>
    <div class="notice">点「解析预览」时就会自动匹配中文名：先查随程序自带的离线词典（${num(dict.builtin_terms)} 部作品）与本地已学对照（${num(dict.learned)} 条），
    未命中的外文名再尝试在线检索。结果当场写入数据库，没有定时任务；预览里的中文名可以直接改，改动会被记住。</div>
    <div class="field"><textarea class="input" id="importText" placeholder="进击的巨人&#10;更衣人偶坠入爱河 S2 01-12&#10;葬送的芙莉莲,1,1,28&#10;Sono Bisque Doll wa Koi wo Suru&#10;Attack on Titan 第1季 1-25"></textarea></div>
    <div class="btn-row" style="margin-bottom:16px">
      <button class="btn btn-primary" data-action="preview">解析预览并匹配中文名</button>
      <button class="btn" data-action="commit">确认导入</button>
      <button class="btn" data-action="goto" data-view="watched">查看已导入</button>
    </div>
    <div id="previewBox">${empty('等待输入', '粘贴清单后点击「解析预览并匹配中文名」')}</div></div>`);
}

function renderPreview(data) {
  lastPreviewRows = data.rows || [];
  const m = data.match || {};
  const onlineNote = m.online_available
    ? '在线检索已启用（连通性在本次导入时实测）'
    : '在线检索已降级为离线词典；可在设置里配置代理后点「重新匹配」';
  const groupCount = (data.groups || []).length;
  document.getElementById('previewBox').innerHTML = `<div class="notice">
    解析出 <b>${num(groupCount || data.total)}</b> 部作品 / ${data.total} 条记录（多季作品会拆成「作品 + 季」）·
    重复 ${data.duplicates} 条（导入时自动跳过）·
    自动匹配 ${num(m.matched)} 条（离线词典 ${num(m.by_builtin)} / 在线 ${num(m.by_online)}）·
    未匹配 ${data.unmatched_cn} 条<br><span class="hint">${esc(onlineNote)}；中文名列可直接编辑，改动会记入词典。
    已自动忽略「（完）」「剧场版说明」「括号集数」等标记。</span></div>
    <div class="table-wrap"><table><thead><tr><th>作品名</th><th>中文名（可编辑）</th><th>季</th><th>集数</th><th>匹配状态</th></tr></thead><tbody>
    ${lastPreviewRows.length ? lastPreviewRows.map((r, i) => {
      const [label, tone] = r.duplicate ? ['已存在', 'amber'] : (MATCH_LABEL[r.match_by] || ['—', '']);
      return `<tr>
      <td>${esc(r.title)}</td>
      <td><input class="input cn-edit" data-idx="${i}" value="${esc(r.title_cn)}"
        style="padding:6px 9px;font-size:12px;min-width:180px"></td>
      <td>${r.season_no || '—'}</td>
      <td>${r.episode_from ? esc(`${r.episode_from}–${r.episode_to}`) : '—'}</td>
      <td><span class="badge ${tone}">${esc(label)}</span></td>
    </tr>`;
    }).join('') : `<tr><td colspan="5">没有解析出有效条目</td></tr>`}</tbody></table></div>`;
}

async function renderScan() {
  if (!fileListState.mode_touched) fileListState.view_mode = 'flat';
  const [dirs, dict, last] = await Promise.all([
    call('get_directories'), call('dictionary_status'), call('get_last_scan'),
  ]);
  setContent(`<div class="card"><h3>添加视频目录</h3>
    <p class="hint">添加后会自动递归扫描常见的视频格式（mkv / mp4 / avi / ts 等），跳过样片、特典与字幕文件，
    并在这次扫描里就把外文文件名匹配成中文名。</p>
    <div class="field"><label>目录绝对路径</label>
      <div style="display:flex;gap:9px"><input class="input" id="dirPath" placeholder="例如 D:\\Anime\\已下载">
        <button class="btn" data-action="choose-dir">浏览</button>
        <button class="btn btn-primary" data-action="add-dir">添加并扫描</button></div>
      <div class="tip">路径含空格也可以，直接粘贴即可。</div></div>
    ${last ? `<div class="notice">上次扫描：新增 ${num(last.added)} 个文件，更新 ${num(last.updated)} 个，
      自动匹配中文名 ${num(last.matched_cn)} / ${num(last.match_total)} 条。</div>` : ''}
    <div class="list">${dirs.length ? dirs.map(d => `<div class="item">
      <div class="thumb">▤</div><div class="body"><div class="name">${esc(d.path)}</div>
        <div class="meta">${num(d.files)} 个文件 · 最近扫描 ${esc(shortDate(d.last_scan_at))}
          ${num(d.enabled) ? '<span class="badge green">已启用</span>' : '<span class="badge">已停用</span>'}</div></div>
      <div class="right"><div class="btn-row" style="justify-content:flex-end">
        <button class="btn btn-sm" data-action="toggle-dir" data-id="${d.id}" data-enabled="${num(d.enabled)}">${num(d.enabled) ? '停用' : '启用'}</button>
        <button class="btn btn-sm btn-danger" data-action="remove-dir" data-id="${d.id}">移除</button>
      </div></div></div>`).join('') : empty('还没有添加目录', '添加一个已下载视频所在的文件夹')}</div>
    <div class="btn-row" style="margin-top:14px"><button class="btn" data-action="scan">重新扫描全部</button></div>
  </div>
  <div class="card"><h3>外文名 → 中文名</h3>
    <p class="hint">匹配只在导入列表、扫描目录或点「重新匹配」时执行，<b>没有每日定时任务、没有待补全队列</b>。
    优先命中离线词典，未命中的外文名会尝试在线检索；本机连不上时自动降级，不阻塞扫描和播放记录。</p>
    <div class="notice">词典共 <b>${num(dict.alias_total)}</b> 条对照（内置 ${num(dict.builtin)} 条 / 已学 ${num(dict.learned)} 条）·
      尚未匹配 <b>${num(dict.unmatched)}</b> 条 ·
      在线检索${dict.online_available ? '<span class="badge green">已启用</span>' : '<span class="badge amber">已降级</span>'}
      ${dict.online_error ? `<br><span class="mono" style="font-size:11px">${esc(dict.online_error)}</span>` : ''}</div>
    <div class="btn-row">
      <button class="btn btn-primary" data-action="rematch">重新匹配未识别标题</button>
      <button class="btn" data-action="import-alias-file">导入手动补充词典</button>
      <button class="btn" data-action="open-data">打开数据目录</button>
    </div></div>
  ${fileListCard('扫描结果 · 全部文件',
    '本次及历次扫描到的视频都会列在这里。默认平铺，可搜索、按目录或匹配状态筛选、翻页或一次显示全部；也可以切换成「剧集展示（作品 → 季 → 集）」。点文件名即可调用 PotPlayer 播放。',
    dirs)}`);
  await loadFileList();
}

function switchHtml(key, on, label, desc) {
  return `<div class="switch-row"><div><div>${esc(label)}</div><div class="desc">${esc(desc)}</div></div>
    <button class="switch ${on ? 'on' : ''}" data-setting="${key}" data-value="${on ? 1 : 0}"></button></div>`;
}

function isOn(v) { return v === true || v === 1 || v === '1' || v === 'true' || v === 'on'; }

async function renderSettings() {
  const s = await call('get_settings');
  const d = s.dictionary || {};
  const o = s.obsidian || {};
  setContent(`<div class="grid-2">
    <div class="card"><h3>播放器与记录</h3>
      <div class="field"><label>PotPlayer 可执行文件路径</label>
        <input class="input" id="set-potplayer_path" value="${esc(s.potplayer_path)}">
        <div class="tip">${s.potplayer_exists ? '已找到该文件' : '⚠ 文件不存在，请修正路径'}</div></div>
      ${switchHtml('embed_player', isOn(s.embed_player), '在软件内播放 PotPlayer',
        '播放器窗口直接嵌入本软件；嵌入失败会自动切回独立窗口，播放和历史记录都不中断')}
      <div class="field"><label>播放状态轮询间隔（秒）</label>
        <input class="input" type="number" min="1" max="30" id="set-poll_interval_sec" value="${esc(s.poll_interval_sec)}"></div>
      <div class="field"><label>自动标记「已看完」的进度阈值（%）</label>
        <input class="input" type="number" min="50" max="100" id="set-complete_percent" value="${esc(s.complete_percent)}"></div>
      ${switchHtml('daemon_auto_start', isOn(s.daemon_auto_start), '启动软件时自动开启后台记录',
        '默认开启：打开本软件就自动拉起托盘守护并续记播放进度；关闭后不再自动启动，可在设置里随时改回')}
      ${switchHtml('recording_enabled', isOn(s.recording_enabled), '启用播放记录', '关闭后守护进程不再写入历史')}
      ${switchHtml('autostart', isOn(s.autostart_enabled), '开机自动启动后台记录', '写当前用户注册表 Run 项，无需管理员权限')}
    </div>
    <div class="card"><h3>播放控制键位</h3>
      <div class="notice">PotPlayer 不提供公开的运行时控制接口，播放/暂停、停止、倍速、全屏是把<b>快捷键投递到播放器窗口</b>实现的。
      不同版本默认键位可能不同，可在 PotPlayer 里按 F5 → 快捷键核对后在此修改。填的是键名（如 <span class="mono">SPACE</span> / <span class="mono">C</span> / <span class="mono">ENTER</span>），无效值会自动回退默认。</div>
      <div class="grid-2">
        <div class="field"><label>播放 / 暂停</label><input class="input" id="set-pot_key_play_pause" value="${esc(s.pot_key_play_pause || 'SPACE')}"></div>
        <div class="field"><label>停止</label><input class="input" id="set-pot_key_stop" value="${esc(s.pot_key_stop || 'S')}"></div>
        <div class="field"><label>加速</label><input class="input" id="set-pot_key_faster" value="${esc(s.pot_key_faster || 'C')}"></div>
        <div class="field"><label>减速</label><input class="input" id="set-pot_key_slower" value="${esc(s.pot_key_slower || 'X')}"></div>
        <div class="field"><label>恢复正常倍速</label><input class="input" id="set-pot_key_reset_speed" value="${esc(s.pot_key_reset_speed || 'Z')}"></div>
        <div class="field"><label>全屏（独立窗口时）</label><input class="input" id="set-pot_key_fullscreen" value="${esc(s.pot_key_fullscreen || 'ENTER')}"></div>
      </div>
      <div class="field"><label>倍速快捷键每次步进</label>
        <select class="input" id="set-pot_speed_step">
          <option value="0.1" ${String(s.pot_speed_step || '0.1') === '0.1' ? 'selected' : ''}>0.1×（默认，1.25× 会就近到 1.2 或 1.3）</option>
          <option value="0.25" ${String(s.pot_speed_step) === '0.25' ? 'selected' : ''}>0.25×（推荐，1.25 / 1.5 / 2.0 全部精确）</option>
          <option value="0.5" ${String(s.pot_speed_step) === '0.5' ? 'selected' : ''}>0.5×</option>
        </select>
        <div class="tip">倍速下拉每次先按「恢复正常倍速」归 1.0，再按 N 次「加速」，结果与当前倍速无关。步进 0.1 时 1.25× 无法精确到达，界面会提示实际值。</div></div>
    </div>
    <div class="card"><h3>中文名自动匹配</h3>
      <div class="notice">只在<b>导入列表 / 扫描目录 / 手动点「重新匹配」</b>时执行，没有定时任务。
      顺序：内置离线词典 → 本地已学对照 → 在线 Bangumi（未命中且允许联网时才查）。结果当场入库并展示。
      导入预览里改过的中文名会被永久记住，下次自动命中。</div>
      <p class="hint" style="margin:0 0 12px">当前词典 ${num(d.alias_total)} 条 · 未匹配 ${num(d.unmatched)} 条 ·
        在线检索${d.online_available ? '已启用' : '已禁用或已降级（可在下方配置代理）'}</p>
      <div class="field"><label>元数据来源策略</label>
        <select class="input" id="set-metadata_source">
          <option value="cache_first" ${s.metadata_source === 'cache_first' ? 'selected' : ''}>离线词典优先，未命中再联网（推荐）</option>
          <option value="online_first" ${s.metadata_source === 'online_first' ? 'selected' : ''}>导入时总是尝试联网校准</option>
          <option value="local_only" ${s.metadata_source === 'local_only' ? 'selected' : ''}>仅使用离线词典，完全不联网</option>
        </select></div>
      <div class="field"><label>HTTP 代理（可选）</label>
        <input class="input" id="set-http_proxy" placeholder="例如 http://127.0.0.1:7890" value="${esc(s.http_proxy)}">
        <div class="tip">本机直连境外元数据接口超时；填代理后保存设置，下次导入即可实时补全长尾作品。</div></div>
      <div class="field"><label>Bangumi API 地址</label>
        <input class="input" id="set-bangumi_base" value="${esc(s.bangumi_base)}"></div>
      <div class="field"><label>主窗口尺寸</label>
        <div style="display:flex;gap:9px">
          <input class="input" type="number" id="set-window_width" value="${esc(s.window_width)}">
          <input class="input" type="number" id="set-window_height" value="${esc(s.window_height)}">
        </div><div class="tip">下次启动生效</div></div>
      <div class="field"><label>展示风格</label>
        <div class="tip">点右上角「深色 / 浅色」即可切换黑白两套主题，设置会保存到本机数据库。</div></div>
    </div>
    <div class="card"><h3>全盘快搜（Everything）</h3>
      <p class="hint">默认优先用 es.exe，其次 Everything 的 HTTP 服务；两者都没启用时自动降级为媒体库检索，功能始终可用。</p>
      <div class="field"><label>es.exe 路径（可选）</label>
        <input class="input" id="set-everything_es_path" value="${esc(s.everything_es_path || '')}" placeholder="D:\\tool\\Everything\\es.exe">
        <div class="tip">本机 D:\\tool\\Everything 目前只有 Everything.exe。要使用全盘检索，可从 Everything 官网下载 es.exe 放到该目录。</div></div>
      <div class="field"><label>Everything HTTP 服务地址（可选）</label>
        <input class="input" id="set-everything_http_url" value="${esc(s.everything_http_url || '')}" placeholder="例如 http://127.0.0.1:8080">
        <div class="tip">需在 Everything 选项里启用 HTTP 服务并设定端口。</div></div>
      <div class="btn-row"><button class="btn btn-sm" data-action="goto" data-view="quicksearch">去全盘快搜</button></div>
    </div></div>
    <div class="card"><h3>Obsidian 知识沉淀</h3>
      <div class="notice">把<b>开发流程、自测结果、优化路线图、功能缺口</b>自动写进 vault 下独立的
        <span class="mono">${esc(o.folder || 'Magic Video')}</span> 文件夹。只读你已有的笔记、只写这个文件夹；
        每篇笔记的「我的批注」区在同步时<b>原样保留</b>，可以直接在 Obsidian 里记想法。</div>
      <p class="hint" style="margin:0 0 12px">状态：${o.resolvable ? '<span class="badge green">可同步</span>' : '<span class="badge amber">需指定 vault</span>'}
        · 已生成 ${num(o.note_count)} 篇笔记 · 上次同步 ${esc(o.last_sync || '尚未同步')}
        ${o.last_test ? ` · 最近测试 <b>${num(o.last_test.total)}</b> 个用例${o.last_test.success ? '（全绿）' : '（未全绿）'}` : ''}</p>
      <div class="grid-2">
        <div class="field"><label>Obsidian vault 路径</label>
          <input class="input" id="set-obsidian_vault" value="${esc(o.configured_vault || o.auto_detected_vault || '')}" placeholder="例如 C:\\Users\\...\\Obsidian Vault">
          <div class="tip">检测到 ${num((o.vaults || []).length)} 个 vault；多个时不会自动猜，请在此明确指定。
            ${(o.vaults || []).map(v => `<br>· ${esc(v.path)}${v.open ? '（当前打开）' : ''}${v.exists ? '' : '（不存在）'}`).join('')}</div></div>
        <div class="field"><label>笔记文件夹（vault 内相对路径）</label>
          <input class="input" id="set-obsidian_folder" value="${esc(o.folder || 'Magic Video')}">
          <div class="tip">只会写这个文件夹，不会碰你已有的任何笔记。</div></div>
      </div>
      ${switchHtml('obsidian_auto_sync', isOn(o.auto_sync), '跑完单元测试自动同步', '命令行执行 cli.py test 通过后自动写入一次结果')}
    </div>
    <div class="card"><div class="btn-row">
      <button class="btn btn-primary" data-action="save-settings">保存设置</button>
      <button class="btn" data-action="rematch">重新匹配未识别标题</button>
      <button class="btn" data-action="obsidian-sync">同步到 Obsidian</button>
      <button class="btn" data-action="obsidian-open">打开笔记目录</button>
      <button class="btn" data-action="open-data">打开数据目录</button>
    </div><p class="hint" style="margin:14px 0 0">数据库：<span class="mono">${esc(s.db_path || '')}</span> · 所有数据仅保存在本机，不会上传。</p></div>`);
}

// ---------- 剧集墙：卡片式展示（参考 SeedHub / AGE动漫 的作品墙） ----------
let posterState = { keyword: '', cards: [], covers: {}, status: {}, loading: false };
let esState = { keyword: '', rows: [], source: '', hint: '' };
let procPollTimer = null;

// 追剧列表：一次载入全量后本地过滤；open 记住哪些行展开了本地文件
let watchlistState = { items: [], keyword: '', open: new Set(), loading: false,
  downloaded: 0, pending: 0, matched: 0 };
let procState = { daemon: {}, covers: {}, cover_dir: '' };

// 封面抓取进度只属于「后台进程」页：停留在该页时才轮询，离开立刻停，不是常驻定时任务
function procPollStart() {
  if (procPollTimer) return;
  procPollTimer = setInterval(async () => {
    if (currentView !== 'processes') {
      clearInterval(procPollTimer); procPollTimer = null;
      return;
    }
    await loadProcessStatus();
  }, 2000);
}

function posterCardHtml(card, cover) {
  const art = cover
    ? `<img class="poster-art" src="${cover}" alt="${esc(card.title)}" loading="lazy">`
    : `<div class="poster-art poster-placeholder"><span>${esc((card.title || '?').slice(0, 2))}</span></div>`;
  const seasons = card.season_count > 1 ? `<span class="poster-tag">${card.season_count} 季</span>` : '';
  const progress = card.playing_count ? `<span class="poster-tag amber">在看 ${num(card.playing_count)}</span>` : '';
  // 用户要求：剧集墙不放「播放下一集」；「查看分集」必须带着搜索词跳过去，
  // 否则切到媒体库后满屏文件，忘了自己是从哪部作品点进来的。
  const cleanup = card.all_done && num(card.removable_count) > 0
    ? `<button class="btn btn-sm btn-danger" data-action="delete-series-files"
        data-node="${esc(card.group_id)}" title="整部已看完：把本地文件移入回收站，观看记录保留">清理本地文件</button>`
    : '';
  const removedTag = card.all_done && num(card.removable_count) === 0
    ? '<span class="poster-tag amber">本地文件已移除</span>' : '';
  return `<div class="poster-card">${art}
    <div class="poster-body">
      <div class="poster-title" title="${esc(card.title)}">${esc(card.title)}</div>
      <div class="poster-meta">${num(card.file_count)} 集${card.watched_count ? ` · 已看 ${num(card.watched_count)}` : ''}</div>
      <div class="poster-tags">${seasons}${progress}${removedTag}</div>
      <div class="poster-actions">${cleanup}
        <button class="btn btn-sm" data-action="goto" data-view="library"
          data-keyword="${esc(card.search_keyword || card.title)}">查看分集</button></div>
    </div></div>`;
}

function renderPosterCards() {
  const box = document.getElementById('posterGrid');
  if (!box) return;
  const kw = posterState.keyword.trim().toLowerCase();
  const cards = kw ? posterState.cards.filter(c => c.title.toLowerCase().includes(kw)) : posterState.cards;
  const count = document.getElementById('posterCount');
  if (count) count.textContent = `${cards.length} 部作品 · 已有封面 ${Object.keys(posterState.covers).length} 张`;
  box.innerHTML = cards.length
    ? cards.map(c => posterCardHtml(c, posterState.covers[c.title])).join('')
    : empty('还没有作品卡片', '先在「扫描目录」添加视频目录；抓取封面后会显示海报');
}

function renderCoverStatus() {
  const el = document.getElementById('coverStatusText');
  if (!el) return;
  const bar = document.getElementById('coverProgress');
  const s = procState.covers || posterState.status || {};
  if (s.running) {
    el.textContent = `后台抓取中… ${num(s.done)} / ${num(s.total)}（成功 ${num(s.ok)} · 失败 ${num(s.failed)}）`
      + (s.current ? ` · 当前：${s.current}` : '');
    if (bar) {
      bar.style.display = '';
      bar.max = Math.max(1, num(s.total));
      bar.value = num(s.done);
    }
  } else {
    el.textContent = `已缓存 ${num(s.cached)} 张封面${s.finished_at ? ` · 上次任务成功 ${num(s.ok)} / ${num(s.total)}` : ''}`;
    if (bar) { bar.style.display = 'none'; bar.value = 0; }
  }
}

async function loadPosterWall() {
  if (posterState.loading) return;
  posterState.loading = true;
  try {
    const data = await call('get_poster_wall', { keyword: posterState.keyword });
    posterState.cards = data.cards || [];
    posterState.covers = data.covers || {};
    posterState.status = data.cover_status || {};
    // 有缓存的封面用 data URI 补齐（后端一次性返回，避免 file:// 跨域）
    const titles = Object.keys(posterState.covers);
    if (titles.length) posterState.covers = await call('cover_data_uris', titles);
    renderPosterCards();
    renderCoverStatus();
  } catch (err) {
    const box = document.getElementById('posterGrid');
    if (box) box.innerHTML = empty('剧集墙加载失败', String(err && err.message || err));
  } finally {
    posterState.loading = false;
  }
}

async function renderPosters() {
  setContent(`<div class="card"><div class="section-head"><h3>剧集墙</h3>
      <span class="badge blue" id="posterCount">正在统计…</span></div>
    <p class="hint">按作品展示海报、季数与分集入口。点「查看分集」会带着作品名跳到媒体库，
      不用再手动输一次。封面的后台抓取与进度已整合到左侧<b>「后台进程」</b>页。</p>
    <div class="watched-toolbar">
      <input class="input" id="posterSearch" placeholder="搜索作品名…" value="${esc(posterState.keyword)}" autocomplete="off">
      <button class="btn btn-sm" data-action="poster-refresh">刷新</button>
      <button class="btn btn-sm" data-action="goto" data-view="processes">去后台进程管理封面</button>
    </div>
    <div id="posterGrid" class="poster-grid">${empty('正在加载剧集墙', '读取媒体库分组…')}</div>
  </div>`);
  await loadPosterWall();
  const input = document.getElementById('posterSearch');
  if (input) input.focus();
}

// 全盘快搜是左侧独立入口，不再挂在剧集墙下面：搜索框、结果、通道状态都在自己这一页
async function renderQuickSearch() {
  setContent(`<div class="card"><div class="section-head"><h3>全盘快搜（Everything）</h3>
      <span class="badge blue" id="esSourceBadge">检测检索通道…</span></div>
    <p class="hint">按文件名关键词搜整台电脑的视频，结果可直接「播放 / 续播」。优先用 es.exe，其次 Everything 的 HTTP 服务；
      两者都没启用时<b>自动降级为媒体库检索</b>，功能始终可用。通道在「设置 → 全盘快搜」里配置。</p>
    <div class="watched-toolbar">
      <input class="input" id="esSearch" placeholder="输入文件名关键词，回车检索…" value="${esc(esState.keyword)}" autocomplete="off">
      <button class="btn btn-sm btn-primary" data-action="es-search">检索</button>
      <button class="btn btn-sm" data-action="goto" data-view="settings">检索设置</button>
      <span class="hint" id="esHint"></span>
    </div>
    <div id="esResults" class="list"></div>
  </div>`);
  renderEsResults();
  const input = document.getElementById('esSearch');
  if (input) input.focus();
  try {
    const cap = await call('everything_capabilities');
    const badge = document.getElementById('esSourceBadge');
    if (!badge) return;
    badge.textContent = cap.es_available ? 'es.exe · 全盘检索'
      : cap.http_configured ? 'Everything HTTP · 全盘检索'
        : '媒体库检索（降级）';
  } catch (e) { /* 能力探测失败不影响检索 */ }
}

function renderEsResults() {
  const box = document.getElementById('esResults');
  if (!box) return;
  const hint = document.getElementById('esHint');
  if (hint) hint.textContent = esState.hint ? `来源：${esState.source} · ${esState.hint}` : '';
  if (!esState.rows.length) {
    box.innerHTML = esState.keyword
      ? empty('没有结果', '换个关键词试试，或确认 Everything 通道是否已启用')
      : empty('输入关键词开始检索', '例如：进击的巨人 / Sono Bisque / .mkv');
    return;
  }
  box.innerHTML = esState.rows.slice(0, 200).map(r => `<div class="item">
    <div class="thumb">▶</div>
    <div class="body"><div class="name">${esc(r.filename)}</div>
      <div class="meta mono">${esc(r.path)}</div>
      <div class="meta">${r.in_library ? '已在媒体库' : '库外文件'}${r.title_cn ? ` · ${esc(r.title_cn)}` : ''}</div></div>
    <div class="right"><div class="btn-row">
      ${r.in_library
        ? `<button class="btn btn-primary btn-sm" data-action="play" data-id="${r.file_id}">续播</button>`
        : `<button class="btn btn-primary btn-sm" data-action="play-external" data-path="${esc(r.path)}">播放</button>`}
    </div></div></div>`).join('');
}

// ---- 追剧列表：两栏按书写顺序，支持排序、状态转换、本地文件匹配 ---------
function watchlistRowHtml(r, idx) {
  const open = watchlistState.open.has(r.id);
  const matched = num(r.match_count) > 0;
  const files = open && (r.files || []).length
    ? `<div class="wl-files">${r.files.map(f => `<div class="wl-file">
        <span class="mono" title="${esc(f.path)}">${esc(f.filename)}</span>
        ${f.completed ? '<span class="badge green">已看完</span>' : ''}
        <button class="btn btn-sm" data-action="play" data-id="${f.id}">播放</button>
      </div>`).join('')}${num(r.match_count) > (r.files || []).length
        ? `<div class="hint">还有 ${num(r.match_count) - (r.files || []).length} 个文件，点「媒体库搜索」看全部</div>` : ''}</div>`
    : '';
  return `<div class="wl-row">
    <span class="wl-idx">${idx}</span>
    <div class="wl-main">
      <div class="wl-title">${esc(r.title)}</div>
      <div class="wl-sub"><span class="wl-match ${matched ? '' : 'none'}">${matched ? `本地 ${num(r.match_count)} 个文件` : '未匹配到文件'}</span>
        ${r.search_keyword ? `<span class="mono"> · 搜索词 ${esc(r.search_keyword)}</span>` : ''}</div>
      ${files}
    </div>
    <div class="wl-ops">
      <button class="btn btn-sm" data-action="watchlist-up" data-id="${r.id}" title="上移">↑</button>
      <button class="btn btn-sm" data-action="watchlist-down" data-id="${r.id}" title="下移">↓</button>
      <button class="btn btn-sm" data-action="watchlist-toggle" data-id="${r.id}"
        data-to="${r.downloaded ? 0 : 1}">${r.downloaded ? '移回未下载' : '标记已下载'}</button>
      ${matched ? `<button class="btn btn-sm" data-action="watchlist-files" data-id="${r.id}">${open ? '收起' : '本地文件'}</button>
        <button class="btn btn-sm" data-action="goto" data-view="library"
          data-keyword="${esc(r.search_keyword || r.title)}">媒体库搜索</button>` : ''}
      <button class="btn btn-sm btn-danger" data-action="watchlist-remove" data-id="${r.id}" title="从追剧列表移除">×</button>
    </div>
  </div>`;
}

function renderWatchlistRows() {
  const kw = watchlistState.keyword.trim().toLowerCase();
  const items = watchlistState.items.filter(r => !kw || r.title.toLowerCase().includes(kw));
  const boxD = document.getElementById('wlDownloaded');
  const boxP = document.getElementById('wlPending');
  if (!boxD || !boxP) return;
  const d = items.filter(r => r.downloaded === 1);
  const p = items.filter(r => r.downloaded === 0);
  boxD.innerHTML = d.length ? d.map((r, i) => watchlistRowHtml(r, i + 1)).join('')
    : empty('已下载清单为空', '把条目从未下载转过来，或导入一份清单');
  boxP.innerHTML = p.length ? p.map((r, i) => watchlistRowHtml(r, i + 1)).join('')
    : empty('未下载清单为空', '点上方「导入清单」粘贴文本');
  const badge = document.getElementById('wlCount');
  if (badge) badge.textContent = `已下载 ${watchlistState.downloaded} · 未下载 ${watchlistState.pending} · 已匹配本地 ${watchlistState.matched}`;
}

async function loadWatchlist() {
  if (watchlistState.loading) return;
  watchlistState.loading = true;
  try {
    const data = await call('get_watchlist', watchlistState.keyword);
    watchlistState.items = data.items || [];
    watchlistState.downloaded = num(data.downloaded);
    watchlistState.pending = num(data.pending);
    watchlistState.matched = num(data.matched);
    renderWatchlistRows();
  } catch (err) {
    const box = document.getElementById('wlDownloaded');
    if (box) box.innerHTML = empty('追剧列表加载失败', String(err && err.message || err));
  } finally { watchlistState.loading = false; }
}

async function renderWatchlist() {
  setContent(`<div class="card"><div class="section-head"><h3>追剧列表</h3>
      <span class="badge blue" id="wlCount">正在统计…</span></div>
    <p class="hint">按你粘贴的书写顺序展示，↑ / ↓ 可调整；「标记已下载」只在两栏间移动，不删除、不改顺序。
      扫描到的本地剧集会显示匹配数，可就地展开点播，或带着搜索词跳到媒体库。</p>
    <div class="watched-toolbar">
      <input class="input" id="wlSearch" placeholder="搜索清单…" value="${esc(watchlistState.keyword)}" autocomplete="off">
      <input class="input" id="wlNewTitle" placeholder="新增一条作品名…" autocomplete="off">
      <button class="btn btn-sm" data-action="watchlist-add">新增到未下载</button>
      <button class="btn btn-sm btn-primary" data-action="watchlist-import-open">导入清单</button>
    </div>
    <div id="wlImportPanel" style="display:none;margin-top:10px">
      <p class="hint">粘贴含「已下载：」与「未下载：」两段的文本；先预览再入库，默认跳过已存在的条目。</p>
      <textarea class="input wl-import" id="wlImportText" placeholder="已下载：&#10;作品 A&#10;作品 B&#10;未下载：&#10;作品 C"></textarea>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn btn-sm" data-action="watchlist-preview">预览</button>
        <button class="btn btn-sm btn-primary" data-action="watchlist-import">确认入库（追加）</button>
        <button class="btn btn-sm btn-danger" data-action="watchlist-replace">清空并重建</button>
        <span class="hint" id="wlImportHint"></span>
      </div>
    </div>
    <div class="watchlist-cols" style="margin-top:14px">
      <div class="card"><div class="section-head"><h3>已下载</h3><span class="badge green">本地可播</span></div>
        <div class="wl-scroll list" id="wlDownloaded"></div></div>
      <div class="card"><div class="section-head"><h3>未下载</h3><span class="badge amber">待补</span></div>
        <div class="wl-scroll list" id="wlPending"></div></div>
    </div>
  </div>`);
  await loadWatchlist();
}

// ---- 后台进程管理：守护启停 + 封面缓存进度（抓取入口不再放剧集墙） -------
function renderProcessStatus() {
  const d = procState.daemon || {}, c = procState.covers || {};
  const state = document.getElementById('procDaemonState');
  if (state) state.innerHTML = d.running
    ? `<span class="dot"></span> 运行中 · PID ${esc(d.pid || '')}`
    : '<span class="dot gray"></span> 未启动';
  const auto = document.getElementById('procDaemonAuto');
  if (auto) auto.textContent = d.auto_start ? '开启（随软件自动启动）' : '关闭';
  const btn = document.getElementById('procDaemonToggle');
  if (btn) btn.textContent = d.auto_start ? '关闭自动启动' : '开启自动启动';
  renderCoverStatus();
  const bar = document.getElementById('coverProgress');
  if (bar) {
    bar.style.display = c.running ? '' : 'none';
    bar.max = Math.max(1, num(c.total)); bar.value = num(c.done);
  }
  const hits = document.getElementById('procCoverHits');
  if (hits) {
    const h = c.source_hits || {};
    hits.textContent = Object.keys(h).length
      ? Object.entries(h).map(([k, v]) => `${k} ${v} 张`).join(' · ')
      : '尚无新增缓存';
  }
  const dir = document.getElementById('procCoverDir');
  if (dir) dir.textContent = procState.cover_dir || '';
  const cur = document.getElementById('procCoverCurrent');
  if (cur) cur.textContent = c.running
    ? `当前：${c.current || '准备中…'}${c.last_query ? ` · 关键词「${c.last_query}」` : ''}${c.last_hit ? ` · 命中「${c.last_hit}」` : ''}`
    : (c.last_query ? `上次关键词「${c.last_query}」` : '');
  const fail = document.getElementById('procCoverFails');
  if (fail) {
    const rows = c.recent_failures || [];
    fail.innerHTML = rows.length
      ? rows.map(f => `<div>${esc(f.title)} — ${esc(f.error)}${f.query ? `（关键词 ${esc(f.query)}）` : ''}</div>`).join('')
      : '<div>暂无失败记录</div>';
  }
  const startBtn = document.getElementById('procCoverStart');
  if (startBtn) startBtn.disabled = !!c.running;
  const cancelBtn = document.getElementById('procCoverCancel');
  if (cancelBtn) cancelBtn.disabled = !c.running;
}

async function loadProcessStatus() {
  try {
    const st = await call('process_status');
    procState = { daemon: st.daemon || {}, covers: st.covers || {}, cover_dir: st.cover_dir || '' };
    renderProcessStatus();
  } catch (e) { /* 状态刷新失败保留上一次显示，不打断界面 */ }
}

async function renderProcesses() {
  setContent(`<div class="card"><div class="section-head"><h3>后台进程</h3>
      <span class="badge blue">仅停留本页时刷新</span></div>
    <p class="hint">在这里启停后台记录守护、查看剧集图片缓存任务。抓取只在点按钮时执行一次，
      <b>不是定时任务</b>；关闭本页后任务仍在后台继续，回来即可看到最新进度。</p>
    <div class="proc-grid">
      <div class="card"><div class="section-head"><h3>后台记录守护</h3></div>
        <div class="proc-line"><span class="label">运行状态</span><span class="value" id="procDaemonState">检测中…</span></div>
        <div class="proc-line"><span class="label">自动启动</span><span class="value" id="procDaemonAuto">—</span></div>
        <p class="proc-meta">负责轮询 PotPlayer 并写入观看历史与断点。关闭自动启动会立即停止守护；
          再次开启会立即补拉起，并同步左下角状态圆点。</p>
        <div class="btn-row">
          <button class="btn btn-sm btn-primary" data-action="proc-daemon-start">启动</button>
          <button class="btn btn-sm" data-action="proc-daemon-stop">停止</button>
          <button class="btn btn-sm" data-action="proc-daemon-auto" id="procDaemonToggle">切换自动启动</button>
        </div>
      </div>
      <div class="card"><div class="section-head"><h3>剧集图片缓存</h3></div>
        <div class="proc-line"><span class="label">任务状态</span><span class="value" id="coverStatusText">检测中…</span></div>
        <progress id="coverProgress" class="proc-bar" max="100" value="0" style="display:none"></progress>
        <div class="proc-meta">
          <div id="procCoverCurrent"></div>
          <div>本次各源命中：<span id="procCoverHits">—</span></div>
          <div>缓存目录：<span class="mono" id="procCoverDir">—</span></div>
        </div>
        <div class="btn-row" style="margin-top:10px">
          <button class="btn btn-sm btn-primary" data-action="cover-fetch" id="procCoverStart">开始抓取</button>
          <button class="btn btn-sm" data-action="cover-force">强制重抓</button>
          <button class="btn btn-sm" data-action="cover-cancel" id="procCoverCancel">停止</button>
          <button class="btn btn-sm" data-action="goto" data-view="posters">看剧集墙</button>
        </div>
        <p class="hint" style="margin-top:8px">失败原因（最近 8 条，含实际使用的搜索关键词）</p>
        <div class="proc-fail" id="procCoverFails"></div>
      </div>
    </div>
  </div>`);
  await loadProcessStatus();
  procPollStart();
}

const RENDERERS = {
  home: renderHome, continue: renderContinue, history: renderHistory, library: renderLibrary,
  posters: renderPosters, quicksearch: renderQuickSearch, watched: renderWatched,
  watchlist: renderWatchlist, processes: renderProcesses,
  import: renderImport, scan: renderScan, settings: renderSettings,
};

async function refresh() {
  if (!apiReady()) return;
  const [title, subtitle] = VIEW_META[currentView];
  document.getElementById('viewTitle').textContent = title;
  document.getElementById('viewSubtitle').textContent = subtitle;
  document.title = 'Magic Video · ' + title;
  // 看过的列表让卡片吃满可用高度、表格内部滚动；其它视图保持整页滚动
  document.getElementById('content').classList.toggle('content-fill', currentView === 'watched');
  try {
    await RENDERERS[currentView]();
  } catch (err) {
    setContent(`<div class="card">${empty('页面加载失败', String(err && err.message || err))}</div>`);
  }
}

function switchView(view, opts = {}) {
  if (!VIEW_META[view]) return;
  // 「查看分集 / 媒体库搜索」带搜索词过来：把作品名带进媒体库并自动展开命中分组，
  // 否则跳过去面对几千个文件就忘了自己要点哪部。
  if (view === 'library' && typeof opts.keyword === 'string' && opts.keyword.trim()) {
    fileListState.keyword = opts.keyword.trim();
    fileListState.page = 1;
    fileListState.view_mode = 'series';
    fileListState.mode_touched = true;
    treeState.open.clear();
  }
  currentView = view;
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  refresh();
}

async function updatePlayerChip() {
  if (!apiReady()) return;
  try {
    const st = await call('get_player_status');
    const chip = document.getElementById('playerChip');
    if (!st.available) {
      chip.innerHTML = '<span class="dot gray"></span><span>PotPlayer 未运行</span>';
      return;
    }
    const label = st.state === 'playing' ? '播放中' : st.state === 'paused' ? '已暂停' : '已停止';
    chip.innerHTML = `<span class="dot ${st.state === 'playing' ? '' : 'amber'}"></span>
      <span><b>${esc(st.title_cn || st.media_name)}</b> · ${label} · ${fmtMs(st.position_ms)} / ${fmtMs(st.duration_ms)}</span>`;
  } catch (e) { /* 静默 */ }
}

async function updateDaemonState() {
  try {
    const st = await call('daemon_running');
    const running = !!st.running;
    const dot = document.getElementById('daemonDot');
    const text = document.getElementById('daemonText');
    if (running) {
      dot.className = 'dot';
      text.textContent = '后台记录正在运行';
      daemonBootUntil = 0;                       // 已就绪，结束启动宽限期与短轮询
    } else if (!daemonAutoStart) {
      dot.className = 'dot gray';
      text.textContent = '后台记录已关闭 · 设置里可开启';
    } else if (Date.now() < daemonBootUntil) {
      // 守护刚被拉起时进程列表未必立即可见，此处不能草率判定「未启动」
      dot.className = 'dot amber';
      text.textContent = '后台记录启动中…';
    } else {
      dot.className = 'dot gray';
      text.textContent = '后台记录未启动 · 设置里可开启';
    }
    return running;
  } catch (e) {
    document.getElementById('daemonText').textContent = '后台记录状态未知';
    return false;
  }
}

// 启动后的短时间内用 1 秒间隔把「启动中」纠正为「正在运行」，就绪或超时即停。
function watchDaemonBoot() {
  const timer = setInterval(async () => {
    const running = await updateDaemonState();
    if (running || Date.now() >= daemonBootUntil) clearInterval(timer);
  }, 1000);
}

async function handleAction(action, btn) {
  const id = btn.dataset.id;
  switch (action) {
    case 'goto':
      if (playerStageActive) await stopEmbedded(true);
      switchView(btn.dataset.view, { keyword: btn.dataset.keyword });
      return;
    case 'toggle-embed': {
      const r = await call('set_embed_player', !embeddedMode);
      embeddedMode = !!r.enabled;
      refreshEmbedState();
      toast(embeddedMode ? '已开启软件内播放（失败会自动降级）' : '已改为独立窗口播放');
      return;
    }
    case 'embed-resync': {
      const r = await call('embed_sync', hostRect());
      toast(r.ok ? '画面位置已重排' : (r.error || '重排失败'), r.ok);
      return;
    }
    case 'embed-stop': await stopEmbedded(); return;
    case 'player-play-pause': await playerControl('play_pause'); return;
    case 'player-stop': await playerControl('stop'); return;
    case 'player-prev': await playerAdjacent('prev'); return;
    case 'player-next': await playerAdjacent('next'); return;
    case 'player-fullscreen': toggleStageZoom(); return;
    case 'toggle-theme': {
      const next = currentTheme === 'dark' ? 'light' : 'dark';
      applyTheme(next);                       // 先本地生效，不等后端往返
      try { await call('set_theme', next); } catch (e) { toast('主题已切换，但未能保存', false); }
      return;
    }
    case 'play': case 'play-start':
      await playSmart('id', id, action === 'play-start');
      return;
    case 'edit-watched': {
      watchedEditing = Number(id);
      renderWatchedRows();
      const el = document.getElementById('watchedEditTitle');
      if (el) { el.focus(); el.select(); }
      return;
    }
    case 'cancel-watched': watchedEditing = 0; renderWatchedRows(); return;
    case 'save-watched': await saveWatchedEdit(Number(id)); return;
    case 'sync-watched-history': {
      const r = await call('sync_watched_from_history');
      toast(`已回补 ${r.added} 条已看完作品 · 跳过 ${r.skipped} 条（扫描 ${r.scanned} 条播放记录）`, r.ok);
      await renderWatched();
      return;
    }
    case 'clean-watched': {
      const preview = await call('clean_watched_list', true);
      if (!preview.updated && !preview.deleted) { toast('没有需要清洗的脏数据'); return; }
      const r = await call('clean_watched_list', false);
      toast(`已清洗 ${r.updated} 条标题 · 删除 ${r.deleted} 条垃圾行`, r.ok);
      await renderWatched();
      return;
    }
    case 'watched-view-mode': {
      watchedGrouped = !watchedGrouped;
      await renderWatched();
      return;
    }
    case 'watched-group-toggle': {
      const key = btn.dataset.key;
      if (watchedOpen.has(key)) watchedOpen.delete(key); else watchedOpen.add(key);
      renderWatchedRows();
      return;
    }
    case 'watched-clear-search': {
      watchedKeyword = '';
      watchedEditing = 0;
      const el = document.getElementById('watchedSearch');
      if (el) { el.value = ''; el.focus(); }
      renderWatchedRows();
      return;
    }
    case 'delete-watched': {
      const rid = Number(id);
      await call('delete_watched', rid);
      if (!document.getElementById('watchedListBody')) { await refresh(); toast('已删除'); return; }
      watchedRows = watchedRows.filter(r => Number(r.id) !== rid);
      if (watchedEditing === rid) watchedEditing = 0;
      renderWatchedRows();
      toast('已删除');
      return;
    }
    case 'delete-history': {
      const sid = Number(id);
      const name = btn.dataset.name || '这条观看会话';
      const ok = await confirmDialog({
        title: '删除这一条观看记录',
        message: `将删除「${name}」的这一条观看会话。本地文件、播放进度、断点与看过的列表都会保留。`,
        note: '只删这一条会话记录，删除后不可恢复；软件不会自动删除任何观看记录。',
        items: [],
        confirmText: '删除这条记录',
      });
      if (!ok) return;
      const r = await call('delete_history_session', sid);
      if (!r.ok) { toast(r.error || '删除失败', false); return; }
      toast('已删除该条观看记录 · 文件与进度均保留');
      await refresh();
      return;
    }
    case 'delete-local-file': {
      await confirmAndDeleteLocalFiles([Number(id)], '');
      return;
    }
    case 'delete-series-files': {
      await confirmAndDeleteLocalFiles([], btn.dataset.node || '');
      return;
    }
    case 'mark-watched': {
      const r = await call('mark_watched', Number(id));
      toast(r.ok ? '已标记为看完' : r.error, r.ok);
      break;
    }
    case 'mark-unwatched': {
      const r = await call('mark_unwatched', Number(id));
      toast(r.ok ? '已恢复为未看，可继续观看' : r.error, r.ok);
      break;
    }
    case 'reset-progress': {
      const r = await call('reset_progress', Number(id));
      toast(r.ok ? '进度已重置，历史记录保留' : r.error, r.ok);
      break;
    }
    case 'remove-dir': await call('remove_directory', Number(id)); toast('目录已移除'); break;
    case 'toggle-dir': await call('set_directory_enabled', Number(id), btn.dataset.enabled !== '1'); toast('已更新'); break;
    case 'choose-dir': {
      const r = await call('choose_directory');
      if (r.ok) document.getElementById('dirPath').value = r.path;
      else toast(r.error, false);
      return;
    }
    case 'add-dir': {
      const path = document.getElementById('dirPath').value.trim();
      if (!path) { toast('请先填写目录路径', false); return; }
      const r = await call('add_directory', path);
      toast(r.ok ? '目录已添加，正在后台扫描并匹配中文名' : r.error, r.ok);
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 3000);
      return;
    }
    case 'scan': {
      await call('scan_now'); toast('扫描已在后台开始，完成后自动刷新下面的文件列表');
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 3000);
      return;
    }
    case 'file-refresh': await loadFileList(); toast('列表已刷新'); return;
    case 'tree-toggle': {
      const key = btn.dataset.node;
      if (!key) return;
      if (treeState.open.has(key)) treeState.open.delete(key);
      else treeState.open.add(key);
      renderTree();
      return;
    }
    case 'tree-expand-all': {
      if (!treeState.data) return;
      // 只展开第一层分组，季 / 子目录仍按需点开，避免一次渲染上千行文件
      treeState.data.groups.forEach(g => treeState.open.add(g.id));
      renderTree();
      return;
    }
    case 'tree-collapse-all': treeState.open.clear(); renderTree(); return;
    case 'file-flat': {
      fileViewModeHandler('flat');
      const sel = document.getElementById('fileViewMode');
      if (sel) sel.value = 'flat';
      await loadFileList();
      return;
    }
    case 'file-prev':
      fileListState.page = Math.max(1, fileListState.page - 1);
      await loadFileList(); return;
    case 'file-next':
      fileListState.page += 1;
      await loadFileList(); return;
    case 'file-jump': {
      const jump = Number((document.getElementById('filePageJump') || {}).value || 1);
      fileListState.page = Math.max(1, Math.floor(jump || 1));
      await loadFileList(); return;
    }
    case 'file-all':
      fileListState.page_size = 0;
      fileListState.page = 1;
      { const sel = document.getElementById('filePageSize'); if (sel) sel.value = '0'; }
      toast('正在载入全部文件，数量较多时请稍候', true);
      await loadFileList(); return;
    case 'file-paged':
      fileListState.page_size = 200;
      fileListState.page = 1;
      { const sel = document.getElementById('filePageSize'); if (sel) sel.value = '200'; }
      await loadFileList(); return;
    case 'preview': {
      const text = document.getElementById('importText').value;
      btn.disabled = true; btn.textContent = '正在匹配中文名…';
      try {
        renderPreview(await call('preview_import', text));
      } finally {
        btn.disabled = false; btn.textContent = '解析预览并匹配中文名';
      }
      return;
    }
    case 'commit': {
      if (!lastPreviewRows.length) { toast('请先点击「解析预览并匹配中文名」', false); return; }
      document.querySelectorAll('.cn-edit').forEach(el => {
        const i = Number(el.dataset.idx);
        if (!lastPreviewRows[i]) return;
        const value = el.value.trim();
        if (value !== lastPreviewRows[i].title_cn) {
          lastPreviewRows[i].cn_edited = true;
          lastPreviewRows[i].title_cn = value || lastPreviewRows[i].title;
        }
      });
      const r = await call('commit_import', lastPreviewRows);
      toast(`导入 ${r.inserted} 条 · 跳过重复 ${r.skipped} 条` +
        (r.learned ? ` · 已记住 ${r.learned} 条中文名` : ''), r.ok);
      lastPreviewRows = [];
      document.getElementById('previewBox').innerHTML = empty('导入完成', '到「看过的列表」查看结果');
      return;
    }
    case 'save-settings': {
      const keys = ['potplayer_path', 'poll_interval_sec', 'complete_percent', 'http_proxy',
        'metadata_source', 'bangumi_base', 'window_width', 'window_height',
        'obsidian_vault', 'obsidian_folder', 'everything_es_path', 'everything_http_url',
        'pot_key_play_pause', 'pot_key_stop', 'pot_key_faster', 'pot_key_slower',
        'pot_key_reset_speed', 'pot_key_fullscreen', 'pot_speed_step'];
      const data = {};
      keys.forEach(k => { const el = document.getElementById('set-' + k); if (el) data[k] = el.value; });
      // 所有开关（含 daemon_auto_start / autostart / embed_player）由 data-setting 统一收集
      document.querySelectorAll('[data-setting]').forEach(el => { data[el.dataset.setting] = el.dataset.value; });
      await call('save_settings', data);
      await refreshEmbedState();
      toast('设置已保存');
      return;
    }
    case 'rematch': {
      btn.disabled = true; btn.textContent = '正在匹配…';
      try {
        const r = await call('rematch_all');
        toast(`重新匹配 ${r.matched} / ${r.total} 条（在线 ${r.by_online} 条）`, r.ok);
      } finally {
        btn.disabled = false;
      }
      break;
    }
    case 'import-alias-file': {
      const r = await call('import_alias_file');
      toast(r.ok ? `已导入 ${r.imported} 条对照，重新匹配 ${r.rematched} 条` : r.error, r.ok);
      break;
    }
    case 'open-data': await call('open_data_folder'); return;
    case 'poster-refresh': await loadPosterWall(); toast('剧集墙已刷新'); return;
    case 'cover-fetch': {
      const r = await call('start_cover_fetch', 0, false);
      toast(r.ok ? `已启动后台抓取（${r.total} 部作品）· 进度在后台进程页可见` : (r.error || '启动失败'), r.ok);
      await loadProcessStatus(); procPollStart();
      return;
    }
    case 'cover-force': {
      const r = await call('start_cover_fetch', 0, true);
      toast(r.ok ? `已启动强制重抓（${r.total} 部作品）` : (r.error || '启动失败'), r.ok);
      await loadProcessStatus(); procPollStart();
      return;
    }
    case 'cover-cancel': {
      await call('cancel_cover_fetch');
      toast('已请求停止，当前这一张完成后即停');
      await loadProcessStatus();
      return;
    }
    case 'proc-daemon-start': {
      const r = await call('start_daemon');
      toast(r.ok ? (r.already ? '守护已在运行' : '已启动后台记录') : (r.error || '启动失败'), !!r.ok);
      await updateDaemonState(); await loadProcessStatus();
      return;
    }
    case 'proc-daemon-stop': {
      const r = await call('stop_daemon');
      toast(r.ok ? `已停止 ${num(r.stopped)} 个守护进程` : (r.error || '停止失败'), !!r.ok);
      await updateDaemonState(); await loadProcessStatus();
      return;
    }
    case 'proc-daemon-auto': {
      const next = !procState.daemon.auto_start;
      await call('save_settings', { daemon_auto_start: next ? 1 : 0 });
      daemonAutoStart = next;
      toast(next ? '已开启自动启动并补拉起守护' : '已关闭自动启动并停止守护');
      await updateDaemonState(); await loadProcessStatus();
      return;
    }
    case 'watchlist-import-open': {
      const panel = document.getElementById('wlImportPanel');
      if (panel) panel.style.display = panel.style.display === 'none' ? '' : 'none';
      return;
    }
    case 'watchlist-preview': {
      const text = (document.getElementById('wlImportText') || {}).value || '';
      const r = await call('watchlist_preview', text);
      const hint = document.getElementById('wlImportHint');
      if (hint) hint.textContent = r.ok
        ? `预览：共 ${num(r.total)} 条 · 已下载 ${num(r.downloaded)} · 未下载 ${num(r.pending)}｜前几条：${(r.sample || []).join('、')}`
        : (r.error || '预览失败');
      return;
    }
    case 'watchlist-import': case 'watchlist-replace': {
      const text = (document.getElementById('wlImportText') || {}).value || '';
      const replace = action === 'watchlist-replace';
      const r0 = await call('watchlist_preview', text);
      if (!r0.ok || !num(r0.total)) { toast(r0.error || '没有可导入的条目', false); return; }
      if (replace) {
        const ok = await confirmDialog({
          title: '清空并重建追剧列表',
          message: `将删除现有追剧清单，按新文本重建 ${num(r0.total)} 条（已下载 ${num(r0.downloaded)} / 未下载 ${num(r0.pending)}）。`,
          note: '只影响追剧清单；媒体库、观看历史与看过的列表不受影响。',
          items: [], confirmText: '清空并重建',
        });
        if (!ok) return;
      }
      const r = await call('watchlist_import', text, replace);
      toast(r.ok ? `已入库 ${num(r.added)} 条 · 跳过重复 ${num(r.skipped)} 条（已下载 ${num(r.downloaded)} / 未下载 ${num(r.pending)}）`
        : (r.error || '导入失败'), !!r.ok);
      if (r.ok) await loadWatchlist();
      return;
    }
    case 'watchlist-add': {
      const el = document.getElementById('wlNewTitle');
      const title = (el && el.value || '').trim();
      if (!title) { toast('请先填写作品名', false); return; }
      const r = await call('watchlist_add', title, false);
      toast(r.ok ? '已加入未下载清单' : (r.error || '新增失败'), !!r.ok);
      if (r.ok && el) el.value = '';
      await loadWatchlist();
      return;
    }
    case 'watchlist-up': case 'watchlist-down': {
      const r = await call('watchlist_move', Number(id), action === 'watchlist-up' ? -1 : 1);
      if (!r.ok) { toast(r.error || '移动失败', false); return; }
      await loadWatchlist();
      return;
    }
    case 'watchlist-toggle': {
      const r = await call('watchlist_set_downloaded', Number(id), btn.dataset.to === '1');
      if (!r.ok) { toast(r.error || '状态修改失败', false); return; }
      toast(r.downloaded ? '已移到「已下载」' : '已移回「未下载」');
      await loadWatchlist();
      return;
    }
    case 'watchlist-files': {
      const rid = Number(id);
      if (watchlistState.open.has(rid)) watchlistState.open.delete(rid);
      else watchlistState.open.add(rid);
      renderWatchlistRows();   // 只重绘行，不重新查库
      return;
    }
    case 'watchlist-remove': {
      const ok = await confirmDialog({
        title: '从追剧列表移除',
        message: '只移除这条追剧记录，本地视频、观看历史与播放进度全部保留。',
        items: [], confirmText: '移除', danger: false,
      });
      if (!ok) return;
      const r = await call('watchlist_remove', Number(id));
      toast(r.ok ? '已移除' : (r.error || '移除失败'), !!r.ok);
      await loadWatchlist();
      return;
    }
    case 'es-search': {
      const kw = (document.getElementById('esSearch') || {}).value || '';
      esState.keyword = kw.trim();
      const r = await call('everything_search', esState.keyword, 200);
      esState.rows = r.rows || [];
      esState.source = r.source || '';
      esState.hint = r.hint || '';
      renderEsResults();
      return;
    }
    case 'play-external':
      await playSmart('path', btn.dataset.path || '', false);
      return;
    case 'obsidian-sync': {
      btn.disabled = true; btn.textContent = '正在同步…';
      try {
        const r = await call('obsidian_sync');
        if (r.ok) toast(`已同步 ${r.created + r.updated + r.unchanged} 篇笔记 · 路线图 ${r.roadmap_items} 条`, true);
        else toast(r.error || '同步失败', false);
      } finally {
        btn.disabled = false; btn.textContent = '同步到 Obsidian';
      }
      break;
    }
    case 'obsidian-open': {
      const r = await call('obsidian_open_folder');
      if (!r.ok) toast(r.error || '打开失败', false);
      return;
    }
    default: return;
  }
  await refresh();
}

document.addEventListener('click', async (ev) => {
  const nav = ev.target.closest('.nav-item');
  if (nav) {
    if (playerStageActive) await stopEmbedded(true);   // 离开播放页 -> 播放器还原为独立窗口，播放不中断
    switchView(nav.dataset.view);
    return;
  }
  const sw = ev.target.closest('[data-setting]');
  if (sw) {
    const on = sw.dataset.value !== '1';
    sw.dataset.value = on ? '1' : '0';
    sw.classList.toggle('on', on);
    return;
  }
  const btn = ev.target.closest('[data-action]');
  if (!btn) return;
  try {
    await handleAction(btn.dataset.action, btn);
  } catch (err) {
    toast(String(err && err.message || err), false);
  }
});

// 搜索与筛选只局部刷新列表容器，不重建输入框，因此不会丢焦点或光标
document.addEventListener('input', (ev) => {
  const el = ev.target;
  // 看过的列表：本地即时过滤，零延迟；不重建输入框
  if (el.id === 'watchedSearch') {
    watchedKeyword = el.value;
    watchedEditing = 0;
    renderWatchedRows();
    return;
  }
  if (el.id === 'posterSearch') {
    posterState.keyword = el.value;
    renderPosterCards();   // 已有卡片本地过滤，不发请求
    return;
  }
  if (el.id === 'wlSearch') {
    watchlistState.keyword = el.value;
    renderWatchlistRows();  // 全量已在内存，本地即时过滤
    return;
  }
  if (el.id !== 'fileSearch') return;
  const value = el.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    fileListState.keyword = value;
    fileListState.page = 1;
    loadFileList();
  }, 250);
});

// 双击单元格直接改名：不必先点右侧「改名」；焦点离开整行时自动保存
document.addEventListener('dblclick', (ev) => {
  const td = ev.target.closest('[data-edit-watched]');
  if (!td) return;
  watchedEditing = Number(td.dataset.editWatched);
  renderWatchedRows();
  const el = document.getElementById(td.dataset.field === 'title_cn' ? 'watchedEditCn' : 'watchedEditTitle');
  if (el) { el.focus(); el.select(); }
});

document.addEventListener('focusout', (ev) => {
  if (!watchedEditing) return;
  if (ev.target.id !== 'watchedEditTitle' && ev.target.id !== 'watchedEditCn') return;
  const rid = watchedEditing;
  setTimeout(() => {
    if (!watchedEditing || watchedEditing !== rid) return;
    const active = document.activeElement;
    // 焦点仍在编辑行内（切到另一个输入框 / 点保存 / 点取消）时不提前保存
    if (active && active.closest && active.closest('.watched-edit')) return;
    saveWatchedEdit(rid);
  }, 0);
});

// 改名行内编辑：Enter 保存、Esc 取消
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && ev.target.id === 'esSearch') {
    ev.preventDefault();
    const btn = document.querySelector('[data-action="es-search"]');
    if (btn) btn.click();
    return;
  }
  if (ev.key === 'Enter' && ev.target.id === 'wlNewTitle') {
    ev.preventDefault();
    const btn = document.querySelector('[data-action="watchlist-add"]');
    if (btn) btn.click();
    return;
  }
  if (!watchedEditing) return;
  const id = ev.target.id;
  if (id !== 'watchedEditTitle' && id !== 'watchedEditCn') return;
  if (ev.key === 'Enter') {
    ev.preventDefault();
    const btn = document.querySelector(`[data-action="save-watched"][data-id="${watchedEditing}"]`);
    if (btn) btn.click();
  } else if (ev.key === 'Escape') {
    watchedEditing = 0;
    renderWatchedRows();
  }
});

document.addEventListener('change', (ev) => {
  const el = ev.target;
  if (el.id === 'playerSpeed') { setPlayerSpeed(el.value); return; }
  const handlers = {
    fileDir: () => { fileListState.directory_id = Number(el.value) || 0; },
    fileMatch: () => { fileListState.match_state = el.value; },
    fileSort: () => { fileListState.sort = el.value; },
    filePageSize: () => { fileListState.page_size = Number(el.value); },
    fileViewMode: () => fileViewModeHandler(el.value),
  };
  if (!handlers[el.id]) return;
  handlers[el.id]();
  fileListState.page = 1;
  loadFileList();
});

async function init() {
  if (inited) return;
  inited = true;
  try {
    const info = await call('get_app_info');
    document.getElementById('versionText').textContent = `v${info.version} · 数据仅存本机`;
    applyTheme(info.theme);
  } catch (e) { /* 忽略 */ }
  // 原生窗口标题栏不受 CSS 控制，窗口就绪后同步一次；重启深色模式也不会退回白条
  try { await call('sync_native_theme'); } catch (e) { /* 不支持 DWM 时静默降级 */ }
  // 后台记录默认自动启动：create_window 已先拉起一次，此处为幂等兜底（不会重复拉起）
  try {
    const boot = await call('ensure_daemon_auto_start');
    daemonAutoStart = !(boot && boot.disabled);
  } catch (e) { /* 守护启动失败不影响界面 */ }
  // 任何一步刷新抛错都不能阻断状态轮询：以前 refresh() 一旦异常，
  // 左下角会永远停在启动那一刻的旧文案，看起来就像「守护没起来」。
  if (daemonAutoStart) daemonBootUntil = Date.now() + 12000;
  try { await refreshEmbedState(); } catch (e) { /* 内嵌播放状态降级不影响界面 */ }
  try { await refresh(); } catch (e) { /* 首页刷新失败不影响状态与轮询注册 */ }
  await updateDaemonState();
  if (daemonAutoStart && daemonBootUntil) watchDaemonBoot();
  try { await updatePlayerChip(); } catch (e) { /* 播放器状态降级不影响界面 */ }
  setInterval(updatePlayerChip, 3000);
  setInterval(updateDaemonState, 8000);
  setInterval(() => { if (currentView === 'home' && !playerStageActive) refresh(); }, 12000);
}

// 切回软件窗口时立即校正守护状态与断点 / 历史，不必等下一轮轮询
window.addEventListener('focus', () => {
  updateDaemonState();
  if ((currentView === 'home' || currentView === 'continue')
      && !watchedEditing && !playerStageActive) refresh();
});

// 窗口缩放 / DPI 变化时把原生播放器重新摆回舞台矩形
window.addEventListener('resize', () => {
  if (!playerStageActive) return;
  clearTimeout(embedResizeTimer);
  embedResizeTimer = setTimeout(() => {
    if (playerStageActive) call('embed_sync', hostRect()).catch(() => {});
  }, 120);
});

window.addEventListener('pywebviewready', init);
if (apiReady()) init();
