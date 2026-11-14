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
        <button class="btn" data-action="start-daemon">启动后台记录</button>
      </div></div>`);
}

async function renderContinue() {
  const [list, done] = await Promise.all([
    call('get_continue_watching', 100), call('get_completed', 20),
  ]);
  setContent(`<div class="card"><h3>继续观看</h3>
    <p class="hint">点击「继续观看」会用 PotPlayer 从上次的位置打开，播放过程按设置页的间隔自动记录。</p>
    <div class="list">${list.length ? list.map(r => progressItem(r)).join('') : empty('暂无进行中的播放', '先扫描目录并播放一集试试')}</div></div>
    <div class="card"><h3>已看完（可纠错）</h3>
    <p class="hint">标错或想重看时，点「标记未看」即可恢复；观看历史不会被删除。</p>
    <div class="list">${done.length ? done.map(r => `<div class="item">
      <div class="thumb">✓</div>
      <div class="body"><div class="name">${esc(r.display_title || r.filename)}
        ${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}</div>
        <div class="meta">${esc(r.filename || '')} · 最近 ${esc(shortDate(r.last_played_at))}</div></div>
      <div class="right"><div class="btn-row" style="justify-content:flex-end">
        <button class="btn btn-primary btn-sm" data-action="mark-unwatched" data-id="${r.file_id}">标记未看</button>
        <button class="btn btn-sm" data-action="reset-progress" data-id="${r.file_id}">重置进度</button>
      </div></div></div>`).join('') : empty('还没有已看完的条目', '进度达到设置页的阈值后会自动出现在这里')}</div></div>`);
}

async function renderHistory() {
  const list = await call('get_history', 200);
  setContent(`<div class="card"><h3>历史记录</h3><p class="hint">共 ${list.length} 次观看会话，按时间倒序。</p>
    <div class="list">${list.length ? list.map(r => `<div class="item">
      <div class="thumb">⌚</div>
      <div class="body"><div class="name">${esc(r.display_title || r.filename || '未知文件')}
        ${r.episode_no ? esc(' · 第 ' + r.episode_no + ' 集') : ''}</div>
        <div class="meta">${esc(shortDate(r.started_at))} → ${esc(shortDate(r.ended_at))}</div></div>
      <div class="right">${fmtMs(r.from_ms)} → ${fmtMs(r.to_ms)}<br><span class="mono">最远 ${fmtMs(r.max_ms)}</span></div>
    </div>`).join('') : empty('还没有历史记录', '播放任意视频后会自动出现在这里')}</div></div>`);
}

async function renderLibrary() {
  const keyword = (document.getElementById('libSearch') || {}).value || '';
  const list = await call('get_media_files', keyword, 400);
  setContent(`<div class="card"><h3>媒体库</h3>
    <p class="hint">已扫描到的本地视频。外文文件名在添加目录时会自动匹配成中文名，原文仍保留在下方。</p>
    <div class="field"><input class="input" id="libSearch" placeholder="按文件名、中文名或原名搜索…" value="${esc(keyword)}"></div>
    <div class="list">${list.length ? list.map(r => `<div class="item">
      <div class="thumb">▶</div>
      <div class="body"><div class="name">${esc(r.display_title || r.filename)}
        ${r.title_cn ? '<span class="badge green">已匹配中文</span>' : ''}</div>
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
    <p class="hint">你手动导入的已观看作品，共 ${list.length} 条。中文名在导入那一刻就已自动匹配完成。</p>
    <div class="table-wrap"><table><thead><tr><th>作品名</th><th>中文名</th><th>匹配方式</th><th>季</th><th>集数范围</th><th>导入时间</th><th></th></tr></thead>
    <tbody>${list.length ? list.map(r => {
      const [label, tone] = MATCH_LABEL[r.match_by] || ['—', ''];
      return `<tr>
      <td>${esc(r.title)}</td><td>${esc(r.title_cn || '—')}</td>
      <td><span class="badge ${tone}">${esc(label)}</span></td>
      <td>${r.season_no ? esc(r.season_no) : '—'}</td>
      <td>${r.episode_from ? esc(`${r.episode_from}–${r.episode_to || r.episode_from}`) : '—'}</td>
      <td>${esc(shortDate(r.imported_at))}</td>
      <td><button class="btn btn-sm btn-danger" data-action="delete-watched" data-id="${r.id}">删除</button></td>
    </tr>`;
    }).join('') : `<tr><td colspan="7">${empty('还没有导入', '到「导入列表」粘贴你的动漫清单')}</td></tr>`}
    </tbody></table></div></div>`);
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
  document.getElementById('previewBox').innerHTML = `<div class="notice">
    解析出 ${data.total} 条 · 重复 ${data.duplicates} 条（导入时自动跳过）·
    自动匹配 ${num(m.matched)} 条（离线词典 ${num(m.by_builtin)} / 在线 ${num(m.by_online)}）·
    未匹配 ${data.unmatched_cn} 条<br><span class="hint">${esc(onlineNote)}；中文名列可直接编辑，改动会记入词典。</span></div>
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
    </div></div>`);
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
      <div class="field"><label>播放状态轮询间隔（秒）</label>
        <input class="input" type="number" min="1" max="30" id="set-poll_interval_sec" value="${esc(s.poll_interval_sec)}"></div>
      <div class="field"><label>自动标记「已看完」的进度阈值（%）</label>
        <input class="input" type="number" min="50" max="100" id="set-complete_percent" value="${esc(s.complete_percent)}"></div>
      ${switchHtml('recording_enabled', isOn(s.recording_enabled), '启用播放记录', '关闭后守护进程不再写入历史')}
      ${switchHtml('autostart', isOn(s.autostart_enabled), '开机自动启动后台记录', '写当前用户注册表 Run 项，无需管理员权限')}
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
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 1500);
      return;
    }
    case 'scan': {
      await call('scan_now'); toast('扫描已在后台开始，完成后自动匹配中文名');
      setTimeout(() => { if (currentView === 'scan') refresh(); }, 1800);
      return;
    }
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
        'obsidian_vault', 'obsidian_folder'];
      const data = {};
      keys.forEach(k => { const el = document.getElementById('set-' + k); if (el) data[k] = el.value; });
      document.querySelectorAll('[data-setting]').forEach(el => { data[el.dataset.setting] = el.dataset.value; });
      await call('save_settings', data);
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
