const DATA_URLS = {
  cme:  'https://mda1125.github.io/nfdm-data/data/cme.json',
  nass: 'https://mda1125.github.io/nfdm-data/data/nass.json',
  c4:   'https://mda1125.github.io/nfdm-data/data/class_iv.json',
  futures: 'https://mda1125.github.io/nfdm-data/data/futures.json',
  futuresHistory: 'https://mda1125.github.io/nfdm-data/data/futures_history.json',
  fundamentals: 'https://mda1125.github.io/nfdm-data/data/fundamentals.json'
};

let RAW = null;
let activeDays = 180;
let charts = {};
let lastUpdated = null;
let dataMode = 'simulated';

function showToast(msg, isError) {
  const t = document.createElement('div');
  t.className = 'toast' + (isError ? ' error' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function(){ t.remove(); }, 4500);
}

function setStatusPill() {
  const el = document.getElementById('status-pill');
  if (!el) return;
  if (dataMode === 'live') {
    const t = lastUpdated ? new Date(lastUpdated).toLocaleString() : '';
    el.innerHTML = '<span class="status-pill status-live">● LIVE</span> <span style="color:#6e7681;font-size:10px">updated ' + t + '</span>';
  } else if (dataMode === 'error') {
    el.innerHTML = '<span class="status-pill status-err">● OFFLINE</span> <span style="color:#6e7681;font-size:10px">using simulated data</span>';
  } else {
    el.innerHTML = '<span class="status-pill status-sim">● DEMO</span> <span style="color:#6e7681;font-size:10px">simulated data</span>';
  }
}

function genData() {
  const MS = 86400000;
  const cme = [], nass = [], c4 = [];
  const cmeSeeds = [[0,1.08],[30,1.12],[60,1.09],[90,1.15],[120,1.28],[150,1.35],[180,1.55],[210,1.72],[240,1.90],[270,2.05],[300,2.15],[330,2.20],[360,2.26]];
  let tradingDay = 0;
  for (let d = new Date('2024-10-14'); d <= new Date('2026-04-29'); d = new Date(d.getTime()+MS)) {
    const dow = d.getDay();
    if (dow === 0 || dow === 6) continue;
    const pct = tradingDay/403*360;
    let price = 1.08;
    for (let i = 0; i < cmeSeeds.length-1; i++) {
      if (pct >= cmeSeeds[i][0] && pct <= cmeSeeds[i+1][0]) {
        const t = (pct - cmeSeeds[i][0]) / (cmeSeeds[i+1][0] - cmeSeeds[i][0]);
        price = cmeSeeds[i][1] + (cmeSeeds[i+1][1] - cmeSeeds[i][1]) * t;
        break;
      }
    }
    price += (Math.random() - 0.45) * 0.012;
    cme.push({date: new Date(d), price: +price.toFixed(4)});
    tradingDay++;
  }
  const nassSeeds = [['2012-03-01',0.95],['2014-07-01',2.05],['2016-07-01',0.72],['2018-01-01',0.97],['2020-01-01',0.88],['2022-07-01',1.95],['2024-01-01',1.10],['2025-07-01',1.72],['2026-03-27',1.96]];
  const ns = nassSeeds.map(function(x){return {t: new Date(x[0]).getTime(), p: x[1]};});
  function interpNass(t) {
    for (let i = 0; i < ns.length-1; i++) {
      if (t >= ns[i].t && t <= ns[i+1].t) {
        const f = (t - ns[i].t) / (ns[i+1].t - ns[i].t);
        return ns[i].p + (ns[i+1].p - ns[i].p) * f;
      }
    }
    return ns[ns.length-1].p;
  }
  let nd = new Date('2012-03-03');
  while (nd <= new Date('2026-03-27')) {
    const base = interpNass(nd.getTime());
    nass.push({date: new Date(nd), price: +(base + (Math.random()-0.5)*0.008).toFixed(4)});
    nd = new Date(nd.getTime() + 7*MS);
  }
  const c4Seeds = [['2012-04',15.80],['2014-07',24.60],['2016-07',12.50],['2020-07',13.50],['2022-07',23.40],['2024-07',15.80],['2026-04',22.10]];
  const cs = c4Seeds.map(function(x){const p=x[0].split('-');return {t: new Date(+p[0],+p[1]-1,1).getTime(), p: x[1]};});
  function interpC4(t) {
    for (let i = 0; i < cs.length-1; i++) {
      if (t >= cs[i].t && t <= cs[i+1].t) {
        const f = (t - cs[i].t) / (cs[i+1].t - cs[i].t);
        return cs[i].p + (cs[i+1].p - cs[i].p) * f;
      }
    }
    return cs[cs.length-1].p;
  }
  let cd = new Date('2012-04-01');
  while (cd <= new Date('2026-04-01')) {
    const announced = +interpC4(cd.getTime()).toFixed(2);
    const nfdmM = interpNass(cd.getTime()) + (Math.random()-0.3)*0.06;
    const butter = 1.80 + Math.random()*0.80;
    const skim = ((nfdmM - 0.1678) * 0.99) * 9;
    const bfat = (butter - 0.1715) * 1.211;
    const implied = +((skim*0.965 + bfat*3.5) * 100).toFixed(2);
    c4.push({date: new Date(cd), announced: announced, implied: Math.max(10, implied), nfdm: +nfdmM.toFixed(4), butter: +butter.toFixed(4), skim: +(skim*100).toFixed(2), bfat: +(bfat*100).toFixed(2)});
    cd = new Date(cd.getFullYear(), cd.getMonth()+1, 1);
  }
  return {cme: cme, nass: nass, c4: c4};
}

