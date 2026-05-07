
(function(){
  'use strict';

  // ---------- bootstrap -------------------------------------------------
  const params = new URLSearchParams(location.search);
  const pid = parseInt(params.get('id'), 10);
  if (!pid) {
    document.getElementById('player-name').textContent = 'Missing ?id=';
    return;
  }
  fetch('../data/rcc/' + pid + '.json')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(boot)
    .catch(err => {
      document.getElementById('player-name').textContent =
        'Failed to load ' + pid + '.json (' + err + ')';
    });

  // ---------- helpers ---------------------------------------------------
  function el(tag, attrs, kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else if (k === 'style') e.setAttribute('style', attrs[k]);
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), attrs[k]);
      else e.setAttribute(k, attrs[k]);
    }
    if (kids) for (const c of kids) {
      if (c == null) continue;
      if (typeof c === 'string' || typeof c === 'number') {
        e.appendChild(document.createTextNode(String(c)));
      } else if (c instanceof Node) {
        e.appendChild(c);
      }
    }
    return e;
  }
  function fmtN(n, dp) {
    if (n == null || isNaN(n)) return '—';
    if (dp == null) dp = 1;
    return n.toFixed(dp);
  }
  function date_yyyymmdd(s) {
    const m = (s || '').split('/');
    if (m.length !== 3) return '';
    return m[2] + m[1] + m[0];
  }

  // ---------- bucket maths (mirror _bbb_buckets.py) --------------------
  const PHASE_50 = ['1-10','11-20','21-30','31-40','41-50'];
  const PI_LABELS = ['0-10','11-20','21-50','51-100','101+'];
  function phaseOf(over_no) {
    const o = (over_no || 0) + 1;
    if (o <= 10) return '1-10';
    if (o <= 20) return '11-20';
    if (o <= 30) return '21-30';
    if (o <= 40) return '31-40';
    return '41-50';
  }
  function piBucket(legalSoFar) {
    if (legalSoFar < 10) return '0-10';
    if (legalSoFar < 20) return '11-20';
    if (legalSoFar < 50) return '21-50';
    if (legalSoFar < 100) return '51-100';
    return '101+';
  }
  function emptyStats() {
    return {balls:0, legal:0, runs_bat:0, runs_extra:0,
      wickets:0, dots:0, fours:0, sixes:0, innings:new Set()};
  }
  function accum(s, b) {
    s.balls++;
    const legal = b.is_legal_ball|0;
    const rb = b.runs_bat|0, re = b.runs_extra|0;
    s.runs_bat += rb; s.runs_extra += re;
    if (legal) {
      s.legal++;
      if (rb === 0 && re === 0) s.dots++;
    }
    if (rb === 4) s.fours++;
    else if (rb === 6) s.sixes++;
    if (b.wicket|0) s.wickets++;
    if (b.match_id != null && b.innings_seq != null) {
      s.innings.add(b.match_id + ':' + b.innings_seq);
    }
  }
  function fin(s) {
    const out = Object.assign({}, s);
    out.innings = s.innings.size;
    const total = s.runs_bat + s.runs_extra;
    out.runs = total;
    out.sr = s.legal ? s.runs_bat / s.legal * 100 : null;
    out.econ = s.legal ? total / s.legal * 6 : null;
    out.dot_pct = s.legal ? s.dots / s.legal * 100 : null;
    out.bowl_avg = s.wickets ? total / s.wickets : null;
    out.bat_avg  = s.wickets ? s.runs_bat / s.wickets : null;
    return out;
  }
  function byPhase(balls) {
    const out = {}; PHASE_50.forEach(p => out[p] = emptyStats());
    balls.forEach(b => accum(out[phaseOf(b.over_no)], b));
    const r = {}; for (const k in out) r[k] = fin(out[k]); return r;
  }
  function byPlayerInns(balls) {
    const out = {}; PI_LABELS.forEach(p => out[p] = emptyStats());
    const counters = {};
    balls.forEach(b => {
      const k = b.match_id + ':' + b.innings_seq;
      const so = counters[k] || 0;
      accum(out[piBucket(so)], b);
      if (b.is_legal_ball|0) counters[k] = so + 1;
    });
    const r = {}; for (const k in out) r[k] = fin(out[k]); return r;
  }
  function byKey(balls, fn, keys) {
    const out = {}; keys.forEach(k => out[k] = emptyStats());
    balls.forEach(b => { const k = fn(b); if (out[k]) accum(out[k], b); });
    const r = {}; for (const k in out) r[k] = fin(out[k]); return r;
  }
  function detectSpells(balls) {
    const byInn = {};
    balls.forEach(b => {
      const k = b.match_id + ':' + b.innings_seq;
      (byInn[k] = byInn[k] || []).push(b);
    });
    const all = [];
    for (const k in byInn) {
      const list = byInn[k]
        .slice().sort((x,y) => (x.over_no - y.over_no)
          || (x.ball_no - y.ball_no));
      let cur = null, spellIdx = 0, lastOver = null;
      list.forEach(b => {
        const o = b.over_no || 0;
        if (cur == null || (lastOver != null && o > lastOver + 2)) {
          if (cur) all.push(cur);
          spellIdx++;
          cur = {match_id: b.match_id, innings_seq: b.innings_seq,
            spell_index: spellIdx, first_over: o, last_over: o,
            balls: []};
        }
        cur.balls.push(b);
        cur.last_over = o; lastOver = o;
      });
      if (cur) all.push(cur);
    }
    return all;
  }

  // ---------- slicer model ---------------------------------------------
  // Per-tab slicer set. The "common" group is shared across both tabs.
  const SLICERS_COMMON = [
    {key:'season',     label:'Season'},
    {key:'home_away',  label:'Home / Away'},
    {key:'result',     label:'Result'},
    {key:'competition',label:'Competition'},
    {key:'opp',        label:'Opposition'},
    {key:'bat_first',  label:'Rainham bat 1st / 2nd'},
    {key:'toss',       label:'Toss'},
    {key:'phase',      label:'Innings phase (overs)'},
  ];
  const SLICERS_BAT = [
    {key:'position', label:'Batting position'},
    {key:'pinns',    label:'Player innings bucket', bbb:true},
    {key:'btype',    label:'Vs bowler type', bbb:true},
    {key:'barm',     label:'Vs bowler arm',  bbb:true},
  ];
  const SLICERS_BOWL = [
    {key:'bhand',  label:'Vs batter hand', bbb:true},
    {key:'spell',  label:'Spell', bbb:true},
  ];

  // ---------- state -----------------------------------------------------
  let DATA = null;
  let STATE = {};      // slicer state
  let TAB = 'bat';     // active tab: 'bat' | 'bowl'

  function loadStateFromHash() {
    STATE = {};
    TAB = 'bat';
    if (!location.hash || location.hash.length < 2) return;
    location.hash.slice(1).split('&').forEach(p => {
      const [k,v] = p.split('=');
      if (!k || !v) return;
      if (k === 't') {
        TAB = v === 'bowl' ? 'bowl' : 'bat';
        return;
      }
      STATE[k] = decodeURIComponent(v).split(',').filter(Boolean);
    });
  }
  function saveStateToHash() {
    const parts = [];
    parts.push('t=' + TAB);
    for (const k in STATE) {
      if (STATE[k] && STATE[k].length) {
        parts.push(k + '=' + encodeURIComponent(STATE[k].join(',')));
      }
    }
    history.replaceState(null, '', '#' + parts.join('&'));
  }

  // ---------- main render -----------------------------------------------
  function boot(data) {
    DATA = data;
    loadStateFromHash();
    document.getElementById('player-name').textContent = data.name;
    document.getElementById('crumb-name').textContent = data.name;
    document.title = data.name + ' — RCC dashboard';
    renderTabs();
    renderHero();
    renderFilterPanel();
    renderCards();
  }

  // ---------- tabs ------------------------------------------------------
  function renderTabs() {
    const root = document.getElementById('tabs');
    root.innerHTML = '';
    [['bat','Batting'], ['bowl','Bowling']].forEach(([k, lbl]) => {
      const b = el('button',
        {class: 'tab' + (TAB === k ? ' on' : ''),
         onclick: () => { TAB = k; saveStateToHash(); renderTabs();
           renderHero(); renderFilterPanel(); renderCards(); }},
        [lbl]);
      root.appendChild(b);
    });
  }

  // ---------- filter helpers -------------------------------------------
  function isInState(k, v) {
    return STATE[k] && STATE[k].indexOf(String(v)) >= 0;
  }
  function matchPasses(m, except) {
    except = except || EMPTY_SET;
    if (!except.has('season') && STATE.season &&
        !isInState('season', m.season)) return false;
    if (!except.has('home_away') && STATE.home_away &&
        !isInState('home_away', m.home_away)) return false;
    if (!except.has('result') && STATE.result &&
        !isInState('result', m.result)) return false;
    if (!except.has('competition') && STATE.competition &&
        !isInState('competition', m.competition)) return false;
    if (!except.has('opp') && STATE.opp &&
        !isInState('opp', m.opp_club_name)) return false;
    if (!except.has('bat_first') && STATE.bat_first &&
        !isInState('bat_first', m.bat_first ? 'yes' : 'no')) return false;
    if (!except.has('toss') && STATE.toss &&
        !isInState('toss', m.toss_won ? 'won' : 'lost')) return false;
    return true;
  }
  const EMPTY_SET = new Set();
  function activeMatchIds(except) {
    const ids = new Set();
    DATA.matches.forEach(m => {
      if (matchPasses(m, except)) ids.add(m.match_id);
    });
    return ids;
  }
  function filteredBatting(except) {
    except = except || EMPTY_SET;
    const ok = activeMatchIds(except);
    return DATA.batting.filter(b => {
      if (!ok.has(b.match_id)) return false;
      if (b.did_not_bat) return false;
      if (!except.has('position') && STATE.position &&
          !isInState('position', b.position)) return false;
      return true;
    });
  }
  function filteredBowling(except) {
    except = except || EMPTY_SET;
    const ok = activeMatchIds(except);
    return DATA.bowling.filter(b => ok.has(b.match_id));
  }
  function filteredFaced() {
    const ok = activeMatchIds();
    let balls = DATA.balls_faced.filter(b => ok.has(b.match_id));
    if (STATE.phase) balls = balls.filter(b =>
      isInState('phase', phaseOf(b.over_no)));
    if (STATE.btype) balls = balls.filter(b =>
      isInState('btype', b.bowling_type || 'unknown'));
    if (STATE.barm) balls = balls.filter(b =>
      isInState('barm', b.bowling_arm || 'unknown'));
    if (STATE.pinns) {
      const counters = {};
      const out = [];
      balls.forEach(b => {
        const k = b.match_id + ':' + b.innings_seq;
        const so = counters[k] || 0;
        if (isInState('pinns', piBucket(so))) out.push(b);
        if (b.is_legal_ball|0) counters[k] = so + 1;
      });
      balls = out;
    }
    return balls;
  }
  function filteredBowled() {
    const ok = activeMatchIds();
    let balls = DATA.balls_bowled.filter(b => ok.has(b.match_id));
    if (STATE.phase) balls = balls.filter(b =>
      isInState('phase', phaseOf(b.over_no)));
    if (STATE.bhand) balls = balls.filter(b =>
      isInState('bhand', b.batting_hand || 'unknown'));
    if (STATE.spell) {
      const spells = detectSpells(balls);
      const keep = new Set();
      spells.forEach(sp => {
        const isFirst = sp.spell_index === 1;
        if ((isFirst && isInState('spell','1st'))
         || (!isFirst && isInState('spell','later'))) {
          sp.balls.forEach(b => keep.add(b));
        }
      });
      balls = balls.filter(b => keep.has(b));
    }
    return balls;
  }

  // ---------- hero (snapshot tile grid per tab) ------------------------
  function snapTile(lbl, v, sub) {
    return el('div', {class:'snap'}, [
      el('div', {class:'lbl'}, [lbl]),
      el('div', {class:'v'}, [v]),
      sub ? el('div', {class:'sub'}, [sub]) : null,
    ]);
  }
  function renderHero() {
    const stats = document.getElementById('player-stats');
    const summary = document.getElementById('player-summary');
    const bbb = document.getElementById('bbb-bar');
    stats.innerHTML = ''; bbb.innerHTML = ''; bbb.removeAttribute('style');

    if (TAB === 'bat') {
      const rows = filteredBatting();
      const inns = rows.length;
      const runs = rows.reduce((s,r) => s + (r.runs||0), 0);
      const nots = rows.filter(r => r.not_out).length;
      const dis = inns - nots;
      const withBalls = rows.filter(r => r.balls);
      const ballsT = withBalls.reduce((s,r) => s + (r.balls||0), 0);
      const runsWB = withBalls.reduce((s,r) => s + (r.runs||0), 0);
      const hs = rows.reduce((m,r) => Math.max(m, r.runs||0), 0);
      const fifties = rows.filter(r => (r.runs||0) >= 50 && (r.runs||0) < 100).length;
      const tons = rows.filter(r => (r.runs||0) >= 100).length;
      const ducks = rows.filter(r => (r.runs||0) === 0 && !r.not_out
        && (r.how_out||'') !== 'did not bat'
        && (r.how_out||'') !== 'absent').length;
      const avg = dis ? runs / dis : null;
      const sr  = ballsT ? runsWB / ballsT * 100 : null;

      stats.classList.remove('stats'); stats.classList.add('snapshot');
      [
        snapTile('Inns', inns),
        snapTile('Runs', runs),
        snapTile('Avg', avg!=null ? fmtN(avg,2) : '—',
          nots ? nots + ' n.o.' : ''),
        snapTile('Strike rate', sr!=null ? fmtN(sr,1) : '—',
          ballsT ? ballsT + ' bls (' + withBalls.length + ' inns)' : '—'),
        snapTile('HS', hs),
        snapTile('50 / 100 / 0', fifties + ' / ' + tons + ' / ' + ducks),
      ].forEach(t => stats.appendChild(t));
      summary.textContent = (DATA.matches.length) + ' matches in window · '
        + DATA.balls_faced.length + ' BBB balls faced';

      renderBBBBar(DATA.balls_faced.length, ballsT);
    } else {
      const rows = filteredBowling();
      const inns = rows.length;
      const balls = rows.reduce((s,r) => s + (r.legal_balls||0), 0);
      const overs = balls / 6;
      const runs = rows.reduce((s,r) => s + (r.runs||0), 0);
      const wkts = rows.reduce((s,r) => s + (r.wickets||0), 0);
      const maids = rows.reduce((s,r) => s + (r.maidens||0), 0);
      const bbi = rows.reduce((best,r) => {
        const w = r.wickets||0;
        if (w > best.wkts || (w === best.wkts && (r.runs||999) < best.runs)) {
          return {wkts:w, runs:r.runs||0};
        }
        return best;
      }, {wkts:0, runs:0});
      const avg = wkts ? runs/wkts : null;
      const econ = balls ? runs/balls * 6 : null;
      const sr = wkts ? balls/wkts : null;

      stats.classList.remove('stats'); stats.classList.add('snapshot');
      [
        snapTile('Inns', inns),
        snapTile('Wkts', wkts),
        snapTile('Avg', avg!=null ? fmtN(avg,2) : '—'),
        snapTile('Econ', econ!=null ? fmtN(econ,2) : '—',
          fmtN(overs,1) + ' overs'),
        snapTile('Strike rate', sr!=null ? fmtN(sr,1) : '—'),
        snapTile('Best · M', bbi.wkts + '/' + bbi.runs,
          maids + ' maidens'),
      ].forEach(t => stats.appendChild(t));
      summary.textContent = (DATA.matches.length) + ' matches in window · '
        + DATA.balls_bowled.length + ' BBB balls bowled';

      renderBBBBar(DATA.balls_bowled.length, balls);
    }
  }
  function renderBBBBar(bbbBalls, scorecardBalls) {
    const bbb = document.getElementById('bbb-bar');
    if (!bbbBalls) { bbb.style.display = 'none'; return; }
    const denom = Math.max(bbbBalls, scorecardBalls);
    const cov = Math.min(100, Math.round(bbbBalls / Math.max(1,denom) * 100));
    bbb.style.display = '';
    bbb.appendChild(el('span', {class:'fill', style:'--w:'+cov+'%'}));
    bbb.appendChild(el('span', {class:'lbl'},
      [bbbBalls + ' BBB balls · ' + cov + '% covered']));
  }

  // ---------- filter panel (collapsible) -------------------------------
  function renderFilterPanel() {
    const panel = document.getElementById('filter-panel');
    panel.innerHTML = '';

    const slicers = [...SLICERS_COMMON,
      ...(TAB === 'bat' ? SLICERS_BAT : SLICERS_BOWL)];
    const has_bbb = TAB === 'bat'
      ? DATA.balls_faced.length : DATA.balls_bowled.length;

    const slicerOptions = {
      season:     uniqueSorted(DATA.matches.map(m => m.season)),
      home_away:  ['home','away'],
      result:     ['W','L','D','T','NR','A'].filter(r =>
        DATA.matches.some(m => m.result === r)),
      competition:uniqueSorted(DATA.matches.map(m => m.competition)
        .filter(Boolean)),
      opp:        uniqueSorted(DATA.matches.map(m => m.opp_club_name)),
      bat_first:  ['yes','no'],
      toss:       ['won','lost'],
      phase:      PHASE_50,
      position:   uniqueSorted(DATA.batting
        .filter(b => !b.did_not_bat)
        .map(b => b.position).filter(p => p != null)),
      pinns:      PI_LABELS,
      btype:      ['pace','spin','unknown'],
      barm:       ['right','left','unknown'],
      bhand:      ['right','left','unknown'],
      spell:      ['1st','later'],
    };

    const nActive = Object.keys(STATE)
      .reduce((s,k) => s + (STATE[k] ? STATE[k].length : 0), 0);
    const ttl = nActive
      ? nActive + ' filter' + (nActive === 1 ? '' : 's') + ' active'
      : 'No filters';

    const head = el('div', {class:'filter-head',
      onclick: () => panel.classList.toggle('open')}, [
      el('span', {class:'ttl'}, [
        el('span', {class:'chev'}, ['▸']),
        'Filters',
      ]),
      el('span', {class:'summary'}, [ttl + ' · tap to ' +
        (panel.classList.contains('open') ? 'collapse' : 'expand')]),
    ]);
    panel.appendChild(head);

    const body = el('div', {class:'filter-body'});
    const rail = el('div', {id:'slicer-rail'});
    slicers.forEach(s => {
      if (s.bbb && !has_bbb) return;
      const opts = slicerOptions[s.key] || [];
      if (!opts.length) return;
      const grp = el('div', {class:'slicer-group'},
        [el('div', {class:'lbl'}, [s.label])]);
      const chips = el('div', {class:'slicer-chips'});
      opts.forEach(o => {
        const active = (STATE[s.key] || []).indexOf(String(o)) >= 0;
        chips.appendChild(el('span',
          {class: 'slicer-chip' + (active ? ' on' : ''),
           onclick: ev => {
             ev.stopPropagation();
             toggleSlicer(s.key, String(o));
           }},
          [String(o)]));
      });
      grp.appendChild(chips);
      rail.appendChild(grp);
    });
    body.appendChild(rail);

    if (nActive) {
      const reset = el('button', {class:'reset-btn',
        onclick: ev => { ev.stopPropagation();
          STATE = {}; saveStateToHash();
          renderHero(); renderFilterPanel(); renderCards();
          panel.classList.add('open'); }
        }, ['Reset all filters']);
      const resetWrap = el('div',
        {style:'margin-top:14px;text-align:right'}, [reset]);
      body.appendChild(resetWrap);
    }
    panel.appendChild(body);

    // Keep panel open if it was open (or there are active filters);
    // collapse otherwise.
    if (nActive) panel.classList.add('open');
  }
  function uniqueSorted(arr) {
    return Array.from(new Set(arr.filter(x => x != null))).sort((a,b) => {
      if (typeof a === 'number' && typeof b === 'number') return a - b;
      return String(a).localeCompare(String(b));
    });
  }
  function toggleSlicer(key, val) {
    const cur = STATE[key] || [];
    const i = cur.indexOf(val);
    if (i >= 0) cur.splice(i, 1); else cur.push(val);
    if (cur.length) STATE[key] = cur; else delete STATE[key];
    saveStateToHash();
    renderHero();
    renderFilterPanel();
    renderCards();
  }

  // ---------- cards (per-tab) ------------------------------------------
  function renderCards() {
    const root = document.getElementById('cards');
    root.innerHTML = '';
    if (TAB === 'bat') {
      // Comparisons (always visible — drive without needing to filter).
      root.appendChild(seasonSplitsBattingCard());
      root.appendChild(compareBattingCard('Position',
        'position', b => b.position,
        uniqueSorted(DATA.batting.filter(b => !b.did_not_bat)
          .map(b => b.position).filter(p => p != null))));
      root.appendChild(compareBattingCard('Home / away',
        'home_away', null, ['home','away'], 'match'));
      root.appendChild(compareBattingCard('Result',
        'result', null, ['W','L','D','T','A'], 'match'));
      root.appendChild(compareBattingCard('Competition',
        'competition', null, ['League','Cup'], 'match'));
      root.appendChild(compareBattingCard('Bat 1st / 2nd',
        'bat_first', null, ['yes','no'], 'match',
        m => m.bat_first ? 'yes' : 'no'));
      root.appendChild(compareBattingCard('Toss',
        'toss', null, ['won','lost'], 'match',
        m => m.toss_won ? 'won' : 'lost'));
      // BBB-driven comparisons
      if (DATA.balls_faced.length) {
        root.appendChild(phaseSplitCard('bat'));
        root.appendChild(playerInningsCard());
        root.appendChild(vsBowlerTypeCard());
      }
      root.appendChild(bestBattingCard());
    } else {
      // Bowling comparisons
      root.appendChild(seasonSplitsBowlingCard());
      root.appendChild(compareBowlingCard('Home / away',
        'home_away', null, ['home','away'], 'match'));
      root.appendChild(compareBowlingCard('Result',
        'result', null, ['W','L','D','T','A'], 'match'));
      root.appendChild(compareBowlingCard('Competition',
        'competition', null, ['League','Cup'], 'match'));
      root.appendChild(compareBowlingCard('Bat 1st / 2nd',
        'bat_first', null, ['yes','no'], 'match',
        m => m.bat_first ? 'yes' : 'no'));
      root.appendChild(compareBowlingCard('Toss',
        'toss', null, ['won','lost'], 'match',
        m => m.toss_won ? 'won' : 'lost'));
      // BBB-driven comparisons
      if (DATA.balls_bowled.length) {
        root.appendChild(phaseSplitCard('bowl'));
        root.appendChild(spellCard());
        root.appendChild(vsBatterHandCard());
      }
      root.appendChild(bestBowlingCard());
    }
  }

  // ----- comparison cards (scorecard-derived, ignore own dimension) ----
  // groupBy:
  //   'match' — group by attribute on the match (slice keys looked up
  //             via groupFn(match) or, if groupFn is null, m[selfKey])
  //   else    — group by attribute on the row itself (groupFn(row))
  function compareBattingCard(title, selfKey, rowFn, keys, groupBy, matchFn) {
    const except = new Set(selfKey ? [selfKey] : []);
    const rows = filteredBatting(except);
    const card = el('div', {class:'card'},
      [el('h2', null, [title])]);
    if (!rows.length) return emptyCard(card,
      'No innings under the current filters.');
    const buckets = {};
    keys.forEach(k => buckets[String(k)] = _emptyBat());
    rows.forEach(r => {
      let key;
      if (groupBy === 'match') {
        const m = matchOf(r.match_id);
        if (!m) return;
        key = matchFn ? matchFn(m) : m[selfKey];
      } else {
        key = rowFn(r);
      }
      const k = String(key);
      if (!buckets[k]) return;
      _accumBat(buckets[k], r);
    });
    card.appendChild(_compareTableBat(buckets, keys, _firstColLabel(title)));
    return card;
  }
  function compareBowlingCard(title, selfKey, rowFn, keys, groupBy, matchFn) {
    const except = new Set(selfKey ? [selfKey] : []);
    const rows = filteredBowling(except);
    const card = el('div', {class:'card'},
      [el('h2', null, [title])]);
    if (!rows.length) return emptyCard(card,
      'No spells under the current filters.');
    const buckets = {};
    keys.forEach(k => buckets[String(k)] = _emptyBowl());
    rows.forEach(r => {
      let key;
      if (groupBy === 'match') {
        const m = matchOf(r.match_id);
        if (!m) return;
        key = matchFn ? matchFn(m) : m[selfKey];
      } else {
        key = rowFn(r);
      }
      const k = String(key);
      if (!buckets[k]) return;
      _accumBowl(buckets[k], r);
    });
    card.appendChild(_compareTableBowl(buckets, keys, _firstColLabel(title)));
    return card;
  }
  function _firstColLabel(title) {
    return title.split(/[\\/]/)[0].trim();
  }
  function matchOf(mid) {
    if (!matchOf._idx) {
      matchOf._idx = {};
      DATA.matches.forEach(m => matchOf._idx[m.match_id] = m);
    }
    return matchOf._idx[mid];
  }
  // -- batting bucket aggregator (scorecard rows) --
  function _emptyBat() {
    return {inns:0, runs:0, balls:0, ballsInns:0, nots:0,
      hs:0, fifties:0, hundreds:0, ducks:0};
  }
  function _accumBat(s, r) {
    s.inns++;
    const runs = r.runs || 0;
    s.runs += runs;
    if (r.balls) { s.balls += r.balls; s.ballsInns++; }
    if (r.not_out) s.nots++;
    if (runs > s.hs) s.hs = runs;
    if (runs >= 50 && runs < 100) s.fifties++;
    else if (runs >= 100) s.hundreds++;
    if (runs === 0 && !r.not_out
        && (r.how_out || '') !== 'did not bat'
        && (r.how_out || '') !== 'absent') s.ducks++;
  }
  function _finBat(s) {
    const dis = s.inns - s.nots;
    return {
      inns: s.inns, runs: s.runs,
      avg:  dis ? s.runs / dis : null,
      sr:   s.balls ? s.runs / s.balls * 100 : null,
      hs:   s.hs,
      fifties: s.fifties, hundreds: s.hundreds, ducks: s.ducks,
    };
  }
  function _compareTableBat(buckets, keys, firstLabel) {
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [el('tr', null, [
        el('th', null, [firstLabel]),
        el('th', null, ['Inns']),
        el('th', null, ['Runs']),
        el('th', null, ['Avg']),
        el('th', null, ['SR']),
        el('th', null, ['HS']),
        el('th', null, ['50/100']),
      ])])
    ]);
    const tb = el('tbody');
    keys.forEach(k => {
      const f = _finBat(buckets[String(k)] || _emptyBat());
      const empty = f.inns === 0;
      tb.appendChild(el('tr', {class: empty ? 'empty' : ''}, [
        el('td', null, [String(k)]),
        el('td', null, [empty ? '—' : String(f.inns)]),
        el('td', null, [empty ? '—' : String(f.runs)]),
        el('td', null, [empty ? '—' : (f.avg!=null ? fmtN(f.avg,2) : '—')]),
        el('td', null, [empty ? '—' : (f.sr!=null ? fmtN(f.sr,1) : '—')]),
        el('td', null, [empty ? '—' : String(f.hs)]),
        el('td', null, [empty ? '—' : (f.fifties + '/' + f.hundreds)]),
      ]));
    });
    tab.appendChild(tb);
    return tab;
  }
  // -- bowling bucket aggregator (scorecard rows) --
  function _emptyBowl() {
    return {sp:0, balls:0, runs:0, wkts:0, maids:0,
      best_w:0, best_r:0};
  }
  function _accumBowl(s, r) {
    s.sp++;
    s.balls += r.legal_balls || 0;
    s.runs  += r.runs || 0;
    s.wkts  += r.wickets || 0;
    s.maids += r.maidens || 0;
    const w = r.wickets || 0;
    if (w > s.best_w
        || (w === s.best_w && (r.runs || 999) < s.best_r)) {
      s.best_w = w; s.best_r = r.runs || 0;
    }
  }
  function _finBowl(s) {
    return {
      sp: s.sp, runs: s.runs, wkts: s.wkts,
      overs: s.balls / 6,
      avg:  s.wkts ? s.runs / s.wkts : null,
      econ: s.balls ? s.runs / s.balls * 6 : null,
      best: s.sp ? (s.best_w + '/' + s.best_r) : '—',
    };
  }
  function _compareTableBowl(buckets, keys, firstLabel) {
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [el('tr', null, [
        el('th', null, [firstLabel]),
        el('th', null, ['Sp']),
        el('th', null, ['O']),
        el('th', null, ['W']),
        el('th', null, ['Avg']),
        el('th', null, ['Econ']),
        el('th', null, ['Best']),
      ])])
    ]);
    const tb = el('tbody');
    keys.forEach(k => {
      const f = _finBowl(buckets[String(k)] || _emptyBowl());
      const empty = f.sp === 0;
      tb.appendChild(el('tr', {class: empty ? 'empty' : ''}, [
        el('td', null, [String(k)]),
        el('td', null, [empty ? '—' : String(f.sp)]),
        el('td', null, [empty ? '—' : fmtN(f.overs,1)]),
        el('td', null, [empty ? '—' : String(f.wkts)]),
        el('td', null, [empty ? '—' : (f.avg!=null ? fmtN(f.avg,2) : '—')]),
        el('td', null, [empty ? '—' : (f.econ!=null ? fmtN(f.econ,2) : '—')]),
        el('td', null, [empty ? '—' : f.best]),
      ]));
    });
    tab.appendChild(tb);
    return tab;
  }

  function volChip(n, label) {
    const cls = n ? 'vol-chip' : 'vol-chip empty';
    return el('span', {class:cls}, [n + ' ' + (label || 'balls')]);
  }
  function emptyCard(card, msg) {
    card.appendChild(el('p', {class:'empty-note'}, [msg]));
    return card;
  }

  // -- season splits ----------------------------------------------------
  function seasonSplitsBattingCard() {
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Runs by season'])]);
    const rows = filteredBatting();
    const bySeason = {};
    rows.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const s = m.season;
      bySeason[s] = bySeason[s] || {season:s, runs:0, inns:0,
        outs:0};
      bySeason[s].runs += r.runs||0; bySeason[s].inns += 1;
      if (!r.not_out) bySeason[s].outs += 1;
    });
    const seasons = Object.values(bySeason).sort((a,b)=>a.season-b.season);
    if (!seasons.length) return emptyCard(card, 'No innings under the current filters.');
    const max = Math.max(1, ...seasons.map(s => s.runs));
    const wrap = el('div', {class:'bucket-bars'});
    seasons.forEach(s => {
      const avg = s.outs ? (s.runs/s.outs).toFixed(1) : '—';
      wrap.appendChild(el('div', {class:'bucket-bar'}, [
        el('span', {class:'lbl'}, [String(s.season)]),
        el('span', {class:'bar'}, [
          el('span', {style:'width:'+(s.runs/max*100)+'%'})]),
        el('span', {class:'num'},
          [s.runs + ' r · avg ' + avg + ' (' + s.inns + ')']),
      ]));
    });
    card.appendChild(wrap);
    return card;
  }
  function seasonSplitsBowlingCard() {
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Wickets by season'])]);
    const rows = filteredBowling();
    const bySeason = {};
    rows.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const s = m.season;
      bySeason[s] = bySeason[s] || {season:s, wkts:0, runs:0,
        balls:0, spells:0};
      bySeason[s].wkts += r.wickets||0;
      bySeason[s].runs += r.runs||0;
      bySeason[s].balls += r.legal_balls||0;
      bySeason[s].spells += 1;
    });
    const seasons = Object.values(bySeason).sort((a,b)=>a.season-b.season);
    if (!seasons.length) return emptyCard(card, 'No spells under the current filters.');
    const max = Math.max(1, ...seasons.map(s => s.wkts));
    const wrap = el('div', {class:'bucket-bars'});
    seasons.forEach(s => {
      const avg = s.wkts ? (s.runs/s.wkts).toFixed(1) : '—';
      const econ = s.balls ? (s.runs/s.balls*6).toFixed(2) : '—';
      wrap.appendChild(el('div', {class:'bucket-bar'}, [
        el('span', {class:'lbl'}, [String(s.season)]),
        el('span', {class:'bar warn'}, [
          el('span', {style:'width:'+(s.wkts/max*100)+'%'})]),
        el('span', {class:'num'},
          [s.wkts + ' w · avg ' + avg + ' · econ ' + econ]),
      ]));
    });
    card.appendChild(wrap);
    return card;
  }

  // -- phase splits -----------------------------------------------------
  function phaseSplitCard(view) {
    const balls = view === 'bat' ? filteredFaced() : filteredBowled();
    const buckets = byPhase(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, [view === 'bat'
        ? 'Faced — by innings phase' : 'Bowled — by innings phase',
        volChip(balls.length)]),
    ]);
    if (!balls.length) return emptyCard(card, 'No BBB balls match.');
    card.appendChild(_breakdownTable(buckets, PHASE_50, 'Phase', view));
    return card;
  }
  function _breakdownTable(buckets, keys, firstLabel, view) {
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [
        el('tr', null, [
          el('th', null, [firstLabel]),
          el('th', null, ['Bls']),
          el('th', null, ['Runs']),
          el('th', null, ['Avg']),
          el('th', null, [view==='bat' ? 'SR' : 'Econ']),
          el('th', null, ['Wkts']),
          el('th', null, ['Dot%']),
        ])
      ]),
    ]);
    const tb = el('tbody');
    keys.forEach(k => {
      const s = buckets[k] || {balls:0};
      const empty = s.balls === 0;
      const avg = view === 'bat' ? s.bat_avg : s.bowl_avg;
      tb.appendChild(el('tr', {class: empty ? 'empty' : ''}, [
        el('td', null, [k]),
        el('td', null, [empty ? '—' : String(s.balls)]),
        el('td', null, [empty ? '—' : String(s.runs)]),
        el('td', null, [empty ? '—' :
          (avg != null ? fmtN(avg,2) : '—')]),
        el('td', null, [empty ? '—' :
          (view==='bat' ? fmtN(s.sr,1) : fmtN(s.econ,2))]),
        el('td', null, [empty ? '—' : String(s.wickets)]),
        el('td', null, [empty ? '—' : fmtN(s.dot_pct,0) + '%']),
      ]));
    });
    tab.appendChild(tb);
    return tab;
  }

  // -- player innings buckets (batting only) ----------------------------
  function playerInningsCard() {
    const balls = filteredFaced();
    const buckets = byPlayerInns(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Within my innings — by ball-faced bucket',
        volChip(balls.length)]),
    ]);
    if (!balls.length) return emptyCard(card, 'No BBB balls match.');
    card.appendChild(_breakdownTable(buckets, PI_LABELS, 'Bucket', 'bat'));
    return card;
  }

  // -- vs bowler type / arm (batting only) ------------------------------
  function vsBowlerTypeCard() {
    const balls = filteredFaced();
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Vs bowler — type & arm', volChip(balls.length)]),
    ]);
    if (!balls.length) return emptyCard(card, 'No BBB balls match.');
    const types = byKey(balls, b => b.bowling_type || 'unknown',
      ['pace','spin','unknown']);
    const arms  = byKey(balls, b => b.bowling_arm || 'unknown',
      ['right','left','unknown']);
    card.appendChild(_breakdownTable(types, ['pace','spin','unknown'],
      'Type', 'bat'));
    card.appendChild(_breakdownTable(arms, ['right','left','unknown'],
      'Arm', 'bat'));
    return card;
  }

  // -- vs batter hand (bowling only) ------------------------------------
  function vsBatterHandCard() {
    const balls = filteredBowled();
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Vs batter hand', volChip(balls.length)]),
    ]);
    if (!balls.length) return emptyCard(card, 'No BBB balls match.');
    const buckets = byKey(balls, b => b.batting_hand || 'unknown',
      ['right','left','unknown']);
    card.appendChild(_breakdownTable(buckets, ['right','left','unknown'],
      'Hand', 'bowl'));
    return card;
  }

  // -- spell breakdown (bowling only) -----------------------------------
  function spellCard() {
    const balls = filteredBowled();
    const card = el('div', {class:'card'}, [
      el('h2', null, ['1st spell vs later spells',
        volChip(balls.length)]),
    ]);
    if (!balls.length) return emptyCard(card, 'No BBB balls match.');
    const spells = detectSpells(balls);
    const first = emptyStats(), later = emptyStats();
    spells.forEach(sp => {
      const target = sp.spell_index === 1 ? first : later;
      sp.balls.forEach(b => accum(target, b));
    });
    const buckets = {'1st': fin(first), 'later': fin(later)};
    card.appendChild(_breakdownTable(buckets, ['1st','later'],
      'Spell', 'bowl'));
    return card;
  }

  // -- best performances ------------------------------------------------
  function bestBattingCard() {
    const rows = filteredBatting().slice().sort((a,b) =>
      (b.runs||0) - (a.runs||0)).slice(0, 5);
    const card = el('div', {class:'card'}, [el('h2', null, ['Top 5 innings'])]);
    if (!rows.length) return emptyCard(card, 'No innings match.');
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [el('tr', null, [
        el('th', null, ['Date']),
        el('th', null, ['Vs']),
        el('th', null, ['Runs']),
        el('th', null, ['Bls']),
        el('th', null, ['SR']),
      ])])
    ]);
    const tb = el('tbody');
    rows.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const sr = r.balls ? (r.runs/r.balls*100).toFixed(0) : '—';
      tb.appendChild(el('tr', null, [
        el('td', null, [m.match_date]),
        el('td', null, [m.opp_club_name]),
        el('td', {class:'num'},
          [(r.runs||0) + (r.not_out ? '*' : '')]),
        el('td', {class:'num'}, [String(r.balls || '—')]),
        el('td', {class:'num'}, [sr]),
      ]));
    });
    tab.appendChild(tb); card.appendChild(tab);
    return card;
  }
  function bestBowlingCard() {
    const rows = filteredBowling().slice().sort((a,b) => {
      const wd = (b.wickets||0) - (a.wickets||0);
      if (wd !== 0) return wd;
      return (a.runs||0) - (b.runs||0);
    }).slice(0, 5);
    const card = el('div', {class:'card'}, [el('h2', null, ['Top 5 spells'])]);
    if (!rows.length) return emptyCard(card, 'No spells match.');
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [el('tr', null, [
        el('th', null, ['Date']),
        el('th', null, ['Vs']),
        el('th', null, ['Fig']),
        el('th', null, ['O']),
        el('th', null, ['Econ']),
      ])])
    ]);
    const tb = el('tbody');
    rows.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const econ = r.legal_balls
        ? (r.runs/r.legal_balls*6).toFixed(2) : '—';
      tb.appendChild(el('tr', null, [
        el('td', null, [m.match_date]),
        el('td', null, [m.opp_club_name]),
        el('td', {class:'num'}, [(r.wickets||0) + '/' + (r.runs||0)]),
        el('td', {class:'num'}, [r.overs || '—']),
        el('td', {class:'num'}, [econ]),
      ]));
    });
    tab.appendChild(tb); card.appendChild(tab);
    return card;
  }

})();
