
// Player metadata page — runtime renderer + submission form.
//
// Reads ?id=<player_id> from the URL, fetches
// ../data/players/<id>.json, and builds the page. The submit form has
// NO backend; it composes a structured plain-text body and opens either
// a mailto: or wa.me/ link.
(function() {
  const SUBMIT_EMAIL = "jono@example.com";
  const SUBMIT_WHATSAPP = "447700900123";

  const params = new URLSearchParams(location.search);
  const pid = parseInt(params.get('id'), 10);
  if (!pid) {
    document.getElementById('player-name').textContent = 'Missing ?id=';
    return;
  }

  // One bundled JSON for every player; cached by the browser after
  // the first hit. Look the player up by id once it lands.
  fetch('../data/players.json')
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(bundle => {
      const p = bundle[String(pid)];
      if (!p) {
        document.getElementById('player-name').textContent =
          'Player #' + pid + ' not in bundle';
        return;
      }
      render(p);
    })
    .catch(err => {
      document.getElementById('player-name').textContent =
        'Failed to load players.json (' + err + ')';
    });

  const FIELD_LABELS = {
    batting_hand: 'Batting hand',
    bowling_type: 'Bowling type',
    pace_type:    'Pace type',
    spin_type:    'Spin type',
    bowling_arm:  'Bowling arm',
    angle_to_rhb: 'Angle to RHB',
  };

  const FIELD_OPTIONS = {
    batting_hand: [['right','Right-handed'],['left','Left-handed'],['unknown','Unknown']],
    bowling_type: [['pace','Pace'],['spin','Spin'],['none',"Doesn't bowl"],['unknown','Unknown']],
    pace_type:    [['fast','Fast'],['medium','Medium'],['slow','Slow'],['unknown','Unknown']],
    spin_type:    [['wrist','Wrist'],['finger','Finger'],['unknown','Unknown']],
    bowling_arm:  [['right','Right arm'],['left','Left arm'],['unknown','Unknown']],
    angle_to_rhb: [['over','Over the wicket'],['round','Round the wicket'],
                   ['varies','Varies'],['unknown','Unknown']],
  };

  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'html') e.innerHTML = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (children) for (const c of children) {
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return e;
  }

  function statusOf(meta) {
    if (!meta) return 'not captured';
    const has = k => meta[k] != null && meta[k] !== '';
    if (!has('batting_hand') || !has('bowling_type')) return 'partial';
    if (meta.bowling_type === 'pace' || meta.bowling_type === 'spin') {
      if (!has('bowling_arm') || !has('angle_to_rhb')) return 'partial';
      if (meta.bowling_type === 'pace' && !has('pace_type')) return 'partial';
      if (meta.bowling_type === 'spin' && !has('spin_type')) return 'partial';
    }
    return 'complete';
  }

  function statusPill(s) {
    const cls = {
      'complete': 'complete',
      'partial': 'partial',
      'not captured': 'notcap',
    }[s] || 'notcap';
    const sp = el('span', {class: 'tag ' + cls});
    sp.textContent = s;
    return sp;
  }

  function render(p) {
    document.title = p.name + ' — metadata';
    const nameEl = document.getElementById('player-name');
    nameEl.innerHTML = '';
    nameEl.appendChild(document.createTextNode(p.name + ' '));
    const sub = el('span', {class: 'muted', style: 'font-size:14px'});
    sub.textContent = '#' + p.player_id;
    nameEl.appendChild(sub);

    const stEl = document.getElementById('player-status');
    stEl.innerHTML = '';
    stEl.appendChild(document.createTextNode('Status: '));
    stEl.appendChild(statusPill(statusOf(p.metadata)));
    if (p.seen_clubs && p.seen_clubs.length) {
      stEl.appendChild(document.createTextNode(' · clubs seen: '));
      p.seen_clubs.forEach((c, i) => {
        if (i) stEl.appendChild(document.createTextNode(', '));
        const a = el('a', {href: 'club/' + c.club_id + '.html'});
        a.textContent = c.club_name;
        stEl.appendChild(a);
      });
    }

    // Current metadata
    const mb = document.getElementById('meta-box');
    mb.appendChild(el('h2', null, ['Current metadata']));
    const m = p.metadata || {};
    const fields = ['batting_hand','bowling_type','pace_type','spin_type',
                    'bowling_arm','angle_to_rhb'];
    const populated = fields.filter(k => m[k]);
    if (!populated.length) {
      mb.appendChild(el('p', {class: 'note'}, ['No metadata captured yet.']));
    } else {
      const row = el('div', {class: 'row', style: 'flex-wrap:wrap'});
      populated.forEach(k => {
        const cell = el('div', null, [
          el('label', null, [FIELD_LABELS[k]]),
          el('div', null, [String(m[k])]),
        ]);
        row.appendChild(cell);
      });
      mb.appendChild(row);
      if (m.notes) mb.appendChild(el('p', {class: 'note'}, [m.notes]));
    }

    // Video evidence
    const vb = document.getElementById('video-box');
    vb.appendChild(el('h2', null, ['Video evidence']));
    const withVideos = (p.evidence_matches || []);
    if (!withVideos.length) {
      vb.appendChild(el('p', {class: 'note'}, [
        'No videos recorded for matches involving this player yet. ' +
        '(Video links live in data/metadata/videos/<match_id>.json.)'
      ]));
    } else {
      const tbl = el('table');
      tbl.innerHTML =
        '<thead><tr><th>Date</th><th>Opp</th><th>Videos</th></tr></thead>';
      const tb = el('tbody');
      withVideos.forEach(mt => {
        const tr = el('tr');
        tr.appendChild(el('td', null, [mt.date]));
        tr.appendChild(el('td', null, [mt.opp]));
        const td = el('td');
        mt.videos.forEach((v, i) => {
          if (i) td.appendChild(document.createTextNode(' · '));
          const a = el('a', {href: v.url, target: '_blank'});
          a.textContent = v.label || 'watch';
          td.appendChild(a);
        });
        tr.appendChild(td);
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      vb.appendChild(tbl);
    }

    // Submit form
    renderForm(p);
  }

  function renderForm(p) {
    const fb = document.getElementById('form-box');
    fb.appendChild(el('h2', null, ['Submit metadata']));
    fb.appendChild(el('p', {class: 'note'}, [
      'Fill in what you know. The button below opens your email or ' +
      'WhatsApp with a structured message; Jono receives it and adds ' +
      'it to the queue. Nothing is sent until you press the button in ' +
      'your mail / WA app.'
    ]));

    const form = el('form', {id: 'metaForm', onsubmit: 'return false;'});
    const m = p.metadata || {};

    function selectFor(name, opts) {
      const sel = el('select', {id: 'f-' + name, name: name});
      sel.appendChild(el('option', {value: ''}, ['(unchanged)']));
      opts.forEach(([v, l]) => {
        const o = el('option', {value: v}, [l]);
        if ((m[name] || '') === v) o.setAttribute('selected', 'selected');
        sel.appendChild(o);
      });
      return sel;
    }

    function row(items) {
      const r = el('div', {class: 'row'});
      items.forEach(it => r.appendChild(it));
      return r;
    }
    function labeled(lbl, ctrl) {
      return el('div', null, [el('label', null, [lbl]), ctrl]);
    }

    form.appendChild(row([
      labeled('Batting hand',     selectFor('batting_hand', FIELD_OPTIONS.batting_hand)),
      labeled('Bowling type',     selectFor('bowling_type', FIELD_OPTIONS.bowling_type)),
    ]));
    form.appendChild(row([
      labeled('Pace type (if pace)', selectFor('pace_type', FIELD_OPTIONS.pace_type)),
      labeled('Spin type (if spin)', selectFor('spin_type', FIELD_OPTIONS.spin_type)),
    ]));
    form.appendChild(row([
      labeled('Bowling arm',  selectFor('bowling_arm', FIELD_OPTIONS.bowling_arm)),
      labeled('Angle to RHB', selectFor('angle_to_rhb', FIELD_OPTIONS.angle_to_rhb)),
    ]));

    const vidMatches = (p.evidence_matches || []);
    const evid = el('select', {id: 'f-evidence', name: 'evidence_match_ids',
                               multiple: 'multiple', size: '5'});
    if (vidMatches.length) {
      vidMatches.forEach(mt => {
        const lbl = mt.date + ' — ' + mt.opp + ' (' + mt.videos.length +
                    ' video' + (mt.videos.length !== 1 ? 's' : '') + ')';
        evid.appendChild(el('option', {value: String(mt.match_id)}, [lbl]));
      });
    } else {
      const o = el('option', null, ['(no matches with video evidence yet)']);
      o.setAttribute('disabled', 'disabled');
      evid.appendChild(o);
    }
    form.appendChild(el('label', null,
      ['Evidence matches (Cmd/Ctrl-click for multi)']));
    form.appendChild(evid);

    form.appendChild(el('label', null, ['Notes / reasoning (optional)']));
    form.appendChild(el('textarea', {
      id: 'f-notes', name: 'notes',
      placeholder: 'Saw him bowl in the U13 game…',
    }));
    form.appendChild(el('label', null, ['Your name (optional)']));
    form.appendChild(el('input', {
      id: 'f-by', name: 'submitted_by',
      placeholder: 'e.g. Jane Smith',
    }));

    const btnRow = el('div', {style: 'margin-top:14px'});
    const mailBtn = el('a', {href: '#', id: 'sendMail', class: 'btn'},
                       ['Send via email']);
    const waBtn   = el('a', {href: '#', id: 'sendWA', class: 'btn secondary'},
                       ['Send via WhatsApp']);
    btnRow.appendChild(mailBtn);
    btnRow.appendChild(waBtn);
    form.appendChild(btnRow);
    form.appendChild(el('p', {class: 'note'}, [
      'Receiving address: ',
      el('code', {id: 'addrPreview'}),
    ]));

    document.getElementById('form-box').appendChild(form);

    document.getElementById('addrPreview').textContent =
      SUBMIT_EMAIL ? SUBMIT_EMAIL :
      (SUBMIT_WHATSAPP ? '+' + SUBMIT_WHATSAPP + ' (WhatsApp)' :
       '(not configured)');

    function valOf(name) {
      const e = document.getElementById('f-' + name);
      if (!e) return '';
      if (e.multiple) {
        return Array.from(e.selectedOptions).map(o => o.value).join(', ');
      }
      return e.value;
    }
    function buildBody() {
      return [
        'PLAYER METADATA SUBMISSION',
        'player_id: ' + p.player_id,
        'player_name: ' + p.name,
        'batting_hand: ' + valOf('batting_hand'),
        'bowling_type: ' + valOf('bowling_type'),
        'pace_type: ' + valOf('pace_type'),
        'spin_type: ' + valOf('spin_type'),
        'bowling_arm: ' + valOf('bowling_arm'),
        'angle_to_rhb: ' + valOf('angle_to_rhb'),
        'evidence_match_ids: ' + valOf('evidence'),
        'notes: ' + valOf('notes'),
        'submitted_by: ' + valOf('by'),
      ].join('\n');
    }

    mailBtn.addEventListener('click', e => {
      e.preventDefault();
      if (!SUBMIT_EMAIL) { alert('Email destination not configured'); return; }
      const subject = 'Player metadata: ' + p.name + ' (#' + p.player_id + ')';
      location.href = 'mailto:' + encodeURIComponent(SUBMIT_EMAIL)
        + '?subject=' + encodeURIComponent(subject)
        + '&body=' + encodeURIComponent(buildBody());
    });
    waBtn.addEventListener('click', e => {
      e.preventDefault();
      if (!SUBMIT_WHATSAPP) { alert('WhatsApp destination not configured'); return; }
      window.open('https://wa.me/' + SUBMIT_WHATSAPP
        + '?text=' + encodeURIComponent(buildBody()), '_blank');
    });
  }
})();