async function fetchLiveData() {
  const btn = document.getElementById('refresh-btn');
  if (btn) btn.classList.add('loading');
  try {
    const responses = await Promise.all([
      fetch(DATA_URLS.cme, {cache: 'no-store'}),
      fetch(DATA_URLS.nass, {cache: 'no-store'}),
      fetch(DATA_URLS.c4, {cache: 'no-store'}),
      fetch(DATA_URLS.futures, {cache: 'no-store'}).catch(function(){ return null; }),
      fetch(DATA_URLS.fundamentals, {cache: 'no-store'}).catch(function(){ return null; }),
      fetch(DATA_URLS.futuresHistory, {cache: 'no-store'}).catch(function(){ return null; })
    ]);
    if (!responses[0].ok || !responses[1].ok || !responses[2].ok) {
      throw new Error('HTTP ' + responses[0].status + '/' + responses[1].status + '/' + responses[2].status);
    }
    const mainJsons = await Promise.all(responses.slice(0,3).map(function(r){return r.json();}));
    var futJ = null, fundJ = null, futHistJ = null;
    if (responses[3] && responses[3].ok) {
      try { futJ = await responses[3].json(); } catch(e) { futJ = null; }
    }
    if (responses[4] && responses[4].ok) {
      try { fundJ = await responses[4].json(); } catch(e) { fundJ = null; }
    }
    if (responses[5] && responses[5].ok) {
      try { futHistJ = await responses[5].json(); } catch(e) { futHistJ = null; }
    }
    const cmeJ = mainJsons[0], nassJ = mainJsons[1], c4J = mainJsons[2];
    const cme = (cmeJ.data || cmeJ).map(function(d){return {date: new Date(d.date), price: +d.price};});
    const nass = (nassJ.data || nassJ).map(function(d){return {date: new Date(d.date), price: +d.price, volume: +(d.volume || 0), final: d.final !== false};});
    const c4 = (c4J.data || c4J).map(function(d){
      return {
        date: new Date(d.date),
        announced: +d.announced,
        implied: +(d.implied || 0),
        nfdm: +(d.nfdm_avg || d.nfdm || 0),
        butter: +(d.butter_avg || d.butter || 0),
        skim: +(d.skim || 0),
        bfat: +(d.butterfat || d.bfat || 0)
      };
    });
    var futures = null;
    if (futJ && futJ.data && futJ.data.length) {
      futures = {
        trade_date: futJ.trade_date || '',
        spot: futJ.spot || null,
        data: futJ.data.map(function(d){return {month: d.month, label: d.label, settle: +d.settle, volume: +(d.volume || 0)};})
      };
    }
    var fund = null;
    if (fundJ && fundJ.data && fundJ.data.length) {
      fund = fundJ.data.map(function(d){return {
        month: d.month,
        nfdm_production: d.nfdm_production || null,
        nfdm_stocks: d.nfdm_stocks || null,
        butter_production: d.butter_production || null,
        butter_stocks: d.butter_stocks || null,
        milk_production: d.milk_production || null
      };});
    }
    var futHist = null;
    if (futHistJ && futHistJ.snapshots && futHistJ.snapshots.length) {
      futHist = futHistJ.snapshots;
    }
    const stamps = [cmeJ.updated_at, nassJ.updated_at, c4J.updated_at].filter(Boolean);
    lastUpdated = stamps.sort().reverse()[0] || new Date().toISOString();
    dataMode = 'live';
    var toastMsg = 'Live data loaded · ' + cme.length + ' CME · ' + nass.length + ' NASS · ' + c4.length + ' months';
    if (futures) toastMsg += ' · ' + futures.data.length + ' futures';
    if (fund) toastMsg += ' · ' + fund.length + ' supply';
    if (futHist) toastMsg += ' · ' + futHist.length + ' snapshots';
    showToast(toastMsg);
    return {cme: cme, nass: nass, c4: c4, futures: futures, fund: fund, futHist: futHist};
  } catch (err) {
    console.warn('Live fetch failed:', err);
    dataMode = 'error';
    showToast("Couldn't reach live data (" + err.message + ") — using simulated", true);
    return genData();
  } finally {
    if (btn) btn.classList.remove('loading');
  }
}

function filterByDays(arr, days) {
  if (days >= 99999) return arr;
  const cutoff = new Date(Date.now() - days * 86400000);
  return arr.filter(function(d){return d.date >= cutoff;});
}

function movingAvg(data, n) {
  n = n || 20;
  return data.map(function(d, i){
    if (i < n-1) return null;
    const slice = data.slice(i-n+1, i+1);
    return +(slice.reduce(function(s,x){return s+x.price;}, 0) / n).toFixed(4);
  });
}

function monthlyAvg(data) {
  var result = [];
  var sum = 0, count = 0, curM = -1, curY = -1;
  for (var i = 0; i < data.length; i++) {
    var m = data[i].date.getMonth(), y = data[i].date.getFullYear();
    if (m !== curM || y !== curY) { sum = 0; count = 0; curM = m; curY = y; }
    sum += data[i].price; count++;
    result.push(+(sum / count).toFixed(4));
  }
  return result;
}

function joinCmeNass(cme, nass) {
  return nass.map(function(n){
    const weekStart = new Date(n.date.getTime() - 6*86400000);
    const weekCme = cme.filter(function(c){return c.date >= weekStart && c.date <= n.date;});
    const avg = weekCme.length ? weekCme.reduce(function(s,c){return s+c.price;}, 0) / weekCme.length : null;
    return Object.assign({}, n, {cmeavg: avg ? +avg.toFixed(4) : null, basis: avg ? +(avg - n.price).toFixed(4) : null});
  }).filter(function(n){return n.cmeavg !== null;});
}

const COLORS = {cme:'#e6a817', nass:'#60a5fa', ma:'#a78bfa', c4a:'#4ade80', c4i:'#e6a817', grid:'rgba(48,54,61,0.8)', tick:'#6e7681'};

function baseOpts(yLabel) {
  return {
    responsive: true, maintainAspectRatio: false,
    interaction: {mode: 'index', intersect: false},
    plugins: {
      legend: {display: false},
      tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10}
    },
    scales: {
      x: {ticks: {color: COLORS.tick, maxTicksLimit: 8, maxRotation: 0}, grid: {color: COLORS.grid}},
      y: {ticks: {color: COLORS.tick, callback: function(v){return '$' + v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: yLabel, color: COLORS.tick}}
    }
  };
}

function fmtDate(d) {
  return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: '2-digit'});
}
function fmtMonth(d) {
  return d.toLocaleDateString('en-US', {month: 'short', year: '2-digit'});
}

