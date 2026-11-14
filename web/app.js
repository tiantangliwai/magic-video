'use strict';

let currentView = 'home';
let lastPreviewRows = [];
let inited = false;
let searchTimer = null;

const VIEW_META = {
  home: ['总览', '你的播放进度一目了然'],
  continue: ['继续观看', '从上次中断的位置接着看'],
  history: ['历史记录', '每次观看的时间与进度'],
  library: ['媒体库', '扫描到的本地视频文件'],
  watched: ['看过的列表', '你导入的已观看作品'],
  import: ['导入列表', '把看过的动漫一次性记录到数据库'],
  scan: ['扫描目录', '添加已下载视频的存放位置'],
  settings: ['设置', '播放器、记录与中文补全'],
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
  el._timer = setTimeout(() => el.classList.remove('show'), 3000);
}

function setContent(html) { document.getElementById('content').innerHTML = html; }

function empty(text, sub) {
  return `<div class="empty"><div class="big">${esc(text)}</div><div>${esc(sub || '')}</div></div>`;
}

function progressItem(r, actions) {
  const name = r.title_guess || r.filename || '未知文件';
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
        <button class="btn" data-action="start-daemon">启动后台记录</button>
      </div></div>`);
}

async function renderContinue() {
  const list = await call('get_continue_watching', 100);
  setContent(`<div class="card"><h3>继续观看</h3>
    <p class="hint">点击「继续观看」会用 PotPlayer 从上次的位置打开，播放过程每 3 秒自动记录。</p>
    <div class="list">${list.length ? list.map(r => progressItem(r)).join('') : empty('暂无进行中的播放', '先扫描目录并播放一集试试')}</div></div>`);
}

async function renderHistory() {
  const list = await call('get_history', 200);
  setContent(`<div class="card"><h3>历史记录</h3><p class="hint">共 ${list.length} 次观看会话，按时间倒序。</p>
    <div class="list">${list.length ? list.map(r => `<div class="item">
      <div class="thumb">⌚</div>
      <div class="body"><div class="name">${esc(r.title_guess || r.filename || '未知文件')}
        ${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}</div>
        <div class="meta">${esc(shortDate(r.started_at))} → ${esc(shortDate(r.ended_at))}</div></div>
      <div class="right">${fmtMs(r.from_ms)} → ${fmtMs(r.to_ms)}<br><span class="mono">最远 ${fmtMs(r.max_ms)}</span></div>
    </div>`).join('') : empty('还没有历史记录', '播放任意视频后会自动出现在这里')}</div></div>`);
}

async function renderLibrary() {
  const keyword = (document.getElementById('libSearch') || {}).value || '';
  const list = await call('get_media_files', keyword, 400);
  setContent(`<div class="card"><h3>媒体库</h3>
    <p class="hint">已扫描到的本地视频。P1 阶段会在这里展示中文名、封面与季度分组。</p>
    <div class="field"><input class="input" id="libSearch" placeholder="按文件名或标题搜索…" value="${esc(keyword)}"></div>
    <div class="list">${list.length ? list.map(r => `<div class="item">
      <div class="thumb">▶</div>
      <div class="body"><div class="name">${esc(r.title_guess || r.filename)}</div>
        <div class="meta">${esc(r.filename)}${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}</div>
        <div class="mono">${esc(r.path)}</div></div>
      <div class="right">${num(r.percent) ? `${num(r.percent).toFixed(0)}%<br>` : ''}
        <div class="btn-row" style="justify-content:flex-end;margin-top:6px">
          <button class="btn btn-primary btn-sm" data-action="play" data-id="${r.id}">播放</button>
        </div></div>
    </div>`).join('') : empty('媒体库还是空的', '到「扫描目录」添加存放视频的文件夹')}</div></div>`);
  const input = document.getElementById('libSearch');
  if (input) {
    input.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderLibrary, 300);
    });
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

async function renderWatched() {
  const list = await call('get_watched_list', 500);
  setContent(`<div class="card"><h3>看过的列表</h3>
    <p class="hint">你手动导入的已观看作品，共 ${list.length} 条。可在「导入列表」继续追加。</p>
    <div class="table-wrap"><table><thead><tr><th>作品名</th><th>中文名</th><th>季</th><th>集数范围</th><th>导入时间</th><th></th></tr></thead>
    <tbody>${list.length ? list.map(r => `<tr>
      <td>${esc(r.title)}</td><td>${esc(r.title_cn || '—')}</td><td>${r.season_no ? esc(r.season_no) : '—'}</td>
      <td>${r.episode_from ? esc(`${r.episode_from}–${r.episode_to || r.episode_from}`) : '—'}</td>
      <td>${esc(shortDate(r.imported_at))}</td>
      <td><button class="btn btn-sm btn-danger" data-action="delete-watched" data-id="${r.id}">删除</button></td>
    </tr>`).join('') : `<tr><td colspan="6">${empty('还没有导入', '到「导入列表」粘贴你的动漫清单')}</td></tr>`}
    </tbody></table></div></div>`);
}

function renderImport() {
  setContent(`<div class="card"><h3>导入看过的动漫列表</h3>
    <p class="hint">每行一个作品，支持 <b>作品名</b>、<b>作品名 S2 01-12</b>、<b>作品名 第1季 1-25集</b>，
    也支持 CSV「作品名,季,起始集,结束集」。以 # 开头的行会被忽略。</p>
    <div class="field"><textarea class="input" id="importText" placeholder="进击的巨人&#10;更衣人偶坠入爱河 S2 01-12&#10;葬送的芙莉莲,1,1,28&#10;间谍过家家 第1季 1-12"></textarea></div>
    <div class="btn-row" style="margin-bottom:16px">
      <button class="btn btn-primary" data-action="preview">解析预览</button>
      <button class="btn" data-action="commit">确认导入</button>
      <button class="btn" data-action="goto" data-view="watched">查看已导入</button>
    </div>
    <div id="previewBox">${empty('等待输入', '粘贴清单后点击「解析预览」')}</div></div>`);
}

function renderPreview(data) {
  lastPreviewRows = data.rows || [];
  document.getElementById('previewBox').innerHTML = `<div class="notice">
    解析出 ${data.total} 条 · 重复 ${data.duplicates} 条（导入时自动跳过）· 待补全中文名 ${data.unmatched_cn} 条</div>
    <div class="table-wrap"><table><thead><tr><th>作品名</th><th>中文名</th><th>季</th><th>集数</th><th>状态</th></tr></thead><tbody>
    ${lastPreviewRows.length ? lastPreviewRows.map(r => `<tr>
      <td>${esc(r.title)}</td><td>${esc(r.title_cn)}</td><td>${r.season_no || '—'}</td>
      <td>${r.episode_from ? esc(`${r.episode_from}–${r.episode_to}`) : '—'}</td>
      <td>${r.duplicate ? '<span class="badge amber">已存在</span>' : r.matched_cn ? '<span class="badge green">已匹配中文</span>' : '<span class="badge blue">待补全</span>'}</td>
    </tr>`).join('') : `<tr><td colspan="5">没有解析出有效条目</td></tr>`}</tbody></table></div>`;
}

async function renderScan() {
  const [dirs, queue, last] = await Promise.all([
    call('get_directories'), call('queue_status'), call('get_last_scan'),
  ]);
  setContent(`<div class="card"><h3>添加视频目录</h3>
    <p class="hint">添加后会自动递归扫描常见的视频格式（mkv / mp4 / avi / ts 等），并跳过样片、特典与字幕文件。</p>
    <div class="field"><label>目录绝对路径</label>
      <div style="display:flex;gap:9px"><input class="input" id="dirPath" placeholder="例如 D:\\Anime\\已下载">
        <button class="btn" data-action="choose-dir">浏览</button>
        <button class="btn btn-primary" data-action="add-dir">添加并扫描</button></div>
      <div class="tip">路径含空格也可以，直接粘贴即可。</div></div>
    ${last ? `<div class="notice">上次扫描：新增 ${num(last.added)} 个文件，更新 ${num(last.updated)} 个。</div>` : ''}
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
    <p class="hint">扫描后识别不出中文名的标题会进入待补全队列；WorkBuddy 定时自动化任务会联网检索并回填，
    软件每次启动与扫描时自动导入结果。本机直连境外元数据接口超时也不会影响这条通道。</p>
    <div class="notice">当前待补全 <b>${num(queue.pending)}</b> 条 · 队列文件 <span class="mono">data/pending_titles.json</span></div>
    <div class="btn-row">
      <button class="btn" data-action="export-queue">导出待补全清单</button>
      <button class="btn" data-action="import-aliases">导入补全结果</button>
      <button class="btn" data-action="open-data">打开数据目录</button>
    </div></div>`);
}

function switchHtml(key, on, label, desc) {
  return `<div class="switch-row"><div><div>${esc(label)}</div><div class="desc">${esc(desc)}</div></div>
    <button class="switch ${on ? 'on' : ''}" data-setting="${key}" data-value="${on ? 1 : 0}"></button></div>`;
}

function isOn(v) { return v === true || v === 1 || v === '1' || v === 'true' || v === 'on'; }

async function renderSettings() {
  const s = await call('get_settings');
  setContent(`<div class="grid-2">
    <div class="card"><h3>播放器与记录</h3>
      <div class="field"><label>PotPlayer 可执行文件路径</label>
        <input class="input" id="set-potplayer_path" value="${esc(s.potplayer_path)}">
        <div class="tip">${s.potplayer_exists ? '已找到该文件' : '⚠ 文件不存在，请修正路径'}</div></div>
      <div class="field"><label>播放状态轮询间隔（秒）</label>
        <input class="input" type="number" min="1" max="30" id="set-poll_interval_sec" value="${esc(s.poll_interval_sec)}"></div>
      <div class="field"><label>自动标记「已看完」的进度阈值（%）</label>
        <input class="input" type="number" min="50" max="100" id="set-complete_percent" value="${esc(s.complete_percent)}"></div>
      ${switchHtml('recording_enabled', isOn(s.recording_enabled), '启用播放记录', '关闭后守护进程不再写入历史')}
      ${switchHtml('autostart', isOn(s.autostart_enabled), '开机自动启动后台记录', '写当前用户注册表 Run 项，无需管理员权限')}
    </div>
    <div class="card"><h3>中文补全与网络</h3>
      <div class="notice">软件独立运行时无法直接调用对话里的联网检索，因此采用「本地队列 + WorkBuddy 定时自动化」：
      软件导出待补全标题 → 自动化任务联网检索中文名并回写 → 软件自动导入。全程无需人工干预。</div>
      <div class="field"><label>元数据来源策略</label>
        <select class="input" id="set-metadata_source">
          <option value="cache_first" ${s.metadata_source === 'cache_first' ? 'selected' : ''}>本地缓存优先（推荐）</option>
          <option value="online_first" ${s.metadata_source === 'online_first' ? 'selected' : ''}>在线检索优先</option>
          <option value="local_only" ${s.metadata_source === 'local_only' ? 'selected' : ''}>仅使用本地词典</option>
        </select></div>
      <div class="field"><label>HTTP 代理（可选）</label>
        <input class="input" id="set-http_proxy" placeholder="例如 http://127.0.0.1:7890" value="${esc(s.http_proxy)}">
        <div class="tip">填写后软件会尝试直连 Bangumi / TMDB；留空则完全依赖本地缓存通道。</div></div>
      <div class="field"><label>Bangumi API 地址</label>
        <input class="input" id="set-bangumi_base" value="${esc(s.bangumi_base)}"></div>
      <div class="field"><label>主窗口尺寸</label>
        <div style="display:flex;gap:9px">
          <input class="input" type="number" id="set-window_width" value="${esc(s.window_width)}">
          <input class="input" type="number" id="set-window_height" value="${esc(s.window_height)}">
        </div><div class="tip">下次启动生效</div></div>
    </div></div>
    <div class="card"><div class="btn-row">
      <button class="btn btn-primary" data-action="save-settings">保存设置</button>
      <button class="btn" data-action="open-data">打开数据目录</button>
    </div><p class="hint" style="margin:14px 0 0">数据库：<span class="mono">${esc(s.db_path || '')}</span> · 所有数据仅保存在本机，不会上传。</p></div>`);
}

const RENDERERS = {
  home: renderHome, continue: renderContinue, history: renderHistory, library: renderLibrary,
  watched: renderWatched, import: renderImport, scan: renderScan, settings: renderSettings,
};

async function refresh() {
  if (!apiReady()) return;
  const [title, subtitle] = VIEW_META[currentView];
  document.getElementById('viewTitle').textContent = title;
  document.getElementById('viewSubtitle').textContent = subtitle;
  try {
    await RENDERERS[currentView]();
  } catch (err) {
    setContent(`<div class="card">${empty('页面加载失败', String(err && err.message || err))}</div>`);
  }
}

function switchView(view) {
  if (!VIEW_META[view]) return;
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
    dot.className = 'dot' + (running ? '' : ' gray');
    document.getElementById('daemonText').textContent = running ? '后台记录正在运行' : '后台记录未启动';
  } catch (e) {
    document.getElementById('daemonText').textContent = '后台记录状态未知';
  }
}

async function handleAction(action, btn) {
  const id = btn.dataset.id;
  switch (action) {
    case 'goto': switchView(btn.dataset.view); return;
    case 'play': case 'play-start': {
      const r = await call('play_file', Number(id), action === 'play-start');
      toast(r.ok ? (r.position_ms ? `从 ${fmtMs(r.position_ms)} 继续播放` : '已开始播放') : r.error, r.ok);
      return;
    }
    case 'delete-watched': await call('delete_watched', Number(id)); toast('已删除'); break;
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
      toast(r.ok ? '目录已添加，正在后台扫描' : r.error, r.ok);
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 1200);
      return;
    }
    case 'scan': {
      await call('scan_now'); toast('扫描已在后台开始，稍后刷新查看');
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 1500);
      return;
    }
    case 'preview': {
      const text = document.getElementById('importText').value;
      renderPreview(await call('preview_import', text));
      return;
    }
    case 'commit': {
      if (!lastPreviewRows.length) { toast('请先点击「解析预览」', false); return; }
      const r = await call('commit_import', lastPreviewRows);
      toast(`导入 ${r.inserted} 条，跳过重复 ${r.skipped} 条`, r.ok);
      lastPreviewRows = [];
      return;
    }
    case 'save-settings': {
      const keys = ['potplayer_path', 'poll_interval_sec', 'complete_percent', 'http_proxy',
        'metadata_source', 'bangumi_base', 'window_width', 'window_height'];
      const data = {};
      keys.forEach(k => { const el = document.getElementById('set-' + k); if (el) data[k] = el.value; });
      document.querySelectorAll('[data-setting]').forEach(el => { data[el.dataset.setting] = el.dataset.value; });
      await call('save_settings', data);
      toast('设置已保存');
      return;
    }
    case 'export-queue': { const r = await call('export_queue'); toast(`已导出 ${r.count} 条待补全标题`, r.ok); return; }
    case 'import-aliases': { const r = await call('import_aliases'); toast(`已导入 ${r.imported} 条中文名`, r.ok); break; }
    case 'open-data': await call('open_data_folder'); return;
    case 'start-daemon': {
      const r = await call('start_daemon');
      toast(r.ok ? '后台记录已启动' : r.error, r.ok);
      setTimeout(updateDaemonState, 900);
      return;
    }
    default: return;
  }
  await refresh();
}

document.addEventListener('click', async (ev) => {
  const nav = ev.target.closest('.nav-item');
  if (nav) { switchView(nav.dataset.view); return; }
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

async function init() {
  if (inited) return;
  inited = true;
  try {
    const info = await call('get_app_info');
    document.getElementById('versionText').textContent = `v${info.version} · 数据仅存本机`;
  } catch (e) { /* 忽略 */ }
  await refresh();
  await updateDaemonState();
  await updatePlayerChip();
  setInterval(updatePlayerChip, 3000);
  setInterval(updateDaemonState, 15000);
  setInterval(() => { if (currentView === 'home') refresh(); }, 12000);
}

window.addEventListener('pywebviewready', init);
if (apiReady()) init();
