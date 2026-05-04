
// Player metadata page — runtime renderer + submission form.
//
// Reads ?id=<player_id> from the URL, fetches ../data/players.json
// (one bundled file, ~3 MB, browser-cached after first hit) and looks
// the player up by id. The submit form has NO backend; it composes a
// structured plain-text body and opens either a mailto: or wa.me/ link.
(function() {
  const SUBMIT_EMAIL = "jjoneill4@gmail.com";
  const SUBMIT_WHATSAPP = "447908474929";

  const params = new URLSearchParams(location.search);
  const pid = parseInt(params.get('id'), 10);
  if (!pid) {
    document.getElementById('player-name').textContent = 'Missing ?id=';
    return;
  }

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
    nameEl.textContent = p.name;

    const stEl = document.getElementById('player-status');
    stEl.innerHTML = '';
    stEl.appendChild(document.createTextNode('#' + p.player_id + ' · '));
    stEl.appendChild(statusPill(statusOf(p.metadata)));
    if (p.seen_clubs && p.seen_clubs.length) {
      stEl.appendChild(document.createTextNode(' · '));
      const clubsBit = el('span', {style: 'opacity:.85'});
      p.seen_clubs.forEach((c, i) => {
        if (i) clubsBit.appendChild(document.createTextNode(', '));
        const a = el('a', {href: 'club/' + c.club_id + '.html',
                           style: 'color:#fff'});
        a.textContent = c.club_name;
        clubsBit.appendChild(a);
      });
      stEl.appendChild(clubsBit);
    }

    // Current metadata
    const mb = document.getElementById('meta-box');
    const card = el('div', {class: 'card'});
    card.appendChild(el('h2', null, ['Current metadata']));
    const m = p.metadata || {};
    const fields = ['batting_hand','bowling_type','pace_type','spin_type',
                    'bowling_arm','angle_to_rhb'];
    const populated = fields.filter(k => m[k]);
    if (!populated.length) {
      card.appendChild(el('p', {class: 'note'}, ['No metadata captured yet.']));
    } else {
      const grid = el('div', {class: 'meta-grid'});
      populated.forEach(k => {
        grid.appendChild(el('div', null, [
          el('div', {class: 'k'}, [FIELD_LABELS[k]]),
          el('div', {class: 'v'}, [String(m[k])]),
        ]));
      });
      card.appendChild(grid);
      if (m.notes) card.appendChild(el('p', {class: 'note'}, [m.notes]));
    }
    mb.appendChild(card);

    // Video evidence
    const vb = document.getElementById('video-box');
    const vCard = el('div', {class: 'card'});
    vCard.appendChild(el('h2', null, ['Video evidence']));
    const withVideos = (p.evidence_matches || []);
    if (!withVideos.length) {
      vCard.appendChild(el('p', {class: 'note'}, [
        'No videos recorded for matches involving this player yet. ' +
        'Video links live in data/metadata/videos/<match_id>.json.'
      ]));
    } else {
      const list = el('div', {class: 'fix-list'});
      withVideos.forEach(mt => {
        const card = el('div', {class: 'fix'});
        const r1 = el('div', {class: 'row1'});
        const left = el('div', {class: 'left'});
        left.appendChild(el('div', {class: 'date'}, [mt.date]));
        left.appendChild(el('div', {class: 'opp'}, ['vs ' + mt.opp]));
        r1.appendChild(left);
        const right = el('div', {class: 'right'});
        right.appendChild(el('span', {class: 'tag yes'},
          ['🎬 ' + mt.videos.length]));
        r1.appendChild(right);
        card.appendChild(r1);
        mt.videos.forEach(v => {
          const a = el('a', {href: v.url, target: '_blank',
                              style: 'display:block;font-size:12px;' +
                                     'margin-top:3px;font-weight:600'});
          a.textContent = v.label || 'watch';
          card.appendChild(a);
        });
        list.appendChild(card);
      });
      vCard.appendChild(list);
    }
    vb.appendChild(vCard);

    renderForm(p);
  }

  function renderForm(p) {
    const fb = document.getElementById('form-box');
    const card = el('div', {class: 'card'});
    card.appendChild(el('h2', null, ['Submit metadata']));
    card.appendChild(el('p', {class: 'note'}, [
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
      const r = el('div', {class: 'field-row'});
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
                               multiple: 'multiple', size: '4'});
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
    form.appendChild(el('label', null, ['Evidence matches (multi-select)']));
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

    const btnRow = el('div', {class: 'btn-row'});
    const mailBtn = el('a', {href: '#', id: 'sendMail', class: 'btn'},
                       ['Send via email']);
    const waBtn   = el('a', {href: '#', id: 'sendWA', class: 'btn secondary'},
                       ['Send via WhatsApp']);
    btnRow.appendChild(mailBtn);
    btnRow.appendChild(waBtn);
    form.appendChild(btnRow);
    form.appendChild(el('p', {class: 'note', style: 'margin-top:10px'},
      ['Receiving address: ', el('code', {id: 'addrPreview'})]));

    card.appendChild(form);
    fb.appendChild(card);

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