function buildCharts() {
  if (!RAW) return;
  const cmeFilt = filterByDays(RAW.cme, activeDays);
  const nassFilt = filterByDays(RAW.nass, activeDays);
  const joined = joinCmeNass(cmeFilt, nassFilt);
  const c4Filt = filterByDays(RAW.c4, activeDays);

  Object.values(charts).forEach(function(c){try{c.destroy();}catch(e){}});
  charts = {};

  function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }
  function setDelta(id, v, suffix) {
    const e = document.getElementById(id);
    if (!e) return;
    const up = v >= 0;
    e.className = 'kpi-delta ' + (up ? 'up' : 'down');
    e.textContent = (up ? '↑ +' : '↓ ') + v.toFixed(4) + ' · ' + (suffix || '');
  }

  function nassAlignedToCme(cmeArr) {
    return cmeArr.map(function(cd){
      const near = RAW.nass.reduce(function(a,b){return Math.abs(b.date - cd.date) < Math.abs(a.date - cd.date) ? b : a;});
      return Math.abs(near.date - cd.date) < 4*86400000 ? near.price : null;
    });
  }

  const ctxM = document.getElementById('chart-main');
  if (ctxM && cmeFilt.length) {
    charts.main = new Chart(ctxM, {
      type: 'line',
      data: {
        labels: cmeFilt.map(function(d){return fmtDate(d.date);}),
        datasets: [
          {label: 'CME spot', data: cmeFilt.map(function(d){return d.price;}), borderColor: COLORS.cme, borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: {target: 'origin', above: 'rgba(230,168,23,0.06)'}},
          {label: 'NASS weekly', data: nassAlignedToCme(cmeFilt), borderColor: COLORS.nass, borderWidth: 1.5, pointRadius: 0, tension: 0.3, spanGaps: true},
          {label: '20-day MA', data: movingAvg(cmeFilt), borderColor: COLORS.ma, borderWidth: 1.5, pointRadius: 0, tension: 0.3, borderDash: [4,3]},
          {label: 'Month avg', data: monthlyAvg(cmeFilt), borderColor: '#f472b6', borderWidth: 1.5, pointRadius: 0, tension: 0, borderDash: [2,2]}
        ]
      },
      options: baseOpts('$/lb')
    });
  }

  const ctxCN = document.getElementById('chart-cme-nass-full');
  if (ctxCN && cmeFilt.length) {
    charts.cn = new Chart(ctxCN, {
      type: 'line',
      data: {
        labels: cmeFilt.map(function(d){return fmtDate(d.date);}),
        datasets: [
          {label: 'CME spot', data: cmeFilt.map(function(d){return d.price;}), borderColor: COLORS.cme, borderWidth: 1.5, pointRadius: 0, tension: 0.3},
          {label: 'NASS weekly', data: nassAlignedToCme(cmeFilt), borderColor: COLORS.nass, borderWidth: 1.5, pointRadius: 0, tension: 0.3, spanGaps: true},
          {label: '20-day MA', data: movingAvg(cmeFilt), borderColor: COLORS.ma, borderWidth: 1.5, pointRadius: 0, tension: 0.3, borderDash: [4,3]},
          {label: 'Month avg', data: monthlyAvg(cmeFilt), borderColor: '#f472b6', borderWidth: 1.5, pointRadius: 0, tension: 0, borderDash: [2,2]}
        ]
      },
      options: baseOpts('$/lb')
    });
  }

  function basisData() {
    return {
      labels: joined.map(function(d){return fmtDate(d.date);}),
      datasets: [{
        label: 'Basis',
        data: joined.map(function(d){return d.basis;}),
        backgroundColor: joined.map(function(d){return d.basis >= 0 ? 'rgba(74,222,128,0.7)' : 'rgba(248,113,113,0.7)';}),
        borderRadius: 2, borderSkipped: false
      }]
    };
  }

  const ctxB = document.getElementById('chart-basis');
  if (ctxB && joined.length) charts.basis = new Chart(ctxB, {type: 'bar', data: basisData(), options: baseOpts('$/lb')});

  const ctxBF = document.getElementById('chart-basis-full');
  if (ctxBF && joined.length) charts.basisFull = new Chart(ctxBF, {type: 'bar', data: basisData(), options: baseOpts('$/lb')});

  function c4Data() {
    return {
      labels: c4Filt.map(function(d){return fmtMonth(d.date);}),
      datasets: [
        {label: 'Announced', data: c4Filt.map(function(d){return d.announced;}), borderColor: COLORS.c4a, borderWidth: 2, pointRadius: 2, tension: 0.3},
        {label: 'Implied', data: c4Filt.map(function(d){return d.implied;}), borderColor: COLORS.c4i, borderWidth: 1.5, pointRadius: 0, tension: 0.3, borderDash: [4,3]}
      ]
    };
  }

  const ctxCm = document.getElementById('chart-c4-mini');
  if (ctxCm && c4Filt.length) charts.c4mini = new Chart(ctxCm, {type: 'line', data: c4Data(), options: baseOpts('$/cwt')});

  const ctxCF = document.getElementById('chart-c4-full');
  if (ctxCF && c4Filt.length) charts.c4full = new Chart(ctxCF, {type: 'line', data: c4Data(), options: baseOpts('$/cwt')});

  var ctxVol = document.getElementById('chart-volume');
  if (ctxVol && nassFilt.length) {
    var volData = nassFilt.filter(function(d){return d.volume > 0;});
    charts.volume = new Chart(ctxVol, {
      type: 'bar',
      data: {
        labels: volData.map(function(d){return fmtDate(d.date);}),
        datasets: [{
          label: 'Volume (lbs)',
          data: volData.map(function(d){return d.volume;}),
          backgroundColor: volData.map(function(d){return d.final ? 'rgba(96,165,250,0.6)' : 'rgba(96,165,250,0.3)';}),
          borderColor: volData.map(function(d){return d.final ? 'rgba(96,165,250,0.9)' : 'rgba(96,165,250,0.5)';}),
          borderWidth: 1,
          borderRadius: 2, borderSkipped: false
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {
          legend: {display: false},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            callbacks: {label: function(ctx){return (ctx.parsed.y/1e6).toFixed(1) + 'M lbs';}}}
        },
        scales: {
          x: {ticks: {color: COLORS.tick, maxTicksLimit: 8, maxRotation: 0}, grid: {color: COLORS.grid}},
          y: {ticks: {color: COLORS.tick, callback: function(v){return (v/1e6).toFixed(0) + 'M';}}, grid: {color: COLORS.grid}, title: {display: true, text: 'lbs', color: COLORS.tick}}
        }
      }
    });
  }

  // Futures curve
  var ctxFut = document.getElementById('chart-futures');
  if (ctxFut && RAW.futures && RAW.futures.data.length) {
    var fd = RAW.futures.data;
    var spotLine = RAW.futures.spot;
    var spotAnnotation = spotLine ? [{
      type: 'line', yMin: spotLine, yMax: spotLine,
      borderColor: '#4ade80', borderWidth: 1.5, borderDash: [6,3],
      label: {display: true, content: 'Spot $' + spotLine.toFixed(4), position: 'start', backgroundColor: '#4ade8033', color: '#4ade80', font: {size: 10}}
    }] : [];
    charts.futures = new Chart(ctxFut, {
      type: 'line',
      data: {
        labels: fd.map(function(d){return d.label;}),
        datasets: [{
          label: 'Settlement',
          data: fd.map(function(d){return d.settle;}),
          borderColor: COLORS.cme, backgroundColor: 'rgba(230,168,23,0.08)',
          borderWidth: 2, pointRadius: 4, pointBackgroundColor: COLORS.cme,
          tension: 0.3, fill: true
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {
          legend: {display: false},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            callbacks: {label: function(ctx){return '$' + ctx.parsed.y.toFixed(4) + '/lb';}}},
          annotation: spotAnnotation.length ? {annotations: {spotLine: spotAnnotation[0]}} : undefined
        },
        scales: {
          x: {ticks: {color: COLORS.tick, maxRotation: 45, font: {size: 11}}, grid: {color: COLORS.grid}},
          y: {ticks: {color: COLORS.tick, callback: function(v){return '$' + v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: '$/lb', color: COLORS.tick}}
        }
      }
    });

    var front = fd[0], back = fd[fd.length - 1];
    var spread = back.settle - front.settle;
    var isBackward = spread < 0;
    setText('kpi-front', '$' + front.settle.toFixed(4));
    setText('kpi-front-label', front.label + ' · $/lb');
    setText('kpi-back', '$' + back.settle.toFixed(4));
    setText('kpi-back-label', back.label + ' · $/lb');
    setText('kpi-spread', (spread >= 0 ? '+' : '') + '$' + spread.toFixed(4));
    setText('kpi-curve-label', isBackward ? 'Backwardation' : 'Contango');
    var ctag = document.getElementById('kpi-curve-tag');
    if (ctag) {
      ctag.textContent = isBackward ? '↘ BACKWARDATION' : '↗ CONTANGO';
      ctag.style.background = isBackward ? '#dc262622' : '#15803d22';
      ctag.style.color = isBackward ? '#f87171' : '#4ade80';
      ctag.style.borderColor = isBackward ? '#dc262644' : '#15803d44';
    }

    var futDate = document.getElementById('futures-trade-date');
    if (futDate) futDate.textContent = 'Trade date: ' + (RAW.futures.trade_date || '—');
    var tblF = document.getElementById('tbl-futures');
    if (tblF) {
      tblF.innerHTML = '<tr><th>Contract</th><th style="text-align:right">Settle</th><th style="text-align:right">vs Spot</th><th style="text-align:right">Volume</th></tr>' +
        fd.map(function(d){
          var diff = spotLine ? d.settle - spotLine : 0;
          var diffStr = spotLine ? (diff >= 0 ? '+' : '') + diff.toFixed(4) : '—';
          var diffCol = diff >= 0 ? '#4ade80' : '#f87171';
          return '<tr><td>' + d.label + '</td><td style="text-align:right">$' + d.settle.toFixed(4) + '</td><td style="text-align:right;color:' + diffCol + '">' + diffStr + '</td><td style="text-align:right">' + (d.volume || '—') + '</td></tr>';
        }).join('');
    }
  }

  // Seasonals
  var ctxSeas = document.getElementById('chart-seasonals');
  if (ctxSeas && RAW.nass.length) {
    var curYear = new Date().getFullYear();
    function weekOfYear(d) { var start = new Date(d.getFullYear(),0,1); return Math.ceil(((d - start) / 86400000 + start.getDay() + 1) / 7); }

    var byYear = {};
    RAW.nass.forEach(function(d) {
      var y = d.date.getFullYear(), w = weekOfYear(d.date);
      if (!byYear[y]) byYear[y] = [];
      byYear[y].push({week: w, price: d.price});
    });
    Object.keys(byYear).forEach(function(y) {
      byYear[y].sort(function(a,b){return a.week - b.week;});
    });

    var rangeYears = [];
    for (var ry = curYear - 5; ry < curYear; ry++) { if (byYear[ry]) rangeYears.push(ry); }

    var weekLabels = [];
    var rangeMin = [], rangeMax = [], rangeAvg = [];
    var weekMonths = ['','Jan','','','','Feb','','','','Mar','','','','Apr','','','','May','','','','Jun','','','','Jul','','','','Aug','','','','Sep','','','','Oct','','','','Nov','','','','Dec','','','','','','',''];
    for (var wk = 1; wk <= 52; wk++) {
      weekLabels.push(weekMonths[wk] || '');
      var vals = [];
      rangeYears.forEach(function(yr) {
        var match = byYear[yr] && byYear[yr].find(function(d){return d.week === wk;});
        if (match) vals.push(match.price);
      });
      rangeMin.push(vals.length ? Math.min.apply(null, vals) : null);
      rangeMax.push(vals.length ? Math.max.apply(null, vals) : null);
      rangeAvg.push(vals.length ? +(vals.reduce(function(s,v){return s+v;},0) / vals.length).toFixed(4) : null);
    }

    var curYearData = new Array(52).fill(null);
    if (byYear[curYear]) {
      byYear[curYear].forEach(function(d) { if (d.week >= 1 && d.week <= 52) curYearData[d.week - 1] = d.price; });
    }

    var priorYearColors = ['#6b7280','#9ca3af','#4b5563','#d1d5db'];
    var showYears = [];
    for (var sy = curYear - 1; sy >= curYear - 4; sy--) { if (byYear[sy]) showYears.push(sy); }

    var datasets = [
      {label: '5Y max', data: rangeMax, borderColor: 'transparent', backgroundColor: 'rgba(167,139,250,0.12)', pointRadius: 0, fill: '+1', tension: 0.3, order: 10},
      {label: '5Y min', data: rangeMin, borderColor: 'transparent', backgroundColor: 'rgba(167,139,250,0.12)', pointRadius: 0, fill: false, tension: 0.3, order: 10},
      {label: '5Y avg', data: rangeAvg, borderColor: '#a78bfa', borderWidth: 1.5, pointRadius: 0, tension: 0.3, borderDash: [6,3], fill: false, order: 5}
    ];
    showYears.forEach(function(yr, idx) {
      var yd = new Array(52).fill(null);
      byYear[yr].forEach(function(d) { if (d.week >= 1 && d.week <= 52) yd[d.week - 1] = d.price; });
      datasets.push({label: '' + yr, data: yd, borderColor: priorYearColors[idx % priorYearColors.length], borderWidth: 1, pointRadius: 0, tension: 0.3, fill: false, order: 3});
    });
    datasets.push({label: '' + curYear, data: curYearData, borderColor: COLORS.cme, borderWidth: 2.5, pointRadius: 2, pointBackgroundColor: COLORS.cme, tension: 0.3, fill: false, order: 1});

    charts.seasonals = new Chart(ctxSeas, {
      type: 'line',
      data: {labels: weekLabels, datasets: datasets},
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {
          legend: {display: false},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            filter: function(item) { return item.raw !== null; },
            callbacks: {label: function(ctx) { return ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(4); }}}
        },
        scales: {
          x: {ticks: {color: COLORS.tick, maxRotation: 0, autoSkip: true, maxTicksLimit: 12}, grid: {color: COLORS.grid}},
          y: {ticks: {color: COLORS.tick, callback: function(v){return '$' + v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: '$/lb', color: COLORS.tick}}
        }
      }
    });

    var legendEl = document.getElementById('seasonal-legend');
    if (legendEl) {
      var items = [
        {color: COLORS.cme, dash: false, width: 2.5, label: curYear},
        {color: '#a78bfa', dash: true, width: 1.5, label: '5Y avg'}
      ];
      showYears.forEach(function(yr, idx) { items.push({color: priorYearColors[idx], dash: false, width: 1, label: yr}); });
      items.push({color: 'rgba(167,139,250,0.35)', dash: false, width: 8, label: '5Y range'});
      legendEl.innerHTML = items.map(function(it) {
        var sty = 'width:12px;height:' + (it.width > 4 ? '6px;background:' + it.color : it.width + 'px;border-top:' + it.width + 'px ' + (it.dash ? 'dashed' : 'solid') + ' ' + it.color) + ';display:inline-block;border-radius:1px';
        return '<span style="display:flex;align-items:center;gap:4px"><span style="' + sty + '"></span>' + it.label + '</span>';
      }).join('');
    }

    var latestWeek = byYear[curYear] ? byYear[curYear][byYear[curYear].length - 1] : null;
    if (latestWeek) {
      setText('kpi-seas-cur', '$' + latestWeek.price.toFixed(4));
      setText('kpi-seas-cur-label', curYear + ' week ' + latestWeek.week + ' · $/lb');
      var wkAvg = rangeAvg[latestWeek.week - 1];
      var wkMin = rangeMin[latestWeek.week - 1];
      var wkMax = rangeMax[latestWeek.week - 1];
      if (wkAvg !== null) {
        setText('kpi-seas-avg', '$' + wkAvg.toFixed(4));
        setText('kpi-seas-avg-label', 'week ' + latestWeek.week + ' · 5-year avg');
      }
      if (wkMin !== null && wkMax !== null && wkMax > wkMin) {
        var pct = Math.round(((latestWeek.price - wkMin) / (wkMax - wkMin)) * 100);
        pct = Math.max(0, Math.min(100, pct));
        setText('kpi-seas-pct', pct + 'th');
        setText('kpi-seas-pct-label', 'percentile in 5Y range');
        var ptag = document.getElementById('kpi-seas-pct-tag');
        if (ptag) {
          var isHigh = pct >= 75;
          ptag.style.background = isHigh ? '#dc262622' : '#15803d22';
          ptag.style.color = isHigh ? '#f87171' : '#4ade80';
          ptag.style.borderColor = isHigh ? '#dc262644' : '#15803d44';
        }
      }
    }
  }

  // Fundamentals
  if (RAW.fund && RAW.fund.length) {
    var fd2 = RAW.fund;
    var fLabels = fd2.map(function(d){
      var p = d.month.split('-');
      var mn = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      return mn[+p[1]-1] + ' ' + p[0].slice(2);
    });
    var M = 1e6;

    var ctxFP = document.getElementById('chart-fund-prod');
    if (ctxFP) {
      charts.fundProd = new Chart(ctxFP, {
        type: 'bar', data: {labels: fLabels, datasets: [{
          label: 'NFDM Production', data: fd2.map(function(d){return d.nfdm_production ? d.nfdm_production/M : null;}),
          backgroundColor: 'rgba(96,165,250,0.6)', borderColor: 'rgba(96,165,250,0.9)', borderWidth: 1, borderRadius: 2, borderSkipped: false
        }]},
        options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{backgroundColor:'#1f2937',borderColor:'#374151',borderWidth:1,titleColor:'#e6edf3',bodyColor:'#9ca3af',padding:10,callbacks:{label:function(ctx){return (ctx.parsed.y).toFixed(1)+'M lbs';}}}},
          scales:{x:{ticks:{color:COLORS.tick,maxTicksLimit:10,maxRotation:0},grid:{color:COLORS.grid}},y:{ticks:{color:COLORS.tick,callback:function(v){return v+'M';}},grid:{color:COLORS.grid},title:{display:true,text:'M lbs',color:COLORS.tick}}}}
      });
    }

    var ctxFS = document.getElementById('chart-fund-stocks');
    if (ctxFS) {
      charts.fundStocks = new Chart(ctxFS, {
        type: 'line', data: {labels: fLabels, datasets: [{
          label: 'NFDM Stocks', data: fd2.map(function(d){return d.nfdm_stocks ? d.nfdm_stocks/M : null;}),
          borderColor: '#f87171', backgroundColor: 'rgba(248,113,113,0.08)', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true
        }]},
        options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{backgroundColor:'#1f2937',borderColor:'#374151',borderWidth:1,titleColor:'#e6edf3',bodyColor:'#9ca3af',padding:10,callbacks:{label:function(ctx){return (ctx.parsed.y).toFixed(1)+'M lbs';}}}},
          scales:{x:{ticks:{color:COLORS.tick,maxTicksLimit:10,maxRotation:0},grid:{color:COLORS.grid}},y:{ticks:{color:COLORS.tick,callback:function(v){return v+'M';}},grid:{color:COLORS.grid},title:{display:true,text:'M lbs',color:COLORS.tick}}}}
      });
    }

    var ctxFB = document.getElementById('chart-fund-butter');
    if (ctxFB) {
      charts.fundButter = new Chart(ctxFB, {
        type: 'bar', data: {labels: fLabels, datasets: [
          {label: 'Butter Production', data: fd2.map(function(d){return d.butter_production ? d.butter_production/M : null;}), backgroundColor: 'rgba(74,222,128,0.5)', borderColor: 'rgba(74,222,128,0.8)', borderWidth: 1, borderRadius: 2, borderSkipped: false, order: 2},
          {label: 'Butter Stocks', data: fd2.map(function(d){return d.butter_stocks ? d.butter_stocks/M : null;}), type: 'line', borderColor: '#e6a817', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: false, order: 1}
        ]},
        options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:true,labels:{color:COLORS.tick,boxWidth:10,font:{size:10}}}, tooltip:{backgroundColor:'#1f2937',borderColor:'#374151',borderWidth:1,titleColor:'#e6edf3',bodyColor:'#9ca3af',padding:10,callbacks:{label:function(ctx){return ctx.dataset.label+': '+(ctx.parsed.y).toFixed(1)+'M lbs';}}}},
          scales:{x:{ticks:{color:COLORS.tick,maxTicksLimit:10,maxRotation:0},grid:{color:COLORS.grid}},y:{ticks:{color:COLORS.tick,callback:function(v){return v+'M';}},grid:{color:COLORS.grid},title:{display:true,text:'M lbs',color:COLORS.tick}}}}
      });
    }

    var ctxFM = document.getElementById('chart-fund-milk');
    if (ctxFM) {
      var B = 1e9;
      charts.fundMilk = new Chart(ctxFM, {
        type: 'line', data: {labels: fLabels, datasets: [{
          label: 'Milk Production', data: fd2.map(function(d){return d.milk_production ? d.milk_production/B : null;}),
          borderColor: '#a78bfa', backgroundColor: 'rgba(167,139,250,0.08)', borderWidth: 2, pointRadius: 0, tension: 0.3, fill: true
        }]},
        options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{backgroundColor:'#1f2937',borderColor:'#374151',borderWidth:1,titleColor:'#e6edf3',bodyColor:'#9ca3af',padding:10,callbacks:{label:function(ctx){return (ctx.parsed.y).toFixed(2)+'B lbs';}}}},
          scales:{x:{ticks:{color:COLORS.tick,maxTicksLimit:10,maxRotation:0},grid:{color:COLORS.grid}},y:{ticks:{color:COLORS.tick,callback:function(v){return v.toFixed(0)+'B';}},grid:{color:COLORS.grid},title:{display:true,text:'B lbs',color:COLORS.tick}}}}
      });
    }

    function findLatest(arr, key) {
      for (var i = arr.length - 1; i >= 0; i--) { if (arr[i][key]) return {val: arr[i][key], month: arr[i].month, idx: i}; }
      return null;
    }
    function findPrev(arr, key, curIdx) {
      var curMonth = arr[curIdx].month;
      var curMo = +curMonth.split('-')[1], curYr = +curMonth.split('-')[0];
      var prevMo = curMo === 1 ? 12 : curMo - 1;
      var prevYr = curMo === 1 ? curYr - 1 : curYr;
      var target = prevYr + '-' + (prevMo < 10 ? '0' : '') + prevMo;
      for (var i = arr.length - 1; i >= 0; i--) { if (arr[i].month === target && arr[i][key]) return arr[i][key]; }
      return null;
    }
    function fmtM(v) { return (v/M).toFixed(0) + 'M'; }
    function fmtMonthLabel(m) { var p=m.split('-'); var mn=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']; return mn[+p[1]-1]+' '+p[0]; }

    var lp = findLatest(fd2, 'nfdm_production');
    if (lp) {
      setText('kpi-fund-prod', fmtM(lp.val));
      setText('kpi-fund-prod-label', fmtMonthLabel(lp.month));
      var pp = findPrev(fd2, 'nfdm_production', lp.idx);
      if (pp) { var d = lp.val - pp; var e = document.getElementById('kpi-fund-prod-delta'); if(e){e.className='kpi-delta '+(d>=0?'up':'down'); e.textContent=(d>=0?'↑ +':'↓ ')+fmtM(Math.abs(d))+' · vs prior month';} }
    }
    var ls = findLatest(fd2, 'nfdm_stocks');
    if (ls) {
      setText('kpi-fund-stocks', fmtM(ls.val));
      setText('kpi-fund-stocks-label', 'end of ' + fmtMonthLabel(ls.month));
      var ps = findPrev(fd2, 'nfdm_stocks', ls.idx);
      if (ps) { var d2 = ls.val - ps; var e2 = document.getElementById('kpi-fund-stocks-delta'); if(e2){e2.className='kpi-delta '+(d2>=0?'up':'down'); e2.textContent=(d2>=0?'↑ +':'↓ ')+fmtM(Math.abs(d2))+' · vs prior month';} }
    }
    var lb = findLatest(fd2, 'butter_stocks');
    if (lb) {
      setText('kpi-fund-bstocks', fmtM(lb.val));
      setText('kpi-fund-bstocks-label', 'end of ' + fmtMonthLabel(lb.month));
      var pb = findPrev(fd2, 'butter_stocks', lb.idx);
      if (pb) { var d3 = lb.val - pb; var e3 = document.getElementById('kpi-fund-bstocks-delta'); if(e3){e3.className='kpi-delta '+(d3>=0?'up':'down'); e3.textContent=(d3>=0?'↑ +':'↓ ')+fmtM(Math.abs(d3))+' · vs prior month';} }
    }
    var lm = findLatest(fd2, 'milk_production');
    if (lm) {
      setText('kpi-fund-milk', (lm.val/1e9).toFixed(1) + 'B');
      setText('kpi-fund-milk-label', fmtMonthLabel(lm.month));
      var pm = findPrev(fd2, 'milk_production', lm.idx);
      if (pm) { var d4 = lm.val - pm; var e4 = document.getElementById('kpi-fund-milk-delta'); if(e4){e4.className='kpi-delta '+(d4>=0?'up':'down'); e4.textContent=(d4>=0?'↑ +':'↓ ')+(Math.abs(d4)/M).toFixed(0)+'M · vs prior month';} }
    }

    var tblFund = document.getElementById('tbl-fund');
    if (tblFund) {
      var recent = fd2.slice(-18).reverse();
      tblFund.innerHTML = '<tr><th>Month</th><th style="text-align:right">NFDM Prod</th><th style="text-align:right">NFDM Stocks</th><th style="text-align:right">Butter Prod</th><th style="text-align:right">Butter Stocks</th><th style="text-align:right">Milk Prod</th></tr>' +
        recent.map(function(d){
          return '<tr><td>' + d.month + '</td>' +
            '<td style="text-align:right">' + (d.nfdm_production ? fmtM(d.nfdm_production) : '—') + '</td>' +
            '<td style="text-align:right">' + (d.nfdm_stocks ? fmtM(d.nfdm_stocks) : '—') + '</td>' +
            '<td style="text-align:right">' + (d.butter_production ? fmtM(d.butter_production) : '—') + '</td>' +
            '<td style="text-align:right">' + (d.butter_stocks ? fmtM(d.butter_stocks) : '—') + '</td>' +
            '<td style="text-align:right">' + (d.milk_production ? (d.milk_production/1e9).toFixed(1)+'B' : '—') + '</td></tr>';
        }).join('');
    }
  }

  // Futures accuracy
  buildAccuracyView();

  // KPIs
  const latestCme = RAW.cme[RAW.cme.length-1];
  const prevCme = RAW.cme[RAW.cme.length-2];
  const latestNass = RAW.nass[RAW.nass.length-1];
  const prevNass = RAW.nass[RAW.nass.length-2];
  const latestC4 = RAW.c4[RAW.c4.length-1];

  var basisWindow = 30;
  var joined30 = [];
  while (basisWindow <= 90 && !joined30.length) {
    var cmeBW = filterByDays(RAW.cme, basisWindow);
    var nassBW = filterByDays(RAW.nass, basisWindow);
    joined30 = joinCmeNass(cmeBW, nassBW);
    if (!joined30.length) basisWindow += 15;
  }
  const avg30basis = joined30.length ? joined30.reduce(function(s,d){return s+d.basis;}, 0) / joined30.length : 0;
  const latestJoined = joined30[joined30.length-1];

  if (latestCme) {
    setText('kpi-cme', '$' + latestCme.price.toFixed(4));
    setText('kpi-cme-date', 'as of ' + fmtDate(latestCme.date) + ' · $/lb');
    if (prevCme) setDelta('kpi-cme-delta', latestCme.price - prevCme.price, 'day');
    var mtdM = latestCme.date.getMonth(), mtdY = latestCme.date.getFullYear();
    var mtdPrices = RAW.cme.filter(function(d){return d.date.getMonth() === mtdM && d.date.getFullYear() === mtdY;});
    if (mtdPrices.length) {
      var mtdAvg = mtdPrices.reduce(function(s,d){return s+d.price;},0) / mtdPrices.length;
      var monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      setText('kpi-cme-mtd', monthNames[mtdM] + ' MTD avg $' + mtdAvg.toFixed(4) + ' (' + mtdPrices.length + ' obs)');
    }
  }
  if (latestNass) {
    setText('kpi-nass', '$' + latestNass.price.toFixed(4));
    setText('kpi-nass-date', 'week ending ' + fmtDate(latestNass.date) + ' · $/lb');
    if (prevNass) setDelta('kpi-nass-delta', latestNass.price - prevNass.price, 'week');
  }
  if (latestJoined) {
    const cb = latestJoined.basis;
    setText('kpi-basis', '$' + (cb >= 0 ? '+' : '') + cb.toFixed(4));
    setText('kpi-basis-avg', basisWindow + '-day avg $' + avg30basis.toFixed(4));
    const be = document.getElementById('kpi-basis-delta');
    if (be) {
      be.className = 'kpi-delta warn';
      be.textContent = '↗ ' + Math.abs(cb - avg30basis).toFixed(4) + ' · vs ' + basisWindow + '-day';
    }
  }
  if (latestC4) {
    setText('kpi-c4', '$' + latestC4.announced.toFixed(2));
    setText('kpi-c4-implied', 'Implied from CME $' + latestC4.implied.toFixed(2) + ' · $/cwt');
    const cd = latestC4.implied - latestC4.announced;
    const ce = document.getElementById('kpi-c4-delta');
    if (ce) {
      ce.className = 'kpi-delta ' + (cd >= 0 ? 'up' : 'down');
      ce.textContent = (cd >= 0 ? '↗ +' : '↘ ') + cd.toFixed(2) + ' · implied vs actual';
    }
  }

  const c4c = document.getElementById('c4-cards');
  if (c4c && latestC4) {
    const delta = latestC4.implied - latestC4.announced;
    c4c.innerHTML = [
      {l: 'Announced Class IV', v: '$' + latestC4.announced.toFixed(2) + '/cwt', s: 'USDA FMMO – latest', col: '#4ade80'},
      {l: 'Implied Class IV', v: '$' + latestC4.implied.toFixed(2) + '/cwt', s: 'From CME avg + butter', col: '#e6a817'},
      {l: 'Delta', v: (delta >= 0 ? '+' : '') + '$' + delta.toFixed(2) + '/cwt', s: 'Positive = CME leading', col: delta >= 0 ? '#4ade80' : '#f87171'}
    ].map(function(x){return '<div class="c4-card"><div class="c4-label">' + x.l + '</div><div class="c4-val" style="color:' + x.col + '">' + x.v + '</div><div class="c4-sub">' + x.s + '</div></div>';}).join('');
  }

  // Tables
  function buildTable(id, rows, cols) {
    const t = document.getElementById(id);
    if (!t) return;
    t.innerHTML = '<tr>' + cols.map(function(c){return '<th' + (c.right ? ' style="text-align:right"' : '') + '>' + c.h + '</th>';}).join('') + '</tr>' +
      rows.map(function(r){return '<tr>' + cols.map(function(c){return '<td' + (c.right ? ' style="text-align:right' + (c.color ? ';color:' + c.color(r) : '') + '"' : '') + '>' + c.f(r) + '</td>';}).join('') + '</tr>';}).join('');
  }

  const cmeRecent = RAW.cme.slice(-20).reverse();
  buildTable('tbl-cme', cmeRecent, [
    {h: 'Date', f: function(r){return fmtDate(r.date);}},
    {h: 'Price', right: true, f: function(r){return '$' + r.price.toFixed(4);}},
    {h: 'Chg', right: true, f: function(r,i){const idx = cmeRecent.indexOf(r); const next = cmeRecent[idx+1]; if (!next) return '—'; const d = r.price - next.price; return (d >= 0 ? '+' : '') + d.toFixed(4);}, color: function(r){const idx = cmeRecent.indexOf(r); const next = cmeRecent[idx+1]; if (!next) return '#6e7681'; return r.price >= next.price ? '#4ade80' : '#f87171';}}
  ]);

  const nassRecent = RAW.nass.slice(-20).reverse();
  buildTable('tbl-nass', nassRecent, [
    {h: 'Week ending', f: function(r){return fmtDate(r.date);}},
    {h: 'Price', right: true, f: function(r){return '$' + r.price.toFixed(4);}},
    {h: 'Chg', right: true, f: function(r){const idx = nassRecent.indexOf(r); const next = nassRecent[idx+1]; if (!next) return '—'; const d = r.price - next.price; return (d >= 0 ? '+' : '') + d.toFixed(4);}, color: function(r){const idx = nassRecent.indexOf(r); const next = nassRecent[idx+1]; if (!next) return '#6e7681'; return r.price >= next.price ? '#4ade80' : '#f87171';}}
  ]);

  buildTable('tbl-c4', RAW.c4.slice(-18).reverse(), [
    {h: 'Month', f: function(r){return fmtMonth(r.date);}},
    {h: 'Announced', right: true, f: function(r){return '$' + r.announced.toFixed(2);}},
    {h: 'Implied', right: true, f: function(r){return '$' + r.implied.toFixed(2);}, color: function(){return '#e6a817';}},
    {h: 'Delta', right: true, f: function(r){const d = r.implied - r.announced; return (d >= 0 ? '+' : '') + '$' + d.toFixed(2);}, color: function(r){return r.implied - r.announced >= 0 ? '#4ade80' : '#f87171';}},
    {h: 'NFDM', right: true, f: function(r){return '$' + r.nfdm.toFixed(4);}},
    {h: 'Butter', right: true, f: function(r){return '$' + r.butter.toFixed(4);}}
  ]);
}

