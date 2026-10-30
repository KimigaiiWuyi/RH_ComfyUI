/* RH_ComfyUI 模型与供应商看板
 * 主视图是「模型 → 供应商」双列图谱:左侧模型(按模态分块),右侧供应商,
 * 连线表示继承关系,颜色表示可用性。
 * 纯 SVG 手绘,零外部依赖:断网也能看。
 */

(function () {
  'use strict';

  var API = '/api/RH_ComfyUI/dashboard/overview';
  var REFRESH_MS = 10000;

  /* 图谱几何(屏幕像素)。
   * 不做整体缩放:缩放会让文字随屏宽一路涨到 20px 以上,行高跟着膨胀,
   * 一屏放不下几行,反而更难读。这里改成两列按容器宽度自适应,
   * 余下的宽度全给中间的连线区。 */
  var ROW_H = 26;
  var NODE_H = 20;
  var GROUP_GAP = 26;
  var GROUP_LABEL_H = 22;
  var HEADER_H = 28;
  var COL_MIN = 250;
  var COL_MAX = 340;
  var COL_RATIO = 0.2;
  var GAP_MIN = 220;
  var PAD_X = 14;
  var TOP_PAD = 10;
  var PROV_MIN_GAP = 28;
  /* 紧凑布局(窄屏):宽度放不下两列连线图时,改画整宽模型卡片,
     通道内联成 chip,不画连线也不产生横向滚动。 */
  var COMPACT_W = 760;
  var NAME_H = 24;
  var ROW_GAP = 8;
  var CHIP_H = 16;
  var CHIP_GAP = 5;
  var CHIP_LINE = 20;
  var CHIP_CHAR_W = 5.6;
  var CHIP_MAX_W = 150;
  /* SVG 里没有原生省略号,只能按字符宽度自己截断。
     13px 字号下:拉丁字符约 6.8px,中文按 textWidth() 记 2 个单位(=13.6px)。 */
  var CHAR_W = 6.8;

  var state = {
    data: null,
    keyword: '',
    modality: 'all',
    onlyIssue: false,
    provider: null,
    selectedModel: null,
    highlight: null,
    timer: null,
    loading: false,
  };

  /* 图谱索引:模型↔供应商的连接关系(高亮时 O(1) 查) */
  var graphIndex = { modelLinks: {}, providerLinks: {}, linkById: {} };

  function t(key, params) {
    if (window.GsHubPlugin && typeof GsHubPlugin.t === 'function') {
      return GsHubPlugin.t(key, params);
    }
    return key;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function svgEl(tag, attrs) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
    }
    return node;
  }

  /* 中文按 2 个单位宽估算,用于截断 SVG 文本(SVG 没有原生 ellipsis) */
  function textWidth(s) {
    var w = 0;
    for (var i = 0; i < s.length; i++) w += s.charCodeAt(i) > 255 ? 2 : 1;
    return w;
  }

  function ellipsize(s, maxUnits) {
    if (textWidth(s) <= maxUnits) return s;
    var out = '';
    var w = 0;
    for (var i = 0; i < s.length; i++) {
      var cw = s.charCodeAt(i) > 255 ? 2 : 1;
      if (w + cw > maxUnits - 1) break;
      out += s[i];
      w += cw;
    }
    return out + '…';
  }

  /* ── 数据 ── */

  function load() {
    if (state.loading) return;
    state.loading = true;
    // 已有快照时静默刷新:不闪"加载中",不隐藏正文
    if (!state.data) showStatus(t('loading'), false);
    window.GsHubPlugin.api(API)
      .then(function (data) {
        state.data = data;
        state.loading = false;
        render();
      })
      .catch(function (err) {
        state.loading = false;
        showStatus(t('error') + ': ' + (err && err.message ? err.message : err), true);
      });
  }

  function showStatus(text, isError) {
    var box = $('status');
    box.className = isError ? 'status error' : 'status';
    $('status-text').textContent = text;
    box.hidden = false;
    // 已有数据时保留正文,只提示错误(自动刷新偶发失败不至于白屏)
    if (!state.data) $('main').hidden = true;
  }

  /* ── 过滤 ── */

  function matchKeyword(model, keyword) {
    if (!keyword) return true;
    var hay = [model.name, model.display_name, model.backend, model.group_label];
    for (var i = 0; i < model.channels.length; i++) {
      hay.push(model.channels[i].name);
      hay.push(model.channels[i].vendor_model || '');
    }
    return hay.join(' ').toLowerCase().indexOf(keyword) !== -1;
  }

  function hasIssue(model) {
    for (var i = 0; i < model.channels.length; i++) {
      if (!model.channels[i].available || model.channels[i].circuit_open) return true;
    }
    return false;
  }

  function visibleModels() {
    var data = state.data;
    if (!data) return [];
    var keyword = state.keyword.trim().toLowerCase();
    return data.models.filter(function (model) {
      if (state.modality !== 'all' && model.catalog_group !== state.modality) return false;
      if (state.onlyIssue && !hasIssue(model)) return false;
      return matchKeyword(model, keyword);
    });
  }

  function visibleProviders(models) {
    var data = state.data;
    if (!data) return [];
    var keyword = state.keyword.trim().toLowerCase();
    return data.providers.filter(function (provider) {
      var inherited = provider.models.some(function (name) {
        return models.some(function (m) {
          return m.name === name;
        });
      });
      if (state.modality !== 'all' && !inherited) return false;
      if (keyword) {
        var hay = [provider.name, provider.label].join(' ').toLowerCase();
        if (hay.indexOf(keyword) === -1 && !inherited) return false;
      }
      return true;
    });
  }

  /* ── 渲染 ── */

  function computeStats(models) {
    var links = 0;
    var linksAvailable = 0;
    var circuitOpen = 0;
    var availableModels = 0;
    models.forEach(function (model) {
      if (model.available) availableModels += 1;
      model.channels.forEach(function (channel) {
        links += 1;
        if (channel.available) linksAvailable += 1;
        if (channel.circuit_open) circuitOpen += 1;
      });
    });
    return {
      total: models.length,
      available: availableModels,
      links: links,
      linksAvailable: linksAvailable,
      circuitOpen: circuitOpen,
    };
  }

  function render() {
    if (!state.data) return;
    $('status').hidden = true;
    $('main').hidden = false;

    renderMeta();
    renderChips();
    renderKpis();
    renderGraph();
    renderProviderTable();
    renderDetail();
  }

  function renderMeta() {
    var data = state.data;
    var box = $('meta-row');
    clear(box);

    function add(label, value) {
      var span = el('span');
      span.appendChild(document.createTextNode(label + ' '));
      span.appendChild(el('b', null, value));
      box.appendChild(span);
    }

    add(t('updatedAt'), data.generated_at);
    add(t('balanceMode'), data.balancer.mode);
    add(t('failureThreshold'), data.balancer.failure_threshold);
    add(t('circuitSeconds'), t('seconds', { n: data.balancer.circuit_breaker_seconds }));
    add(t('meta.global'), t('kpi.modelsAvailable') + ' ' + data.totals.models_available + '/' + data.totals.models);
  }

  function renderChips() {
    var data = state.data;
    var box = $('modality-chips');
    clear(box);

    var groups = [{ key: 'all', label: t('all'), total: data.totals.models }];
    data.modalities.forEach(function (m) {
      groups.push({ key: m.key, label: m.label, total: m.total });
    });

    groups.forEach(function (group) {
      var chip = el('button', 'chip' + (state.modality === group.key ? ' active' : ''));
      chip.type = 'button';
      chip.appendChild(document.createTextNode(group.label));
      chip.appendChild(el('span', 'chip-count', group.total));
      chip.addEventListener('click', function () {
        state.modality = group.key;
        render();
      });
      box.appendChild(chip);
    });
  }

  function renderKpis() {
    var models = visibleModels();
    var providers = visibleProviders(models);
    var stats = computeStats(models);
    var healthy = providers.filter(function (p) {
      return p.fully_available;
    }).length;
    var inflight = providers.reduce(function (sum, p) {
      return sum + p.inflight;
    }, 0);

    var box = $('kpis');
    clear(box);

    var cards = [
      {
        label: t('kpi.models'),
        value: stats.available,
        sub: t('kpi.modelsAvailable') + ' / ' + stats.total,
      },
      {
        label: t('kpi.providers'),
        value: healthy,
        sub: t('kpi.providersHealthy') + ' / ' + providers.length,
      },
      {
        label: t('kpi.links'),
        value: stats.linksAvailable,
        sub: t('kpi.linksAvailable') + ' / ' + stats.links,
      },
      {
        label: t('kpi.circuitOpen'),
        value: stats.circuitOpen,
        sub: t('kpi.inflight') + ' ' + inflight,
      },
    ];

    cards.forEach(function (card) {
      var node = el('div', 'kpi');
      node.appendChild(el('div', 'k-label', card.label));
      node.appendChild(el('div', 'k-value', card.value));
      node.appendChild(el('div', 'k-sub', card.sub));
      box.appendChild(node);
    });
  }

  /* ── 图谱 ── */

  function groupModels(models) {
    var order = [];
    var map = {};
    models.forEach(function (m) {
      var key = m.catalog_group;
      if (!map[key]) {
        map[key] = { key: key, label: m.group_label, models: [] };
        order.push(map[key]);
      }
      map[key].models.push(m);
    });
    return order;
  }

  /* 图谱可用宽度:量图谱容器本身。.graph-wrap 不内滚
     (overflow:visible),clientWidth 不受滚动条影响,测完即定。 */
  function containerWidth(wrap) {
    return wrap.clientWidth || COL_MIN * 2 + GAP_MIN + PAD_X * 2;
  }

  /* 紧凑判定统一量 #main(各面板共同的父容器),两处共用一个结果:
     各自量容器会因滚动条占位等差 ~15px,曾出现
     「图谱紧凑、供应商仍表格」的夹缝宽度。 */
  function pageCompact() {
    var main = $('main');
    var w = main && !main.hidden ? main.clientWidth : 0;
    if (!w && document.documentElement) w = document.documentElement.clientWidth;
    if (!w) w = window.innerWidth || 0;
    return w > 0 && w < COMPACT_W;
  }

  /* 供应商声明顺序:后端 providers 列表本身就是声明顺序,下标即序号 */
  function providerOrderMap() {
    var map = {};
    if (!state.data) return map;
    state.data.providers.forEach(function (p, i) {
      map[p.name] = i + 1;
    });
    return map;
  }

  /* 从事件目标往上找带 data-model / data-provider 的节点。
     手写父链遍历,不用 closest():SVG 元素上的选择器匹配各家实现有差异。 */
  function graphNodeOf(target) {
    var node = target;
    while (node && node.getAttribute) {
      var modelName = node.getAttribute('data-model');
      if (modelName) return { model: modelName };
      var providerName = node.getAttribute('data-provider');
      if (providerName) return { provider: providerName };
      node = node.parentNode;
    }
    return null;
  }

  /* 图谱交互用事件委托,监听器挂在不会重建的容器上。
   * 逐节点挂监听的话,自动刷新正好把 SVG 重建在「按下」和「抬起」之间时,
   * 那一下点击就丢了(实测复现过)。 */
  function bindGraphEvents() {
    var wrap = $('graph-wrap');

    wrap.addEventListener('click', function (ev) {
      var hit = graphNodeOf(ev.target);
      if (!hit) return;
      if (hit.model) {
        openDetail(hit.model);
        return;
      }
      toggleProvider(hit.provider);
    });

    wrap.addEventListener('mouseover', function (ev) {
      var hit = graphNodeOf(ev.target);
      if (!hit) return;
      var key = hit.model ? 'model:' + hit.model : 'provider:' + hit.provider;
      if (state.highlight === key) return;
      state.highlight = key;
      applyHighlight();
    });

    wrap.addEventListener('mouseout', function (ev) {
      var to = ev.relatedTarget;
      if (to && graphNodeOf(to)) return;
      state.highlight = null;
      applyHighlight();
    });
  }

  function renderGraph() {
    var models = visibleModels();
    var providers = visibleProviders(models);
    var wrap = $('graph-wrap');
    // 自动刷新每 10 秒重画一次,不保住滚动位置用户会被弹回顶部
    var keepTop = wrap.scrollTop;
    var keepLeft = wrap.scrollLeft;
    clear(wrap);

    $('graph-hint').textContent = t('graph.hint');

    var legend = $('graph-legend');
    clear(legend);
    legend.appendChild(legendItem('ok', t('graph.available')));
    legend.appendChild(legendItem('bad', t('graph.unavailable')));
    legend.appendChild(legendItem('warn', t('graph.circuitOpen')));

    if (!models.length) {
      wrap.appendChild(el('div', 'empty', t('graph.empty')));
      wrap.scrollTop = keepTop;
      return;
    }

    graphIndex = { modelLinks: {}, providerLinks: {}, linkById: {} };

    var groups = groupModels(models);
    var avail = containerWidth(wrap);
    // 窄屏走紧凑卡片布局,两列图谱只在大容器里画。
    // compact class 顺带把容器改成 overflow:visible:整页滚动而不是内部滚,
    // 宽屏必须保留 overflow-y:scroll 常驻占位防 clientWidth 抖动。
    var compact = pageCompact();
    wrap.classList.toggle('compact', compact);
    $('graph-panel').classList.toggle('compact', compact);
    if (compact) {
      renderCompactGraph(wrap, models, groups, keepTop);
      return;
    }
    // 两列按容器宽度自适应,余下宽度全给中间的连线区;
    // 容器太窄时保底宽度,横向滚动而不是把字挤没
    var colW = Math.round(Math.min(COL_MAX, Math.max(COL_MIN, avail * COL_RATIO)));
    var modelW = colW;
    var provW = colW;
    var gap = Math.max(GAP_MIN, avail - modelW - provW - PAD_X * 2 - 12);
    var width = Math.max(avail, PAD_X * 2 + 12 + modelW + gap + provW);
    var modelX = PAD_X + 6;
    var provX = modelX + modelW + gap;

    // ── 模型行位置:模态分块,标签独占一行 ──
    var modelRows = [];
    var y = HEADER_H;
    groups.forEach(function (group, gi) {
      if (gi > 0) y += GROUP_GAP;
      group.ruleY = y - 6;
      group.labelY = y + GROUP_LABEL_H / 2;
      y += GROUP_LABEL_H;
      group.bandY = y - 3;
      var start = y;
      group.models.forEach(function (m) {
        modelRows.push({ model: m, y: y });
        y += ROW_H;
      });
      group.bandH = y - start + 6;
    });
    var contentHeight = y + TOP_PAD;

    // ── 供应商位置:按所连模型的平均位置排序,再解冲突 ──
    var yOf = {};
    modelRows.forEach(function (row) {
      yOf[row.model.name] = row.y;
    });
    var slots = providers.map(function (p) {
      var sum = 0;
      var n = 0;
      p.models.forEach(function (name) {
        if (name in yOf) {
          sum += yOf[name];
          n += 1;
        }
      });
      return { provider: p, target: n ? sum / n : 0, y: 0 };
    });
    slots.sort(function (a, b) {
      return a.target - b.target;
    });
    var prev = -1e9;
    slots.forEach(function (slot) {
      slot.y = Math.max(HEADER_H + 6, Math.max(slot.target, prev + PROV_MIN_GAP));
      prev = slot.y;
    });
    var overflow = prev + NODE_H + TOP_PAD - contentHeight;
    if (overflow > 0) {
      slots.forEach(function (slot) {
        slot.y = Math.max(HEADER_H + 6, slot.y - overflow);
      });
    }
    var provPos = {};
    slots.forEach(function (slot) {
      provPos[slot.provider.name] = slot.y;
    });

    var height = Math.max(contentHeight, prev + NODE_H + TOP_PAD);

    var svg = svgEl('svg', {
      width: width,
      height: height,
      viewBox: '0 0 ' + width + ' ' + height,
    });

    // ── 列头:左模型,右供应商 ──
    var headY = HEADER_H / 2 + 3;
    var headLeft = svgEl('text', { class: 'col-head', x: modelX, y: headY });
    headLeft.textContent = t('graph.columnModels') + ' (' + models.length + ')';
    svg.appendChild(headLeft);
    var headRight = svgEl('text', {
      class: 'col-head',
      x: provX + provW,
      y: headY,
      'text-anchor': 'end',
    });
    headRight.textContent = t('graph.columnProviders') + ' (' + providers.length + ')';
    svg.appendChild(headRight);

    // ── 模态分组背景带 + 标签与分隔线 ──
    groups.forEach(function (group, gi) {
      // 背景带横贯整个画布:宽屏下中间的连线区就不会是一片死白,
      // 每个模态一条泳道,供应商落在哪条泳道一眼看出它主要服务哪个模态
      svg.appendChild(
        svgEl('rect', {
          class: 'group-band',
          x: PAD_X,
          y: group.bandY,
          width: width - PAD_X * 2,
          height: group.bandH,
          rx: 6,
        })
      );
      if (gi > 0) {
        svg.appendChild(
          svgEl('line', {
            class: 'group-rule',
            x1: PAD_X,
            y1: group.ruleY,
            x2: width - PAD_X,
            y2: group.ruleY,
          })
        );
      }
      var text = svgEl('text', { class: 'group-label', x: modelX, y: group.labelY });
      text.textContent = group.label;
      svg.appendChild(text);
    });

    // ── 连线 ──
    // 控制点收在两端,中间留长直线:连线区很宽时曲线不会糊成一片
    var ctrl = Math.min(gap * 0.45, 240);
    modelRows.forEach(function (row) {
      row.model.channels.forEach(function (channel) {
        if (!(channel.name in provPos)) return;
        var y1 = row.y + NODE_H / 2;
        var y2 = provPos[channel.name] + NODE_H / 2;
        var x1 = modelX + modelW;
        var x2 = provX;
        var id = row.model.name + '||' + channel.name;
        var path = svgEl('path', {
          class: 'link ' + (channel.circuit_open ? 'broken' : channel.available ? 'ok' : 'bad'),
          d:
            'M ' + x1 + ' ' + y1 +
            ' C ' + (x1 + ctrl) + ' ' + y1 +
            ', ' + (x2 - ctrl) + ' ' + y2 +
            ', ' + x2 + ' ' + y2,
          'stroke-width': channel.weight > 1 ? 2.2 : 1.2,
          'stroke-opacity': channel.available ? 0.45 : 0.38,
          'data-owner': id,
        });
        graphIndex.linkById[id] = path;
        if (!graphIndex.modelLinks[row.model.name]) graphIndex.modelLinks[row.model.name] = [];
        if (!graphIndex.providerLinks[channel.name]) graphIndex.providerLinks[channel.name] = [];
        graphIndex.modelLinks[row.model.name].push(channel.name);
        graphIndex.providerLinks[channel.name].push(row.model.name);
        svg.appendChild(path);
      });
    });

    // ── 供应商节点 ──
    var orderOf = providerOrderMap();
    // 右侧要给「可用/总数」留位,别让名字压到数字上
    var countReserve = 36;
    slots.forEach(function (slot) {
      var p = slot.provider;
      var order = orderOf[p.name] || 0;
      var g = svgEl('g', { class: 'node prov-node' });
      g.setAttribute('data-provider', p.name);
      g.appendChild(
        svgEl('rect', { x: provX, y: slot.y, width: provW, height: NODE_H, rx: 4 })
      );
      // 声明顺序徽标:与供应商表「顺序」列一致
      var seq = svgEl('text', { class: 'n-order', x: provX + 9, y: slot.y + NODE_H / 2 });
      seq.textContent = '#' + order;
      g.appendChild(seq);
      g.appendChild(
        svgEl('circle', {
          cx: provX + 33,
          cy: slot.y + NODE_H / 2,
          r: 3.5,
          class: 'badge ' + (p.fully_available ? 'ok' : p.available_model_count ? 'warn' : 'bad'),
        })
      );
      var nameX = provX + 43;
      var nameText = ellipsize(p.name, Math.floor((provW - 43 - countReserve) / CHAR_W));
      var label = svgEl('text', { x: nameX, y: slot.y + NODE_H / 2 });
      label.textContent = nameText;
      g.appendChild(label);
      // 中文标签跟在名字后面,列够宽才放,免得跟右侧计数打架
      var labelX = nameX + textWidth(nameText) * CHAR_W + 14;
      var labelRoom = provW - 9 - countReserve - (labelX - provX);
      if (labelRoom > 56) {
        var sub = svgEl('text', { class: 'n-sub', x: labelX, y: slot.y + NODE_H / 2 });
        sub.textContent = ellipsize(p.label, Math.floor(labelRoom / CHAR_W));
        g.appendChild(sub);
      }
      var count = svgEl('text', {
        x: provX + provW - 9,
        y: slot.y + NODE_H / 2,
        'text-anchor': 'end',
        class: 'n-sub',
      });
      count.textContent = p.available_model_count + '/' + p.model_count;
      g.appendChild(count);

      var tip = svgEl('title');
      tip.textContent = '#' + order + ' ' + p.name + ' · ' + p.label;
      g.appendChild(tip);

      svg.appendChild(g);
    });

    // ── 模型节点 ──
    modelRows.forEach(function (row) {
      var m = row.model;
      var g = svgEl('g', { class: 'node model-node' });
      g.setAttribute('data-model', m.name);
      g.appendChild(
        svgEl('rect', { x: modelX, y: row.y, width: modelW, height: NODE_H, rx: 4 })
      );
      // 品牌/模态图标(icons.js 查表),右下角叠可用性徽标
      var icon = modelIcon(m);
      var pic = svgEl('svg', {
        class: 'm-icon',
        x: modelX + 5,
        y: row.y + 3,
        width: 14,
        height: 14,
        viewBox: icon.vb,
      });
      if (icon.f) pic.setAttribute('fill', icon.f);
      pic.innerHTML = icon.b;
      g.appendChild(pic);
      g.appendChild(
        svgEl('circle', {
          cx: modelX + 17.5,
          cy: row.y + 15.5,
          r: 3.2,
          class: 'badge ' + (m.available ? 'ok' : 'bad'),
        })
      );
      var label = svgEl('text', { x: modelX + 26, y: row.y + NODE_H / 2 });
      label.textContent = ellipsize(m.display_name, Math.floor((modelW - 26 - countReserve) / CHAR_W));
      g.appendChild(label);
      var count = svgEl('text', {
        x: modelX + modelW - 9,
        y: row.y + NODE_H / 2,
        'text-anchor': 'end',
        class: 'n-sub',
      });
      count.textContent =
        m.channels.filter(function (c) {
          return c.available;
        }).length + '/' + m.channels.length;
      g.appendChild(count);

      var tip = svgEl('title');
      tip.textContent = m.display_name + ' · ' + m.name;
      g.appendChild(tip);

      svg.appendChild(g);
    });

    wrap.appendChild(svg);
    wrap.scrollTop = keepTop;
    wrap.scrollLeft = keepLeft;
    applyHighlight();
  }

  /* 紧凑图谱:整宽模型卡片,通道以内联 chip 呈现。
     chip 走与供应商节点相同的 data-provider 语义,
     点击/悬停高亮逻辑复用 graphIndex,不单独写一套。 */
  function renderCompactGraph(wrap, models, groups, keepTop) {
    var avail = containerWidth(wrap);
    var nodeW = avail - PAD_X * 2 - 12;
    var modelX = PAD_X + 6;
    var orderOf = providerOrderMap();
    var countReserve = 36;

    // 通道 chip 按可用宽度换行,先量一遍拿行数,卡片高度跟着走
    function layoutChips(m) {
      var chips = [];
      var x = 0;
      var lines = m.channels.length ? 1 : 0;
      m.channels.forEach(function (c) {
        var w = Math.min(CHIP_MAX_W, Math.ceil(textWidth(c.name) * CHIP_CHAR_W) + 22);
        if (x > 0 && x + w > nodeW - 12) {
          lines += 1;
          x = 0;
        }
        chips.push({ channel: c, w: w, line: lines - 1, x: x });
        x += w + CHIP_GAP;
      });
      return { chips: chips, nodeH: NAME_H + 4 + (lines ? lines * CHIP_LINE : 2) };
    }

    var rows = [];
    var y = HEADER_H;
    groups.forEach(function (group, gi) {
      if (gi > 0) y += GROUP_GAP;
      group.ruleY = y - 6;
      group.labelY = y + GROUP_LABEL_H / 2;
      y += GROUP_LABEL_H;
      group.bandY = y - 3;
      var start = y;
      group.models.forEach(function (m) {
        var lay = layoutChips(m);
        rows.push({ model: m, lay: lay, y: y });
        y += lay.nodeH + ROW_GAP;
      });
      group.bandH = y - ROW_GAP - start + 6;
    });
    var height = y + TOP_PAD;

    var svg = svgEl('svg', {
      width: avail,
      height: height,
      viewBox: '0 0 ' + avail + ' ' + height,
    });

    var headLeft = svgEl('text', { class: 'col-head', x: modelX, y: HEADER_H / 2 + 3 });
    headLeft.textContent = t('graph.columnModels') + ' (' + models.length + ')';
    svg.appendChild(headLeft);

    groups.forEach(function (group, gi) {
      svg.appendChild(
        svgEl('rect', {
          class: 'group-band',
          x: PAD_X,
          y: group.bandY,
          width: avail - PAD_X * 2,
          height: group.bandH,
          rx: 6,
        })
      );
      if (gi > 0) {
        svg.appendChild(
          svgEl('line', {
            class: 'group-rule',
            x1: PAD_X,
            y1: group.ruleY,
            x2: avail - PAD_X,
            y2: group.ruleY,
          })
        );
      }
      var text = svgEl('text', { class: 'group-label', x: modelX, y: group.labelY });
      text.textContent = group.label;
      svg.appendChild(text);
    });

    rows.forEach(function (row) {
      var m = row.model;
      var g = svgEl('g', { class: 'node model-node' });
      g.setAttribute('data-model', m.name);
      g.appendChild(
        svgEl('rect', { x: modelX, y: row.y, width: nodeW, height: row.lay.nodeH, rx: 6 })
      );
      var icon = modelIcon(m);
      var pic = svgEl('svg', {
        class: 'm-icon',
        x: modelX + 5,
        y: row.y + 5,
        width: 14,
        height: 14,
        viewBox: icon.vb,
      });
      if (icon.f) pic.setAttribute('fill', icon.f);
      pic.innerHTML = icon.b;
      g.appendChild(pic);
      g.appendChild(
        svgEl('circle', {
          cx: modelX + 17.5,
          cy: row.y + 17.5,
          r: 3.2,
          class: 'badge ' + (m.available ? 'ok' : 'bad'),
        })
      );
      var label = svgEl('text', { x: modelX + 26, y: row.y + NAME_H / 2 });
      label.textContent = ellipsize(m.display_name, Math.floor((nodeW - 26 - countReserve) / CHAR_W));
      g.appendChild(label);
      var count = svgEl('text', {
        x: modelX + nodeW - 9,
        y: row.y + NAME_H / 2,
        'text-anchor': 'end',
        class: 'n-sub',
      });
      count.textContent =
        m.channels.filter(function (c) {
          return c.available;
        }).length + '/' + m.channels.length;
      g.appendChild(count);

      row.lay.chips.forEach(function (chip) {
        var c = chip.channel;
        if (!graphIndex.modelLinks[m.name]) graphIndex.modelLinks[m.name] = [];
        if (!graphIndex.providerLinks[c.name]) graphIndex.providerLinks[c.name] = [];
        graphIndex.modelLinks[m.name].push(c.name);
        graphIndex.providerLinks[c.name].push(m.name);

        var cx = modelX + 6 + chip.x;
        var cy = row.y + NAME_H + 2 + chip.line * CHIP_LINE;
        var cls = c.circuit_open ? 'broken' : c.available ? 'ok' : 'bad';
        var cg = svgEl('g', { class: 'node chip-node' });
        cg.setAttribute('data-provider', c.name);
        cg.appendChild(
          svgEl('rect', { class: 'chip-box ' + cls, x: cx, y: cy, width: chip.w, height: CHIP_H, rx: 8 })
        );
        cg.appendChild(
          svgEl('circle', {
            cx: cx + 8,
            cy: cy + CHIP_H / 2,
            r: 2.4,
            class: 'badge ' + (c.available ? 'ok' : 'bad'),
          })
        );
        var ct = svgEl('text', { class: 'chip-text', x: cx + 15, y: cy + CHIP_H / 2 });
        ct.textContent = ellipsize(c.name, Math.floor((chip.w - 19) / CHIP_CHAR_W));
        cg.appendChild(ct);
        var tip = svgEl('title');
        tip.textContent =
          '#' + (orderOf[c.name] || 0) + ' ' + c.name + (c.vendor_model ? ' · ' + c.vendor_model : '');
        cg.appendChild(tip);
        g.appendChild(cg);
      });

      var tip = svgEl('title');
      tip.textContent = m.display_name + ' · ' + m.name;
      g.appendChild(tip);
      svg.appendChild(g);
    });

    wrap.appendChild(svg);
    wrap.scrollTop = keepTop;
    applyHighlight();
  }

  /* 模型 → 图标:后端给的 key 直查,落空时按模态/通用回落(icons.js 未加载时给空表) */
  function modelIcon(m) {
    var table = window.RH_MODEL_ICONS || {};
    return (
      table[m.icon] ||
      table['mod:' + m.catalog_group] ||
      table['mod:other'] || { vb: '0 0 24 24', f: '', b: '' }
    );
  }

  function legendItem(kind, label) {
    var span = el('span');
    span.appendChild(el('span', 'dot ' + kind));
    span.appendChild(document.createTextNode(label));
    return span;
  }

  /* ── 高亮 / 过滤 ── */

  function applyHighlight() {
    var wrap = $('graph-wrap');
    if (!wrap) return;
    var links = wrap.querySelectorAll('.link');
    var nodes = wrap.querySelectorAll('.node');

    var modelName = null;
    var providerName = null;
    if (state.highlight) {
      var parts = state.highlight.split(':');
      if (parts[0] === 'model') modelName = parts[1];
      if (parts[0] === 'provider') providerName = parts[1];
    }
    var filterProvider = state.provider;

    var hot = {};
    var keepModels = {};
    var keepProviders = {};
    var anyFocus = false;

    if (modelName) {
      anyFocus = true;
      keepModels[modelName] = true;
      (graphIndex.modelLinks[modelName] || []).forEach(function (p) {
        hot[modelName + '||' + p] = true;
        keepProviders[p] = true;
      });
    }
    if (providerName) {
      anyFocus = true;
      keepProviders[providerName] = true;
      (graphIndex.providerLinks[providerName] || []).forEach(function (m) {
        hot[m + '||' + providerName] = true;
        keepModels[m] = true;
      });
    }
    if (filterProvider) {
      anyFocus = true;
      keepProviders[filterProvider] = true;
      (graphIndex.providerLinks[filterProvider] || []).forEach(function (m) {
        hot[m + '||' + filterProvider] = true;
        keepModels[m] = true;
      });
    }

    for (var i = 0; i < links.length; i++) {
      links[i].classList.remove('dim', 'hot');
      if (!anyFocus) continue;
      links[i].classList.add(hot[links[i].getAttribute('data-owner')] ? 'hot' : 'dim');
    }

    for (var j = 0; j < nodes.length; j++) {
      var node = nodes[j];
      node.classList.remove('dim', 'sel');
      if (anyFocus) {
        var mName = node.getAttribute('data-model');
        var pName = node.getAttribute('data-provider');
        var keep = mName ? !!keepModels[mName] : !!keepProviders[pName];
        if (!keep) node.classList.add('dim');
      }
      if (state.selectedModel && node.getAttribute('data-model') === state.selectedModel) {
        node.classList.add('sel');
      }
    }
  }

  function toggleProvider(name) {
    state.provider = state.provider === name ? null : name;
    render();
  }

  /* ── 供应商表 ── */

  function renderProviderTable() {
    var models = visibleModels();
    var providers = visibleProviders(models);
    var box = $('provider-table');
    var keepTop = box.scrollTop;
    var keepLeft = box.scrollLeft;
    clear(box);

    // 窄屏:8 列表格放不下,改走卡片列表(同样走 compact class 去掉内部滚动)
    var compact = pageCompact();
    box.classList.toggle('compact', compact);

    if (!providers.length) {
      box.appendChild(el('div', 'empty', t('empty.providers')));
      box.scrollTop = keepTop;
      return;
    }

    var modelByName = {};
    state.data.models.forEach(function (m) {
      modelByName[m.name] = m;
    });
    var orderOf = providerOrderMap();

    if (compact) {
      renderProviderCards(box, providers, orderOf, modelByName);
      return;
    }

    var table = el('table', 'providers');
    var thead = el('thead');
    var hr = el('tr');
    [
      t('table.order'),
      t('table.provider'),
      t('table.available'),
      t('table.inherited'),
      t('table.weight'),
      t('table.load'),
      t('table.capability'),
      t('table.models'),
    ].forEach(function (label) {
      hr.appendChild(el('th', null, label));
    });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el('tbody');
    providers.forEach(function (p) {
      var tr = el('tr');
      if (state.provider === p.name) tr.classList.add('sel');
      else if (state.provider) tr.classList.add('dim');

      tr.appendChild(el('td', 'num order', '#' + (orderOf[p.name] || 0)));

      var nameCell = el('td');
      var nameBox = el('div', 'pname');
      nameBox.appendChild(el('div', 'p-title', p.name));
      nameBox.appendChild(el('div', 'p-sub', p.label));
      nameCell.appendChild(nameBox);
      tr.appendChild(nameCell);

      var availCell = el('td');
      availCell.appendChild(
        el('span', 'pill ' + (p.fully_available ? 'ok' : 'bad'),
          p.available_model_count + ' / ' + p.model_count)
      );
      tr.appendChild(availCell);

      tr.appendChild(el('td', 'num', p.model_count));
      tr.appendChild(el('td', 'num', p.weight));
      tr.appendChild(el('td', 'num', p.inflight + ' / ' + p.concurrency_limit));

      var capCell = el('td');
      if (p.supports_remote_cancel) {
        capCell.appendChild(el('span', 'pill ok', t('provider.remoteCancel')));
      }
      if (p.circuit_open_models.length) {
        capCell.appendChild(
          el('span', 'pill warn', t('provider.circuitOpen') + ' ' + p.circuit_open_models.length)
        );
      }
      if (p.failure_total > 0) {
        capCell.appendChild(el('span', 'pill bad', t('provider.failureCount', { n: p.failure_total })));
      }
      tr.appendChild(capCell);

      var tagCell = el('td');
      var tags = el('div', 'tags');
      p.models.slice(0, 8).forEach(function (name) {
        var m = modelByName[name];
        tags.appendChild(el('span', 'tag ' + (m && m.available ? 'ok' : 'bad'), name));
      });
      if (p.models.length > 8) {
        tags.appendChild(el('span', 'tag more', '+' + (p.models.length - 8)));
      }
      tagCell.appendChild(tags);
      tr.appendChild(tagCell);

      tr.addEventListener('click', function () {
        toggleProvider(p.name);
      });
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    box.appendChild(table);
    box.scrollTop = keepTop;
    box.scrollLeft = keepLeft;
  }

  /* 窄屏供应商卡片:表格的 8 列折成「头行 + 统计行 + 能力 pill + 模型 tag」。
     点击语义与表格行一致(toggleProvider)。 */
  function renderProviderCards(box, providers, orderOf, modelByName) {
    var list = el('div', 'p-cards');
    providers.forEach(function (p) {
      var card = el('div', 'p-card');
      if (state.provider === p.name) card.classList.add('sel');
      else if (state.provider) card.classList.add('dim');

      var head = el('div', 'p-card-head');
      head.appendChild(el('span', 'order', '#' + (orderOf[p.name] || 0)));
      var nameBox = el('div', 'pname');
      nameBox.appendChild(el('div', 'p-title', p.name));
      nameBox.appendChild(el('div', 'p-sub', p.label));
      head.appendChild(nameBox);
      head.appendChild(
        el(
          'span',
          'pill ' + (p.fully_available ? 'ok' : 'bad'),
          p.available_model_count + ' / ' + p.model_count
        )
      );
      card.appendChild(head);

      var stats = el('div', 'p-stats');
      stats.appendChild(el('span', null, t('table.inherited') + ' ' + p.model_count));
      stats.appendChild(el('span', null, t('table.weight') + ' ' + p.weight));
      stats.appendChild(
        el('span', null, t('table.load') + ' ' + p.inflight + ' / ' + p.concurrency_limit)
      );
      card.appendChild(stats);

      var caps = el('div', 'p-caps');
      if (p.supports_remote_cancel) {
        caps.appendChild(el('span', 'pill ok', t('provider.remoteCancel')));
      }
      if (p.circuit_open_models.length) {
        caps.appendChild(
          el('span', 'pill warn', t('provider.circuitOpen') + ' ' + p.circuit_open_models.length)
        );
      }
      if (p.failure_total > 0) {
        caps.appendChild(el('span', 'pill bad', t('provider.failureCount', { n: p.failure_total })));
      }
      if (caps.children.length) card.appendChild(caps);

      var tags = el('div', 'tags');
      p.models.slice(0, 8).forEach(function (name) {
        var m = modelByName[name];
        tags.appendChild(el('span', 'tag ' + (m && m.available ? 'ok' : 'bad'), name));
      });
      if (p.models.length > 8) {
        tags.appendChild(el('span', 'tag more', '+' + (p.models.length - 8)));
      }
      card.appendChild(tags);

      card.addEventListener('click', function () {
        toggleProvider(p.name);
      });
      list.appendChild(card);
    });
    box.appendChild(list);
  }

  /* ── 详情抽屉 ── */

  function openDetail(name) {
    state.selectedModel = name;
    renderDetail();
    $('detail').hidden = false;
    $('scrim').hidden = false;
    applyHighlight();
  }

  function closeDetail() {
    state.selectedModel = null;
    $('detail').hidden = true;
    $('scrim').hidden = true;
    applyHighlight();
  }

  function renderDetail() {
    if (!state.selectedModel) return;
    var model = null;
    state.data.models.forEach(function (m) {
      if (m.name === state.selectedModel) model = m;
    });
    if (!model) return;

    $('detail-name').textContent = model.display_name;
    $('detail-id').textContent = model.name;

    var body = $('detail-body');
    clear(body);

    function row(label, value) {
      var r = el('div', 'd-row');
      r.appendChild(el('div', 'd-label', label));
      r.appendChild(el('div', 'd-value', value));
      body.appendChild(r);
    }

    row(t('model.status'), model.available ? t('model.available') : t('model.unavailable'));
    row(t('model.modality'), model.group_label);
    row(t('model.priority'), model.priority);
    row(
      t('model.points'),
      model.point_min === model.point_max
        ? model.point_cost
        : t('model.pointsRange', { min: model.point_min, max: model.point_max })
    );
    row(t('model.mode'), model.execution_mode);
    if (model.backend) row(t('model.backend'), model.backend);

    if (!model.available && model.unavailable_reason) {
      body.appendChild(el('div', 'd-reason', t('model.reason') + ': ' + model.unavailable_reason));
    }

    body.appendChild(el('div', 'd-section', t('model.order')));
    if (!model.channels.length) {
      body.appendChild(el('div', 'd-value', t('model.noChannel')));
    }
    var chain = el('div', 'chain');
    model.channels.forEach(function (c, i) {
      var kind = c.circuit_open ? 'broken' : c.available ? 'ok' : 'bad';
      var item = el('div', 'chain-item ' + kind);
      item.appendChild(el('div', 'chain-idx', String(i + 1)));
      var main = el('div', 'chain-main');
      main.appendChild(el('div', 'chain-name', c.name));
      var sub = [];
      if (c.vendor_model) sub.push(c.vendor_model);
      if (c.weight > 1) sub.push(t('provider.weight') + ' ×' + c.weight);
      if (c.failure_count > 0) sub.push(t('provider.failureCount', { n: c.failure_count }));
      if (c.supports_remote_cancel) sub.push(t('provider.remoteCancel'));
      if (sub.length) main.appendChild(el('div', 'chain-sub', sub.join(' · ')));
      item.appendChild(main);
      chain.appendChild(item);
    });
    body.appendChild(chain);
  }

  /* ── 启动 ── */

  function bindStatic() {
    $('title').textContent = t('title');
    document.title = t('title');
    $('subtitle').textContent = t('subtitle');
    $('search').placeholder = t('search');
    $('auto-refresh-label').textContent = t('autoRefresh');
    $('only-issue-label').textContent = t('onlyIssue');
    $('btn-refresh').textContent = t('refresh');
    $('sec-graph').textContent = t('sections.graph');
    $('sec-providers').textContent = t('sections.providers');
    $('detail-close').textContent = t('model.close');

    $('btn-refresh').addEventListener('click', load);
    $('detail-close').addEventListener('click', closeDetail);
    $('scrim').addEventListener('click', closeDetail);
    bindGraphEvents();
    $('search').addEventListener('input', function (ev) {
      state.keyword = ev.target.value || '';
      render();
    });
    $('only-issue').addEventListener('change', function (ev) {
      state.onlyIssue = ev.target.checked;
      render();
    });
    $('auto-refresh').addEventListener('change', function (ev) {
      if (ev.target.checked) startTimer();
      else stopTimer();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeDetail();
    });
    // 容器变宽变窄时重排图谱与供应商表(紧凑/宽屏模式随容器宽度切换)
    var resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () {
        if (state.data) {
          renderGraph();
          renderProviderTable();
        }
      }, 160);
    });
  }

  function startTimer() {
    stopTimer();
    state.timer = window.setInterval(load, REFRESH_MS);
  }

  function stopTimer() {
    if (state.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  }

  function boot() {
    bindStatic();
    load();
    startTimer();
    if (window.GsHubPlugin && typeof GsHubPlugin.onTheme === 'function') {
      GsHubPlugin.onTheme(function () {
        if (state.data) render();
      });
    }
  }

  if (window.GsHubPlugin && GsHubPlugin.ready) {
    GsHubPlugin.ready.then(boot);
  } else {
    window.addEventListener('load', boot);
  }
})();
