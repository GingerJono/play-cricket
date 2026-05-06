
(function(){
  'use strict';

  // ---------- bootstrap ---------------------------------------------------
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

  // ---------- shared helpers ---------------------------------------------
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
      e.appendChild(typeof c === 'string'
        ? document.createTextNode(c) : c);
    }
    return e;
  }
  function fmtN(n, dp) {
    if (n == null || isNaN(n)) return '—';
    if (dp == null) dp = 1;
    return n.toFixed(dp);
  }
  function fmtInt(n) {
    if (n == null || isNaN(n)) return '—';
    return Math.round(n).toString();
  }
  function date_yyyymmdd(s) {
    const m = (s || '').split('/');
    if (m.length !== 3) return '';
    return m[2] + m[1] + m[0];
  }

  // ---------- bucket maths (mirrors _bbb_buckets.py) ----------------------
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
    out.bowl_sr = s.wickets ? s.legal / s.wickets : null;
    out.bat_avg = s.wickets ? s.runs_bat / s.wickets : null;
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
  function byBattingHand(balls) {
    const out = {right: emptyStats(), left: emptyStats(),
      unknown: emptyStats()};
    balls.forEach(b => {
      const h = (b.batting_hand && out[b.batting_hand])
        ? b.batting_hand : 'unknown';
      accum(out[h], b);
    });
    const r = {}; for (const k in out) r[k] = fin(out[k]); return r;
  }
  function byBowlerType(balls) {
    const types = {pace: emptyStats(), spin: emptyStats(),
      unknown: emptyStats()};
    const arms  = {right: emptyStats(), left: emptyStats(),
      unknown: emptyStats()};
    balls.forEach(b => {
      let t = (b.bowling_type && types[b.bowling_type])
        ? b.bowling_type : 'unknown';
      accum(types[t], b);
      let a = (b.bowling_arm && arms[b.bowling_arm])
        ? b.bowling_arm : 'unknown';
      accum(arms[a], b);
    });
    const finT = {}, finA = {};
    for (const k in types) finT[k] = fin(types[k]);
    for (const k in arms)  finA[k] = fin(arms[k]);
    return {type: finT, arm: finA};
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
          if (cur) { cur.stats = fin(cur.stats); all.push(cur); }
          spellIdx++;
          cur = {match_id: b.match_id, innings_seq: b.innings_seq,
            spell_index: spellIdx, first_over: o, last_over: o,
            stats: emptyStats()};
        }
        accum(cur.stats, b);
        cur.last_over = o; lastOver = o;
      });
      if (cur) { cur.stats = fin(cur.stats); all.push(cur); }
    }
    return all;
  }

  // ---------- slicer model -----------------------------------------------
  const SLICERS = [
    {key:'season',     label:'Season'},
    {key:'home_away',  label:'Home / Away'},
    {key:'result',     label:'Result'},
    {key:'competition',label:'Competition'},
    {key:'opp',        label:'Opposition'},
    {key:'position',   label:'Batting position'},
    {key:'bat_first',  label:'Bat 1st / 2nd'},
    {key:'toss',       label:'Toss'},
    // BBB only:
    {key:'phase',      label:'Innings phase', bbbOnly:true},
    {key:'pinns',      label:'Player innings bucket', bbbOnly:true,
                       bbbView:'bat'},
    {key:'btype',      label:'Bowler type', bbbOnly:true, bbbView:'bat'},
    {key:'barm',       label:'Bowler arm',  bbbOnly:true, bbbView:'bat'},
    {key:'bhand',      label:'Batter hand', bbbOnly:true, bbbView:'bowl'},
    {key:'spell',      label:'Spell',       bbbOnly:true, bbbView:'bowl'},
  ];

  function loadStateFromHash() {
    const out = {};
    if (!location.hash || location.hash.length < 2) return out;
    const params = location.hash.slice(1).split('&');
    params.forEach(p => {
      const [k,v] = p.split('=');
      if (!k || !v) return;
      out[k] = decodeURIComponent(v).split(',').filter(Boolean);
    });
    return out;
  }
  function saveStateToHash(state) {
    const parts = [];
    for (const k in state) {
      if (state[k] && state[k].length) {
        parts.push(k + '=' + encodeURIComponent(state[k].join(',')));
      }
    }
    history.replaceState(null, '', parts.length
      ? '#' + parts.join('&') : location.pathname + location.search);
  }

  // ---------- main render ------------------------------------------------
  let DATA = null;
  let STATE = {};

  function boot(data) {
    DATA = data;
    STATE = loadStateFromHash();
    document.getElementById('player-name').textContent = data.name;
    document.getElementById('crumb-name').textContent = data.name;
    document.title = data.name + ' — RCC dashboard';
    renderHero();
    renderSlicers();
    renderCards();
  }

  // ---------- hero -------------------------------------------------------
  function renderHero() {
    const matches = DATA.matches.length;
    const inns = DATA.batting.filter(b => !b.did_not_bat).length;
    const runs = DATA.batting.reduce((s,b) =>
      s + (b.runs || 0), 0);
    const wkts = DATA.bowling.reduce((s,b) =>
      s + (b.wickets || 0), 0);
    const ballsFaced = DATA.balls_faced.length;
    const ballsBowled = DATA.balls_bowled.length;
    const totalBat = DATA.batting.reduce((s,b) =>
      s + (b.balls || 0), 0);
    const totalBowl = DATA.bowling.reduce((s,b) =>
      s + (b.legal_balls || 0), 0);
    const stats = document.getElementById('player-stats');
    stats.innerHTML = '';
    [[matches,'apps'],[runs,'runs'],[wkts,'wkts'],
     [ballsFaced+ballsBowled,'BBB balls']
    ].forEach(([n,l]) => {
      const d = el('div', {class:'stat'}, [
        el('div', {class:'n'}, [String(n)]),
        el('div', {class:'lbl'}, [l]),
      ]);
      stats.appendChild(d);
    });
    const summary = document.getElementById('player-summary');
    summary.textContent = matches + ' matches · ' + inns +
      ' innings · ' + DATA.bowling.length + ' bowling spells';
    const bbb = document.getElementById('bbb-bar');
    if (ballsFaced + ballsBowled > 0) {
      const total = ballsFaced + ballsBowled;
      // Scorecard balls is partial in the older seasons (no balls
      // field), so we cap coverage at 100% to avoid >100% noise.
      const denom = Math.max(total, totalBat + totalBowl);
      const cov = Math.min(100, Math.round(total / Math.max(1,denom) * 100));
      bbb.innerHTML = '';
      const inner = el('span', {class:'fill', style:'--w:'+cov+'%'});
      const lbl = el('span', {class:'lbl'},
        ['§ ' + total + ' BBB balls · ~' + cov + '% covered']);
      bbb.appendChild(inner);
      bbb.appendChild(lbl);
    } else {
      bbb.style.display = 'none';
    }
  }

  // ---------- slicers ----------------------------------------------------
  function uniqueSorted(arr) {
    return Array.from(new Set(arr)).sort();
  }
  function renderSlicers() {
    const rail = document.getElementById('slicer-rail');
    rail.innerHTML = '';
    const has_bbb = DATA.balls_faced.length || DATA.balls_bowled.length;

    const slicerOptions = {
      season:     uniqueSorted(DATA.matches.map(m => m.season)),
      home_away:  ['home','away'],
      result:     ['W','L','D','T','NR','A'].filter(r =>
        DATA.matches.some(m => m.result === r)),
      competition:uniqueSorted(DATA.matches.map(m => m.competition)
        .filter(Boolean)),
      opp:        uniqueSorted(DATA.matches.map(m => m.opp_club_name)),
      position:   uniqueSorted(DATA.batting
        .filter(b => !b.did_not_bat)
        .map(b => b.position).filter(p => p != null)),
      bat_first:  ['yes','no'],
      toss:       ['won','lost'],
      phase:      PHASE_50,
      pinns:      PI_LABELS,
      btype:      ['pace','spin','unknown'],
      barm:       ['right','left','unknown'],
      bhand:      ['right','left','unknown'],
      spell:      ['1st','later'],
    };

    SLICERS.forEach(s => {
      if (s.bbbOnly && !has_bbb) return;
      const opts = slicerOptions[s.key] || [];
      if (!opts.length) return;
      const group = el('div', {class:'slicer-group'}, [
        el('div', {class:'lbl'}, [s.label])
      ]);
      const chips = el('div', {class:'slicer-chips'});
      opts.forEach(o => {
        const active = (STATE[s.key] || []).indexOf(String(o)) >= 0;
        const chip = el('span', {class:'slicer-chip' + (active?' on':''),
          onclick: () => toggleSlicer(s.key, String(o))},
          [String(o)]);
        chips.appendChild(chip);
      });
      group.appendChild(chips);
      rail.appendChild(group);
    });

    document.getElementById('reset-filters').onclick = () => {
      STATE = {}; saveStateToHash(STATE); renderSlicers(); renderCards();
    };
    updateFilterSummary();
  }
  function toggleSlicer(key, val) {
    const cur = STATE[key] || [];
    const i = cur.indexOf(val);
    if (i >= 0) cur.splice(i, 1); else cur.push(val);
    if (cur.length) STATE[key] = cur; else delete STATE[key];
    saveStateToHash(STATE);
    renderSlicers();
    renderCards();
  }
  function updateFilterSummary() {
    const out = document.getElementById('filter-summary');
    const n = Object.keys(STATE).reduce((s,k) =>
      s + (STATE[k] ? STATE[k].length : 0), 0);
    out.textContent = n
      ? n + ' slicer chip' + (n === 1 ? '' : 's') + ' active'
      : 'No slicers — full career view.';
  }

  // ---------- filter helpers ---------------------------------------------
  function isInState(k, v) {
    return STATE[k] && STATE[k].indexOf(String(v)) >= 0;
  }
  function matchPasses(m) {
    if (STATE.season && !isInState('season', m.season)) return false;
    if (STATE.home_away && !isInState('home_away', m.home_away)) return false;
    if (STATE.result && !isInState('result', m.result)) return false;
    if (STATE.competition &&
        !isInState('competition', m.competition)) return false;
    if (STATE.opp && !isInState('opp', m.opp_club_name)) return false;
    if (STATE.bat_first && !isInState('bat_first',
        m.bat_first ? 'yes' : 'no')) return false;
    if (STATE.toss && !isInState('toss',
        m.toss_won ? 'won' : 'lost')) return false;
    return true;
  }
  function activeMatchIds() {
    const ids = new Set();
    DATA.matches.forEach(m => {
      if (matchPasses(m)) ids.add(m.match_id);
    });
    return ids;
  }
  function filteredBatting() {
    const ok = activeMatchIds();
    return DATA.batting.filter(b => {
      if (!ok.has(b.match_id)) return false;
      if (b.did_not_bat) return false;
      if (STATE.position &&
          !isInState('position', b.position)) return false;
      return true;
    });
  }
  function filteredBowling() {
    const ok = activeMatchIds();
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
      // need running counter per innings
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
      const ok2 = new Set();
      spells.forEach(sp => {
        if ((sp.spell_index === 1 && isInState('spell','1st'))
         || (sp.spell_index >  1 && isInState('spell','later'))) {
          // approximate: include all balls in matching spells.
          // Re-walk to mark them.
          balls.forEach(b => {
            if (b.match_id === sp.match_id &&
                b.innings_seq === sp.innings_seq &&
                b.over_no >= sp.first_over && b.over_no <= sp.last_over) {
              ok2.add(b);
            }
          });
        }
      });
      balls = balls.filter(b => ok2.has(b));
    }
    return balls;
  }

  // ---------- cards ------------------------------------------------------
  function renderCards() {
    updateFilterSummary();
    const root = document.getElementById('cards');
    root.innerHTML = '';

    root.appendChild(careerBattingCard());
    root.appendChild(careerBowlingCard());
    root.appendChild(seasonSplitsCard());

    if (DATA.balls_faced.length) {
      root.appendChild(phaseSplitCard('bat'));
      root.appendChild(playerInningsCard());
      root.appendChild(vsBowlerTypeCard());
    }
    if (DATA.balls_bowled.length) {
      root.appendChild(phaseSplitCard('bowl'));
      root.appendChild(spellCard());
      root.appendChild(vsBatterHandCard());
    }
    root.appendChild(inningsListCard());
    root.appendChild(bowlingListCard());
    root.appendChild(bestPerformancesCard());
  }

  function statTile(k, v, vol) {
    return el('div', null, [
      el('div', {class:'k'}, [k]),
      el('div', {class:'v'}, [v]),
      vol ? el('div', {class:'vol'}, [vol]) : null,
    ]);
  }
  function volChip(n) {
    return el('span', {class:'vol-chip'}, ['§ ' + n + ' balls']);
  }

  // -- career batting --
  function careerBattingCard() {
    const rows = filteredBatting();
    const inns = rows.length;
    const runs = rows.reduce((s,r) => s + (r.runs||0), 0);
    // SR is only meaningful across innings where balls are recorded.
    // Older PC matches don't carry balls-faced, so we pair-sum over
    // those innings only.
    const withBalls = rows.filter(r => r.balls);
    const runsWithBalls = withBalls.reduce((s,r) => s + (r.runs||0), 0);
    const ballsTotal = withBalls.reduce((s,r) => s + (r.balls||0), 0);
    const nots = rows.filter(r => r.not_out).length;
    const dis = inns - nots;
    const hs = rows.reduce((m,r) =>
      Math.max(m, r.runs||0), 0);
    const fifties = rows.filter(r => (r.runs||0) >= 50 && (r.runs||0) < 100).length;
    const tons = rows.filter(r => (r.runs||0) >= 100).length;
    const ducks = rows.filter(r => (r.runs||0) === 0 && !r.not_out
      && (r.how_out||'') !== 'did not bat'
      && (r.how_out||'') !== 'absent').length;
    const avg = dis ? runs / dis : null;
    const sr  = ballsTotal ? runsWithBalls / ballsTotal * 100 : null;

    const card = el('div', {class:'card'}, [el('h2', null, ['Batting'])]);
    if (!inns) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No batting innings under the current slicers.']));
      return card;
    }
    card.appendChild(el('div', {class:'kv-grid'}, [
      statTile('Innings', String(inns)),
      statTile('Runs', String(runs)),
      statTile('Average', avg!=null ? fmtN(avg,2) : '—',
        nots ? nots + ' not out' : ''),
      statTile('Strike rate', sr!=null ? fmtN(sr,1) : '—',
        ballsTotal + ' balls (' + withBalls.length + ' inns recorded)'),
      statTile('HS', String(hs)),
      statTile('50 / 100 / 0', fifties + ' / ' + tons + ' / ' + ducks),
    ]));
    return card;
  }
  function careerBowlingCard() {
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

    const card = el('div', {class:'card'}, [el('h2', null, ['Bowling'])]);
    if (!inns) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No bowling spells under the current slicers.']));
      return card;
    }
    card.appendChild(el('div', {class:'kv-grid'}, [
      statTile('Innings', String(inns)),
      statTile('Overs / Maids', fmtN(overs,1) + ' / ' + maids),
      statTile('Wickets', String(wkts)),
      statTile('Average', avg!=null ? fmtN(avg,2) : '—'),
      statTile('Economy', econ!=null ? fmtN(econ,2) : '—'),
      statTile('Best', bbi.wkts + '/' + bbi.runs),
    ]));
    return card;
  }
  function seasonSplitsCard() {
    const card = el('div', {class:'card'}, [el('h2', null, ['By season'])]);
    const bat = filteredBatting();
    const bowl = filteredBowling();
    const bySeason = {};
    [...bat, ...bowl].forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const s = m.season;
      bySeason[s] = bySeason[s] || {season:s, runs:0, wkts:0, inns:0, spells:0};
    });
    bat.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      bySeason[m.season].runs += r.runs||0;
      bySeason[m.season].inns += 1;
    });
    bowl.forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      bySeason[m.season].wkts += r.wickets||0;
      bySeason[m.season].spells += 1;
    });
    const seasons = Object.values(bySeason).sort((a,b)=>a.season-b.season);
    if (!seasons.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No matches under the current slicers.']));
      return card;
    }
    const maxR = Math.max(1, ...seasons.map(s => s.runs));
    const maxW = Math.max(1, ...seasons.map(s => s.wkts));
    const wrap = el('div', {class:'bucket-bars'});
    seasons.forEach(s => {
      wrap.appendChild(el('div', {class:'bucket-bar'}, [
        el('span', {class:'lbl'}, [String(s.season)]),
        el('span', {class:'bar'}, [
          el('span', {style:'width:' + (s.runs/maxR*100) + '%'})
        ]),
        el('span', {class:'num'}, [s.runs + ' r · ' + s.inns + ' inn']),
      ]));
      if (s.wkts) wrap.appendChild(el('div', {class:'bucket-bar'}, [
        el('span', {class:'lbl'}, [' ']),
        el('span', {class:'bar pitch'}, [
          el('span', {style:'width:' + (s.wkts/maxW*100) + '%'})
        ]),
        el('span', {class:'num'}, [s.wkts + ' w · ' + s.spells + ' sp']),
      ]));
    });
    card.appendChild(wrap);
    return card;
  }
  function phaseSplitCard(view) {
    const balls = view === 'bat' ? filteredFaced() : filteredBowled();
    const buckets = byPhase(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, [view === 'bat'
        ? 'Faced — by innings phase' : 'Bowled — by innings phase',
        volChip(balls.length)]),
    ]);
    if (!balls.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No BBB balls under these slicers.']));
      return card;
    }
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [
        el('tr', null, [
          el('th', null, ['Phase']),
          el('th', null, ['Balls']),
          el('th', null, ['Runs']),
          el('th', null, [view==='bat' ? 'SR' : 'Econ']),
          el('th', null, ['Wkts']),
          el('th', null, ['Dot %']),
        ])
      ]),
    ]);
    const tb = el('tbody');
    PHASE_50.forEach(p => {
      const s = buckets[p];
      const empty = s.balls === 0;
      const tr = el('tr', {class: empty ? 'empty' : ''}, [
        el('td', null, [p]),
        el('td', null, [String(s.balls)]),
        el('td', null, [empty ? '—' : String(s.runs)]),
        el('td', null, [empty ? '—' : (view==='bat'
          ? fmtN(s.sr,1) : fmtN(s.econ,2))]),
        el('td', null, [empty ? '—' : String(s.wickets)]),
        el('td', null, [empty ? '—' : fmtN(s.dot_pct,0) + '%']),
      ]);
      tb.appendChild(tr);
    });
    tab.appendChild(tb);
    card.appendChild(tab);
    return card;
  }
  function playerInningsCard() {
    const balls = filteredFaced();
    const buckets = byPlayerInns(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Within my innings — by ball-faced bucket',
        volChip(balls.length)]),
    ]);
    if (!balls.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No BBB balls.']));
      return card;
    }
    const max = Math.max(1, ...PI_LABELS.map(p => buckets[p].sr || 0));
    const wrap = el('div', {class:'bucket-bars'});
    PI_LABELS.forEach(p => {
      const s = buckets[p];
      if (!s.balls) return;
      wrap.appendChild(el('div', {class:'bucket-bar'}, [
        el('span', {class:'lbl'}, [p]),
        el('span', {class:'bar'}, [
          el('span', {style:'width:' + ((s.sr||0)/max*100) + '%'})
        ]),
        el('span', {class:'num'},
          ['SR ' + fmtN(s.sr,1) + ' · ' + s.balls + ' bls']),
      ]));
    });
    card.appendChild(wrap);
    return card;
  }
  function vsBowlerTypeCard() {
    const balls = filteredFaced();
    const split = byBowlerType(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['vs bowler type', volChip(balls.length)]),
    ]);
    if (!balls.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No BBB balls.']));
      return card;
    }
    card.appendChild(_makeSplitTable(split.type, ['pace','spin','unknown'],
      'Type', 'bat'));
    card.appendChild(_makeSplitTable(split.arm, ['right','left','unknown'],
      'Arm', 'bat'));
    return card;
  }
  function vsBatterHandCard() {
    const balls = filteredBowled();
    const buckets = byBattingHand(balls);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['vs batter hand', volChip(balls.length)]),
    ]);
    if (!balls.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No BBB balls.']));
      return card;
    }
    card.appendChild(_makeSplitTable(buckets, ['right','left','unknown'],
      'Hand', 'bowl'));
    return card;
  }
  function _makeSplitTable(buckets, keys, label, view) {
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [
        el('tr', null, [
          el('th', null, [label]),
          el('th', null, ['Balls']),
          el('th', null, ['Runs']),
          el('th', null, [view==='bat' ? 'SR' : 'Econ']),
          el('th', null, ['Wkts']),
          el('th', null, ['Dot %']),
        ])
      ]),
    ]);
    const tb = el('tbody');
    keys.forEach(k => {
      const s = buckets[k];
      const empty = !s || s.balls === 0;
      tb.appendChild(el('tr', {class:empty?'empty':''}, [
        el('td', null, [k]),
        el('td', null, [empty ? '—' : String(s.balls)]),
        el('td', null, [empty ? '—' : String(s.runs)]),
        el('td', null, [empty ? '—' : (view==='bat'
          ? fmtN(s.sr,1) : fmtN(s.econ,2))]),
        el('td', null, [empty ? '—' : String(s.wickets)]),
        el('td', null, [empty ? '—' : fmtN(s.dot_pct,0) + '%']),
      ]));
    });
    tab.appendChild(tb);
    return tab;
  }
  function spellCard() {
    const balls = filteredBowled();
    const spells = detectSpells(balls);
    const first = {balls:0, runs:0, wickets:0, dots:0, legal:0,
      runs_bat:0, runs_extra:0};
    const later = {balls:0, runs:0, wickets:0, dots:0, legal:0,
      runs_bat:0, runs_extra:0};
    spells.forEach(sp => {
      const t = sp.spell_index === 1 ? first : later;
      const s = sp.stats;
      t.balls += s.balls; t.legal += s.legal;
      t.runs_bat += s.runs_bat; t.runs_extra += s.runs_extra;
      t.runs = (t.runs_bat||0) + (t.runs_extra||0);
      t.wickets += s.wickets; t.dots += s.dots;
    });
    function calc(t) {
      const econ = t.legal ? t.runs / t.legal * 6 : null;
      const sr = t.wickets ? t.legal / t.wickets : null;
      const dot_pct = t.legal ? t.dots / t.legal * 100 : null;
      return {balls:t.balls, runs:t.runs, wickets:t.wickets,
        econ, sr, dot_pct};
    }
    const a = calc(first), b = calc(later);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['1st spell vs later spells', volChip(balls.length)]),
    ]);
    if (!balls.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No BBB balls.']));
      return card;
    }
    const tab = el('table', {class:'bucket-table'}, [
      el('thead', null, [
        el('tr', null, [
          el('th', null, ['']),
          el('th', null, ['Balls']),
          el('th', null, ['Runs']),
          el('th', null, ['Econ']),
          el('th', null, ['Wkts']),
          el('th', null, ['Dot %']),
        ])
      ]),
      el('tbody', null, [
        el('tr', null, [
          el('td', null, ['1st spell']),
          el('td', null, [String(a.balls)]),
          el('td', null, [String(a.runs)]),
          el('td', null, [a.econ!=null ? fmtN(a.econ,2) : '—']),
          el('td', null, [String(a.wickets)]),
          el('td', null, [a.dot_pct!=null ? fmtN(a.dot_pct,0)+'%' : '—']),
        ]),
        el('tr', null, [
          el('td', null, ['Later spells']),
          el('td', null, [String(b.balls)]),
          el('td', null, [String(b.runs)]),
          el('td', null, [b.econ!=null ? fmtN(b.econ,2) : '—']),
          el('td', null, [String(b.wickets)]),
          el('td', null, [b.dot_pct!=null ? fmtN(b.dot_pct,0)+'%' : '—']),
        ]),
      ])
    ]);
    card.appendChild(tab);
    return card;
  }
  function inningsListCard() {
    const rows = filteredBatting().slice().sort((a,b) => {
      const ma = DATA.matches.find(m => m.match_id === a.match_id);
      const mb = DATA.matches.find(m => m.match_id === b.match_id);
      return date_yyyymmdd((mb||{}).match_date||'').localeCompare(
             date_yyyymmdd((ma||{}).match_date||''));
    });
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Batting innings (' + rows.length + ')']),
    ]);
    if (!rows.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No innings under the current slicers.']));
      return card;
    }
    const list = el('div', {class:'fix-list'});
    rows.slice(0, 30).forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const sr = r.balls ? (r.runs / r.balls * 100).toFixed(0) : '—';
      const det = '#' + r.position + ' · ' + (r.runs||0) + 'r' +
        (r.balls ? ' (' + r.balls + ' b, SR ' + sr + ')' : '') +
        ' · ' + (r.how_out || '');
      list.appendChild(el('div', {class:'fix'}, [
        el('div', {class:'row1'}, [
          el('div', {class:'left'}, [
            el('div', {class:'date'},
              [m.match_date + ' · ' + m.season + ' · ' + m.home_away.toUpperCase()]),
            el('div', {class:'opp'},
              [(m.home_away==='home'?'vs ':'@ ') + m.opp_club_name]),
          ]),
          el('span', {class:'pill ' + m.result}, [m.result || '?']),
        ]),
        el('div', {class:'meta'}, [det]),
      ]));
    });
    if (rows.length > 30) {
      card.appendChild(el('p', {class:'note'},
        ['Showing 30 most-recent innings; ' + (rows.length-30) +
         ' more match the current slicers.']));
    }
    card.appendChild(list);
    return card;
  }
  function bowlingListCard() {
    const rows = filteredBowling().slice().sort((a,b) => {
      const ma = DATA.matches.find(m => m.match_id === a.match_id);
      const mb = DATA.matches.find(m => m.match_id === b.match_id);
      return date_yyyymmdd((mb||{}).match_date||'').localeCompare(
             date_yyyymmdd((ma||{}).match_date||''));
    });
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Bowling spells (' + rows.length + ')']),
    ]);
    if (!rows.length) {
      card.appendChild(el('p', {class:'empty-note'},
        ['No spells under the current slicers.']));
      return card;
    }
    const list = el('div', {class:'fix-list'});
    rows.slice(0, 30).forEach(r => {
      const m = DATA.matches.find(x => x.match_id === r.match_id);
      if (!m) return;
      const det = (r.overs||'0') + 'o · ' + (r.maidens||0) + 'm · ' +
        (r.runs||0) + 'r · ' + (r.wickets||0) + 'w';
      list.appendChild(el('div', {class:'fix'}, [
        el('div', {class:'row1'}, [
          el('div', {class:'left'}, [
            el('div', {class:'date'},
              [m.match_date + ' · ' + m.season + ' · ' + m.home_away.toUpperCase()]),
            el('div', {class:'opp'},
              [(m.home_away==='home'?'vs ':'@ ') + m.opp_club_name]),
          ]),
          el('span', {class:'pill ' + m.result}, [m.result || '?']),
        ]),
        el('div', {class:'meta'}, [det]),
      ]));
    });
    if (rows.length > 30) {
      card.appendChild(el('p', {class:'note'},
        ['Showing 30 most-recent spells; ' + (rows.length-30) +
         ' more match the current slicers.']));
    }
    card.appendChild(list);
    return card;
  }
  function bestPerformancesCard() {
    const bat = filteredBatting().slice().sort((a,b) =>
      (b.runs||0) - (a.runs||0)).slice(0, 5);
    const bowl = filteredBowling().slice().sort((a,b) => {
      const wd = (b.wickets||0) - (a.wickets||0);
      if (wd !== 0) return wd;
      return (a.runs||0) - (b.runs||0);
    }).slice(0, 5);
    const card = el('div', {class:'card'}, [
      el('h2', null, ['Best performances']),
    ]);
    if (bat.length) {
      card.appendChild(el('h3', null, ['Top 5 batting']));
      const tab = el('table', {class:'bucket-table'}, [
        el('thead', null, [
          el('tr', null, [
            el('th', null, ['Date']),
            el('th', null, ['Vs']),
            el('th', null, ['Runs']),
            el('th', null, ['Bls']),
          ])
        ])
      ]);
      const tb = el('tbody');
      bat.forEach(r => {
        const m = DATA.matches.find(x => x.match_id === r.match_id);
        if (!m) return;
        tb.appendChild(el('tr', null, [
          el('td', null, [m.match_date]),
          el('td', null, [m.opp_club_name]),
          el('td', null, [String(r.runs||0) + (r.not_out?'*':'')]),
          el('td', null, [String(r.balls||'—')]),
        ]));
      });
      tab.appendChild(tb); card.appendChild(tab);
    }
    if (bowl.length) {
      card.appendChild(el('h3', null, ['Top 5 bowling']));
      const tab = el('table', {class:'bucket-table'}, [
        el('thead', null, [
          el('tr', null, [
            el('th', null, ['Date']),
            el('th', null, ['Vs']),
            el('th', null, ['Figures']),
            el('th', null, ['Overs']),
          ])
        ])
      ]);
      const tb = el('tbody');
      bowl.forEach(r => {
        const m = DATA.matches.find(x => x.match_id === r.match_id);
        if (!m) return;
        tb.appendChild(el('tr', null, [
          el('td', null, [m.match_date]),
          el('td', null, [m.opp_club_name]),
          el('td', null, [(r.wickets||0) + '/' + (r.runs||0)]),
          el('td', null, [r.overs||'—']),
        ]));
      });
      tab.appendChild(tb); card.appendChild(tab);
    }
    if (!bat.length && !bowl.length) {
      card.appendChild(el('p', {class:'empty-note'}, ['No data.']));
    }
    return card;
  }

})();