function computeMonthlyAvgs(cmeData) {
  var byMonth = {};
  cmeData.forEach(function(d) {
    var key = d.date.getFullYear() + '-' + (d.date.getMonth() + 1 < 10 ? '0' : '') + (d.date.getMonth() + 1);
    if (!byMonth[key]) byMonth[key] = [];
    byMonth[key].push(d.price);
  });
  var result = {};
  Object.keys(byMonth).forEach(function(m) {
    var prices = byMonth[m];
    result[m] = +(prices.reduce(function(s,v){return s+v;}, 0) / prices.length).toFixed(4);
  });
  return result;
}

function buildAccuracyView() {
  if (!RAW || !RAW.cme || !RAW.cme.length) return;
  function setText(id, v) { var e = document.getElementById(id); if (e) e.textContent = v; }

  var monthlyActuals = computeMonthlyAvgs(RAW.cme);
  var snapshots = RAW.futHist || [];
  var currentCurve = RAW.futures;

  var allMonths = Object.keys(monthlyActuals).sort();

  var comparisons = [];
  snapshots.forEach(function(snap) {
    var snapDate = snap.trade_date;
    var snapYM = snapDate.slice(0, 7);
    (snap.contracts || []).forEach(function(c) {
      var actual = monthlyActuals[c.month];
      if (actual === undefined) return;
      var snapParts = snapYM.split('-');
      var cParts = c.month.split('-');
      var horizon = (+cParts[0] - +snapParts[0]) * 12 + (+cParts[1] - +snapParts[1]);
      if (horizon < 0) return;
      comparisons.push({
        snapDate: snapDate,
        month: c.month,
        predicted: c.settle,
        actual: actual,
        error: +(c.settle - actual).toFixed(4),
        absError: +Math.abs(c.settle - actual).toFixed(4),
        horizon: horizon
      });
    });
  });

  setText('kpi-acc-snaps', '' + snapshots.length);
  var settledMonths = {};
  comparisons.forEach(function(c) { settledMonths[c.month] = true; });
  setText('kpi-acc-settled', '' + Object.keys(settledMonths).length);

  if (comparisons.length) {
    var totalAbs = 0, totalSigned = 0;
    comparisons.forEach(function(c) { totalAbs += c.absError; totalSigned += c.error; });
    var mae = totalAbs / comparisons.length;
    var bias = totalSigned / comparisons.length;
    setText('kpi-acc-mae', '$' + mae.toFixed(4));
    setText('kpi-acc-mae-label', 'across ' + comparisons.length + ' predictions');
    setText('kpi-acc-bias', (bias >= 0 ? '+' : '') + '$' + bias.toFixed(4));
    setText('kpi-acc-bias-label', bias >= 0 ? 'futures priced above actuals' : 'futures priced below actuals');
  }

  var ctxAcc = document.getElementById('chart-accuracy');
  if (ctxAcc) {
    var futureMonths = currentCurve ? currentCurve.data.map(function(d){return d.month;}) : [];
    var pastMonths = allMonths.filter(function(m) { return m >= '2024-10'; });
    var combinedMonths = pastMonths.slice();
    futureMonths.forEach(function(m) { if (combinedMonths.indexOf(m) < 0) combinedMonths.push(m); });
    combinedMonths.sort();

    var mnArr = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var labels = combinedMonths.map(function(m) {
      var p = m.split('-');
      return mnArr[+p[1]-1] + ' ' + p[0].slice(2);
    });

    var actualLine = combinedMonths.map(function(m) {
      return monthlyActuals[m] !== undefined ? monthlyActuals[m] : null;
    });

    var datasets = [];

    datasets.push({
      label: 'Actual monthly avg',
      data: actualLine,
      borderColor: '#4ade80', backgroundColor: 'rgba(74,222,128,0.08)',
      borderWidth: 2.5, pointRadius: 3, pointBackgroundColor: '#4ade80',
      tension: 0.3, fill: true, order: 1
    });

    var snapColors = ['#6b7280','#9ca3af','#4b5563','#d1d5db','#374151','#78716c','#a3a3a3','#525252'];
    snapshots.forEach(function(snap, idx) {
      var line = combinedMonths.map(function(m) {
        var c = (snap.contracts || []).find(function(ct){return ct.month === m;});
        return c ? c.settle : null;
      });
      var hasAny = line.some(function(v){return v !== null;});
      if (!hasAny) return;

      var mnParts = snap.trade_date.split('-');
      var snapLabel = mnArr[+mnParts[1]-1] + ' ' + mnParts[2] + ', ' + mnParts[0].slice(2);
      datasets.push({
        label: 'Curve ' + snapLabel,
        data: line,
        borderColor: snapColors[idx % snapColors.length],
        borderWidth: 1.5, pointRadius: 0, tension: 0.3,
        borderDash: [5,3], fill: false, order: 3
      });
    });

    if (currentCurve && currentCurve.data.length) {
      var curLine = combinedMonths.map(function(m) {
        var c = currentCurve.data.find(function(ct){return ct.month === m;});
        return c ? c.settle : null;
      });
      datasets.push({
        label: 'Current curve',
        data: curLine,
        borderColor: '#e6a817', backgroundColor: 'rgba(230,168,23,0.08)',
        borderWidth: 2, pointRadius: 3, pointBackgroundColor: '#e6a817',
        tension: 0.3, fill: false, order: 2
      });
    }

    charts.accuracy = new Chart(ctxAcc, {
      type: 'line',
      data: {labels: labels, datasets: datasets},
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {
          legend: {display: false},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            filter: function(item) { return item.raw !== null; },
            callbacks: {label: function(ctx) { return ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(4); }}}
        },
        scales: {
          x: {ticks: {color: COLORS.tick, maxRotation: 45, font: {size: 11}}, grid: {color: COLORS.grid}},
          y: {ticks: {color: COLORS.tick, callback: function(v){return '$' + v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: '$/lb', color: COLORS.tick}}
        }
      }
    });
  }

  // Horizon chart
  var ctxHor = document.getElementById('chart-acc-horizon');
  if (ctxHor && comparisons.length) {
    var byHorizon = {};
    comparisons.forEach(function(c) {
      if (!byHorizon[c.horizon]) byHorizon[c.horizon] = {abs: [], signed: []};
      byHorizon[c.horizon].abs.push(c.absError);
      byHorizon[c.horizon].signed.push(c.error);
    });
    var horizons = Object.keys(byHorizon).map(Number).sort(function(a,b){return a-b;});
    var hLabels = horizons.map(function(h){return h + 'mo';});
    var hMAE = horizons.map(function(h) {
      var arr = byHorizon[h].abs;
      return +(arr.reduce(function(s,v){return s+v;},0) / arr.length).toFixed(4);
    });
    var hBias = horizons.map(function(h) {
      var arr = byHorizon[h].signed;
      return +(arr.reduce(function(s,v){return s+v;},0) / arr.length).toFixed(4);
    });

    charts.accHorizon = new Chart(ctxHor, {
      type: 'bar',
      data: {
        labels: hLabels,
        datasets: [
          {label: 'MAE', data: hMAE, backgroundColor: 'rgba(96,165,250,0.6)', borderColor: 'rgba(96,165,250,0.9)', borderWidth: 1, borderRadius: 3, borderSkipped: false, order: 2},
          {label: 'Bias', data: hBias, type: 'line', borderColor: '#f87171', borderWidth: 2, pointRadius: 4, pointBackgroundColor: '#f87171', tension: 0, fill: false, order: 1}
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {
          legend: {display: true, labels: {color: COLORS.tick, boxWidth: 10, font: {size: 10}}},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            callbacks: {label: function(ctx) { return ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(4); }}}
        },
        scales: {
          x: {ticks: {color: COLORS.tick}, grid: {color: COLORS.grid}},
          y: {ticks: {color: COLORS.tick, callback: function(v){return '$' + v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: '$/lb error', color: COLORS.tick}}
        }
      }
    });
  }

  // Scatter plot
  var ctxScat = document.getElementById('chart-acc-scatter');
  if (ctxScat && comparisons.length) {
    var scatterData = comparisons.map(function(c) {
      return {x: c.predicted, y: c.actual};
    });
    var allVals = comparisons.map(function(c){return c.predicted;}).concat(comparisons.map(function(c){return c.actual;}));
    var scatMin = Math.min.apply(null, allVals) - 0.05;
    var scatMax = Math.max.apply(null, allVals) + 0.05;

    charts.accScatter = new Chart(ctxScat, {
      type: 'scatter',
      data: {
        datasets: [
          {label: 'Predictions', data: scatterData, backgroundColor: 'rgba(230,168,23,0.6)', borderColor: '#e6a817', pointRadius: 5, pointHoverRadius: 7},
          {label: 'Perfect', data: [{x: scatMin, y: scatMin}, {x: scatMax, y: scatMax}], type: 'line', borderColor: '#4ade8066', borderWidth: 1.5, borderDash: [6,3], pointRadius: 0, fill: false}
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: {display: false},
          tooltip: {backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, titleColor: '#e6edf3', bodyColor: '#9ca3af', padding: 10,
            callbacks: {label: function(ctx) { return 'Predicted: $' + ctx.parsed.x.toFixed(4) + ' → Actual: $' + ctx.parsed.y.toFixed(4); }}}
        },
        scales: {
          x: {min: scatMin, max: scatMax, ticks: {color: COLORS.tick, callback: function(v){return '$'+v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: 'Predicted $/lb', color: COLORS.tick}},
          y: {min: scatMin, max: scatMax, ticks: {color: COLORS.tick, callback: function(v){return '$'+v.toFixed(2);}}, grid: {color: COLORS.grid}, title: {display: true, text: 'Actual $/lb', color: COLORS.tick}}
        }
      }
    });
  }

  // Accuracy table
  var tblAcc = document.getElementById('tbl-accuracy');
  if (tblAcc) {
    if (comparisons.length) {
      var byHorizon2 = {};
      comparisons.forEach(function(c) {
        if (!byHorizon2[c.horizon]) byHorizon2[c.horizon] = {abs: [], signed: [], count: 0};
        byHorizon2[c.horizon].abs.push(c.absError);
        byHorizon2[c.horizon].signed.push(c.error);
        byHorizon2[c.horizon].count++;
      });
      var hKeys = Object.keys(byHorizon2).map(Number).sort(function(a,b){return a-b;});
      tblAcc.innerHTML = '<tr><th>Horizon</th><th style="text-align:right">Predictions</th><th style="text-align:right">MAE</th><th style="text-align:right">Bias</th><th style="text-align:right">Max Error</th></tr>' +
        hKeys.map(function(h) {
          var d = byHorizon2[h];
          var mae2 = d.abs.reduce(function(s,v){return s+v;},0) / d.abs.length;
          var bias2 = d.signed.reduce(function(s,v){return s+v;},0) / d.signed.length;
          var maxErr = Math.max.apply(null, d.abs);
          var biasCol = bias2 >= 0 ? '#4ade80' : '#f87171';
          return '<tr><td>' + h + ' month' + (h !== 1 ? 's' : '') + '</td>' +
            '<td style="text-align:right">' + d.count + '</td>' +
            '<td style="text-align:right">$' + mae2.toFixed(4) + '</td>' +
            '<td style="text-align:right;color:' + biasCol + '">' + (bias2 >= 0 ? '+' : '') + '$' + bias2.toFixed(4) + '</td>' +
            '<td style="text-align:right">$' + maxErr.toFixed(4) + '</td></tr>';
        }).join('');
    } else {
      tblAcc.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#6e7681">No settled predictions yet. As futures snapshots accumulate and contract months expire, accuracy data will appear here automatically.</td></tr>';
    }
  }
}

function showView(name, el) {
  document.querySelectorAll('.view').forEach(function(v){v.classList.remove('active');});
  document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active');});
  const v = document.getElementById('view-' + name);
  if (v) v.classList.add('active');
  if (el) el.classList.add('active');
  setTimeout(buildCharts, 50);
}

function setRange(days, btn) {
  activeDays = days;
  document.querySelectorAll('.range-btn').forEach(function(b){b.classList.remove('active');});
  if (btn) btn.classList.add('active');
  buildCharts();
}

async function refreshData() {
  RAW = await fetchLiveData();
  setStatusPill();
  buildCharts();
}

function exportCSV() {
  if (!RAW) return;
  let csv = 'date,cme_spot,nass_weekly,basis,c4_announced,c4_implied\n';
  const cmeFilt = filterByDays(RAW.cme, activeDays);
  const joined = joinCmeNass(cmeFilt, filterByDays(RAW.nass, activeDays));
  cmeFilt.forEach(function(c){
    const n = joined.find(function(j){return Math.abs(j.date - c.date) < 4*86400000;});
    const c4 = RAW.c4.find(function(x){return x.date.getFullYear() === c.date.getFullYear() && x.date.getMonth() === c.date.getMonth();});
    csv += c.date.toISOString().slice(0,10) + ',' + c.price + ',' + (n ? n.price : '') + ',' + (n ? n.basis : '') + ',' + (c4 ? c4.announced : '') + ',' + (c4 ? c4.implied : '') + '\n';
  });
  const blob = new Blob([csv], {type: 'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'nfdm_prices.csv'; a.click();
}

// Mobile sidebar toggle
var sidebar = document.getElementById('sidebar');
var overlay = document.getElementById('sb-overlay');
function toggleSidebar(open) {
  sidebar.classList.toggle('open', open);
  overlay.classList.toggle('open', open);
}
document.getElementById('menu-btn').addEventListener('click', function(){ toggleSidebar(true); });
overlay.addEventListener('click', function(){ toggleSidebar(false); });

// Wire up event listeners (no inline onclick)
document.querySelectorAll('.nav-item').forEach(function(item){
  item.addEventListener('click', function(){ showView(item.dataset.view, item); toggleSidebar(false); });
});
document.querySelectorAll('.range-btn').forEach(function(btn){
  btn.addEventListener('click', function(){ setRange(parseInt(btn.dataset.days, 10), btn); });
});
document.getElementById('refresh-btn').addEventListener('click', refreshData);
document.getElementById('export-btn').addEventListener('click', exportCSV);

// Initial load
(async function(){
  RAW = await fetchLiveData();
  setStatusPill();
  buildCharts();
})();
